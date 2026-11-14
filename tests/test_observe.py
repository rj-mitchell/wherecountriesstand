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
from wcs.api.app import create_app, start_logging
from wcs.reference.countries import countries
from wcs.state import open_state

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
    assert first["model"] == site.settings.answer["model"] and first["parse_model"] == "claude-haiku-5-5"
    assert first["instructions"] == "3" and first["config_id"] and first["package_version"] == "0.2.0"
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


def test_what_the_reader_did_next_is_noted_on_the_briefings_record(site):
    bid = ask(site)[-1]["briefing_id"]
    assert (records(site)[0]["reported"], records(site)[0]["corrected"], records(site)[0]["followed_up"]) == (0, 0, 0)
    # a follow-up names the briefing it follows on from: a yes on that record, and no link between the two
    ask(site, shared.Q2, history=shared.EARLIER, follows=bid)
    first, second = records(site)
    assert first["followed_up"] == 1 and second["follow_up"] == 1 and bid not in json.dumps(
        {k: v for k, v in second.items() if k != "briefing_id"})
    assert site.post("/api/ask", json={"question": shared.Q2, "follows": "not an id"}).status_code == 422
    # a report that names the briefing; and its withdrawal
    site.post("/api/report", json={"kind": "error", "page": f"https://wherecountriesstand.com/b/{bid}",
                                   "message": "A date is wrong."})
    assert records(site)[0]["reported"] == 1
    run("withdraw", "--reason", "It misread a statement.", "--", bid)
    assert records(site)[0]["corrected"] == 1
    # which briefing a question follows is not saved with the briefing or made part of its cache key
    with site.db.connect() as con:
        assert all("follows" not in r[0] for r in con.execute("SELECT options FROM wts_briefings"))


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
