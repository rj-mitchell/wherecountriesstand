"""The state database, ``data/state.db``: the one thing on the server that can't be rebuilt from the UN's records.

It is a small SQLite file opened through the Where They Stand sites' shared package (wts-core, ``wts_core.state``;
its docs/adr/0005). Every table in it is the package's, named ``wts_…``: spending against the caps, the answer
cache, saved briefings, notes of failed briefings, the record of each briefing, readers' reports, statements under
the right of reply and their log, items hidden while a concern is decided, and the day's visitor secret. The
package makes them, and the site reads and writes them only through it.

Before wts-core the site kept the same things in tables of its own (``usage``, ``kv``, ``answer_cache``,
``failures``, ``briefings``, ``reports``, ``statements``). The first time the site starts on a database that has
them, their rows are copied into the ``wts_`` tables, once (``IMPORTS``), in one transaction. The old tables are
left exactly as they were, so the version before this one still runs on the file; and a copy of the whole file as
it was is kept beside it first, as ``state.pre-wts-core.<date>.db``. ``wcs tidy`` deletes that copy once it is
older than the backups are kept. docs/OPERATIONS.md, "The state database", says how to go back.
"""

from __future__ import annotations

from wts_core import observe, replies, usage
from wts_core.ops import BackupSpec
from wts_core.ports import SYSTEM_CLOCK, Clock
from wts_core.state import Import, Migration, StateDB

from wcs.config import SITE, Settings

# The tables the site made for itself before wts-core. Nothing reads them now; they are dropped in a later release.
OLD_TABLES = ("usage", "kv", "answer_cache", "failures", "briefings", "reports", "statements")

# What the site's backups are called and hold (`wcs backup`, `wcs restore`; src/wcs/cli.py): the database, gzipped,
# as wcs-state-YYYY-MM-DD.sqlite.gz; the newest 14 kept on the server and 60 off site. A restore takes only a
# database with these two tables, which every backup of this site has had, made before the shared package or after
# (they stay until the old tables are dropped; the list changes to the package's tables in that release). The site
# has no table that only it has: Where Councils Stand's backups hold tables of the same two names. So the database
# is also marked as this site's when it is opened (``open_state``), and a restore refuses one marked as another's.
BACKUPS = BackupSpec(prefix="wcs-state-", keep=14, keep_offsite=60, required_tables=("usage", "briefings"),
                     site=SITE.package)


def migrations() -> tuple[Migration, ...]:
    """The tables of every part of the package this site uses. Each module names its own; the site joins them."""
    return (*usage.migrations(), *replies.migrations(), *observe.migrations())


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
            "INSERT OR IGNORE INTO wts_kv (key, value) SELECT key, value FROM kv "
            "WHERE key LIKE 'visitor_salt:%' AND value IS NOT NULL",
            "INSERT INTO wts_spending (id, at, kind, model, visitor, input_tokens, output_tokens, cache_read_tokens, "
            "cache_write_tokens, cost_usd) SELECT id, coalesce(at, ''), coalesce(kind, ''), model, visitor, "
            "coalesce(input_tokens, 0), coalesce(output_tokens, 0), coalesce(cache_read_tokens, 0), "
            "coalesce(cache_write_tokens, 0), coalesce(cost_usd, 0) FROM usage",
            "INSERT OR IGNORE INTO wts_answer_cache (key, created_at, events) SELECT key, created_at, events "
            "FROM answer_cache WHERE created_at IS NOT NULL AND events IS NOT NULL",
            "INSERT INTO wts_failures (id, at, kind, question, error) SELECT id, coalesce(at, ''), "
            "coalesce(kind, ''), question, coalesce(error, '') FROM failures",
            "INSERT INTO wts_briefings (id, created_at, question, options, events, withdrawn_at, withdrawn_reason) "
            "SELECT id, at, question, options, events, withdrawn_at, withdrawn_reason FROM briefings",
            "INSERT INTO wts_reports (id, at, kind, page, message, contact, status, closed_at) "
            "SELECT id, at, kind, nullif(page, ''), message, nullif(contact, ''), "
            "CASE WHEN closed_at IS NULL THEN 'open' ELSE 'resolved' END, closed_at FROM reports",
            "INSERT INTO wts_statements (id, at, item, about, name, role, text, link, email, status, published_at, "
            "closed_at, reason) SELECT id, at, page, country, name, role, text, nullif(link, ''), nullif(email, ''), "
            "status, shown_on, ended_at, reason FROM statements",
            "INSERT INTO wts_statement_log (statement, at, action, note) "
            "SELECT s.id, coalesce(json_extract(entry.value, '$.at'), s.at), "
            "coalesce(json_extract(entry.value, '$.action'), 'imported'), json_extract(entry.value, '$.note') "
            "FROM statements AS s, json_each(s.log) AS entry ORDER BY s.id, entry.key",
        ),
        needs=OLD_TABLES,
    ),
)


def open_state(settings: Settings, clock: Clock = SYSTEM_CLOCK) -> StateDB:
    """The site's state database, with the package's tables made (and the old rows brought across, the first time)
    on the way in. It is marked as this site's the first time it is opened, and a file marked as another site's is
    refused."""
    return StateDB(settings.state_db, migrations=migrations(), imports=IMPORTS, clock=clock, site=SITE.package)
