"""The one-off import of the site's old rows into the shared package's tables (src/wcs/state.py, ``IMPORTS``;
wts-core docs/adr/0005).

tests/golden/old-state.sql is a state database made by the site's own code before the move, with rows of every
kind: spending last month, this month and today, by two visitors and by nobody, and rows with nothing in the
columns that allowed it; a cached answer that is served, one too old and one that never could be; notes of failed
briefings; saved briefings, one withdrawn; reports open and closed, a takedown request, one with no page and no
contact; statements waiting, published (one updated since), declined and withdrawn, one declined with no closing
time, and one that has waited longer than three months. tests/golden/old-state.json is what the old code read from
it. Here the site as it is now opens that database, and must read the same.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
import shared
from wts_core.testing import FixedClock

from wcs.state import OLD_TABLES, open_state

GOLDEN = shared.read("old-state.json")
NOW = shared.START  # when the old code's figures were read: 13 November 2026


def dump(path: Path) -> list[str]:
    con = sqlite3.connect(path)
    try:
        return list(con.iterdump())
    finally:
        con.close()


def not_the_packages(lines: list[str]) -> list[str]:
    """A dump without the shared package's tables (and SQLite's own count of their rows)."""
    return [line for line in lines if "wts_" not in line and "sqlite_sequence" not in line]


def rows(path: Path, sql: str, *args) -> list[tuple]:
    con = sqlite3.connect(path)
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


@pytest.fixture
def old(settings):
    """A data folder holding the old database."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(settings.state_db)
    con.executescript((shared.GOLDEN / "old-state.sql").read_text(encoding="utf-8"))
    con.close()
    return settings


def test_every_old_row_is_brought_across_and_the_old_tables_are_left_as_they_were(old):
    before = dump(old.state_db)
    counts = {t: rows(old.state_db, f"SELECT count(*) FROM {t}")[0][0] for t in OLD_TABLES}
    assert counts == {"usage": 10, "kv": 1, "answer_cache": 3, "failures": 4, "briefings": 3, "reports": 6,
                      "statements": 7}

    open_state(old, FixedClock(NOW))

    assert not_the_packages(dump(old.state_db)) == not_the_packages(before)  # the old tables, byte for byte
    path = old.state_db
    # spending: row for row; a column that held nothing holds an empty value, which counts for what it counted
    assert rows(path, "SELECT id, at, kind, model, visitor, input_tokens, output_tokens, cache_read_tokens, "
                      "cache_write_tokens, cost_usd, account FROM wts_spending ORDER BY id") == rows(
        path, "SELECT id, coalesce(at, ''), coalesce(kind, ''), model, visitor, coalesce(input_tokens, 0), "
              "coalesce(output_tokens, 0), coalesce(cache_read_tokens, 0), coalesce(cache_write_tokens, 0), "
              "coalesce(cost_usd, 0), NULL FROM usage ORDER BY id")
    assert rows(path, "SELECT at, kind, model, visitor, input_tokens, cost_usd FROM wts_spending WHERE id = 9") == [
        ("", "", None, None, 0, 0.0)]
    # the day's visitor secret, under its own name: a visitor's code, and so their count for today, carries on
    assert rows(path, "SELECT key, value FROM wts_kv WHERE key LIKE 'visitor_salt:%'") == [
        (f"visitor_salt:{shared.DAY}", shared.SALT)]
    # the cache: the two rows that could be served (one of them too old by now); the one with no time and no
    # events, which never could be, is left behind
    assert rows(path, "SELECT key, created_at, events FROM wts_answer_cache ORDER BY key") == rows(
        path, "SELECT key, created_at, events FROM answer_cache WHERE events IS NOT NULL ORDER BY key")
    assert len(rows(path, "SELECT 1 FROM wts_answer_cache")) == 2
    assert rows(path, "SELECT id, at, kind, question, error FROM wts_failures ORDER BY id") == rows(
        path, "SELECT id, coalesce(at, ''), coalesce(kind, ''), question, coalesce(error, '') FROM failures "
              "ORDER BY id")
    # saved briefings: the same ids, and so the same addresses; their events byte for byte
    assert rows(path, "SELECT id, created_at, question, options, events, withdrawn_at, withdrawn_reason, extra "
                      "FROM wts_briefings ORDER BY id") == rows(
        path, "SELECT id, at, question, options, events, withdrawn_at, withdrawn_reason, '{}' FROM briefings "
              "ORDER BY id")
    # reports: an empty page or contact is none; a closed report is recorded as resolved, with no note of either kind
    assert rows(path, "SELECT id, at, kind, page, message, contact, status, closed_at, resolution, public_note, "
                      "briefing_id FROM wts_reports ORDER BY id") == [
        (1, "2026-07-01T09:00:00+00:00", "problem", "/b/Briefing01", "An old report, never closed.",
         "old@example.org", "open", None, None, None, None),
        (2, "2026-11-10T09:00:00+00:00", "error", "/b/Briefing01", "The date is wrong.", "reader@example.org",
         "open", None, None, None, None),
        (3, "2026-11-11T09:00:00+00:00", "takedown", "https://wherecountriesstand.com/b/Briefing02",
         "Please remove my name.", "named@example.org", "open", None, None, None, None),
        (4, "2026-11-12T09:00:00+00:00", "unfair", None, "A loaded word.", None, "open", None, None, None, None),
        (5, "2026-11-12T10:00:00+00:00", "other", None, "Closed the same day.", None, "resolved",
         "2026-11-12T15:00:00+00:00", None, None, None),
        (6, "2026-11-13T08:00:00+00:00", "error", None, "No page and no contact.", None, "open", None, None, None,
         None),
    ]
    # statements: the page is the item, the country whom it is about, the day it was shown its publication
    assert rows(path, "SELECT id, at, item, part, about, name, role, text, link, email, status, published_at, "
                      "updated_at, closed_at, reason FROM wts_statements ORDER BY id") == rows(
        path, "SELECT id, at, page, '', country, name, role, text, nullif(link, ''), nullif(email, ''), status, "
              "shown_on, NULL, ended_at, reason FROM statements ORDER BY id")
    # the log: one row for each entry of the old JSON, in its order, with the note of how the sender was confirmed
    logged = rows(path, "SELECT statement, at, action, note, previous FROM wts_statement_log ORDER BY id")
    kept = [(sid, e["at"], e["action"], e.get("note"), None)
            for sid, log in rows(path, "SELECT id, log FROM statements ORDER BY id") for e in json.loads(log)]
    assert logged == kept and len(logged) == 14
    assert (1, "2026-11-09T09:00:00+00:00", "published", "Reply from the mission's published address, 9 November.",
            None) in logged


def test_a_copy_of_the_database_as_it_was_is_kept_beside_it(old):
    before = dump(old.state_db)
    open_state(old, FixedClock(NOW))
    copy = old.data_dir / f"state.pre-wts-core.{shared.DAY}.db"
    # made once the package's tables exist and before a row is copied into them: the old tables as they were
    assert not_the_packages(dump(copy)) == not_the_packages(before)
    assert rows(copy, "SELECT (SELECT count(*) FROM wts_spending), (SELECT count(*) FROM wts_statements)") == [(0, 0)]
    assert sorted(p.name for p in old.data_dir.glob("state.pre-wts-core.*")) == [copy.name]


def test_a_second_start_imports_nothing(old):
    open_state(old, FixedClock(NOW))
    once = dump(old.state_db)
    later = FixedClock("2026-11-20T12:00:00+00:00")
    open_state(old, later)
    open_state(old, later)
    assert dump(old.state_db) == once
    assert len(list(old.data_dir.glob("state.pre-wts-core.*"))) == 1  # and no second copy


def test_a_new_site_has_nothing_to_import_and_keeps_no_copy(settings):
    db = open_state(settings, FixedClock(NOW))
    assert {"wts_spending", "wts_briefings", "wts_reports", "wts_statements", "wts_briefing_log"} <= db.tables()
    assert not set(OLD_TABLES) & db.tables()
    assert list(settings.data_dir.glob("state.pre-wts-core.*")) == []


def test_the_database_is_marked_as_this_sites(old):
    db = open_state(old, FixedClock(NOW))
    assert db.get("site") == "wcs"


# ---- the site's own figures are the same after the import as before -----------------------------------------------


@pytest.fixture
def site(old, monkeypatch):
    """The site as it is now, opened on the old database at the moment the old code's figures were read."""
    from fastapi.testclient import TestClient

    from wcs import cli
    from wcs.api.app import create_app
    from wcs.reference.countries import countries

    clock = FixedClock(NOW)
    monkeypatch.setattr(cli, "CLOCK", clock)
    pipeline = shared.ScriptedPipeline(clock.advance, old.answer["model"], countries())
    api = TestClient(create_app(old, pipeline=pipeline, state=open_state(old, clock), clock=clock))
    api.clock, api.settings = clock, old
    return api


def run(*args):
    from typer.testing import CliRunner

    from wcs import cli

    result = CliRunner().invoke(cli.app, list(args))
    return [result.exit_code, result.output]


def test_spending_today_and_this_month_and_each_visitors_count_are_what_they_were(site):
    from wcs.spending import summary, warnings

    parts = site.app.state.parts
    assert parts.spending.spent_since(shared.DAY) == pytest.approx(GOLDEN["spent_today"], abs=1e-9)
    assert parts.spending.spent_since(shared.DAY[:7]) == pytest.approx(GOLDEN["spent_this_month"], abs=1e-9)
    for visitor, asked in GOLDEN["briefings_today"].items():
        assert parts.spending.briefings_today(visitor=visitor) == asked, visitor
    # a visitor at their daily limit before the move is at it after: the same words, for the same visitors
    limited = type(parts.caps)(parts.spending, type(parts.limits)(2, None, parts.limits.daily_cap_usd,
                                                                   parts.limits.monthly_cap_usd), parts.messages,
                               site.clock)
    for visitor, said in GOLDEN["refused"].items():
        refusal = limited.refusal(visitor=visitor)
        assert (refusal.message if refusal else None) == said, visitor
    # `wcs usage`: the same report, the same warnings, the same lines printed
    report = summary(site.settings, site.app.state.db, site.clock)
    assert report == GOLDEN["summary"] and warnings(report) == GOLDEN["warnings"]
    assert shared.plain(run("usage"), tmp=site.settings.data_dir.parent) == GOLDEN["usage_command"]
    assert shared.plain(run("usage", "--json"), tmp=site.settings.data_dir.parent) == GOLDEN["usage_command_json"]
    # the day's visitor secret came across, so a visitor's code today is what it was this morning
    assert site.app.state.db.get(f"visitor_salt:{shared.DAY}") == GOLDEN["salt"]


def test_cached_answers_are_still_served_and_too_old_ones_are_not(site):
    cache = site.app.state.parts.cache
    for key, events in GOLDEN["cached"].items():
        assert cache.get(key, 168) == events, key
    assert sum(1 for events in GOLDEN["cached"].values() if events) == 1


def test_saved_briefings_still_open_at_their_addresses(site):
    for bid, was in GOLDEN["briefings"].items():
        r = site.get(f"/api/briefings/{bid}")
        if was is None:
            assert r.status_code == 404
        elif was["withdrawn_at"]:
            assert r.status_code == 410 and r.json()["detail"].endswith(was["withdrawn_reason"])
        else:
            from wcs.rag.checks import rechecked

            assert r.status_code == 200
            assert r.json() == {**{k: was[k] for k in ("id", "at", "question", "options")},
                                "events": rechecked(was["events"])}
            assert site.get(f"/b/{bid}").status_code == 200
    log = site.get("/api/corrections").json()
    assert [{k: entry[k] for k in ("id", "at", "question", "withdrawn_at", "reason")} for entry in log] == \
        GOLDEN["withdrawn"]


def test_statements_are_still_shown_and_reports_still_listed(site):
    for where, was in GOLDEN["published"].items():
        params = {"country": where} if len(where) == 3 else {"page": where}
        assert site.get("/api/statements", params=params).json()["statements"] == was, where
    # the owner's listings: every report, with the takedown request first now; every statement, line for line but
    # for what is said of the three below
    code, text = run("reports", "--all")
    was_code, was_text = GOLDEN["reports_command"]

    def blocks(listing):
        return ["#" + block for block in ("\n" + listing).split("\n#") if block]

    assert code == was_code == 0
    now_blocks, was_blocks = blocks(text), blocks(was_text)
    assert now_blocks[0].startswith("#3 ") and "takedown" in now_blocks[0]
    # report 1 had waited more than three months unanswered: its contact address is gone (below)
    assert [b for b in now_blocks if not b.startswith("#1 ")] == [
        b for b in [was_blocks[3], *was_blocks[:3], *was_blocks[4:]] if not b.startswith("#1 ")]


def test_what_has_waited_too_long_lapses_at_the_first_write_and_its_address_goes(site):
    """wts-core docs/AS-BUILT.md, "replies", "At adoption": the first write or listing after the import applies the
    three months to rows the old code never applied them to. On this database: a statement declined by hand with no
    closing time (the old code judged by the closing time, so its sender's address would have been kept for ever),
    a statement nobody decided since July, and a report open since July."""
    replies = site.app.state.replies
    before = {s["id"]: s for s in GOLDEN["statements"]}
    assert before[6]["status"] == "declined" and before[6]["ended_at"] is None and before[6]["email"]
    assert before[7]["status"] == "pending" and before[7]["email"] == "waiting@example.org"
    now = {s["id"]: s for s in replies.statements()}  # the owner's listing tidies first, as every write does
    assert now[6]["email"] is None  # judged by when it was sent: gone
    assert now[7]["status"] == "declined" and now[7]["email"] is None
    assert now[7]["reason"] == "No decision was made in time; please send it again."
    assert replies.log(7)[-1]["action"] == "lapsed"
    # everything else is as it was: who, what, where, and the addresses still within their three months
    for sid, was in before.items():
        if sid in (6, 7):
            continue
        s = now[sid]
        # statement 4 was withdrawn on 1 August, more than three months ago: its address is due to go, as it was
        # under the old code at its next tidying
        email = None if sid == 4 else (was["email"] or None)
        assert (s["status"], s["email"], s["item"], s["about"], s["name"], s["text"], s["reason"]) == (
            was["status"], email, was["page"], was["country"], was["name"], was["text"], was["reason"]), sid
    assert now[5]["email"] == "mission@example.org"  # withdrawn on 6 November: kept until February
    reports = {r["id"]: r for r in replies.reports(open_only=False)}
    assert reports[1]["contact"] is None and reports[1]["closed_at"] is None  # still open; its address deleted
    assert reports[2]["contact"] == "reader@example.org"
    assert replies.waiting().statements == 1 and replies.waiting().reports == 5
    # the old tables still hold what they held: going back loses nothing
    assert rows(site.settings.state_db, "SELECT email FROM statements WHERE id IN (6, 7) ORDER BY id") == [
        ("mission@example.org",), ("waiting@example.org",)]


def test_new_rows_are_numbered_on_from_the_old_ones(site):
    r = site.post("/api/report", json={"kind": "error", "message": "A new report."})
    assert r.json() == {"ok": True, "id": 7}
    r = site.post("/api/statements", json={"page": "/b/Briefing01", "name": "New Sender", "role": "Ambassador",
                                           "text": "New words.", "email": "new@example.org"})
    assert r.json() == {"ok": True, "id": 8}
