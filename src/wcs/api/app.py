"""The web service: the site's pages, and an API for questions, briefings and the record's coverage.

    wcs serve                      # http://127.0.0.1:8000
    wcs serve --behind-proxy       # on a server, behind Caddy (trusts X-Forwarded-For)

Endpoints:

* ``POST /api/ask``: a briefing, streamed as server-sent events (``plan``, ``sources``, ``text``, ``citation``,
  ``final``, ``error``), the same events as ``Pipeline.run``. When a cap is reached, or no API key is set, the
  sources are still shown, which costs nothing.
* ``POST /api/search``: the plan and ranked sources only, read by the built-in rules (no model call).
* ``GET /api/meta``: what the record covers, and whether briefings are being written.
* ``GET /api/status``: the last data update and its warnings, the latest record from each source, and whether
  briefings are available or paused by a spending cap (for ``/status.html``, and checks from outside).
* ``POST /api/report``: a problem report from the corrections page ("Something is wrong", "You've been characterised
  unfairly", a takedown request, or something else).
* ``POST /api/statements``: a statement under the right of reply, held until the owner has confirmed who sent it;
  ``GET /api/statements?page=/b/<id>`` (or ``?country=KEN``): the published statements beside a briefing (or a
  country's). They are never part of the record (``wcs.replies``).
* ``GET /api/briefings/<id>``: a saved briefing; ``GET /api/corrections``: the public log of what has been put right.
* ``GET /api/check``: counts and ages for the charity's nightly check of its sites, to whoever holds the check
  token (``WCS_CHECK_TOKEN`` in deploy/.env; wts-core docs/adr/0010). With no token set, the address isn't there.
* ``GET /api/countries``, ``GET /api/groups``: for the filters.
* ``GET /api/health``.
* ``GET /robots.txt``, ``GET /sitemap.xml``, and the pages, with the site's address filled in for link previews
  (``[site] url``, or ``WCS_SITE_URL``, or ``https://`` and ``DOMAIN``). The pages share one masthead and footer:
  ``<!--#include header-->`` is replaced by ``web/partials/header.html``, and ``<!--#include family/shelf-->`` by the
  Where They Stand family kit's ``web/site/family/shelf.html`` (see ``render_page``).

Spending is capped per day and per month (``[usage]``), each visitor has a daily limit and a per-minute rate limit,
and identical questions are answered from a cache until the data changes. The flow of a question, from its
arrival to its last event, is the Where They Stand sites' shared briefing desk (wts-core, ``wts_core.briefing``;
its docs/adr/0009): the route here describes the request (``Ask``) and streams what the desk gives back. What is
this site's own in that flow is in ``SiteHooks``, ``answer_key`` and ``sources_only`` below.
"""

from __future__ import annotations

import hashlib
import hmac
import html
import json
import logging
import mimetypes
import os
import re
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException
from wts_core.briefing import Ask, Hooks, TooManyRequests, desk_parts, sse
from wts_core.cli import backup_ages, check_route, last_day_figures
from wts_core.events import Event
from wts_core.observe import LOGGER, SqliteBriefingLog, briefing_config, json_logging
from wts_core.ports import SYSTEM_CLOCK, SYSTEM_RANDOM, BriefingConfig, Clock, Random
from wts_core.replies import Item, Refused
from wts_core.state import StateDB
from wts_core.usage import cache_key, client_address, token_id, visitor_code

from wcs import adapters
from wcs.config import INSTRUCTIONS_VERSION, REPO_ROOT, SITE, Settings, load_settings
from wcs.messages import MESSAGES
from wcs.rag.checks import rechecked
from wcs.rag.pipeline import AskRequest, Pipeline
from wcs.rag.query_parse import parse_model
from wcs.replies import (
    MAX_CHARS,
    NOT_ENDORSED,
    RECORD,
    BriefingItems,
    as_shown,
    briefing_id,
    check_country,
    open_replies,
)
from wcs.sources import gadebate
from wcs.state import BACKUPS, open_state

log = logging.getLogger(__name__)
mimetypes.add_type("font/woff2", ".woff2")  # missing from slim images' MIME lists

SITE_DIR = REPO_ROOT / "web" / "site"
PARTIALS_DIR = REPO_ROOT / "web" / "partials"
# so that a proxy passes each event on as it is written
STREAM_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
# How many visitors the per-minute limiter holds before it forgets the idle ones.
FORGET_IDLE_VISITORS_OVER = 50_000
# Pages carry this in place of the site's address (in canonical links and link previews); it is filled in as they
# are served.
SITE_MARK = "__SITE__"
PAGE_NAME = re.compile(r"[a-z0-9-]{1,40}")


class Turn(BaseModel):
    question: str = Field(max_length=500)
    answer: str = Field(default="", max_length=4000)


PLAN_MAX_CHARS = 20_000  # an edited plan is a few hundred characters; this is far more than any reader needs


class AskBody(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    mode: Literal["summary", "comprehensive"] = "summary"
    plan: dict[str, Any] | None = None
    recency: float | None = Field(default=None, ge=0, le=1)
    role_weight: float | None = Field(default=None, ge=1, le=2)
    history: list[Turn] = Field(default_factory=list, max_length=4)
    sources_only: bool = False
    # The saved briefing a follow-up question follows on from, so that briefing's record can note that it was
    # followed up (a yes or no on it; the two questions are not linked). Not part of what an answer depends on.
    follows: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{6,20}$")

    @field_validator("plan")
    @classmethod
    def _plan_size(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        # as Where MPs Stand: a plan is saved with its briefing and goes into the prompt, so it cannot be any size
        if v is not None and len(json.dumps(v, default=str)) > PLAN_MAX_CHARS:
            raise ValueError("the search plan is too large")
        return v


class ReportBody(BaseModel):
    # The first two are the family's routes (the impartiality policy, section 7.1): "Something is wrong" and "You've
    # been characterised unfairly". A statement in someone's own words has its own form (StatementBody). "problem" is
    # what the form sent before the routes were named, and is still taken from links and pages kept from then;
    # "takedown" is a request to remove something about a person, dealt with first.
    kind: Literal["error", "unfair", "problem", "takedown", "other"] = "error"
    page: str = Field(default="", max_length=300)
    message: str = Field(min_length=5, max_length=4000)
    contact: str = Field(default="", max_length=200)
    website: str = Field(default="", max_length=200)  # a trap: no form of the site's has it, so people leave it empty


class StatementBody(BaseModel):
    """A statement under the right of reply (``wcs.replies``): checked again in the handler, with words for the
    reader."""
    page: str = Field(max_length=300)
    name: str = Field(max_length=200)
    role: str = Field(max_length=200)
    text: str = Field(max_length=MAX_CHARS + 100)
    link: str = Field(default="", max_length=500)
    email: str = Field(max_length=200)
    website: str = Field(default="", max_length=200)  # a trap, as on a report


# Browsers and Cloudflare may keep these for a year without asking again: a stylesheet's or script's address
# carries a hash of its content (/styles.css?v=<hash>), so a changed file has a new address, and the fonts' names
# carry their own codes (a changed font must be given a new name). Pages are checked on every visit, so they always
# point at the current files.
FOREVER = "public, max-age=31536000, immutable"
ALWAYS_CHECK = "no-cache"
# The family kit's fonts keep their names when the kit changes, so they are kept for a week, not a year.
A_WEEK = "public, max-age=604800"
A_DAY = "public, max-age=86400"
ASSET_REF = re.compile(r'((?:href|src)=")/([A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)?\.(?:css|js))(")')
# The family kit's partials are put into the pages by the server, not served on their own. Its VERSION file is
# served, as on every site of the family: the charity's nightly family check reads it at /family/VERSION.
FAMILY_PRIVATE = re.compile(r"family/[^/]+\.html")
INCLUDE = re.compile(r"^([ \t]*)<!--#include ([a-z0-9-]+(?:/[a-z0-9-]+)?)-->", re.M)
NAV = re.compile(r"<nav\b.*?</nav>", re.S)


def asset_versions(site: Path) -> dict[str, str]:
    """A short hash of the content of each stylesheet and script in the site's folder and the family kit's."""
    files = [*site.glob("*.css"), *site.glob("*.js"), *site.glob("family/*.css")]
    return {p.relative_to(site).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()[:12] for p in sorted(files)}


def versioned(page: str, versions: dict[str, str]) -> str:
    """The page with its stylesheets' and scripts' addresses carrying their content's hash."""
    return ASSET_REF.sub(lambda m: f"{m[1]}/{m[2]}?v={versions[m[2]]}{m[3]}" if m[2] in versions else m[0], page)


def expand_includes(page: str, site: Path, partials: Path) -> str:
    """The page with each ``<!--#include name-->`` replaced by ``partials/name.html``, or, for ``family/name``, by
    the family kit's ``site/family/name.html``. Partials may include others (the header includes the family's
    endorsement). A missing partial is an error, so a mistake shows when the site starts, not on a visitor's page."""
    def one(m: re.Match) -> str:
        indent, name = m[1], m[2]
        path = site / f"{name}.html" if name.startswith("family/") else partials / f"{name}.html"
        if not path.is_file():
            raise FileNotFoundError(f"{m[0].strip()}: there is no {path}")
        lines = path.read_text(encoding="utf-8").rstrip("\n").split("\n")
        return "\n".join(indent + line if line else line for line in lines)  # indented where it is included

    for _ in range(5):
        if "<!--#include " not in page:
            break
        page = INCLUDE.sub(one, page)
    return page


def mark_current(page: str, here: str | None) -> str:
    """Marks the link to this page in the page's navigation with ``aria-current``, as the masthead and footer are
    shared by every page."""
    if not here:
        return page
    link = f'<a href="{here}">'
    return NAV.sub(lambda m: m[0].replace(link, f'<a href="{here}" aria-current="page">'), page)


def render_page(path: Path, site: Path, partials: Path, versions: dict[str, str]) -> str:
    """A page as served (before the site's address is filled in): partials included, its own link in the
    navigation marked, and its stylesheets' and scripts' addresses carrying their hash."""
    here = None if path.stem == "404" else "/" if path.stem == "index" else f"/{path.name}"
    return versioned(mark_current(expand_includes(path.read_text(encoding="utf-8"), site, partials), here), versions)


class SiteFiles(StaticFiles):
    """The site's files, with how long each may be kept (see FOREVER)."""

    def __init__(self, *, versions: dict[str, str], **kw: Any):
        super().__init__(**kw)
        self.versions = versions

    def cache_control(self, path: str, scope: dict[str, Any]) -> str:
        query = dict(p.split("=", 1) for p in scope.get("query_string", b"").decode().split("&") if "=" in p)
        if path in self.versions and query.get("v") == self.versions[path]:
            return FOREVER
        if path.startswith("fonts/") and path.endswith(".woff2"):
            return FOREVER
        if path.startswith("family/fonts/") and path.endswith(".woff2"):
            return A_WEEK
        if path.endswith((".png", ".svg", ".ico")):
            return A_DAY
        return ALWAYS_CHECK  # pages, and a script or stylesheet asked for without (or with an old) hash

    async def get_response(self, path: str, scope):
        if FAMILY_PRIVATE.fullmatch(path):
            raise HTTPException(404, "Not found")
        response = await super().get_response(path, scope)
        if response.status_code == 404:  # Starlette's own 404.html: shown by the app instead, as for every page
            raise HTTPException(404, "Not found")
        if response.status_code in (200, 304):
            response.headers["Cache-Control"] = self.cache_control(path, scope)
        return response


def data_stamp(settings: Settings) -> str:
    """Changes whenever the index is rebuilt, so cached briefings are not served from older data."""
    path = settings.index_dir / "state.json"
    return str(int(path.stat().st_mtime)) if path.exists() else "none"


def coverage(settings: Settings) -> dict[str, Any]:
    """First and last date, and number of speeches, for each source; the votes' range."""
    import duckdb

    from wcs.store.parquet import record_path, votes_path, years

    out: dict[str, Any] = {}
    con = duckdb.connect()
    for source in ("sc", "gd"):
        files = [str(record_path(settings.parquet_dir, source, y)) for y in years(settings.parquet_dir, source)]
        if files:
            first, last, n = con.execute(f"SELECT min(date), max(date), count(*) FROM read_parquet({files!r}, "
                                         "union_by_name=true)").fetchone()
            out[source] = {"from": first, "to": last, "speeches": n}
    if votes_path(settings.parquet_dir).exists():
        first, last, n = con.execute(f"SELECT min(date), max(date), count(DISTINCT undl_id) FROM "
                                     f"read_parquet('{votes_path(settings.parquet_dir)}')").fetchone()
        out["votes"] = {"from": first, "to": last, "resolutions": n}
    return out


def update_status(settings: Settings) -> dict[str, Any]:
    reports = sorted((settings.data_dir / "logs").glob("update-*.json"))
    if not reports:
        return {"last_update": None}
    r = json.loads(reports[-1].read_text())
    return {"last_update": {k: r.get(k) for k in ("started", "finished", "ok", "latest_meeting", "warnings", "errors")},
            "general_debate": r.get("gd"), "general_debate_website": gadebate.last_check(settings),
            "years_written": r.get("years_written"), "updates_kept": len(reports)}


def site_url(settings: Settings, request: Request) -> str:
    """The site's public address, without a trailing slash."""
    url = settings.section("site").get("url") or os.environ.get("WCS_SITE_URL")
    if not url and os.environ.get("DOMAIN"):
        url = f"https://{os.environ['DOMAIN']}"
    return (url or str(request.base_url)).rstrip("/")


def question_page(page: str, question: str, base: str, query: str, path: str = "/") -> str:
    """The home page for a shared question (``/?q=...``) or a saved briefing (``/b/<id>``): its title and link
    preview name the question. The briefing itself is shown by the page's script, so the page is not for search
    engines to index."""
    q = html.escape(question.strip()[:500], quote=True)
    page = re.sub(r"<title>.*?</title>", f"<title>{q} · Where Countries Stand</title>", page, count=1, flags=re.S)
    page = page.replace('<meta property="og:title" content="Where Countries Stand">',
                        f'<meta property="og:title" content="{q}">', 1)
    here = path + (f"?{html.escape(query, quote=True)}" if query else "")
    page = page.replace(f'content="{SITE_MARK}/"', f'content="{SITE_MARK}{here}"')
    page = page.replace(f'href="{SITE_MARK}/"', f'href="{SITE_MARK}{here}"')
    return page.replace("</head>", '  <meta name="robots" content="noindex">\n</head>', 1).replace(SITE_MARK, base)


def new_briefing_id(chance: Random) -> str:
    """A saved briefing's id, for its address (/b/<id>): eleven characters from the source of chance."""
    return token_id(8, random=chance)


def answer_key(settings: Settings, body: AskBody, *, model: bool, cache_hours: float) -> str | None:
    """The answer cache's key for a question: everything an answer depends on. The question (its case and spacing
    apart), what the reader chose, the model, the data's stamp and the version of the instructions: change any of
    them and an answer written before is not served again. None when nothing is to be cached or looked for: there
    is no model or no cache, or the question follows on from another (its answer depends on what came before)."""
    if not model or cache_hours <= 0 or body.history:
        return None
    return cache_key({"q": " ".join(body.question.lower().split()), **body.model_dump(exclude={"question", "follows"}),
                      "model": settings.answer.get("model"), "data": data_stamp(settings),
                      "instructions": INSTRUCTIONS_VERSION}, length=64)


def filters_set(body: AskBody) -> tuple[str, ...]:
    """Which choices the reader made beyond the question, for the record of the briefing: their names, never their
    values."""
    chosen = [k for k, v in ((body.plan or {}).get("filters") or {}).items() if v not in (None, [], "", True)]
    chosen += [name for name in ("recency", "role_weight") if getattr(body, name) is not None]
    return tuple(sorted(chosen))


def configuration(settings: Settings, log: SqliteBriefingLog | None) -> BriefingConfig:
    """Everything that could change a briefing, and the short id of it all: what goes into each briefing's record,
    so a figure can be compared before and after a change. Never a key, and nothing about a reader. Given the record
    of briefings, the same call keeps the settings behind the id there."""
    answer, embedding = settings.answer, settings.embedding
    return briefing_config(
        log=log, model=str(answer.get("model")), parse_model=parse_model(settings),
        instructions=SITE.instructions_version, site_version=SITE.version,
        also={"ranking": dict(settings.ranking),
              "embedding": {k: embedding.get(k) for k in ("backend", "model", "dim", "passage_words")},
              "answer": {k: answer.get(k) for k in ("summary_sources", "comprehensive_sources", "summary_effort",
                                                    "comprehensive_effort", "query_parse_effort", "max_tokens",
                                                    "use_fallbacks", "cache_instructions")}})


def start_logging(settings: Settings, clock: Clock = SYSTEM_CLOCK) -> logging.Handler:
    """Turn the structured log on for this process: one line of JSON for each stage a briefing reaches and one for
    how it ended, in data/logs/briefings.jsonl. A line holds the record's id, a stage's name, milliseconds and an
    error's class name, and nothing else: never a question, an address or a visitor's code (wts-core
    docs/adr/0012). Kept for 14 days or 50 MB, whichever is less (``[logs]``)."""
    logs = settings.section("logs")
    handler = json_logging(settings.logs_dir / "briefings.jsonl", site=SITE, max_bytes=int(logs["max_bytes"]),
                           keep=int(logs["keep"]), days=int(logs["days"]), clock=clock)
    lines = logging.getLogger(LOGGER)
    lines.addHandler(handler)
    lines.setLevel(logging.INFO)
    lines.propagate = False
    return handler


def hidden_records(hidden: list[dict[str, Any]]) -> dict[str, str]:
    """Of the items hidden now, the records of the archive: each record's id and the note shown in its place."""
    return {h["item"].removeprefix(RECORD): h["note"] for h in hidden if h["item"].startswith(RECORD)}


def without_hidden(events: list[Event], hidden: dict[str, str]) -> list[Event]:
    """A saved or cached briefing's events with each source that is hidden now replaced by the note shown in its
    place: its number stays, so the briefing's references still point somewhere, and nothing else of it is sent."""
    if not hidden:
        return events
    return [{**e, "sources": [{"n": card.get("n"), "kind": "hidden", "note": hidden[card["record_id"]]}
                              if card.get("record_id") in hidden else card for card in e.get("sources") or []]}
            if e.get("type") == "sources" else e for e in events]


class SiteHooks(Hooks):
    """This site's own steps in the flow of a briefing (``wts_core.briefing.Hooks``)."""

    def __init__(self, hidden: Callable[[], list[dict[str, Any]]]) -> None:
        self._hidden = hidden  # the items hidden now, while a concern about each is decided

    def on_cached(self, ask: Ask, events: list[Event]) -> list[Event]:
        """A cached briefing as shown today: its points to check and its judgemental-word marks judged again by
        the current rules, and its quotes curled (``rechecked``). If the briefing itself is hidden while a concern
        about it is decided, the note is sent in its place, with the sources; a source that is hidden is replaced
        by its note."""
        hidden = self._hidden()
        notes = {h["item"]: h["note"] for h in hidden}
        final = next((e for e in events if e.get("type") == "final"), {})
        note = notes.get(f"/b/{final.get('briefing_id')}")
        events = without_hidden(events, hidden_records(hidden))
        if note is not None:
            return [*(e for e in events if e.get("type") in ("plan", "sources")),
                    {"type": "final", "answer": None, "usage": None, "note": note}]
        return rechecked(events)

    def on_event(self, ask: Ask, event: Event) -> Event | None:
        """An error is sent to the reader as its message and nothing else, as it always was here: whatever else
        the pipeline put in the event (the token counts of a call the model declined) is for the spending record,
        which has it by now."""
        if event.get("type") == "error":
            return {"type": "error", "message": event.get("message", "")}
        return event


def create_app(settings: Settings | None = None, pipeline: Pipeline | None = None, state: StateDB | None = None,
               behind_proxy: bool = False, *, clock: Clock = SYSTEM_CLOCK, random: Random = SYSTEM_RANDOM) -> FastAPI:
    settings = settings or load_settings()
    db = state or open_state(settings, clock)
    # Everything the flow of a briefing is made of, from [usage]: prices, limits, the spending record, the caps,
    # the per-minute limiter, the places in progress, the answer cache, saved briefings and the notes of failed
    # briefings (kept 90 days, as the privacy notice says), on the one clock. This is also the start-up check:
    # every limit, price and message the flow needs, with whatever is missing named together in one error.
    parts = desk_parts(settings, db, MESSAGES, new_id=lambda chance: new_briefing_id(chance),
                       at_once_per_visitor="briefings_at_once_per_visitor", forget_over=FORGET_IDLE_VISITORS_OVER,
                       keep_failures=True, clock=clock, random=random)
    record = SqliteBriefingLog(db, clock=clock)  # one record for each question asked (wts-core docs/adr/0012)
    replies = open_replies(db, parts.archive, clock)
    desk = parts.desk(configuration(settings, record), log=record, hooks=SiteHooks(replies.all_hidden),
                      group_of=lambda card: card.get("group"))  # the record counts each briefing's sources by group
    # No generated API pages: Swagger's page loads its script from a CDN and runs inline code, which the site's
    # content security policy blocks, so it showed a blank page. The endpoints are listed at the top of this file.
    app = FastAPI(title="Where Countries Stand", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.parts, app.state.replies, app.state.record, app.state.db = parts, replies, record, db
    holder: dict[str, Any] = {"pipeline": pipeline}
    memo: dict[str, tuple[float, Any]] = {}
    check_token = SITE.env("CHECK_TOKEN")

    def get_pipeline() -> Pipeline:
        if holder["pipeline"] is None:
            holder["pipeline"] = Pipeline(settings)
        p = holder["pipeline"]
        engine = getattr(p, "engine", None)
        if engine is not None and getattr(engine, "hidden_records", None) is None:
            # a record hidden while a concern about it is decided is left out of every search, and so of every
            # new briefing's sources (the engine is told the ids, and knows nothing of why)
            engine.hidden_records = lambda: hidden_records(replies.all_hidden())
        return p

    def remembered(key: str, compute: Callable[[], Any], ttl: float = 600.0) -> Any:
        hit = memo.get(key)
        if hit and clock.monotonic() - hit[0] < ttl:
            return hit[1]
        value = compute()
        memo[key] = (clock.monotonic(), value)
        return value

    def visitor(request: Request) -> str:
        """The day's code for this visitor, counted against the per-minute limit. The address itself is never
        stored or logged."""
        address = client_address(request.headers, request.client.host if request.client else None,
                                 behind_proxy=behind_proxy, trust_forwarded_first=True)
        code = visitor_code(db, address, clock=clock, random=random)
        try:
            desk.allow(code)
        except TooManyRequests as exc:
            raise HTTPException(429, exc.message) from None
        return code

    def request_for(body: AskBody) -> AskRequest:
        return AskRequest(question=body.question, mode=body.mode, plan=body.plan, recency=body.recency,
                          role_weight=body.role_weight, history=[t.model_dump() for t in body.history])

    def is_the_nightly_check(request: Request) -> bool:
        """Whether the request carries the family's check token (wts-core docs/adr/0010). Such a question skips
        the answer cache, or it would prove nothing on six nights in seven, and its record is kept apart from
        readers'."""
        wanted = (check_token or "").strip()
        scheme, _, presented = request.headers.get("authorization", "").partition(" ")
        return bool(wanted) and scheme.lower() == "bearer" and hmac.compare_digest(presented.strip().encode(),
                                                                                  wanted.encode())

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.get("/api/meta")
    def meta():
        return {"briefings": get_pipeline().has_model(), "coverage": remembered("coverage", lambda: coverage(settings)),
                "start_year": settings.start_date.year, "default_years": int(settings.archive.get("default_years", 10))}

    @app.get("/api/briefings/{bid}")
    def briefing(bid: str):
        b = parts.archive.get(bid)
        if b is None:
            raise HTTPException(404, "There is no briefing at this address.")
        if b.withdrawn_at:
            raise HTTPException(410, f"This briefing has been withdrawn: {b.withdrawn_reason}")
        hidden = replies.all_hidden()
        note = next((h["note"] for h in hidden if h["item"] == f"/b/{b.id}"), None)
        if note is not None:  # hidden while a concern about it is decided: the note is shown in its place
            raise HTTPException(423, note)
        return {"id": b.id, "at": b.created_at, "question": b.question, "options": b.options,
                "events": rechecked(without_hidden(b.events, hidden_records(hidden)))}

    @app.get("/api/corrections")
    def corrections():
        """The public log of what has been put right, newest first: briefings withdrawn, with the reason (the
        question, not the briefing), and what the owner chose to say publicly when closing a report."""
        def build() -> list[dict[str, Any]]:
            withdrawn = [{"date": w.withdrawn_at, "briefing_id": w.id, "question": w.question,
                          "note": w.withdrawn_reason,
                          # under the names the corrections page has always read
                          "id": w.id, "at": w.created_at, "withdrawn_at": w.withdrawn_at, "reason": w.withdrawn_reason}
                         for w in parts.archive.withdrawn()]
            return replies.corrections(withdrawn=withdrawn)
        return remembered("corrections", build, ttl=60)

    @app.post("/api/report")
    def report(body: ReportBody, request: Request):
        visitor(request)  # the rate limit
        about = briefing_id(body.page)
        try:
            rid = replies.report(kind=body.kind, page=body.page, message=body.message, contact=body.contact,
                                 briefing_id=about, trap=body.website)
        except Refused as exc:
            raise HTTPException(422, str(exc)) from None
        if about and rid is not None:
            record.mark(about, reported=True)  # what happened next, on that briefing's record
        return {"ok": True, "id": rid}

    # ---- statements under the right of reply (decision 16) ------------------------------------------------------

    def site_hosts(request: Request) -> tuple[str, ...]:
        own = (urlsplit(site_url(settings, request)).hostname or "").lower()
        return (*SITE.hosts, *([own] if own else []), (request.url.hostname or "").lower())

    @app.post("/api/statements")
    def add_statement(body: StatementBody, request: Request):
        """A statement as sent: held until the owner has confirmed who sent it, then published or declined."""
        visitor(request)  # the rate limit
        try:
            beside = Item(address=BriefingItems.address(body.page, site_hosts(request)))
            sid = replies.submit(item=beside, name=body.name, role=body.role, text=body.text, link=body.link,
                                 email=body.email, trap=body.website)
        except Refused as exc:
            raise HTTPException(422, str(exc)) from None
        return {"ok": True, "id": sid}

    @app.get("/api/statements")
    def published_statements(page: str = "", country: str = ""):
        """The published statements beside one briefing (``page``, its address) or of one country (``country``)."""
        try:
            item = BriefingItems.address(page) if page else None
            code = check_country(country)
        except ValueError as exc:  # Refused is one
            raise HTTPException(422, str(exc)) from exc
        if not item and not code:
            raise HTTPException(422, "Give the address of a briefing (page) or a country's code (country).")
        found = replies.published(items=[Item(address=item)] if item else [], about=code)
        if item and code:  # both asked for: the statements beside that briefing that speak for that country
            found = [s for s in found if s["item"] == item and s["about"] == code]
        return {"statements": [as_shown(s) for s in found], "note": NOT_ENDORSED}

    def model_available() -> bool:
        """Whether briefings can be written at all (an API key is set), without loading the search engine."""
        if holder["pipeline"] is not None:
            return holder["pipeline"].has_model()
        return adapters.has_model(settings)

    @app.get("/api/status")
    def status():
        """For the status page, and to check the site from outside the server: the last data update (when, whether
        it went well, and its warnings, which are about the UN's sources and spending), the latest record from each
        source, and whether new briefings are being written, or paused because a spending cap is reached."""
        def build() -> dict[str, Any]:
            on = model_available()
            paused = parts.caps.site_paused() if on else None
            return {**update_status(settings), "checked_at": clock.now().isoformat(timespec="seconds"),
                    "briefings": "unavailable" if not on else "paused" if paused else "available",
                    "briefings_note": paused.message if paused else None,
                    "coverage": remembered("coverage", lambda: coverage(settings))}
        return remembered("status", build, ttl=60)

    # Counts and ages for whoever holds the family's check token, and for no one else: 404 with no token set on
    # this site (as any address of the API that isn't there), 401 for a wrong one. Never a question, a name or a
    # word of a report.
    check = check_route(SITE, token=check_token, replies=replies,
                        backups=lambda: backup_ages(Path(os.environ.get("WCS_BACKUP_DIR") or "/backups"), BACKUPS,
                                                    clock=clock),
                        figures=lambda: last_day_figures(record, clock=clock), clock=clock)

    @app.get("/api/check")
    def api_check(authorization: str | None = Header(None)):
        status_code, answer = check(authorization)
        return JSONResponse(answer, status_code=status_code)

    @app.get("/api/countries")
    def countries_list():
        def build():
            c = get_pipeline().engine.countries
            states = ("member_state", "observer_state", "former_member_state")
            return sorted(({"code": x.code, "name": x.name, "kind": x.kind} for x in c.by_code.values()
                           if x.kind in states), key=lambda x: x["name"])
        return remembered("countries", build, ttl=86400)

    @app.get("/api/groups")
    def groups_list():
        c = get_pipeline().engine.countries
        return [{"key": k, "label": v} for k, v in c.group_labels.items()]

    def sources_only(body: AskBody, note: str) -> Iterator[dict[str, Any]]:
        """The plan and the sources, and no briefing, ending with ``note`` for the reader. The question is always
        read by the built-in rules here, whoever asks and whatever stopped the briefing: it costs nothing."""
        p = get_pipeline()
        req = request_for(body)
        from wcs.rag.query_parse import QueryPlan

        if body.plan:
            plan = QueryPlan.from_dict({"question": body.question, **body.plan})
        else:
            from datetime import date

            from wcs.rag.query_parse import parse_offline

            plan = parse_offline(body.question, p.engine.countries, p.today or date.today(),
                                 int(settings.archive.get("default_years", 10)), settings.start_date)
        result, votes, sources = p.find(plan, req)
        yield {"type": "plan", "plan": plan.to_dict(), "usage": None}
        yield {"type": "sources", "sources": [s.card() for s in sources], "candidates": result.candidates,
               "semantic": result.semantic_available, "evidence": {**result.evidence, "votes": len(votes)}}
        yield {"type": "final", "answer": None, "usage": None, "note": note}

    @app.post("/api/search")
    def search(body: AskBody, request: Request):
        """The plan and ranked sources, read by the built-in rules (no model call)."""
        visitor(request)
        out: dict[str, Any] = {}
        for ev in sources_only(body, ""):
            out[ev["type"]] = ev
        # sources carry dates (a General Assembly vote's, for one), which plain JSON cannot write
        return JSONResponse(jsonable_encoder({"plan": out["plan"]["plan"],
                                              **{k: v for k, v in out["sources"].items() if k != "type"}}))

    @app.post("/api/ask")
    def ask(body: AskBody, request: Request):
        who = visitor(request)
        p = get_pipeline()
        model_on = p.has_model()
        nightly = is_the_nightly_check(request)
        if body.follows and body.history:
            record.mark(body.follows, followed_up=True)  # on the earlier briefing's record: a yes, and no link
        events = desk.handle(Ask(
            question=body.question,
            visitor=who,
            write=lambda: p.run(request_for(body)),
            sources_only=lambda note, free: sources_only(body, note),  # always the built-in rules, free or not
            # a follow-up has no key (answer_key), and neither has the nightly check's question
            cache_key=None if nightly else answer_key(settings, body, model=model_on, cache_hours=parts.cache_hours),
            only_sources=body.sources_only,
            model_available=model_on,
            follow_up=bool(body.history),
            mode=body.mode,
            filters_set=filters_set(body),
            kind="check" if nightly else "reader",
            options=body.model_dump(exclude={"question", "history", "follows"}),  # saved with the briefing
        ))
        return StreamingResponse(sse(events), media_type="text/event-stream", headers=STREAM_HEADERS)

    site = Path(settings.section("site").get("dir") or SITE_DIR)
    partials = Path(settings.section("site").get("partials") or PARTIALS_DIR)
    versions = asset_versions(site) if site.exists() else {}
    # Every page is put together once, as the site starts: a missing partial stops it starting.
    rendered = {p.stem: render_page(p, site, partials, versions)
                for p in sorted(site.glob("*.html")) if PAGE_NAME.fullmatch(p.stem)} if site.exists() else {}

    def html_page(text: str, status_code: int = 200) -> HTMLResponse:
        return HTMLResponse(text, status_code=status_code, headers={"Cache-Control": ALWAYS_CHECK})

    def pages() -> list[str]:
        return sorted(name for name in rendered if name != "404")

    @app.get("/robots.txt", include_in_schema=False)
    def robots(request: Request):
        # /api/ is closed to crawlers: a crawler that runs the page's script would otherwise ask for briefings
        return PlainTextResponse(f"User-agent: *\nAllow: /\nDisallow: /api/\n\n"
                                 f"Sitemap: {site_url(settings, request)}/sitemap.xml\n")

    @app.get("/sitemap.xml", include_in_schema=False)
    def sitemap(request: Request):
        base = site_url(settings, request)
        urls = "".join(f"  <url><loc>{base}/{'' if name == 'index' else name + '.html'}</loc></url>\n"
                       for name in pages())
        return Response('<?xml version="1.0" encoding="UTF-8"?>\n'
                        f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{urls}</urlset>\n',
                        media_type="application/xml")

    def page(request: Request, name: str) -> HTMLResponse:
        if name not in rendered or name == "404":
            raise HTTPException(404, "Not found")
        text, base = rendered[name], site_url(settings, request)
        q = request.query_params.get("q", "") if name == "index" else ""
        if len(q.strip()) >= 3:
            return html_page(question_page(text, q, base, request.url.query))
        return html_page(text.replace(SITE_MARK, base))

    @app.get("/", include_in_schema=False)
    def home(request: Request):
        return page(request, "index")

    @app.get("/{name}.html", include_in_schema=False)
    def named_page(request: Request, name: str):
        return page(request, name)

    @app.get("/b/{bid}", include_in_schema=False)
    def shared_briefing(request: Request, bid: str):
        """A saved briefing: the home page, which loads it, with the question as its title and link preview."""
        b = parts.archive.get(bid) if re.fullmatch(r"[A-Za-z0-9_-]{6,20}", bid) else None
        text, base = rendered["index"], site_url(settings, request)
        if b is None:
            return html_page(text.replace(SITE_MARK, base), status_code=404)
        if replies.hidden(f"/b/{b.id}"):  # hidden for now: the page says so (its script asks), and names nothing
            return html_page(text.replace(SITE_MARK, base).replace(
                "</head>", '  <meta name="robots" content="noindex">\n</head>', 1))
        return html_page(question_page(text, b.question, base, "", f"/b/{bid}"))

    @app.exception_handler(StarletteHTTPException)
    async def not_found(request: Request, exc: StarletteHTTPException):
        """A page of the site's own for an address with nothing there; the API keeps its JSON answers."""
        if exc.status_code == 404 and not request.url.path.startswith("/api/") and "404" in rendered:
            return html_page(rendered["404"].replace(SITE_MARK, site_url(settings, request)), status_code=404)
        return await http_exception_handler(request, exc)

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        """Browsers ask for /favicon.ico whatever the page says: the family kit's, the site's spine in its cloth."""
        icon = site / "family" / "favicon.ico"
        if not icon.is_file():
            raise HTTPException(404, "Not found")
        return FileResponse(icon, media_type="image/x-icon", headers={"Cache-Control": A_DAY})

    if site.exists():
        app.mount("/", SiteFiles(directory=site, html=True, versions=versions), name="site")
    return app
