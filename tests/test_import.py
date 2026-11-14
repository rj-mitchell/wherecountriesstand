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
