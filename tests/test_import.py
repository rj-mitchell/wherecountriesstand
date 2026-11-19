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

from wcs.state import OLD_TABLE_NAMES, open_state

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
    counts = {t: rows(old.state_db, f"SELECT count(*) FROM {t}")[0][0] for t in OLD_TABLE_NAMES}
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
    assert not set(OLD_TABLE_NAMES) & db.tables()
    assert list(settings.data_dir.glob("state.pre-wts-core.*")) == []


def test_the_database_is_marked_as_this_sites(old):
    db = open_state(old, FixedClock(NOW))
    assert db.get("site") == "wcs"


# The site's own figures are the same after the import as before


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



def cap_then(report: dict) -> dict:
    """The summary with today's cap as it was when the golden was recorded: the owner raised the daily cap from $3 to
    $25 on 19 November 2026 (test_parity.CHANGED). Only that figure is put back; everything else is compared as is."""
    today = dict(report["today"])
    assert today["cap_usd"] == 25.0
    return {**report, "today": {**today, "cap_usd": 3.0}}

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
    assert cap_then(report) == GOLDEN["summary"] and warnings(report) == GOLDEN["warnings"]
    # One line is the owner's to notice, and is not what the old code printed: the heading of the days listed.
    # wts-core 0.3.0, where a spending row says who it was for and what time of day it was made for its own day
    # only, keeps a day gone by with no time of day, so "the last 7 days" from this time a week ago would
    # have listed the whole of its first day; the command now asks for whole days, today and the six before, and
    # says so. Every figure, day and line under it is the old code's.
    said = shared.plain(run("usage"), tmp=site.settings.data_dir.parent)
    assert said == [GOLDEN["usage_command"][0], GOLDEN["usage_command"][1].replace(
        "\nLast 7 days:\n", "\nToday and the 6 days before:\n").replace(
        "Today (UTC): $0.06 of $3.00 (2%)", "Today (UTC): $0.06 of $25.00 (0%)")]  # the daily cap, $25 since 19 November
    assert "Last 7 days" in GOLDEN["usage_command"][1]
    code, printed = shared.plain(run("usage", "--json"), tmp=site.settings.data_dir.parent)
    assert '"cap_usd": 25.0' in printed  # the daily cap, $25 since 19 November 2026; only that figure differs
    assert [code, printed.replace('"cap_usd": 25.0', '"cap_usd": 3.0', 1)] == GOLDEN["usage_command_json"]
    # the day's visitor secret came across, so a visitor's code today is what it was this morning
    assert site.app.state.db.get(f"visitor_salt:{shared.DAY}") == GOLDEN["salt"]


def test_the_owners_list_of_days_is_whole_days_and_its_heading_says_which(site):
    """`wcs usage` lists each day's spending for today and the days before it, and says exactly that: with a row
    on the morning of the eighth day back, which on 0.2.1 fell outside "the last 7 days" from this time a week ago
    and on 0.3.0 would have been counted whole, it is listed with neither heading's period but its own."""
    from wcs.spending import summary

    path = site.settings.state_db
    con = sqlite3.connect(path)
    for day in ("2026-11-06", "2026-11-07"):  # the eighth day back, and the seventh
        con.execute("INSERT INTO wts_spending (at, kind, model, visitor, cost_usd) VALUES (?, 'answer', "
                    "'claude-sonnet-5-5', '', 0.5)", (f"{day}T00:00:00+00:00",))
    con.commit()
    con.close()
    report = summary(site.settings, site.app.state.db, site.clock)
    assert [d["day"] for d in report["by_day"]] == ["2026-11-07", "2026-11-13"]
    code, said = run("usage")
    assert "\nToday and the 6 days before:\n  2026-11-07  $0.50  briefings: 1\n  2026-11-13" in said
    assert "2026-11-06" not in said
    code, said = run("usage", "--days", "1")
    assert "\nToday:\n  2026-11-13  $0.06  briefings: 5\n" in said and "2026-11-07" not in said


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
    """The first write or listing after the import applies the
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


def test_what_would_lapse_is_said_beforehand_and_nothing_is_done(site):
    """`wcs would-lapse` (wts-core 0.3.0): what has waited three months undecided, read and not done. Numbers,
    dates and ages: no name, address or word of what was sent."""
    path = site.settings.state_db
    before = dump(path)
    code, said = run("would-lapse")
    assert code == 0 and said.splitlines() == [
        "sent before 2026-08-13 and still undecided: 1 statements, 1 reports with a contact address",
        "    statement 7: sent 2026-07-01, 135 days ago: it would be declined as lapsed, and its confirming address "
        "deleted",
        "    report 1: sent 2026-07-01, 135 days ago: it would stay open, and its contact address would be deleted"]
    assert dump(path) == before  # read, and nothing done
    for kept_back in ("example.org", "Waiting Too Long", "An old report"):
        assert kept_back not in said


def test_new_rows_are_numbered_on_from_the_old_ones(site):
    r = site.post("/api/report", json={"kind": "error", "message": "A new report."})
    assert r.json() == {"ok": True, "id": 7}
    r = site.post("/api/statements", json={"page": "/b/Briefing01", "name": "New Sender", "role": "Ambassador",
                                           "text": "New words.", "email": "new@example.org"})
    assert r.json() == {"ok": True, "id": 8}


# Going back


UNDO = shared.GOLDEN.parents[1] / "deploy" / "undo-wts-core.sql"
REDO = shared.GOLDEN.parents[1] / "deploy" / "redo-wts-core.sql"
COPIED = (("usage", "wts_spending"), ("answer_cache", "wts_answer_cache"), ("failures", "wts_failures"),
          ("briefings", "wts_briefings"), ("reports", "wts_reports"), ("statements", "wts_statements"))


def script(path: Path, which: Path) -> None:
    """Run one of deploy/'s SQL scripts on the database, as docs/OPERATIONS.md says (Python's sqlite3, not `wcs`)."""
    con = sqlite3.connect(path)
    try:
        con.executescript(which.read_text(encoding="utf-8"))
    finally:
        con.close()


def test_going_back_writes_the_old_tables_again_with_what_has_come_in_since(site):
    """deploy/undo-wts-core.sql: after the move and some days of use, the old tables hold everything the old code
    reads, under its own names and within its own rules.

    From wts-core 0.3.0 (`state.StateError` and `Import(written_at=…)`; its docs/UPGRADING.md, "Going back to the
    old code") the script also forgets the move, where it left that as a step to do by hand:
    the package's copies of the rows are emptied, with ``wts_account_months`` and the month rows of
    ``wts_visitor_days``, and the move's mark goes. Until then this test held the package's tables untouched; it now
    holds them to the rows they had, carried to the old tables, and then to being emptied."""
    import json

    path = site.settings.state_db
    replies, parts = site.app.state.replies, site.app.state.parts
    # since the move: a question asked, a report, a statement sent, published and then removed by the site
    final = [e for e in shared.events_of(site.post("/api/ask", json={"question": "A new question?"}).text)
             if isinstance(e, dict)][-1]
    rid = site.post("/api/report", json={"kind": "takedown", "message": "Please remove my name.",
                                         "contact": "named@example.org"}).json()["id"]
    sid = site.post("/api/statements", json={"page": f"/b/{final['briefing_id']}", "name": "New Sender",
                                             "role": "Ambassador", "text": "New words.",
                                             "email": "new@example.org"}).json()["id"]
    replies.publish(sid, checked="Telephoned the mission.", about="KEN")
    replies.remove(sid, "It names a private person.")
    replies.hide("/b/Briefing01", "Hidden while a concern is looked into.")
    counts = {new: rows(path, f"SELECT count(*) FROM {new}") for _, new in COPIED}
    briefing = rows(path, "SELECT id, created_at, question, events FROM wts_briefings WHERE id = ?",
                    final["briefing_id"])
    spent_today = round(parts.spending.spent_since(shared.DAY), 6)
    kept = {table: rows(path, f"SELECT * FROM {table}") for table in ("wts_briefing_log", "wts_hidden")}
    assert kept["wts_briefing_log"] and kept["wts_hidden"]

    script(path, UNDO)

    # every row the package held is in the old table, and nothing else is
    for old, new in COPIED:
        assert rows(path, f"SELECT count(*) FROM {old}") == counts[new], old
    assert rows(path, "SELECT id, at, question, events FROM briefings WHERE id = ?", final["briefing_id"]) == briefing
    assert rows(path, "SELECT kind, page, message, contact, closed_at FROM reports WHERE id = ?", rid) == [
        ("takedown", "", "Please remove my name.", "named@example.org", None)]
    # spending today and this month are what the package counted, so the caps carry on from where they are
    assert rows(path, "SELECT round(sum(cost_usd), 6) FROM usage WHERE at >= ?", shared.DAY) == [(spent_today,)]
    # and the move is forgotten: the package's copies are empty and its mark is gone; the record of each briefing
    # and the list of hidden items are kept
    for _, new in COPIED:
        assert rows(path, f"SELECT count(*) FROM {new}") == [(0,)], new
    assert rows(path, "SELECT count(*) FROM wts_schema WHERE name = 'import:wcs-state'") == [(0,)]
    assert {table: rows(path, f"SELECT * FROM {table}") for table in kept} == kept
    assert rows(path, "SELECT value FROM kv WHERE key = ?", f"visitor_salt:{shared.DAY}") == [(shared.SALT,)]
    # the statement the site removed is "withdrawn" there, with its reason and its whole log
    (status, shown_on, reason, country, link, log), = rows(
        path, "SELECT status, shown_on, reason, country, link, log FROM statements WHERE id = ?", sid)
    assert (status, shown_on, reason, country, link) == ("withdrawn", shared.DAY, "It names a private person.",
                                                         "KEN", "")
    assert [(e["action"], e.get("note")) for e in json.loads(log)] == [
        ("received", None), ("published", "Telephoned the mission."), ("removed", "It names a private person.")]
    # the published statement from before the move reads as the old code left it
    was = next(s for s in GOLDEN["statements"] if s["id"] == 1)
    assert rows(path, "SELECT page, country, status, shown_on, text, link, email FROM statements WHERE id = 1") == [
        (was["page"], was["country"], "published", was["shown_on"], was["text"], was["link"], was["email"])]
    assert json.loads(rows(path, "SELECT log FROM statements WHERE id = 1")[0][0]) == was["log"]


# What the old code writes, one row of each kind, in its own tables' shapes (tests/golden/old-state.sql), at
# :at. Each is what a reader or the owner does on the old code: a question asked (a model call, a saved briefing, a
# cached answer, or a note that it failed), a problem reported, a statement sent.
OLD_CODE_WRITES = {
    "a model call": "INSERT INTO usage (visitor, at, kind, model, input_tokens, output_tokens, cache_read_tokens, "
                    "cost_usd, cache_write_tokens) VALUES ('c3c3c3c3c3c3c3c3c3c3c3c3', :at, 'answer', "
                    "'claude-sonnet-5-5', 9000, 700, 3000, 0.0266, 2000)",
    "a saved briefing": "INSERT INTO briefings (id, at, question, options, events) VALUES ('OldCodeOne', :at, "
                        "'Asked on the old code?', '{}', '[]')",
    "a cached answer": "INSERT INTO answer_cache (key, created_at, events) VALUES ('" + "d" * 64 + "', :at, '[]')",
    "a note of a failed briefing": "INSERT INTO failures (at, kind, question, error) VALUES (:at, 'briefing', "
                                   "'Asked on the old code?', 'RuntimeError: on the old code')",
    "a report": "INSERT INTO reports (at, kind, page, message, contact) VALUES (:at, 'error', '/b/Briefing01', "
                "'Sent to the old code.', '')",
    "a statement": "INSERT INTO statements (at, page, country, name, role, text, email) VALUES (:at, "
                   "'/b/Briefing01', 'KEN', 'Old Code Sender', 'Ambassador', 'Sent to the old code.', "
                   "'oldcode@example.org')",
}
# where each lands once the move is made again, and how to find it there
BROUGHT = {
    "a model call": ("SELECT count(*) FROM wts_spending WHERE visitor = 'c3c3c3c3c3c3c3c3c3c3c3c3'", "usage"),
    "a saved briefing": ("SELECT count(*) FROM wts_briefings WHERE id = 'OldCodeOne'", "briefings"),
    "a cached answer": ("SELECT count(*) FROM wts_answer_cache WHERE key = '" + "d" * 64 + "'", "answer_cache"),
    "a note of a failed briefing": ("SELECT count(*) FROM wts_failures WHERE error = 'RuntimeError: on the old code'",
                                    "failures"),
    "a report": ("SELECT count(*) FROM wts_reports WHERE message = 'Sent to the old code.'", "reports"),
    "a statement": ("SELECT count(*) FROM wts_statements WHERE name = 'Old Code Sender'", "statements"),
}


@pytest.mark.parametrize("written", list(OLD_CODE_WRITES))
def test_the_old_code_run_after_the_move_stops_the_new_code_with_the_mend_named(site, written):
    """wts-core 0.3.0 (`state.StateError` and `Import(written_at=…)`): the site put back on its old
    code without deploy/undo-wts-core.sql writes to the old tables, which the new code no longer reads, and the
    move is marked as made. Deployed again, the new code would have started without those rows and said nothing:
    a briefing saved on the old code would answer "no briefing at this address", and its cost would count towards
    no cap. Now it does not start. Nothing is changed, and the words name the tables and both of the site's
    scripts, with what each discards. The mend for this case, deploy/redo-wts-core.sql, then brings the old code's
    row across, and the site starts."""
    from wts_core.state import StateError

    path = site.settings.state_db
    an_hour_on = "2026-11-13T13:00:00+00:00"  # the move was made at noon (NOW), when the site first opened it
    con = sqlite3.connect(path)
    con.execute(OLD_CODE_WRITES[written], {"at": an_hour_on})
    con.commit()
    con.close()
    before = dump(path)
    site.clock.set("2026-11-13T14:00:00+00:00")
    with pytest.raises(StateError) as refused:
        open_state(site.settings, site.clock)
    said = str(refused.value)
    table = BROUGHT[written][1]
    assert f"{table} has 1 row written after the import 'wcs-state' ran" in said
    assert "Nothing has been changed" in said
    assert "deploy/redo-wts-core.sql, which keeps what the old code wrote" in said
    assert "deploy/undo-wts-core.sql, which keeps what the new code wrote" in said
    assert "Do not run undo-wts-core.sql to keep what the old code wrote" in said
    assert dump(path) == before
    # the mend: the move made again from the old tables, with the old code's row among them
    script(path, REDO)
    db = open_state(site.settings, site.clock)
    query, _ = BROUGHT[written]
    with db.connect() as con:
        assert con.execute(query).fetchone()[0] == 1, written
        assert con.execute("SELECT count(*) FROM wts_schema WHERE name = 'import:wcs-state'").fetchone()[0] == 1
    open_state(site.settings, site.clock)  # and it starts again after that, as it would every day


def test_the_old_code_run_after_the_undo_is_brought_across_when_the_new_code_comes_back(site):
    """The rule for going back (docs/OPERATIONS.md): the undo script first, then the old code. The old code then
    writes to the old tables as it always did; the new code, deployed again, makes the move afresh and finds all of
    it, with no refusal."""
    path = site.settings.state_db
    script(path, UNDO)
    con = sqlite3.connect(path)
    for statement in OLD_CODE_WRITES.values():
        con.execute(statement, {"at": "2026-11-14T09:00:00+00:00"})
    con.commit()
    con.close()
    site.clock.set("2026-11-15T10:00:00+00:00")
    db = open_state(site.settings, site.clock)
    with db.connect() as con:
        for written, (query, _) in BROUGHT.items():
            assert con.execute(query).fetchone()[0] == 1, written
    # and the move is not refused at the next start, since the old tables now hold nothing newer than it
    open_state(site.settings, site.clock)


def test_a_mistake_in_the_scripts_order_is_refused_and_changes_nothing(old):
    """Either script on a database that has not been moved, or run twice: there is no move to undo or forget, and
    the old tables would be written afresh from empty ones. Each stops at its first step and changes nothing."""
    path = old.state_db
    before = dump(path)
    for which in (UNDO, REDO):
        with pytest.raises(sqlite3.OperationalError, match="no such table: wts_schema"):  # never moved at all
            script(path, which)
        assert dump(path) == before, which.name
    open_state(old, FixedClock(NOW))
    script(path, UNDO)
    after_one = dump(path)
    for which in (UNDO, REDO):
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
            script(path, which)
        assert dump(path) == after_one, which.name


def test_both_scripts_forget_the_move_with_the_same_statements():
    """What the undo script ends with and what the redo script does are one list, so that neither forgets a table
    the other empties (wts-core docs/UPGRADING.md, "Going back to the old code")."""
    def forgetting(which: Path) -> list[str]:
        text = which.read_text(encoding="utf-8")
        return [line for line in text[text.index("-- Forgetting the move"):].splitlines()
                if line.startswith("DELETE FROM")]

    assert forgetting(UNDO) == forgetting(REDO)
    assert "DELETE FROM wts_account_months;" in forgetting(UNDO)
    assert "DELETE FROM wts_visitor_days WHERE who != '*';" in forgetting(UNDO)
    assert forgetting(UNDO)[-1] == "DELETE FROM wts_schema WHERE name = 'import:wcs-state';"
    # every table the move fills is emptied (with what the package keeps in summary of spending), and no other
    assert [line.split()[2].rstrip(";") for line in forgetting(UNDO)] == [
        "wts_spending", "wts_account_months", "wts_visitor_days", "wts_answer_cache", "wts_failures", "wts_briefings",
        "wts_reports", "wts_statement_log", "wts_statements", "wts_kv", "wts_schema"]
    from wcs.state import IMPORTS

    sql = " ".join(statement if isinstance(statement, str) else " ".join(statement(_AllThere()))
                   for statement in IMPORTS[0].statements)
    filled = {word for word in sql.replace("(", " ").split() if word.startswith("wts_")}
    assert filled == {"wts_kv", "wts_spending", "wts_answer_cache", "wts_failures", "wts_briefings", "wts_reports",
                      "wts_statements", "wts_statement_log"}


class _AllThere:
    """Stands in for a connection to a database that has every old table and column, so that each of the move's
    statements that depends on what is there gives its SQL."""

    def execute(self, sql, *args):
        class Rows:
            def fetchall(self):
                return [(0, name, "TEXT", 0, None, 0) for name in ("cache_write_tokens", "id", "at")]

            def __iter__(self):
                return iter(self.fetchall())

        return Rows()


def test_a_week_of_nightly_tidies_of_the_old_tables_never_stops_the_site(site):
    """The nightly tidy changes and deletes rows of the old tables, and adds none: cutting a spending row's time to
    its day only ever makes it earlier. So night after night the site starts, and the check that refuses a
    database the old code has written to since the move is never set off by the site's own housekeeping."""
    path = site.settings.state_db
    for night in range(1, 9):
        site.clock.set(f"2026-11-{13 + night}T04:25:00+00:00")
        code, said = run("tidy")
        assert code == 0, (night, said)
        open_state(site.settings, site.clock)  # as the site and every `wcs` command does
    # by then every old spending row is of a day gone by, and keeps its day and no time of day, as the new do
    assert rows(path, "SELECT count(*) FROM usage WHERE at NOT LIKE '%T00:00:00+00:00'") == [(0,)]
    assert rows(path, "SELECT count(*) FROM usage WHERE visitor IS NOT NULL") == [(0,)]
    assert sorted({at[:10] for at, in rows(path, "SELECT at FROM usage WHERE at IS NOT NULL")}) == sorted(
        {at[:10] for at, in rows(path, "SELECT at FROM wts_spending WHERE at != ''")})


def test_going_back_and_coming_forward_again_counts_no_spending_twice(site):
    """wts-core docs/UPGRADING.md, "Going back to the old code": the package keeps, beside the spending rows, the
    public's visitors by the month (``wts_visitor_days``), and on a site with accounts each account's month
    (``wts_account_months``). Left behind by an undo, the second move would bring the same spending back beside
    them. Here: questions asked on two days, the first day forgotten, the undo, and the move made again. The
    owner's figures are what they were, line for line, and no summary row is there twice."""
    parts = site.app.state.parts
    path = site.settings.state_db
    for question in ("A question on the day of the move?", "Another question on the day of the move?"):
        assert [e for e in shared.events_of(site.post("/api/ask", json={"question": question}).text)
                if isinstance(e, dict)][-1]["type"] == "final"
    site.clock.set("2026-11-14T10:00:00+00:00")
    site.post("/api/ask", json={"question": "A question the day after?"})
    assert run("tidy")[0] == 0  # the day of the move is forgotten: codes and times of day
    month = shared.DAY[:7]
    figures = (parts.spending.by_account(month), parts.spending.totals(month), parts.spending.by_day(month))
    summaries = rows(path, "SELECT * FROM wts_visitor_days ORDER BY 1, 2")
    assert [row for row in summaries if row[1] != "*"], "the public's month is kept in summary"
    assert rows(path, "SELECT count(*) FROM wts_spending WHERE substr(at, 1, 10) = ? AND at NOT LIKE '%T00:00:00%'",
                shared.DAY) == [(0,)]

    script(path, UNDO)
    # what the old code is given back: the forgotten day with no visitor's code and its midnight; costs exact
    assert rows(path, "SELECT DISTINCT visitor, at FROM usage WHERE substr(at, 1, 10) = ?", shared.DAY) == [
        (None, f"{shared.DAY}T00:00:00+00:00")]
    assert rows(path, "SELECT count(*) FROM wts_account_months") == [(0,)]
    assert rows(path, "SELECT count(*) FROM wts_visitor_days WHERE who != '*'") == [(0,)]

    db = open_state(site.settings, site.clock)  # the new code again: the move is made afresh
    from wcs.spending import spending_record

    again = spending_record(site.settings, db, site.clock)
    assert again.totals(month).spent_usd == pytest.approx(figures[1].spent_usd)
    assert again.totals(month).calls == figures[1].calls and again.totals(month).briefings == figures[1].briefings
    assert again.by_day(month) == figures[2]
    # who the spending was for: nothing is counted twice, and the lines add up to what was spent. (One thing moves,
    # and the undo script says so: the old code has no mark for "a member of the public, code forgotten", so a
    # forgotten day's rows go back with no visitor and return as calls that were nobody's. The site shows this
    # report nowhere; `wcs usage` and the caps read totals and days, which are exact.)
    lines = again.by_account(month)
    assert sum(line.spent_usd for line in lines) == pytest.approx(figures[1].spent_usd)
    assert sum(line.calls for line in lines) == sum(line.calls for line in figures[0]) == figures[1].calls
    assert all(line.account is None for line in lines) and len(lines) == len({line.public for line in lines})
    # and no summary row is there twice: the whole site's visitors by the day as they were, the public's months
    # emptied by the undo and not brought back beside the rows
    assert rows(path, "SELECT * FROM wts_visitor_days WHERE who = '*' ORDER BY 1") == [
        row for row in summaries if row[1] == "*"]
    assert len(rows(path, "SELECT * FROM wts_visitor_days WHERE who != '*'")) <= len(
        [row for row in summaries if row[1] != "*"])


def test_a_database_from_before_cache_writes_were_recorded_is_brought_across_too(old):
    """The oldest backups have no ``cache_write_tokens`` column in ``usage`` (the old code added it when it first
    opened such a file): restored and opened by the code as it is now, those rows had no cache writes."""
    con = sqlite3.connect(old.state_db)
    con.execute("ALTER TABLE usage DROP COLUMN cache_write_tokens")
    con.commit()
    con.close()
    open_state(old, FixedClock(NOW))
    assert rows(old.state_db, "SELECT count(*), sum(cache_write_tokens), round(sum(cost_usd), 6) FROM wts_spending") \
        == [(10, 0, rows(old.state_db, "SELECT round(sum(cost_usd), 6) FROM usage")[0][0])]


# The old tables, for as long as they are there: within what the site promises, and then gone

GONE = ("waiting@example.org", "old@example.org")  # a lapsed statement's address; a report's, open since July
AFRESH = "Wrote the database out afresh: nothing deleted is left in the file."  # the tidy's last line (wts-core)


def everything_in(path: Path) -> str:
    """Every byte of a database file as text, free pages and all, and every row of every table."""
    return path.read_bytes().decode("latin-1") + "\n".join(dump(path))


def test_the_old_tables_keep_nothing_the_site_has_promised_to_delete(site, tmp_path):
    """An independent review found that the old tables, left as they were so the move could be undone, went on
    holding what the package had since deleted from its own: senders' addresses, reports' contacts, failed
    questions past 90 days, the move's own day's visitor secret beside that day's codes; and that every night's
    backup then held them too. Each night `wcs tidy` now deletes from the old tables what has gone from the new."""
    import gzip

    from wts_core.ops import make_backup

    from wcs.state import BACKUPS

    path, db = site.settings.state_db, site.app.state.db
    before = everything_in(path)
    assert all(address in before for address in GONE) and shared.SALT in before
    assert run("statements", "--all")[0] == 0 and run("reports", "--all")[0] == 0  # the first listings: what is due goes
    site.clock.advance(days=1)
    code, said = run("tidy")                                # that night
    # wts-core 0.3.0, where the nightly `tidy` runs in a fixed order and says what it did: writing
    # the file out afresh is the command's own last step, and its line (the site's own "the database file was
    # compacted" went with the site's own step)
    assert code == 0 and said.splitlines()[-1] == AFRESH
    # row for row, the old tables hold no address, contact, secret or code that the new ones do not
    assert rows(path, "SELECT o.id FROM statements o JOIN wts_statements w ON w.id = o.id "
                      "WHERE o.email != coalesce(w.email, '')") == []
    assert rows(path, "SELECT o.id FROM reports o JOIN wts_reports w ON w.id = o.id "
                      "WHERE coalesce(o.contact, '') != coalesce(w.contact, '')") == []
    assert rows(path, "SELECT count(*) FROM kv WHERE key LIKE 'visitor_salt:%'") == [(0,)]  # yesterday's secret
    assert rows(path, "SELECT count(*) FROM usage WHERE visitor IS NOT NULL") == [(0,)]     # and its codes
    assert rows(path, "SELECT count(*) FROM wts_spending WHERE visitor != ''") == [(0,)]
    # the notes of failed briefings: the one with no time has gone; the oldest is 74 days old and has 16 to go
    assert rows(path, "SELECT question FROM failures ORDER BY id") == [("An old question?",), (shared.Q,),
                                                                      (shared.Q2,)]
    assert rows(path, "SELECT count(*) FROM answer_cache") == [(1,)]
    for sid, in rows(path, "SELECT id FROM statements"):
        old_log = [(e["at"], e["action"], e.get("note")) for e in
                   json.loads(rows(path, "SELECT log FROM statements WHERE id = ?", sid)[0][0])]
        assert old_log == rows(path, "SELECT at, action, note FROM wts_statement_log WHERE statement = ? "
                                     "ORDER BY id", sid), sid
    # and nowhere in the file itself, nor in the backup made that night
    archive = make_backup(path, tmp_path / "backups", BACKUPS, clock=site.clock)
    copy = tmp_path / "copy.db"
    copy.write_bytes(gzip.decompress(archive.read_bytes()))
    for where in (path, copy):
        held = everything_in(where)
        assert not [address for address in GONE if address in held], where
        assert VISITOR not in held, where  # no visitor's code of the day of the move
        assert shared.SALT not in held, where  # nor the secret it was made with
    # a month on, the note of the briefing that failed on 1 September is past its 90 days, and gone
    site.clock.advance(days=30)
    run("tidy")
    assert rows(path, "SELECT question FROM failures ORDER BY id") == [(shared.Q,), (shared.Q2,)]
    assert db.retire_old().dropped == ()  # the tables themselves stay until 1 February: the next test


VISITOR = "a1" * 12  # a visitor's code for the day of the move, in the old spending rows


def test_what_the_tidy_deletes_tonight_is_gone_from_the_old_tables_the_file_and_a_backup_tonight(site, tmp_path):
    """Found while Where Councils Stand made the same fix: the package's `tidy` keeps the old tables within the
    site's promises first and does the right of reply's deletions after, so an address deleted tonight stayed in
    its old row until tomorrow night; and SQLite does not wipe what it deletes, so the address was still in the
    bytes of the file, and of a backup made from it, after its row had gone. Here nothing lists or writes before
    the night's tidy: the tidy alone must leave no trace, in the tables, the file or a backup made then; and the
    same once the old tables have been dropped."""
    import gzip
    from datetime import date

    from wts_core.ops import make_backup

    from wcs.state import BACKUPS

    path, db = site.settings.state_db, site.app.state.db

    def nowhere(day: date, label: str) -> None:
        assert site.clock.now().date() == day
        archive = make_backup(path, tmp_path / label, BACKUPS, clock=site.clock)
        copy = tmp_path / f"{label}.db"
        copy.write_bytes(gzip.decompress(archive.read_bytes()))
        for where in (path, copy):
            held = everything_in(where)
            assert not [address for address in GONE if address in held], (label, where)
            assert VISITOR not in held and shared.SALT not in held, (label, where)

    before = everything_in(path)
    assert all(address in before for address in GONE) and shared.SALT in before and VISITOR in before
    site.clock.advance(days=1)
    code, said = run("tidy")  # the first thing to touch reports and statements since the move
    assert code == 0, said
    assert rows(path, "SELECT o.id FROM statements o JOIN wts_statements w ON w.id = o.id "
                      "WHERE o.email != coalesce(w.email, '')") == []
    assert rows(path, "SELECT o.id FROM reports o JOIN wts_reports w ON w.id = o.id "
                      "WHERE coalesce(o.contact, '') != coalesce(w.contact, '')") == []
    assert rows(path, "SELECT email, status FROM wts_statements WHERE id = 7") == [(None, "declined")]
    assert rows(path, "SELECT email FROM statements WHERE id = 7") == [("",)]
    assert rows(path, "SELECT contact FROM reports WHERE id = 1") == [("",)]
    nowhere(site.clock.now().date(), "that night")
    # The night's steps, each line and their order (wts-core 0.3.0): the package's own deletions; the site's one step, the right of
    # reply's; the old tables once, after it (they were gone over before it, and the site went over them a second
    # time itself); and the file written out afresh last of all, by the command itself. On this database: eight
    # spending rows of days gone by, the failure note with no time, the day of the move's visitor secret, the
    # cached answer past its week; statement 7 lapsed and report 1's contact deleted (asserted above).
    assert said.splitlines() == [
        "Deleted 0 records of briefings older than 13 months.",
        "Removed the question from 0 records of failed briefings older than 90 days.",
        "Deleted 0 weekly report files older than 13 months.",
        "Forgot the visitor codes on 8 spending rows from before today.",
        "Deleted 1 note of failed briefings older than 90 days.",
        "Deleted 1 visitor secret of earlier days.",
        "Deleted 1 cached answer older than the 168 hours the site keeps one.",
        "Deleted 0 copies of the database made before an import, older than 60 days.",
        "right of reply: 1 statements lapsed undecided, 1 confirming addresses deleted, 1 old reports' contact "
        "details deleted, 0 old daily counts deleted",
        # 24 where it was 16: the eight old spending rows of days gone by now lose their time of day too, as the
        # package's do from 0.3.0 (a spending row says who it was for and what time of day it was made for its
        # own day only; src/wcs/state.py, OLD_TABLES)
        "Kept the site's old tables within what it promises: 24 rows changed or deleted. They are dropped on "
        "2027-02-01.",
        AFRESH,
    ]
    from wcs import cli
    assert not [name for name in ("_compact", "_old_tables_again", "_forget_old_secrets") if hasattr(cli, name)]

    # the day the old tables go: dropped, and nothing of them left in the file's unused space or in a backup
    site.clock.set("2027-02-01T04:25:00+00:00")
    code, said = run("tidy")
    assert code == 0 and not set(OLD_TABLE_NAMES) & db.tables()
    # the command's own line for the night they go, and still the file written out afresh after it
    assert said.splitlines()[-2:] == [
        "Dropped the site's old tables, kept since it moved to the shared package: usage, kv, answer_cache, "
        "failures, briefings, reports, statements.", AFRESH]
    nowhere(date(2027, 2, 1), "after the drop")
    held = everything_in(path)
    assert "CREATE TABLE statements" not in held and "CREATE TABLE usage" not in held


def test_the_old_tables_are_dropped_on_1_february_2027_and_backups_are_then_told_by_the_new(site, tmp_path):
    from datetime import date

    from wts_core.ops import RestoreRefused, make_backup, restore_backup

    from wcs.state import BACKUPS, DROP_OLD_TABLES_ON, OLD_TABLES

    path, db = site.settings.state_db, site.app.state.db
    assert DROP_OLD_TABLES_ON == OLD_TABLES.drop_on == date(2027, 2, 1)
    site.clock.set("2027-01-31T04:25:00+00:00")
    said = run("tidy")[1]
    assert "dropped on 2027-02-01" in said and set(OLD_TABLE_NAMES) <= db.tables()
    early = make_backup(path, tmp_path / "b", BACKUPS, clock=site.clock)
    site.clock.set("2027-02-01T04:25:00+00:00")
    code, said = run("tidy")
    assert code == 0 and not set(OLD_TABLE_NAMES) & db.tables()
    assert all(name in said for name in OLD_TABLE_NAMES)  # the owner is told which tables went
    # the site carries on as it was
    assert site.get("/api/briefings/Briefing01").status_code == 200 and run("usage")[0] in (0, 1)
    assert run("tidy")[0] == 0  # and the next night there is nothing more to do
    # the way back is closed, as its file and the operations page say: it fails at once and changes nothing
    before = dump(path)
    con = sqlite3.connect(path)
    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        con.executescript((shared.GOLDEN.parents[1] / "deploy" / "undo-wts-core.sql").read_text(encoding="utf-8"))
    con.close()
    assert dump(path) == before
    assert "VALID ONLY UNTIL 1 FEBRUARY 2027" in (shared.GOLDEN.parents[1] / "deploy" /
                                                  "undo-wts-core.sql").read_text(encoding="utf-8")
    # a backup made now has none of the old tables: a restore tells this site's by the package's, from this day
    # (one spec for both sides of the day, which a restore reads from its clock: wts-core 0.3.0's
    # ``BackupSpec(required_tables_until=…)``, in place of the site's own function of the date)
    late = make_backup(path, tmp_path / "b", BACKUPS, clock=site.clock)
    assert BACKUPS.required_on(date(2027, 1, 31)) == ("usage", "briefings")
    assert BACKUPS.required_on(date(2027, 2, 1)) == ("wts_spending", "wts_briefings")
    for archive in (early, late):  # the last night's with the old tables still restores too: it has both
        target = tmp_path / archive.stem / "state.db"
        restore_backup(archive, target, BACKUPS, clock=site.clock)
        assert rows(target, "SELECT count(*) FROM wts_briefings") == [(3,)]
    # a backup made by the old code, before the move: taken the day before, and no longer from this day on
    before_the_move = tmp_path / "before-the-move.db"
    con = sqlite3.connect(before_the_move)
    con.executescript((shared.GOLDEN / "old-state.sql").read_text(encoding="utf-8"))
    con.close()
    oldest = make_backup(before_the_move, tmp_path / "old", BACKUPS, clock=FixedClock("2026-11-12T04:20:00+00:00"))
    restore_backup(oldest, tmp_path / "the day before" / "state.db", BACKUPS,
                   clock=FixedClock("2027-01-31T23:59:59+00:00"))
    with pytest.raises(RestoreRefused) as refused:
        restore_backup(oldest, tmp_path / "the day" / "state.db", BACKUPS, clock=site.clock)
    assert refused.value.why == "foreign" and not (tmp_path / "the day" / "state.db").exists()


def test_a_backup_from_before_statements_were_kept_restores_starts_and_shows_what_it_holds(settings, tmp_path, caplog):
    """An independent review found that a backup from before 12 November 2026, when the ``statements`` table
    arrived, restored and then showed no spending and no saved briefings: the import waited for a table that
    would never come, and said nothing. It waits now only for the two tables every backup has."""
    from fastapi.testclient import TestClient
    from wts_core.ops import make_backup, restore_backup

    from wcs.api.app import create_app
    from wcs.reference.countries import countries
    from wcs.spending import summary
    from wcs.state import BACKUPS

    older = tmp_path / "older.db"
    con = sqlite3.connect(older)
    con.executescript((shared.GOLDEN / "old-state.sql").read_text(encoding="utf-8"))
    for table in ("statements", "reports", "failures", "answer_cache", "kv"):  # an early database: the two it began with
        con.execute(f"DROP TABLE {table}")
    con.commit()
    con.close()
    clock = FixedClock(NOW)
    archive = make_backup(older, tmp_path / "b", BACKUPS, clock=clock)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    restore_backup(archive, settings.state_db, BACKUPS, clock=clock)

    db = open_state(settings, clock)
    assert not [r for r in caplog.records if r.name == "wts_core.state"]  # nothing was skipped
    api = TestClient(create_app(settings, pipeline=shared.ScriptedPipeline(clock.advance, "m", countries()),
                                state=db, clock=clock))
    report = summary(settings, db, clock)
    report = cap_then(report)
    assert (report["today"], report["month"]) == (GOLDEN["summary"]["today"], GOLDEN["summary"]["month"])
    assert api.get("/api/briefings/Briefing01").json()["question"] == shared.Q
    assert api.get("/api/briefings/Withdrawn1").status_code == 410
    assert api.app.state.replies.statements() == [] and api.app.state.replies.reports() == []
    assert api.post("/api/report", json={"kind": "error", "message": "A first report."}).json()["id"] == 1


def test_after_the_old_tables_are_dropped_neither_script_changes_anything(site):
    """From 1 February 2027 the nightly tidy drops the old tables. deploy/redo-wts-core.sql would then forget a move
    it could not make again, and the site would be left with no spending, no saved briefings, no reports and no
    statements. So it first asks for each old table the import needs, and stops ("no such table") before it deletes
    anything. deploy/undo-wts-core.sql writes the old tables before it forgets the move, so it stops at its first
    write. Either way the package's tables and the move's mark are as they were."""
    path, db = site.settings.state_db, site.app.state.db
    site.clock.set("2027-02-01T04:25:00+00:00")
    assert run("tidy")[0] == 0 and not set(OLD_TABLE_NAMES) & db.tables()
    for new in ("wts_spending", "wts_briefings", "wts_reports", "wts_statements"):
        assert rows(path, f"SELECT count(*) FROM {new}") != [(0,)], new
    before = dump(path)
    for which in (REDO, UNDO):
        with pytest.raises(sqlite3.OperationalError, match="no such table"):
            script(path, which)
        assert dump(path) == before, which.name
        assert rows(path, "SELECT count(*) FROM wts_schema WHERE name = 'import:wcs-state'") == [(1,)]


def test_the_redo_script_asks_for_the_tables_the_import_needs_and_no_others(old):
    """The redo script's first step asks for exactly the old tables the import needs (``ALWAYS_THERE``), before
    anything is deleted. It asks for no others: a database put back from an early backup has only those two, and the
    move is made again on one all the same."""
    from wcs.state import ALWAYS_THERE, IMPORTS

    text = REDO.read_text(encoding="utf-8")
    asked = [line.removeprefix("SELECT count(*) FROM ").rstrip(";") for line in text.splitlines()
             if line.startswith("SELECT count(*) FROM ")]
    assert asked == list(ALWAYS_THERE) == list(IMPORTS[0].needs)
    assert text.index("\nSELECT count(*) FROM ") < text.index("\nDELETE FROM ")
    path = old.state_db
    con = sqlite3.connect(path)
    for table in ("statements", "reports", "failures", "answer_cache", "kv"):  # an early database: the two it began with
        con.execute(f"DROP TABLE {table}")
    con.commit()
    con.close()
    clock = FixedClock(NOW)
    open_state(old, clock)
    script(path, REDO)
    assert rows(path, "SELECT count(*) FROM wts_spending") == rows(path, "SELECT count(*) FROM wts_briefings") == [(0,)]
    assert rows(path, "SELECT count(*) FROM wts_schema WHERE name = 'import:wcs-state'") == [(0,)]
    open_state(old, clock)  # the next start makes the move again
    for was, now in (("usage", "wts_spending"), ("briefings", "wts_briefings")):
        assert rows(path, f"SELECT count(*) FROM {now}") == rows(path, f"SELECT count(*) FROM {was}") != [(0,)], was
    assert rows(path, "SELECT count(*) FROM wts_schema WHERE name = 'import:wcs-state'") == [(1,)]
