"""Record what Where Countries Stand's own code gave before it moved onto wts-core (14 November 2026).

Run on the code as it was (the commit this file was added in, or any before the switch):

    uv run python tests/golden/record.py [folder to write into; default: this one]

It writes the JSON files beside it, which tests/test_parity.py and its neighbours compare with what the site gives
now; ``old-state.sql``, a state database made by the old code with rows of every kind, which the one-off import is
tested on; and ``old-index.tar.gz``, a small search index built by the old code, which the new code must open and
search the same. It needs the modules the switch deleted (``wcs.api.usage``, ``wcs.api.statements``, ``wcs.backup``,
``wcs.offsite``, ``wcs.rag.verify`` and the rest), so it does not run on the code as it is now: check out the old
commit to record again.
"""

# ruff: noqa: I001
# The imports below are not sorted by the linter: several are of modules the switch deleted, and the linter sorts
# an import differently when its module is not there (it takes it for an outside library). Sorted either way, the
# other checkout would complain: a fresh one in CI, or one where the old modules' cache folders are still about.

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tarfile
import tempfile
import types
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
os.environ["_TYPER_FORCE_DISABLE_TERMINAL"] = "1"
os.environ["NO_COLOR"] = "1"
for _name in [k for k in os.environ if k.startswith(("WCS_", "ANTHROPIC_"))]:
    del os.environ[_name]

import shared  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

from wcs import backup, cli, offsite  # noqa: E402
from wcs import evaluate as ev  # noqa: E402
from wcs.api import app as web  # noqa: E402
from wcs.api import statements as st  # noqa: E402
from wcs.api import usage as use  # noqa: E402
from wcs.config import REPO_ROOT, load_settings  # noqa: E402
from wcs.embed.base import HashingEmbedder  # noqa: E402
from wcs.engine import Engine, Filters  # noqa: E402
from wcs.index import build as index_build  # noqa: E402
from wcs.index import lance  # noqa: E402
from wcs.rag.british import britishise_segments  # noqa: E402
from wcs.rag.neutral import flag_loaded  # noqa: E402
from wcs.rag.pipeline import Pipeline  # noqa: E402
from wcs.rag.sources import Source  # noqa: E402
from wcs.rag.typography import curl_answer  # noqa: E402
from wcs.rag.verify import check_quotes, drop_preambles, verify  # noqa: E402
from wcs.reference.countries import countries  # noqa: E402
from wcs.store.parquet import write_by_year  # noqa: E402


class Time:
    """The old code reads the clock itself: this stands in for ``datetime`` in its modules, and for ``time``."""

    def __init__(self) -> None:
        self.at = datetime.fromisoformat(shared.START)
        self.seconds = 1000.0

    def set(self, when: str) -> None:
        self.at = datetime.fromisoformat(when)

    def advance(self, seconds: float) -> None:
        self.at += timedelta(seconds=seconds)
        self.seconds += seconds

    def install(self) -> None:
        clock = self

        class Frozen(datetime):
            @classmethod
            def now(cls, tz=None):
                return clock.at

        for module in (use, st, backup, offsite, web):
            module.datetime = Frozen
        use.time = web.time = types.SimpleNamespace(monotonic=lambda: clock.seconds)


TIME = Time()


def environment(data_dir: Path, **more: str) -> None:
    for key in [k for k in os.environ if k.startswith("WCS_")]:
        del os.environ[key]
    os.environ.update({"WCS_DATA_DIR": str(data_dir), **more})


def run_cli(args: list[str]) -> tuple[int, str]:
    result = CliRunner().invoke(cli.app, args)
    return result.exit_code, result.output


# ---- the small tables ----------------------------------------------------------------------------------------------


def limiter() -> dict[str, Any]:
    rate = use.RateLimiter(shared.LIMITER_PER_MINUTE)
    allowed = []
    for t, who in shared.LIMITER_SCRIPT:
        TIME.seconds = 1000.0 + t
        allowed.append(rate.allow(who))
    TIME.seconds = 1000.0
    return {"per_minute": shared.LIMITER_PER_MINUTE, "script": shared.LIMITER_SCRIPT, "allowed": allowed}


def costs() -> list[dict[str, Any]]:
    prices = load_settings().usage
    return [{"usage": u, "model": m, "cost_usd": use.cost_usd(u, prices, m), "stored_as": use.token_counts(u)}
            for u in shared.USAGES for m in shared.MODELS]


def cache_keys(tmp: Path) -> list[dict[str, Any]]:
    """The key ``/api/ask`` works out for each body, as its route does (None: this request has no key)."""
    environment(tmp / "keys")
    settings = load_settings()
    out = []
    for world in shared.KEY_WORLDS:
        for raw in shared.KEY_BODIES:
            body = web.AskBody(**raw)
            key = None
            if not body.history:  # a follow-up is never cached
                key = use.cache_key({"q": " ".join(body.question.lower().split()),
                                     **body.model_dump(exclude={"question"}),
                                     "model": world.get("model", settings.answer.get("model")),
                                     "data": world.get("data", web.data_stamp(settings)),
                                     "instructions": world.get("instructions", web.INSTRUCTIONS_VERSION)})
            out.append({"body": raw, "world": world, "key": key})
    return out


def caps(tmp: Path) -> dict[str, Any]:
    limits = {**load_settings().usage, **shared.CAP_LIMITS}
    state = use.StateDB(tmp / "caps" / "state.db")
    said = []
    for when, what, *rest in shared.CAPS_SCRIPT:
        TIME.set(when)
        if what == "spend":
            kind, model, usage, visitor = rest
            said.append(use.record_usage(state, kind, model, usage, limits, visitor))
        else:
            visitor, pending = rest
            said.append(use.check_caps(state, limits, visitor, pending))
    TIME.set(shared.START)
    flight = use.InFlight(**shared.IN_PROGRESS)
    places = []
    for what, arg in shared.IN_PROGRESS_SCRIPT:
        if what == "start":
            places.append(flight.start(arg))
        elif what == "finish":
            flight.finish(arg)
            places.append(dict(flight.running))
        else:
            places.append(flight.pending_usd(besides=arg))
    return {"limits": shared.CAP_LIMITS, "script": shared.CAPS_SCRIPT, "said": said,
            "in_progress": shared.IN_PROGRESS, "in_progress_script": shared.IN_PROGRESS_SCRIPT, "places": places}


def checks() -> dict[str, Any]:
    sources = [Source(**s) for s in shared.SOURCES]
    written = []
    for case in shared.CHECK_CASES:
        report = verify(case["blocks"], sources)
        drop_preambles(report.segments)
        britishise_segments(report.segments)
        loaded = flag_loaded(report.segments)
        check_quotes(report, sources)
        written.append({"name": case["name"], "answer": curl_answer({**report.to_dict(), "loaded_words": loaded})})
    shown = [{"name": case["name"], "events": web.rechecked(json.loads(json.dumps(case["events"])))}
             for case in shared.SAVED_BRIEFINGS]
    return {"written": written, "shown_again": shown}


# ---- /api/ask ------------------------------------------------------------------------------------------------------


def old_side(folder: Path, pipeline: Any, settings: Any) -> shared.Side:
    state = use.StateDB(settings.data_dir / "state.db")
    with state.connect() as con:
        con.execute("INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)", (f"visitor_salt:{shared.DAY}", shared.SALT))
    ids = (f"briefing{n:03d}" for n in range(1, 100))
    web.new_briefing_id = lambda: next(ids)
    app = web.create_app(settings, pipeline=pipeline, state=state)
    client = TestClient(app)

    def stream(body: dict[str, Any]):
        route = next(r.endpoint for r in app.routes if getattr(r, "path", "") == "/api/ask")
        real, web.StreamingResponse = web.StreamingResponse, lambda content, **kwargs: content
        try:
            request = types.SimpleNamespace(client=types.SimpleNamespace(host="testclient"), headers={}, cookies={})
            return route(web.AskBody(**body), request)
        finally:
            web.StreamingResponse = real

    side = shared.Side(client=client, pipeline=pipeline, connect=lambda: sqlite3.connect(state.path), new=False,
                       stream=stream, another_client=lambda: TestClient(app))
    side.state, side.settings, side.app = state, settings, app
    return side


def settings_in(folder: Path, **usage: Any) -> Any:
    environment(folder / "data")
    settings = load_settings(**shared.SETTINGS_FOR_TESTS)
    settings.raw["usage"] = {**settings.raw["usage"], **usage}
    return settings


def scripted(tmp: Path) -> dict[str, Any]:
    n = iter(range(1000))
    reference = countries()

    def make(has_model: bool = True, **usage: Any) -> shared.Side:
        TIME.set(shared.START)
        folder = tmp / f"scripted-{next(n)}"
        settings = settings_in(folder, **usage)
        pipeline = shared.ScriptedPipeline(TIME.advance, settings.answer["model"], reference, has_model=has_model)
        return old_side(folder, pipeline, settings)

    return shared.scripted_asks(make)


def engine_for(settings: Any, today: Any) -> Engine:
    """The site's search engine over the index in ``settings``, with the hashing embedder."""
    engine = Engine(settings, embedder=HashingEmbedder(64), today=today)
    # Engine.mostly_embedded fails on a machine started less than ten minutes ago (its first check is skipped and
    # nothing is ever kept): the check is made to run, so a recording does not depend on the machine's uptime
    engine._embedded_checked = (None, float("-inf"))
    return engine


def build_index(settings: Any, records: list[dict[str, Any]], dim: int = 64) -> None:
    write_by_year(settings.parquet_dir, "sc", records)
    idx = index_build.Indexer(settings, countries())
    idx.index_all()
    index_build.embed_pending(settings, idx.index, idx.state, HashingEmbedder(dim))
    index_build.refresh_indices(idx.index, idx.state)


def real(tmp: Path) -> dict[str, Any]:
    template = tmp / "real-template"
    build_index(settings_in(template), shared.SPEECHES)
    n = iter(range(1000))

    def make(sdk: Any, **usage: Any):
        TIME.set(shared.START)
        folder = tmp / f"real-{next(n)}"
        shutil.copytree(template, folder)
        settings = settings_in(folder, **usage)
        os.utime(settings.index_dir / "state.json", (shared.STAMP, shared.STAMP))
        side = old_side(folder, Pipeline(settings, engine_for(settings, shared.TODAY), client=sdk,
                                         today=shared.TODAY), settings)

        def get(path: str) -> list[Any]:
            r = side.client.get(path)
            return [r.status_code, r.json()]

        return side, get

    return shared.real_asks(make)


# ---- reports, statements and the owner's commands ------------------------------------------------------------------


def replies_kept(state: use.StateDB) -> dict[str, Any]:
    con = sqlite3.connect(state.path)
    con.row_factory = sqlite3.Row
    try:
        return {"reports": [dict(r) for r in con.execute("SELECT * FROM reports ORDER BY id")],
                "statements": [{**dict(r), "log": json.loads(r["log"])}
                               for r in con.execute("SELECT * FROM statements ORDER BY id")],
                "briefings": [dict(r) for r in con.execute(
                    "SELECT id, at, question, withdrawn_at, withdrawn_reason FROM briefings ORDER BY id")]}
    finally:
        con.close()


def replies(tmp: Path) -> dict[str, Any]:
    TIME.set(shared.START)
    folder = tmp / "replies"
    settings = settings_in(folder)
    pipeline = shared.ScriptedPipeline(TIME.advance, settings.answer["model"], countries())
    side = old_side(folder, pipeline, settings)
    return shared.replies_scenario(
        client=side.client, run=run_cli, tick=TIME.advance,
        save=lambda bid, question, options, events: use.save_briefing(side.state, bid, question, options, events),
        kept=lambda: replies_kept(side.state))


# ---- backups -------------------------------------------------------------------------------------------------------


def backups(tmp: Path) -> dict[str, Any]:
    def alerts(folder: Path, env: dict[str, str], now: str) -> list[dict[str, str]]:
        return offsite.backup_alerts(folder, env, datetime.fromisoformat(now))

    return shared.backups_scenario(
        tmp / "backups", run=run_cli, set_day=lambda day: TIME.set(f"{day}T04:20:00+00:00"),
        environment=environment, alerts=alerts)


def offsite_copies(tmp: Path) -> list[dict[str, Any]]:
    TIME.set("2026-11-03T04:20:05+00:00")
    return shared.offsite_scenario(
        tmp / "offsite", error=offsite.OffsiteError,
        send=lambda archive, keep, env, client: offsite.send(archive, keep=keep, env=env, client=client),
        check=lambda env, client: offsite.check(env=env, client=client))


# ---- a state database made by the old code, with rows of every kind ---------------------------------------------

OLD = "2026-08-01T10:00:00+00:00"       # more than three months before the scenarios' day
KEY_FRESH, KEY_STALE, KEY_BROKEN = "a" * 64, "b" * 64, "c" * 64
VISITOR_A, VISITOR_B = "a1" * 12, "b2" * 12


def old_state(tmp: Path) -> tuple[str, dict[str, Any]]:
    """The database as SQL, and what the old code says of it: the figures the import must leave unchanged."""
    TIME.set(shared.START)
    folder = tmp / "old-state"
    settings = settings_in(folder)
    state = use.StateDB(settings.data_dir / "state.db")
    limits = settings.usage
    with state.connect() as con:
        con.execute("INSERT INTO kv (key, value) VALUES (?, ?)", (f"visitor_salt:{shared.DAY}", shared.SALT))
    # spending: last month, this month, today; two visitors; the two kinds; and what the old columns allow
    for when, kind, model, usage, visitor in (
            ("2026-10-20T08:00:00+00:00", "answer", "claude-sonnet-5-5", shared.USAGES[4], "0f" * 12),
            ("2026-11-02T08:00:00+00:00", "parse", "claude-haiku-5-5", shared.USAGES[5], "1e" * 12),
            ("2026-11-02T08:00:09+00:00", "answer", "claude-sonnet-5-5", shared.USAGES[6], "1e" * 12),
            ("2026-11-13T00:00:00+00:00", "parse", "claude-haiku-5-5", shared.USAGES[5], VISITOR_A),
            ("2026-11-13T00:00:07+00:00", "answer", "claude-sonnet-5-5", shared.USAGES[4], VISITOR_A),
            ("2026-11-13T09:30:00+00:00", "answer", "claude-sonnet-5-5", shared.USAGES[3], VISITOR_A),
            ("2026-11-13T10:00:00+00:00", "answer", "claude-sonnet-5-5", shared.USAGES[4], VISITOR_B),
            ("2026-11-13T10:00:01+00:00", "answer", None, shared.USAGES[3], None)):
        TIME.set(when)
        use.record_usage(state, kind, model, usage, limits, visitor)
    with state.connect() as con:
        con.execute("INSERT INTO usage (visitor, at, kind, model) VALUES (NULL, NULL, NULL, NULL)")
        con.execute("INSERT INTO usage (visitor, at, kind, model, input_tokens, cost_usd, cache_write_tokens) "
                    "VALUES (?, '2026-11-13T11:00:00+00:00', 'answer', 'claude-sonnet-5-5', NULL, NULL, NULL)",
                    (VISITOR_B,))
    # the answer cache: one that is served, one too old, one that never could be
    TIME.set("2026-11-13T11:00:00+00:00")
    use.store_events(state, KEY_FRESH, shared.a_saved_briefing(shared.Q)[:3] + [
        {**shared.a_saved_briefing(shared.Q)[3], "briefing_id": "Briefing01", "cached": True}], 168)
    with state.connect() as con:
        con.execute("INSERT INTO answer_cache (key, created_at, events) VALUES (?, ?, ?)",
                    (KEY_STALE, "2026-11-01T00:00:00+00:00", json.dumps([{"type": "final", "answer": None}])))
        con.execute("INSERT INTO answer_cache (key, created_at, events) VALUES (?, NULL, NULL)", (KEY_BROKEN,))
    # failures
    for when, kind, question, error in (
            ("2026-09-01T00:00:00+00:00", "briefing", "An old question?", "RuntimeError: an old failure"),
            ("2026-11-12T23:00:00+00:00", "question reading", shared.Q, "AuthenticationError: bad key"),
            ("2026-11-13T11:30:00+00:00", "briefing", shared.Q2, "The model declined to answer this question.")):
        TIME.set(when)
        use.record_failure(state, kind, question, error)
    with state.connect() as con:
        con.execute("INSERT INTO failures (at, kind, question, error) VALUES (NULL, NULL, NULL, NULL)")
    # saved briefings, one withdrawn
    for when, bid, question, options in (
            ("2026-10-25T12:00:00+00:00", "Briefing01", shared.Q, {"mode": "summary", "plan": None, "recency": None,
                                                                   "role_weight": None, "sources_only": False}),
            ("2026-11-05T12:00:00+00:00", "Briefing02", shared.Q2, {"mode": "comprehensive"}),
            ("2026-11-06T12:00:00+00:00", "Withdrawn1", "An older question?", {})):
        TIME.set(when)
        use.save_briefing(state, bid, question, options, shared.a_saved_briefing(question))
    TIME.set("2026-11-07T09:00:00+00:00")
    use.withdraw_briefing(state, "Withdrawn1", "It quoted the wrong meeting.")
    # reports: open, closed, a takedown request, and the columns that may be empty
    for when, kind, page, message, contact in (
            ("2026-07-01T09:00:00+00:00", "problem", "/b/Briefing01", "An old report, never closed.", "old@example.org"),
            ("2026-11-10T09:00:00+00:00", "error", "/b/Briefing01", "The date is wrong.", "reader@example.org"),
            ("2026-11-11T09:00:00+00:00", "takedown", "https://wherecountriesstand.com/b/Briefing02",
             "Please remove my name.", "named@example.org"),
            ("2026-11-12T09:00:00+00:00", "unfair", "", "A loaded word.", ""),
            ("2026-11-12T10:00:00+00:00", "other", "", "Closed the same day.", "gone@example.org")):
        TIME.set(when)
        use.add_report(state, kind, page, message, contact)
    TIME.set("2026-11-12T15:00:00+00:00")
    use.close_report(state, 5)
    with state.connect() as con:
        con.execute("INSERT INTO reports (at, kind, page, message, contact) VALUES "
                    "('2026-11-13T08:00:00+00:00', 'error', NULL, 'No page and no contact.', NULL)")
    # statements in every state the old code has
    def sent(when: str, page: str, name: str, text: str, link: str = "", email: str = "mission@example.org") -> int:
        TIME.set(when)
        return st.add_statement(state, page, name, "Permanent Representative", text, link, email)

    one = sent("2026-11-08T09:00:00+00:00", "/b/Briefing01", "Amina Example", shared.WORDS + "\n\nSecond paragraph.",
               "https://undocs.org/en/S/PV.9000")
    TIME.set("2026-11-09T09:00:00+00:00")
    st.publish_statement(state, one, "KEN", checked="Reply from the mission's published address, 9 November.")
    TIME.set("2026-11-10T09:00:00+00:00")
    st.update_statement(state, one, text="New words, as the author sent them.")
    sent("2026-11-12T09:00:00+00:00", "/b/Briefing01", "Still Waiting", "A statement not yet decided.")
    two = sent("2026-11-01T09:00:00+00:00", "/b/Briefing02", "Declined Lately", "About another delegation.")
    TIME.set("2026-11-02T09:00:00+00:00")
    st.decline_statement(state, two, "It is about another delegation's conduct.")
    three = sent("2026-07-01T09:00:00+00:00", "/b/Briefing02", "Withdrawn Long Ago", "Shown for a while.")
    TIME.set("2026-07-02T09:00:00+00:00")
    st.publish_statement(state, three, checked="Telephoned the mission.")
    TIME.set(OLD)
    st.withdraw_statement(state, three, "Withdrawn by its author.")
    four = sent("2026-07-05T09:00:00+00:00", "/b/Briefing02", "Withdrawn Lately", "Shown until last week.")
    TIME.set("2026-07-06T09:00:00+00:00")
    st.publish_statement(state, four, "FJI", checked="Reply from fiji@example.org's own address.")
    TIME.set("2026-11-06T09:00:00+00:00")
    st.withdraw_statement(state, four, "It broke the rules: it named a private person.")
    five = sent("2026-06-01T09:00:00+00:00", "/b/Briefing01", "Declined With No Time", "Never shown.")
    with state.connect() as con:  # declined by hand, long ago, with no closing time: its address must still go
        con.execute("UPDATE statements SET status = 'declined', reason = 'Not from the delegation.' WHERE id = ?",
                    (five,))
    sent("2026-07-01T09:00:00+00:00", "/b/Withdrawn1", "Waiting Too Long", "Nobody decided this one.",
         email="waiting@example.org")
    TIME.set(shared.START)

    # what the old code says of it
    summary = use.summary(state, limits, now=TIME.at)
    with state.connect() as con:
        asked = {v: con.execute("SELECT count(*) FROM usage WHERE visitor = ? AND kind = 'answer' AND at >= ?",
                                (v, shared.DAY)).fetchone()[0] for v in (VISITOR_A, VISITOR_B, "nobody")}
    figures = {
        "summary": summary, "warnings": use.warnings(summary),
        "spent_today": use.spent(state, shared.DAY), "spent_this_month": use.spent(state, shared.DAY[:7]),
        "briefings_today": asked,
        "refused": {v: use.check_caps(state, {**limits, "per_visitor_daily_questions": 2}, v)
                    for v in (VISITOR_A, VISITOR_B, "nobody")},
        "cached": {key: use.cached_events(state, key, 168) for key in (KEY_FRESH, KEY_STALE)},
        "briefings": {bid: use.saved_briefing(state, bid) for bid in ("Briefing01", "Briefing02", "Withdrawn1",
                                                                      "NoSuch0001")},
        "withdrawn": use.withdrawn_briefings(state),
        "reports": use.reports(state, open_only=False),
        "statements": st.statements(state, open_only=False),
        "published": {"/b/Briefing01": st.published_statements(state, "/b/Briefing01"),
                      "/b/Briefing02": st.published_statements(state, "/b/Briefing02"),
                      "KEN": st.published_statements(state, None, "KEN")},
        "salt": state.get(f"visitor_salt:{shared.DAY}"),
        "usage_command": run_cli(["usage"]), "usage_command_json": run_cli(["usage", "--json"]),
        "reports_command": run_cli(["reports", "--all"]), "statements_command": run_cli(["statements", "--all"]),
    }
    con = sqlite3.connect(state.path)
    sql = "\n".join(con.iterdump()) + "\n"
    con.close()
    return sql, shared.plain(figures, tmp=tmp)


# ---- a search index built by the old code ----------------------------------------------------------------------

WORDS = str.split("climate finance adaptation islands sea level security council reform peacekeeping sanctions trade debt "
         "development health vaccines water food energy migration refugees disarmament nuclear treaty oceans "
         "fisheries drought floods famine ceasefire mediation elections justice reparations education women youth "
         "technology artificial intelligence cyber terrorism piracy mining forests biodiversity pollution")
CODES = ("KEN", "FJI", "FRA", "USA", "CHN", "BRA", "IND", "ZAF", "DEU", "JPN", "NGA", "MEX", "EGY", "TUV", "GBR")
QUERIES = ["climate finance for small islands", "nuclear disarmament treaty", "food and water", "peacekeeping"]


def many_speeches(n: int = 300) -> list[dict[str, Any]]:
    from datetime import date

    out = []
    for i in range(n):
        words = [WORDS[(i * 7 + k * 3) % len(WORDS)] for k in range(9)]
        code = CODES[i % len(CODES)]
        d = date(2015 + i % 10, 1 + i % 12, 1 + i % 27)
        out.append(shared.speech(f"sc:S/PV.{8000 + i}:{i % 9:04d}", d, code,
                                 f"The delegation of {code} speaks of {' '.join(words[:5])}. It asks the Council to "
                                 f"consider {' and '.join(words[5:])} without delay.", n=i % 9,
                                 meeting=f"{8000 + i}th meeting", symbol=f"S/PV.{8000 + i}",
                                 participant="guest" if i % 4 == 0 else "member"))
    return out


def old_index(tmp: Path, out: Path) -> dict[str, Any]:
    lance.MIN_VECTOR_INDEX_ROWS = 256  # so that a vector index is trained on a few hundred passages
    folder = tmp / "old-index"
    settings = settings_in(folder)
    build_index(settings, many_speeches())
    index = lance.SearchIndex(settings.index_dir, 64)
    state = index_build.State(settings.index_dir / "state.json")
    embedder = HashingEmbedder(64)
    wheres = [None, "country_code = 'KEN'", "year >= 2020 AND participant = 'guest'"]
    found: dict[str, Any] = {
        "stats": index.stats(), "trained_on": state.vector_index_trained_on,
        "has_word_index": index.has_index("passages", "search_text"),
        "has_vector_index": index.has_index("vectors", "vector"),
        "words": [], "meaning": [], "engine": []}
    for q in QUERIES:
        for where in wheres:
            found["words"].append({"query": q, "where": where,
                                   "hits": shared.close_hits(index.bm25(q, where, 16), "_score")})
            # every vector compared in full, so the answer does not depend on how the index was trained
            found["meaning"].append({"query": q, "where": where, "hits": shared.close_hits(
                index.vector(embedder.embed_query(q), where, 16, exact=True), "_distance")})
    engine = engine_for(settings, shared.TODAY)
    for q in QUERIES:
        for filters in (Filters(), Filters(countries=["FJI", "KEN"])):
            res = engine.search(q, filters, limit=6)
            found["engine"].append({"query": q, "countries": filters.countries, "candidates": res.candidates,
                                    "semantic": res.semantic_available,
                                    "records": [[r["record_id"], round(r["ranking"]["score"], 4)]
                                                for r in res.records]})
    found["rows_for_keys"] = sorted(r["passage_id"] for r in index.rows_for_keys(["sc:2020"]))
    found["embedded_ids"] = sorted(index.embedded_ids(["sc:2020"]))
    found["group_rows"] = sorted([r["passage_id"], sorted(r["groups"] or [])]
                                 for r in index.group_rows("passages"))[:20]
    with tarfile.open(out / "old-index.tar.gz", "w:gz") as tar:
        tar.add(settings.index_dir, arcname="index")
        tar.add(settings.parquet_dir, arcname="parquet")
    lance.MIN_VECTOR_INDEX_ROWS = 10_000
    return found


# ---- the test set, scored as the site scores it ------------------------------------------------------------------


def evaluation(tmp: Path) -> dict[str, Any]:
    folder = tmp / "evaluate"
    settings = settings_in(folder)
    build_index(settings, shared.SPEECHES)
    today, _ = ev.load_questions()
    pipeline = Pipeline(settings, engine_for(settings, today), today=today)
    results = ev.run(settings, pipeline=pipeline)
    return {"summary": ev.summary(results), "results": [asdict(r) for r in results]}


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else shared.GOLDEN
    out.mkdir(parents=True, exist_ok=True)
    TIME.install()
    with tempfile.TemporaryDirectory() as folder:
        tmp = Path(folder)
        shared.write(out, "settings.json", shared.settings_cases(tmp / "settings", REPO_ROOT, load_settings))
        shared.write(out, "limiter.json", limiter())
        shared.write(out, "costs.json", costs())
        shared.write(out, "cache-keys.json", cache_keys(tmp))
        shared.write(out, "caps.json", caps(tmp))
        shared.write(out, "checks.json", checks())
        shared.write(out, "ask-scripted.json", shared.plain(scripted(tmp), tmp=tmp))
        shared.write(out, "ask-real.json", shared.plain(real(tmp), tmp=tmp))
        shared.write(out, "replies.json", shared.plain(replies(tmp), tmp=tmp))
        shared.write(out, "backups.json", backups(tmp))
        shared.write(out, "offsite.json", offsite_copies(tmp))
        sql, figures = old_state(tmp)
        (out / "old-state.sql").write_text(sql, encoding="utf-8")
        shared.write(out, "old-state.json", figures)
        shared.write(out, "index.json", old_index(tmp, out))
        shared.write(out, "evaluation.json", evaluation(tmp))
    print(f"recorded into {out}")


if __name__ == "__main__":
    main()
