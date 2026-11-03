"""The web service: the site's pages, and an API for questions, briefings and the record's coverage.

    wcs serve                      # http://127.0.0.1:8000
    wcs serve --behind-proxy       # on a server, behind Caddy (trusts X-Forwarded-For)

Endpoints:

* ``POST /api/ask``: a briefing, streamed as server-sent events (``plan``, ``sources``, ``text``, ``citation``,
  ``final``, ``error``), the same events as ``Pipeline.run``. When a cap is reached, or no API key is set, the
  sources are still shown, which costs nothing.
* ``POST /api/search``: the plan and ranked sources only, read by the built-in rules (no model call).
* ``GET /api/meta``: what the record covers, and whether briefings are being written.
* ``GET /api/countries``, ``GET /api/groups``: for the filters.
* ``GET /api/health``.
* ``GET /robots.txt``, ``GET /sitemap.xml``, and the pages, with the site's address filled in for link previews
  (``[site] url``, or ``WCS_SITE_URL``, or ``https://`` and ``DOMAIN``).

Spending is capped per day and per month (``[usage]``), each visitor has a daily limit and a per-minute rate limit,
and identical questions are answered from a cache until the data changes (``wcs.api.usage``).
"""

from __future__ import annotations

import html
import logging
import mimetypes
import os
import queue
import re
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from wcs.api.usage import (
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
from wcs.rag.pipeline import AskRequest, Pipeline

log = logging.getLogger(__name__)
mimetypes.add_type("font/woff2", ".woff2")  # missing from slim images' MIME lists

SITE_DIR = REPO_ROOT / "web" / "site"
KEEP_ALIVE_SECONDS = 15
# Raise when the briefing instructions change, so cached briefings are written again.
INSTRUCTIONS_VERSION = 2
# Pages carry this in place of the site's address (in canonical links and link previews); it is filled in as they
# are served.
SITE_MARK = "__SITE__"
PAGE_NAME = re.compile(r"[a-z0-9-]{1,40}")


class Turn(BaseModel):
    question: str = Field(max_length=500)
    answer: str = Field(default="", max_length=4000)


class AskBody(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    mode: Literal["summary", "comprehensive"] = "summary"
    plan: dict[str, Any] | None = None
    recency: float | None = Field(default=None, ge=0, le=1)
    role_weight: float | None = Field(default=None, ge=1, le=2)
    history: list[Turn] = Field(default_factory=list, max_length=4)
    sources_only: bool = False


class ReportBody(BaseModel):
    kind: Literal["problem", "takedown", "other"] = "problem"
    page: str = Field(default="", max_length=300)
    message: str = Field(min_length=5, max_length=4000)
    contact: str = Field(default="", max_length=200)


def sse(events: Iterator[dict[str, Any]], keep_alive: float = KEEP_ALIVE_SECONDS) -> Iterator[bytes]:
    """Server-sent events, with a comment line after a few seconds of silence (while the model is thinking): proxies
    close a response that sends nothing for too long. The events are worked out in a thread of their own, which stops
    at the next event once the reader has gone."""
    import json

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
    app = FastAPI(title="Where Countries Stand", docs_url="/api/docs", openapi_url="/api/openapi.json")
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
        return {k: b[k] for k in ("id", "at", "question", "options", "events")}

    @app.get("/api/corrections")
    def corrections():
        return remembered("corrections", lambda: withdrawn_briefings(state), ttl=60)

    @app.post("/api/report")
    def report(body: ReportBody, request: Request):
        visitor(request)  # the rate limit
        return {"ok": True, "id": add_report(state, body.kind, body.page.strip(), body.message.strip(),
                                             body.contact.strip())}

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
                yield from hit
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
                            record_usage(state, "parse", settings.answer.get("query_parse_model"), ev["usage"],
                                         limits, who)
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

        return StreamingResponse(sse(events()), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    site = Path(settings.section("site").get("dir") or SITE_DIR)

    def pages() -> list[str]:
        return sorted(p.stem for p in site.glob("*.html") if PAGE_NAME.fullmatch(p.stem) and p.stem != "404")

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
        path = site / f"{name}.html"
        if not PAGE_NAME.fullmatch(name) or not path.is_file():
            raise HTTPException(404, "Not found")
        text, base = path.read_text(encoding="utf-8"), site_url(settings, request)
        q = request.query_params.get("q", "") if name == "index" else ""
        if len(q.strip()) >= 3:
            return HTMLResponse(question_page(text, q, base, request.url.query))
        return HTMLResponse(text.replace(SITE_MARK, base))

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
        text, base = (site / "index.html").read_text(encoding="utf-8"), site_url(settings, request)
        if b is None:
            return HTMLResponse(text.replace(SITE_MARK, base), status_code=404)
        return HTMLResponse(question_page(text, b["question"], base, "", f"/b/{bid}"))

    if site.exists():
        app.mount("/", StaticFiles(directory=site, html=True), name="site")
    return app
