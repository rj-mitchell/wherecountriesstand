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
  country's). They are never part of the record (``wcs.api.statements``).
* ``GET /api/countries``, ``GET /api/groups``: for the filters.
* ``GET /api/health``.
* ``GET /robots.txt``, ``GET /sitemap.xml``, and the pages, with the site's address filled in for link previews
  (``[site] url``, or ``WCS_SITE_URL``, or ``https://`` and ``DOMAIN``). The pages share one masthead and footer:
  ``<!--#include header-->`` is replaced by ``web/partials/header.html``, and ``<!--#include family/shelf-->`` by the
  Where They Stand family kit's ``web/site/family/shelf.html`` (see ``render_page``).

Spending is capped per day and per month (``[usage]``), each visitor has a daily limit and a per-minute rate limit,
and identical questions are answered from a cache until the data changes (``wcs.api.usage``).
"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import mimetypes
import os
import queue
import re
import threading
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
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

from wcs.api import statements
from wcs.api.usage import (
    InFlight,
    RateLimiter,
    StateDB,
    add_report,
    cache_key,
    cached_events,
    check_caps,
    new_briefing_id,
    record_failure,
    record_usage,
    save_briefing,
    saved_briefing,
    store_events,
    visitor_key,
    withdrawn_briefings,
)
from wcs.config import REPO_ROOT, Settings, load_settings
from wcs.rag.neutral import still_loaded
from wcs.rag.pipeline import AskRequest, Pipeline
from wcs.rag.query_parse import parse_model
from wcs.rag.typography import curl_events
from wcs.rag.verify import recheck_flags
from wcs.sources import gadebate

log = logging.getLogger(__name__)
mimetypes.add_type("font/woff2", ".woff2")  # missing from slim images' MIME lists

SITE_DIR = REPO_ROOT / "web" / "site"
PARTIALS_DIR = REPO_ROOT / "web" / "partials"
KEEP_ALIVE_SECONDS = 15
# Raise when the briefing instructions change, so cached briefings are written again.
INSTRUCTIONS_VERSION = 3
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


class StatementBody(BaseModel):
    """A statement under the right of reply (``wcs.api.statements``): checked again in the handler, with words for
    the reader."""
    page: str = Field(max_length=300)
    name: str = Field(max_length=200)
    role: str = Field(max_length=200)
    text: str = Field(max_length=statements.MAX_CHARS + 100)
    link: str = Field(default="", max_length=500)
    email: str = Field(max_length=200)


def sse(events: Iterator[dict[str, Any]], keep_alive: float = KEEP_ALIVE_SECONDS) -> Iterator[bytes]:
    """Server-sent events, with a comment line after a few seconds of silence (while the model is thinking): proxies
    close a response that sends nothing for too long. The events are worked out in a thread of their own, which stops
    at the next event once the reader has gone, and closes them, so a briefing nobody is reading stops being written
    (and paid for)."""
    q: queue.Queue = queue.Queue()
    gone = threading.Event()
    end = object()

    def work() -> None:
        try:
            for ev in events:
                q.put(ev)
                if gone.is_set():
                    break
        except Exception as exc:  # passed on, to be raised where the response is being sent
            q.put(exc)
        finally:
            if hasattr(events, "close"):
                events.close()  # here, in the thread that ran it: the model's stream is closed with it
            q.put(end)

    threading.Thread(target=work, name="sse", daemon=True).start()
    try:
        while True:
            try:
                ev = q.get(timeout=keep_alive)
            except queue.Empty:
                yield b": still working\n\n"
                continue
            if ev is end:
                return
            if isinstance(ev, Exception):
                raise ev
            yield f"event: {ev['type']}\ndata: {json.dumps(ev, default=str)}\n\n".encode()
    finally:
        gone.set()


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


def rechecked(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A saved or cached briefing as shown today: its points to check and its judgemental-word marks judged again
    by the current rules, so a mark since found to be wrong is not shown. Nothing is ever newly flagged. (Where MPs
    Stand does the same with its saved briefings.) Its quotes are curled, as a new briefing's are, for one saved
    before they were."""
    return curl_events([{**e, "answer": still_loaded(_recheck_straight(e["answer"]))}
                        if e.get("type") == "final" and isinstance(e.get("answer"), dict) else e for e in events])


_STRAIGHT = str.maketrans("“”‘’", "\"\"''")


def _recheck_straight(answer: dict[str, Any]) -> dict[str, Any]:
    """recheck_flags on the text as the checks first read it, with straight quotes: a briefing saved since quotes
    were curled would otherwise not show a sentence ending in a closing quote (He said “no.” Smith said …) and could
    lose a point to check. Only the statuses come back; the saved text is kept."""
    segs = answer.get("segments")
    if not segs:
        return recheck_flags(answer)
    straight = {**answer, "segments": [{**s, "text": s["text"].translate(_STRAIGHT)}
                                       if isinstance(s.get("text"), str) else s for s in segs]}
    out = recheck_flags(straight)
    if out is straight:
        return answer
    return {**out, "segments": [{**o, "text": s["text"]} if "text" in s else o
                                for o, s in zip(out["segments"], segs, strict=True)]}


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


def create_app(settings: Settings | None = None, pipeline: Pipeline | None = None, state: StateDB | None = None,
               behind_proxy: bool = False) -> FastAPI:
    settings = settings or load_settings()
    state = state or StateDB(settings.data_dir / "state.db")
    limits = settings.usage
    limiter = RateLimiter(int(limits.get("requests_per_minute", 20)))
    cache_hours = float(limits.get("answer_cache_hours", 168))
    inflight = InFlight(int(limits.get("briefings_at_once_per_visitor", 2)), int(limits.get("briefings_at_once", 8)),
                        float(limits.get("briefing_estimate_usd", 0.10)))
    # No generated API pages: Swagger's page loads its script from a CDN and runs inline code, which the site's
    # content security policy blocks, so it showed a blank page. The endpoints are listed at the top of this file.
    app = FastAPI(title="Where Countries Stand", docs_url=None, redoc_url=None, openapi_url=None)
    holder: dict[str, Any] = {"pipeline": pipeline}
    memo: dict[str, tuple[float, Any]] = {}

    def get_pipeline() -> Pipeline:
        if holder["pipeline"] is None:
            holder["pipeline"] = Pipeline(settings)
        return holder["pipeline"]

    def remembered(key: str, compute: Callable[[], Any], ttl: float = 600.0) -> Any:
        hit = memo.get(key)
        if hit and time.monotonic() - hit[0] < ttl:
            return hit[1]
        value = compute()
        memo[key] = (time.monotonic(), value)
        return value

    def visitor(request: Request) -> str:
        address = request.client.host if request.client else None
        if behind_proxy:
            forwarded = request.headers.get("x-forwarded-for", "")
            address = forwarded.split(",")[0].strip() or address
        code = visitor_key(state, address)
        if not limiter.allow(code):
            raise HTTPException(429, "Too many requests. Please wait a minute and try again.")
        return code

    def request_for(body: AskBody) -> AskRequest:
        return AskRequest(question=body.question, mode=body.mode, plan=body.plan, recency=body.recency,
                          role_weight=body.role_weight, history=[t.model_dump() for t in body.history])

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.get("/api/meta")
    def meta():
        return {"briefings": get_pipeline().has_model(), "coverage": remembered("coverage", lambda: coverage(settings)),
                "start_year": settings.start_date.year, "default_years": int(settings.archive.get("default_years", 10))}

    @app.get("/api/briefings/{bid}")
    def briefing(bid: str):
        b = saved_briefing(state, bid)
        if b is None:
            raise HTTPException(404, "There is no briefing at this address.")
        if b["withdrawn_at"]:
            raise HTTPException(410, f"This briefing has been withdrawn: {b['withdrawn_reason']}")
        return {**{k: b[k] for k in ("id", "at", "question", "options")}, "events": rechecked(b["events"])}

    @app.get("/api/corrections")
    def corrections():
        return remembered("corrections", lambda: withdrawn_briefings(state), ttl=60)

    @app.post("/api/report")
    def report(body: ReportBody, request: Request):
        visitor(request)  # the rate limit
        return {"ok": True, "id": add_report(state, body.kind, body.page.strip(), body.message.strip(),
                                             body.contact.strip())}

    # ---- statements under the right of reply (decision 16) ------------------------------------------------------

    def site_hosts(request: Request) -> tuple[str, ...]:
        own = (urlsplit(site_url(settings, request)).hostname or "").lower()
        return (*statements.SITE_HOSTS, *([own] if own else []), (request.url.hostname or "").lower())

    @app.post("/api/statements")
    def add_statement(body: StatementBody, request: Request):
        """A statement as sent: held until the owner has confirmed who sent it, then published or declined."""
        visitor(request)  # the rate limit
        try:
            page = statements.item_address(body.page, site_hosts(request))
            name = statements.check_line(body.name, "your name, as it should be shown")
            role = statements.check_line(body.role, "your role, such as Permanent Representative of Kenya to the "
                                                    "United Nations")
            text = statements.check_text(body.text)
            link = statements.check_link(body.link)
            email = statements.check_email(body.email)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        b = saved_briefing(state, page.removeprefix("/b/"))
        if b is None or b["withdrawn_at"]:
            raise HTTPException(422, "There is no briefing at that address. Check the address of the briefing the "
                                     "statement is about.")
        return {"ok": True, "id": statements.add_statement(state, page, name, role, text, link, email)}

    @app.get("/api/statements")
    def published_statements(page: str = "", country: str = ""):
        """The published statements beside one briefing (``page``, its address) or of one country (``country``)."""
        try:
            item = statements.item_address(page) if page else None
            code = statements.check_country(country)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if not item and not code:
            raise HTTPException(422, "Give the address of a briefing (page) or a country's code (country).")
        return {"statements": statements.published_statements(state, item, code), "note": statements.NOT_ENDORSED}

    def model_available() -> bool:
        """Whether briefings can be written at all (an API key is set), without loading the search engine."""
        if holder["pipeline"] is not None:
            return holder["pipeline"].has_model()
        from wcs.rag.answer import api_key

        return bool(api_key() or os.environ.get("ANTHROPIC_AUTH_TOKEN"))

    @app.get("/api/status")
    def status():
        """For the status page, and to check the site from outside the server: the last data update (when, whether
        it went well, and its warnings, which are about the UN's sources and spending), the latest record from each
        source, and whether new briefings are being written, or paused because a spending cap is reached."""
        def build() -> dict[str, Any]:
            on = model_available()
            paused = check_caps(state, limits) if on else None
            return {**update_status(settings), "checked_at": datetime.now(UTC).isoformat(timespec="seconds"),
                    "briefings": "unavailable" if not on else "paused" if paused else "available",
                    "briefings_note": paused, "coverage": remembered("coverage", lambda: coverage(settings))}
        return remembered("status", build, ttl=60)

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
        key = None
        if model_on and cache_hours > 0 and not body.history:
            key = cache_key({"q": " ".join(body.question.lower().split()), **body.model_dump(exclude={"question"}),
                             "model": settings.answer.get("model"), "data": data_stamp(settings),
                             "instructions": INSTRUCTIONS_VERSION})
        hit = cached_events(state, key, cache_hours) if key and not body.sources_only else None
        blocked = check_caps(state, limits, who) if model_on and hit is None and not body.sources_only else None

        def events() -> Iterator[dict[str, Any]]:
            if hit is not None:
                yield from rechecked(hit)
                return
            if body.sources_only:
                yield from sources_only(body, "Sources only: no briefing was written.")
                return
            if blocked:
                yield from sources_only(body, f"{blocked} The sources found are shown below.")
                return
            kept: list[dict[str, Any]] = []
            text: list[str] = []
            try:
                for ev in p.run(request_for(body)):
                    if ev["type"] == "plan":
                        if ev.get("parse_error"):
                            record_failure(state, "question reading", body.question, ev.pop("parse_error"))
                        if ev.get("usage"):
                            # priced as the model that read it (the briefing's own when no other is set)
                            record_usage(state, "parse", parse_model(settings), ev["usage"], limits, who)
                    if ev["type"] == "error":
                        # the model declined to answer: noted for the owner, and its call still counted as spent
                        record_failure(state, "briefing", body.question, ev.get("message", ""))
                        if ev.get("usage"):
                            record_usage(state, "answer", ev.get("model"), ev["usage"], limits, who)
                        ev = {"type": "error", "message": ev.get("message", "")}
                    if ev["type"] == "final" and ev.get("usage"):
                        record_usage(state, "answer", ev.get("model"), ev["usage"], limits, who)
                    if ev["type"] == "final" and ev.get("answer") and not ev["answer"].get("no_records"):
                        # kept at its own address, /b/<id>, to share and to check complaints against
                        ev = {**ev, "briefing_id": new_briefing_id()}
                        saved = [e for e in kept if e["type"] in ("plan", "sources")]
                        saved += [{"type": "text", "text": "".join(text)}, {**ev, "usage": None}]
                        save_briefing(state, ev["briefing_id"], body.question,
                                      body.model_dump(exclude={"question", "history"}), saved)
                    if ev["type"] == "text":
                        text.append(ev["text"])
                    elif ev["type"] != "citation":
                        kept.append(ev)
                    yield ev
            except Exception as exc:  # never leave the reader hanging
                log.exception("ask failed")
                record_failure(state, "briefing", body.question, f"{type(exc).__name__}: {exc}")
                yield {"type": "error", "message": "The briefing could not be written just now. The sources found "
                                                   "are shown above; please try again later."}
                return
            final = kept[-1] if kept else {}
            if key and final.get("type") == "final" and final.get("answer"):
                replay = [e for e in kept if e["type"] in ("plan", "sources")]
                replay += [{"type": "text", "text": "".join(text)}, {**final, "usage": None, "cached": True}]
                store_events(state, key, replay, cache_hours)

        def guarded() -> Iterator[dict[str, Any]]:
            """A briefing about to be written is reserved at its estimated cost while it runs, and the caps are
            checked again counting the others in progress, so many questions at once cannot all slip past them."""
            if hit is not None or body.sources_only or blocked or not model_on:
                yield from events()
                return
            refused = inflight.start(who)
            if refused:
                yield from sources_only(body, f"{refused} The sources found are shown below.")
                return
            try:
                late = check_caps(state, limits, who, inflight.pending_usd(besides=1))
                if late:
                    yield from sources_only(body, f"{late} The sources found are shown below.")
                    return
                yield from events()
            finally:
                inflight.finish(who)

        return StreamingResponse(sse(guarded()), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

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
        b = saved_briefing(state, bid) if re.fullmatch(r"[A-Za-z0-9_-]{6,20}", bid) else None
        text, base = rendered["index"], site_url(settings, request)
        if b is None:
            return html_page(text.replace(SITE_MARK, base), status_code=404)
        return html_page(question_page(text, b["question"], base, "", f"/b/{bid}"))

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
