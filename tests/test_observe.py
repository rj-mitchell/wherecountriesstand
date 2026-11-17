"""The record of each briefing, the structured log, the statistics, the weekly report, the nightly tidy and the
alerts (wts-core's ``wts_core.observe``; its docs/adr/0012), as this site turns them on."""

from __future__ import annotations

import json
import logging
import sqlite3

import pytest
import shared
from fastapi.testclient import TestClient
from typer.testing import CliRunner
from wts_core.adapters.hashing import HashingEmbedder
from wts_core.observe import FIELDS, LOGGER
from wts_core.testing import FixedClock

from wcs import cli
from wcs.api.app import SITE_DIR, create_app, start_logging
from wcs.reference.countries import countries
from wcs.state import open_state

PACKAGE = __import__("importlib.metadata").metadata.version("wts-core")
CANARY = "What did delegations say about the xylophonecanary treaty of 2020?"


@pytest.fixture
def site(settings, monkeypatch):
    """The site over a scripted pipeline, on a fixed clock, with the structured log turned on."""
    clock = FixedClock(shared.START)
    monkeypatch.setattr(cli, "CLOCK", clock)
    monkeypatch.setattr(cli, "_embedder", lambda: HashingEmbedder(64))  # no model is fetched in a test
    handler = start_logging(settings, clock)
    pipeline = shared.ScriptedPipeline(clock.advance, settings.answer["model"], countries())
    db = open_state(settings, clock)
    api = TestClient(create_app(settings, pipeline=pipeline, state=db, clock=clock))
    api.clock, api.settings, api.db, api.pipeline = clock, settings, db, pipeline
    api.log_lines = lambda: (settings.logs_dir / "briefings.jsonl").read_text().splitlines()
    yield api
    logging.getLogger(LOGGER).removeHandler(handler)
    handler.close()


def ask(api, question=shared.Q, play="written", **body):
    if play != "written":
        api.pipeline.plays.append(play)
    sent = shared.events_of(api.post("/api/ask", json={"question": question, **body}).text)
    return [e for e in sent if isinstance(e, dict)]


def records(api):
    con = sqlite3.connect(api.db.path)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute("SELECT * FROM wts_briefing_log ORDER BY rowid")]
    finally:
        con.close()


def run(*args, exit_code=0):
    result = CliRunner().invoke(cli.app, list(args))
    assert result.exit_code == exit_code, result.output
    return result.output


def test_every_question_leaves_one_record_with_what_it_cost_what_the_checks_found_and_how_it_ended(site):
    bid = ask(site)[-1]["briefing_id"]
    ask(site)                                   # the same again: from the cache
    ask(site, shared.Q2, "no_records")
    ask(site, "A third question?", "raises")
    ask(site, "A fourth question?", sources_only=True)
    written = records(site)
    assert [r["outcome"] for r in written] == ["completed", "cached", "no_records", "error", "sources_only"]
    first = written[0]
    assert first["question"] == shared.Q and first["kind"] == "reader" and first["briefing_id"] == bid
    # the question is kept only where the site already kept it (wts-core 0.2.1): with a briefing written, replayed,
    # answered "no records" or failed; never for a request that got the sources only
    assert [r["question"] for r in written] == [shared.Q, shared.Q, shared.Q2, "A third question?", ""]
    assert first["model"] == site.settings.answer["model"] and first["parse_model"] == "claude-haiku-5-5"
    assert first["instructions"] == "3" and first["config_id"] and first["package_version"] == PACKAGE
    assert (first["sources"], json.loads(first["spread"])) == (3, {"KEN": 2, "FJI": 1})  # the sources, by group
    assert (first["citations_total"], first["unsupported"], first["loaded_words"]) == (2, 2, 1)
    assert first["input_tokens"] == 1200 + 9000 and first["cost_usd"] > 0  # the reading and the writing
    assert first["t_parse"] == 400 and first["t_sources_sent"] == 700 and first["t_total"] == 4800
    assert written[3]["error_type"] == "RuntimeError" and "stream broke" not in json.dumps(written[3])
    # never the visitor's code, in any record
    with site.db.connect() as con:
        codes = {r[0] for r in con.execute("SELECT visitor FROM wts_spending") if r[0]}
    assert codes and not any(code in json.dumps(written) for code in codes)
    # the configuration behind the id is kept once, so a figure can be compared before and after a change
    with site.db.connect() as con:
        kept = json.loads(con.execute("SELECT settings FROM wts_configs WHERE config_id = ?",
                                      (first["config_id"],)).fetchone()[0])
    assert kept["ranking"]["max_per_country"] == 2 and kept["embedding"]["backend"] == "hashing"
    assert "api_key" not in json.dumps(kept).lower() and "sk-" not in json.dumps(kept)


def test_what_happened_next_is_noted_on_the_briefings_record(site):
    bid = ask(site)[-1]["briefing_id"]
    assert (records(site)[0]["reported"], records(site)[0]["corrected"], records(site)[0]["followed_up"]) == (0, 0, 0)
    # a report that names the briefing; and its withdrawal
    site.post("/api/report", json={"kind": "error", "page": f"https://wherecountriesstand.com/b/{bid}",
                                   "message": "A date is wrong."})
    assert records(site)[0]["reported"] == 1
    run("withdraw", "--reason", "It misread a statement.", "--", bid)
    assert records(site)[0]["corrected"] == 1


def test_the_api_takes_the_fields_the_pages_send_and_ignores_any_other_as_it_always_did(site):
    """The adoption added two fields the pages never send: `follows` on a question (the briefing a follow-up
    follows on from) and `website` on the two forms (a trap for robots). The independent review found that a
    malformed `follows` was refused where an unknown field had always been ignored, and that any caller could
    mark any briefing "followed up". Both are gone: the API is the one the pages have always used."""
    bid = ask(site)[-1]["briefing_id"]
    # a follow-up, as the page sends it: its own record says it was one; the earlier briefing's is not marked
    for extra in ({}, {"follows": bid}, {"follows": "not an id"}, {"website": "x", "anything": ["else"]}):
        sent = site.post("/api/ask", json={"question": shared.Q2, "history": shared.EARLIER, **extra})
        assert sent.status_code == 200, extra
    first, *later = records(site)
    assert first["followed_up"] == 0 and [r["follow_up"] for r in later] == [1, 1, 1, 1]
    # nothing of an unknown field is kept: not with the briefing, and not in its record
    with site.db.connect() as con:
        kept = json.dumps([tuple(r) for r in con.execute("SELECT options, events FROM wts_briefings")])
    assert "follows" not in kept and "website" not in kept and "anything" not in kept
    # the forms: a `website` field is one more unknown field, and what was sent is kept as it always was
    report = site.post("/api/report", json={"kind": "error", "message": "A date is wrong.", "website": "https://x.example"})
    assert report.status_code == 200 and isinstance(report.json()["id"], int)
    statement = site.post("/api/statements", json={
        "page": f"/b/{bid}", "name": "Ms. Amina Odhiambo", "role": "Deputy Permanent Representative",
        "text": "Our position is set out in document A/79/123.", "email": "press@mission.example",
        "website": "https://x.example"})
    assert statement.status_code == 200 and isinstance(statement.json()["id"], int)
    with site.db.connect() as con:
        assert "x.example" not in json.dumps([tuple(r) for r in con.execute("SELECT * FROM wts_reports")]
                                             + [tuple(r) for r in con.execute("SELECT * FROM wts_statements")])
    # and the pages send neither
    scripts = "".join((SITE_DIR / name).read_text(encoding="utf-8") for name in ("app.js", "corrections.js"))
    assert "follows" not in scripts and "website" not in scripts


def test_the_structured_log_holds_stages_and_times_and_never_the_question(site):
    ask(site, CANARY)
    lines = [json.loads(line) for line in site.log_lines()]
    assert [line["stage"] for line in lines] == ["parsed", "sources_sent", "first_token", "checked", None]
    assert [line["event"] for line in lines] == ["briefing.stage"] * 4 + ["briefing.completed"]
    assert all(set(line) == set(FIELDS) and line["site"] == "wcs" for line in lines)
    (record,) = records(site)
    assert {line["briefing"] for line in lines} == {record["id"]} and len(record["id"]) == 11
    assert "xylophonecanary" not in "\n".join(site.log_lines())
    logs = site.settings.section("logs")  # 14 days or 50 MB, whichever is less
    assert logs["days"] == 14 and logs["max_bytes"] * (logs["keep"] + 1) <= 50_000_000


def test_the_statistics_command_and_the_one_the_site_already_had(site):
    ask(site)
    ask(site)
    ask(site, shared.Q2, "raises")
    figures = json.loads(run("briefing-stats", "--since", "7d", "--json"))["stats"]
    assert figures["period"]["questions"] == 3
    assert {k: v for k, v in figures["period"]["by_outcome"].items() if v} == {"completed": 1, "cached": 1,
                                                                               "error": 1}
    assert "questions" in run("briefing-stats", "--since", "7d")
    # `wcs stats` is still what it was: what is loaded and indexed
    assert "index: {'passages': 0, 'vectors': 0}" in run("stats")
    commands = __import__("typer").main.get_command(cli.app).commands
    assert {"stats", "briefing-stats", "weekly-report", "tidy"} <= set(commands)


def test_the_weekly_report_is_written_and_proposes_thresholds_while_none_are_set(site):
    for n in range(3):
        ask(site, f"What has been said about question {n}?")
    site.clock.set("2026-11-16T07:07:00+00:00")  # the Monday after
    out = run("weekly-report")
    folder = site.settings.reports_dir
    assert sorted(p.name for p in folder.iterdir()) == ["2026-W46.json", "2026-W46.md"]
    assert all(oct(p.stat().st_mode & 0o777) == "0o600" for p in folder.iterdir())  # they quote readers' questions
    page = (folder / "2026-W46.md").read_text()
    assert page.startswith("# Where Countries Stand: how the briefings went, 9 November 2026 to 16 November 2026")
    # no threshold is set, so nothing can trip: the command proposes some from the week's figures, for the owner
    assert site.settings.section("alerts") == {}
    assert "No alert thresholds are set yet." in out and "daily_cost_usd = " in out
    with site.db.connect() as con:
        assert con.execute("SELECT count(*) FROM wts_weekly WHERE week = '2026-W46'").fetchone()[0] >= 1


def test_the_nightly_tidy_deletes_what_the_privacy_page_says_is_deleted(site):
    ask(site, CANARY, "raises")                       # a briefing that could not be written
    ask(site)
    with site.db.connect() as con:
        visitor = con.execute("SELECT visitor FROM wts_spending").fetchone()[0]
    old_copy = site.settings.data_dir / "state.pre-wts-core.2026-08-01.db"
    old_copy.write_bytes(b"the database as it was before the move")
    recent_copy = site.settings.data_dir / "state.pre-wts-core.2026-11-13.db"
    recent_copy.write_bytes(b"x")
    site.clock.advance(days=1)
    assert "right of reply: 0 statements lapsed" in run("tidy")
    with site.db.connect() as con:  # a visitor's code is kept for its own day only
        assert {r[0] for r in con.execute("SELECT visitor FROM wts_spending")} == {""} and visitor
    assert recent_copy.exists()
    site.clock.advance(days=91)                       # 90 days on: the failed briefing's question is removed
    run("tidy")
    kept = records(site)
    assert [r["question"] for r in kept] == ["", shared.Q] and kept[0]["outcome"] == "error"
    assert not old_copy.exists() and not recent_copy.exists()  # older than the 60 backups kept off site
    site.clock.advance(days=310)                      # 13 months on: the records themselves go
    run("tidy")
    assert records(site) == []


def test_an_update_says_when_briefings_are_worse_than_the_owners_thresholds(site):
    from wcs.update import Report, briefings

    ask(site)
    ask(site, shared.Q2, "raises")
    report = Report(started="test")
    briefings(site.settings, report, site.clock)      # no thresholds set: nothing is said
    assert report.errors == [] and report.warnings == []
    site.settings.raw["alerts"] = {"error_rate": 0.1, "daily_cost_usd": 100.0}
    briefings(site.settings, report, site.clock)      # one question in two failed: over the threshold of one in ten
    assert len(report.errors) == 1 and report.errors[0].startswith("briefings: ") and "error" in report.errors[0]
    assert report.needs_attention                     # so the scheduled update exits 1, and the email goes
    site.settings.raw["alerts"] = {"no_such_figure": 1}
    briefings(site.settings, report, site.clock)
    assert any("could not check the alerts" in w for w in report.warnings)


def test_the_familys_check_gets_counts_ages_and_the_weeks_totals_and_nothing_a_reader_wrote(settings, monkeypatch,
                                                                                           tmp_path):
    """`GET /api/check`, for the charity's nightly check of its sites (wts-core docs/adr/0010). From wts-core 0.3.0
    (`cli.latest_week_figures(db)` and `check_route(..., weekly=…)`) its answer has one more key,
    ``weekly``: the newest weekly report's totals, numbers only, so that the charity's check can write the
    family's weekly summary without reaching the server. Every key is named here, and nothing a reader wrote is
    under any of them."""
    monkeypatch.setenv("WCS_CHECK_TOKEN", "a-token-of-the-familys")
    monkeypatch.setenv("WCS_BACKUP_DIR", str(tmp_path / "no-backups-yet"))
    clock = FixedClock(shared.START)
    monkeypatch.setattr(cli, "CLOCK", clock)
    monkeypatch.setattr(cli, "_embedder", lambda: HashingEmbedder(64))
    pipeline = shared.ScriptedPipeline(clock.advance, settings.answer["model"], countries())
    db = open_state(settings, clock)
    api = TestClient(create_app(settings, pipeline=pipeline, state=db, clock=clock))
    api.pipeline = pipeline
    held = {"Authorization": "Bearer a-token-of-the-familys"}
    assert api.get("/api/check").status_code == 401
    assert api.get("/api/check", headers={"Authorization": "Bearer another"}).status_code == 401

    ask(api, CANARY)
    ask(api, CANARY)
    ask(api, shared.Q2, "no_records")
    api.post("/api/report", json={"kind": "error", "message": "The xylophonecanary report.",
                                  "contact": "reader@example.org"})
    answer = api.get("/api/check", headers=held)
    assert answer.status_code == 200
    body = answer.json()
    assert set(body) == {"site", "at", "versions", "waiting", "backups", "figures", "weekly"}
    assert body["site"] == "wcs" and body["versions"]["package"] == PACKAGE
    assert body["waiting"] == {"reports": 1, "statements": 0, "hidden": 0, "oldest_working_days": 0}
    assert body["weekly"] == {"week": None, "figures": {}}  # no weekly report has been written yet

    clock.set("2026-11-16T07:07:00+00:00")  # the Monday after: the timer writes the week's report
    run("weekly-report")
    body = api.get("/api/check", headers=held).json()
    week = body["weekly"]
    assert week["week"] == "2026-W46" and week["figures"]["questions"] == 3

    def leaves(value):
        if isinstance(value, dict):
            for inner in value.values():
                yield from leaves(inner)
        elif isinstance(value, list):
            for inner in value:
                yield from leaves(inner)
        else:
            yield value

    assert all(isinstance(leaf, int | float) or leaf is None for leaf in leaves(week["figures"]))  # numbers only
    said = json.dumps(body)
    for kept_back in ("xylophonecanary", "reader@example.org", shared.Q2, "climate"):
        assert kept_back not in said, kept_back
    # with no token set on the site, the address is not there at all
    monkeypatch.delenv("WCS_CHECK_TOKEN")
    bare = TestClient(create_app(settings, pipeline=pipeline, state=db, clock=clock))
    assert bare.get("/api/check", headers=held).status_code == 404
