"""The state database, ``data/state.db``: the one thing on the server that can't be rebuilt from the UN's records.

It is a small SQLite file opened through the Where They Stand sites' shared package (wts-core, ``wts_core.state``;
its docs/adr/0005). Every table in it is the package's, named ``wts_…``: spending against the caps, the answer
cache, saved briefings, notes of failed briefings, the record of each briefing, readers' reports, statements under
the right of reply and their log, items hidden while a concern is decided, and the day's visitor secret. The
package makes them, and the site reads and writes them only through it.

Before wts-core the site kept the same things in tables of its own (``usage``, ``kv``, ``answer_cache``,
``failures``, ``briefings``, ``reports``, ``statements``). The first time the site starts on a database that has
them, their rows are copied into the ``wts_`` tables, once (``IMPORTS``), in one transaction. The old tables are
left there for a while, so the version before this one still runs on the file; and a copy of the whole file as it was
is kept beside it first, as ``state.pre-wts-core.<date>.db``. ``wcs tidy`` deletes that copy once it is older than
the backups are kept; each night it also deletes from the old tables whatever the site has promised to delete
(``OLD_TABLES``), and from 1 February 2027 it drops them. docs/OPERATIONS.md, "The state database", says how to go
back until then: deploy/undo-wts-core.sql first, always (it writes the old tables again and forgets the move).
Should the old code be started on the moved database without it, what it writes goes to the old tables only; the
import says which column of each old table holds the time a row was written (``written_at``), and the site then
refuses to start on the new code, naming the mend (deploy/redo-wts-core.sql, or the undo script; ``WHICH_SCRIPT``),
where it would have carried on without those rows and said nothing.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import date

from wts_core import observe, replies, usage
from wts_core.ops import AlertWording, BackupSpec
from wts_core.ports import SYSTEM_CLOCK, Clock
from wts_core.state import Import, Migration, OldTables, StateDB, StateError, columns

from wcs.config import SITE, Settings

# The tables the site made for itself before wts-core. Nothing reads them now. They are kept for a while so that
# the move can be undone (deploy/undo-wts-core.sql), kept within the site's promises each night meanwhile
# (``OLD_TABLES`` below), and dropped on ``DROP_OLD_TABLES_ON``.
OLD_TABLE_NAMES = ("usage", "kv", "answer_cache", "failures", "briefings", "reports", "statements")
DROP_OLD_TABLES_ON = date(2027, 2, 1)
# What every backup `wcs restore` has ever accepted holds: the import waits for these two and for nothing else.
ALWAYS_THERE = ("usage", "briefings")


# What the site's backups are called and hold (`wcs backup`, `wcs restore`; src/wcs/cli.py): the database, gzipped,
# as wcs-state-YYYY-MM-DD.sqlite.gz; the newest 14 kept on the server and 60 off site.
#
# A restore takes only a database with two tables every backup of this site has. Until the old tables are dropped
# those are the old ``usage`` and ``briefings``, which every backup made before the shared package or since holds;
# from that day, when no backup older than the move is kept any longer, the package's own. Which side of the day a
# restore is on is read from the clock when it is made (``BackupSpec.required_on``), so the one spec serves both.
# The site has no table that only it has (Where Councils Stand's backups hold tables of the same names), so the
# database is also marked as this site's when it is opened (``open_state``), and a restore refuses one marked as
# another's.
BACKUPS = BackupSpec(prefix="wcs-state-", keep=14, keep_offsite=60, required_tables=("wts_spending", "wts_briefings"),
                     required_tables_until={DROP_OLD_TABLES_ON: ALWAYS_THERE}, site=SITE.package)

# What the update says when the nightly backup or its off-site copy has stopped (`wcs update`, which runs in the
# same container every day and whose failure is emailed): this site's own words, as they were.
BACKUP_ALERTS = AlertWording(
    no_backups="no backups have been made yet",
    backup_stale="the last backup was made over two days ago ({name}): is wcs-backup.timer running?",
    server_only="backups are kept on this server only: no off-site copy is set up",
    no_offsite_yet="no off-site copy has been made yet; one is made with the next backup",
    offsite_failed="the last off-site copy failed ({when}): {error}",
    offsite_stale="the last off-site copy was over two days ago ({day})",
)


def migrations() -> tuple[Migration, ...]:
    """The tables of every part of the package this site uses. Each module names its own; the site joins them."""
    return (*usage.migrations(), *replies.migrations(), *observe.migrations())


def _bring_spending(con: sqlite3.Connection) -> list[str]:
    """The spending rows. A database from before cache writes were recorded (the oldest backups) has no such
    column in ``usage``: those rows had none."""
    writes = "coalesce(cache_write_tokens, 0)" if "cache_write_tokens" in columns(con, "usage") else "0"
    return ["INSERT INTO wts_spending (id, at, kind, model, visitor, input_tokens, output_tokens, cache_read_tokens, "
            "cache_write_tokens, cost_usd) SELECT id, coalesce(at, ''), coalesce(kind, ''), model, visitor, "
            f"coalesce(input_tokens, 0), coalesce(output_tokens, 0), coalesce(cache_read_tokens, 0), {writes}, "
            "coalesce(cost_usd, 0) FROM usage"]


def _if_there(table: str, *statements: str) -> Callable[[sqlite3.Connection], list[str]]:
    """Statements that copy from an old table, run only where the database has it. A backup from before the table
    existed (``statements`` arrived on 12 November 2026, ``reports`` and ``briefings``' neighbours earlier) is
    restored without it: there is then nothing of that kind to bring, and the rest must still come across."""
    return lambda con: list(statements) if columns(con, table) else []


# The one-off copy of the site's old rows into the shared package's tables (wts-core docs/adr/0005). What changes
# its name: a briefing's ``at`` is its ``created_at``; a statement's ``page`` is its ``item``, its ``country`` whom
# it is ``about``, its ``shown_on`` when it was published and its ``ended_at`` when it closed; its JSON ``log`` is
# one row for each entry. Several of the old columns allowed nothing where the package's do not, and such a row is
# given an empty value, which counts for exactly what it counted before (a row with no time is in no day's or
# month's spending). A cached answer with no time or no events could never be served, and is left behind. A
# statement with no link, a report with no page and anything with no address had an empty text and now has none.
# A closed report is recorded as resolved: the old table had no outcome. The day's visitor secret comes across
# under its own name, so a visitor's count for today carries on.
IMPORTS = (
    Import(
        name="wcs-state",
        statements=(
            _if_there("kv", "INSERT OR IGNORE INTO wts_kv (key, value) SELECT key, value FROM kv "
                            "WHERE key LIKE 'visitor_salt:%' AND value IS NOT NULL"),
            _bring_spending,
            _if_there("answer_cache",
                      "INSERT OR IGNORE INTO wts_answer_cache (key, created_at, events) SELECT key, created_at, "
                      "events FROM answer_cache WHERE created_at IS NOT NULL AND events IS NOT NULL"),
            _if_there("failures", "INSERT INTO wts_failures (id, at, kind, question, error) SELECT id, "
                                  "coalesce(at, ''), coalesce(kind, ''), question, coalesce(error, '') FROM failures"),
            "INSERT INTO wts_briefings (id, created_at, question, options, events, withdrawn_at, withdrawn_reason) "
            "SELECT id, at, question, options, events, withdrawn_at, withdrawn_reason FROM briefings",
            _if_there("reports",
                      "INSERT INTO wts_reports (id, at, kind, page, message, contact, status, closed_at) "
                      "SELECT id, at, kind, nullif(page, ''), message, nullif(contact, ''), "
                      "CASE WHEN closed_at IS NULL THEN 'open' ELSE 'resolved' END, closed_at FROM reports"),
            _if_there("statements",
                      "INSERT INTO wts_statements (id, at, item, about, name, role, text, link, email, status, "
                      "published_at, closed_at, reason) SELECT id, at, page, country, name, role, text, "
                      "nullif(link, ''), nullif(email, ''), status, shown_on, ended_at, reason FROM statements",
                      "INSERT INTO wts_statement_log (statement, at, action, note) "
                      "SELECT s.id, coalesce(json_extract(entry.value, '$.at'), s.at), "
                      "coalesce(json_extract(entry.value, '$.action'), 'imported'), "
                      "json_extract(entry.value, '$.note') "
                      "FROM statements AS s, json_each(s.log) AS entry ORDER BY s.id, entry.key"),
        ),
        needs=ALWAYS_THERE,
        # Which column of each old table holds the time a row was written (wts-core docs/UPGRADING.md, "Going
        # back to the old code": the line given there for this site, checked against
        # tests/golden/old-state.sql). While the old tables are there, a row in one of them written after the
        # second this import ran means the old code has run on the database since the move, and what it wrote is
        # in none of the package's tables: the database is then not opened, and the error names the tables and
        # the mend. ``kv`` has no time of writing and is left out.
        written_at={"usage": "at", "answer_cache": "created_at", "failures": "at", "briefings": "at",
                    "reports": "at", "statements": "at"},
    ),
)

# The old tables, for as long as they are there, keep to what the site promises of the new ones (the privacy page):
# each night (`wcs tidy`) whatever the package has deleted from its own tables is deleted from them too, and they
# are dropped on 1 February 2027. Until that day deploy/undo-wts-core.sql can write them again from the package's
# tables, to go back to the old code; after it, nothing reads or holds them.
_LOG_AS_KEPT = (
    "coalesce((SELECT json_group_array(json(entry)) FROM (SELECT CASE WHEN l.note IS NULL "
    "THEN json_object('at', l.at, 'action', l.action) ELSE json_object('at', l.at, 'action', l.action, 'note', "
    "l.note) END AS entry FROM wts_statement_log l WHERE l.statement = statements.id ORDER BY l.id)), '[]')")
OLD_TABLES = OldTables(
    tables=OLD_TABLE_NAMES,
    drop_on=DROP_OLD_TABLES_ON,
    nightly=(
        # A statement's confirming address, once the package has deleted its own copy (three months after the
        # statement ended, or lapsed): gone from the old row too, with the log and the reason as the package now
        # keeps them (it takes the address out of both, should the owner have written it there).
        "UPDATE statements SET email = '', "
        "reason = (SELECT w.reason FROM wts_statements w WHERE w.id = statements.id), "
        f"log = {_LOG_AS_KEPT} "
        "WHERE id IN (SELECT id FROM wts_statements WHERE email IS NULL OR email = '')",
        # a report's contact address, once the report is closed or has waited three months
        "UPDATE reports SET contact = '' WHERE coalesce(contact, '') != '' AND id IN "
        "(SELECT id FROM wts_reports WHERE contact IS NULL OR contact = '')",
        # the note of a briefing that failed, with its question: 90 days
        "DELETE FROM failures WHERE at IS NULL OR substr(at, 1, 10) < date(:today, '-90 days')",
        # a cached answer: a week, as the cache keeps one
        "DELETE FROM answer_cache WHERE created_at IS NULL OR substr(created_at, 1, 10) < date(:today, '-8 days')",
        # every visitor secret but today's, and the visitor's code on spending rows of days gone by
        "DELETE FROM kv WHERE key LIKE 'visitor_salt:%' AND key != 'visitor_salt:' || :today",
        "UPDATE usage SET visitor = NULL WHERE visitor IS NOT NULL AND (at IS NULL OR substr(at, 1, 10) < :today)",
        # and their time of day: a spending row of a day gone by keeps its day only, as the package's do from
        # wts-core 0.3.0 (midnight, written as the package writes it). Only ever earlier than it was, so this
        # never looks like a row written since the move (``written_at``, above)
        "UPDATE usage SET at = substr(at, 1, 10) || 'T00:00:00+00:00' WHERE length(at) >= 10 "
        "AND substr(at, 1, 10) < :today AND at != substr(at, 1, 10) || 'T00:00:00+00:00'",
    ))


# Said after the package's own words when the database is refused because the old code has written to the old
# tables since the move (``Import.written_at``, above). The package says to run "the site's script for undoing the
# move". This site has two, and which is right depends on what happened: deploy/undo-wts-core.sql writes the old
# tables afresh from the package's, so run in that state it would discard the very rows the refusal is about.
WHICH_SCRIPT = ("On Where Countries Stand there are two such scripts, and each discards something. If the old code "
                "was started after the move: deploy/redo-wts-core.sql, which keeps what the old code wrote and "
                "discards what the new code wrote between the move and then. If the old code was never started "
                "after the move (the false alarm): deploy/undo-wts-core.sql, which keeps what the new code wrote "
                "and writes the old tables afresh from it. Do not run undo-wts-core.sql to keep what the old code "
                "wrote: it would discard it. docs/OPERATIONS.md, \"Going back, and coming forward again\".")


def open_state(settings: Settings, clock: Clock = SYSTEM_CLOCK) -> StateDB:
    """The site's state database, with the package's tables made (and the old rows brought across, the first time)
    on the way in. It is marked as this site's the first time it is opened, and a file marked as another site's is
    refused. So is one whose old tables hold rows written after the move (the site was put back on its old code
    without deploy/undo-wts-core.sql): nothing is changed, and the error says which script mends it."""
    try:
        return StateDB(settings.state_db, migrations=migrations(), imports=IMPORTS, old_tables=OLD_TABLES,
                       clock=clock, site=SITE.package)
    except StateError as exc:
        if "newer than the import" not in str(exc):  # some other fault of the database's: as the package says it
            raise
        raise StateError(f"{exc} {WHICH_SCRIPT}") from exc
