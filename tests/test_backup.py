"""Backups of the state database, restores and off-site copies: the Where They Stand sites' shared ones (wts-core,
``wts_core.ops``), with what this site's backups are called and hold (``wcs.state.BACKUPS``) and its own words.

That the files made, the lines printed, the refusals and the alerts are what the site's own code gave is in
tests/test_parity.py, case by case. Here: what is this site's own, and what the move added.
"""

from __future__ import annotations

import gzip
import os
import sqlite3

import pytest
import shared
from typer.testing import CliRunner
from wts_core.ops import RestoreRefused, make_backup, restore_backup
from wts_core.testing import FixedClock

from wcs import cli
from wcs.state import BACKUPS, open_state


def test_what_the_sites_backups_are_called_and_hold(settings, tmp_path):
    open_state(settings).set("something", "kept")
    out = make_backup(settings.state_db, tmp_path / "b", BACKUPS, clock=FixedClock("2026-11-14T04:20:00+00:00"))
    assert out.name == "wcs-state-2026-11-14.sqlite.gz" and oct(out.stat().st_mode & 0o777) == "0o600"
    assert BACKUPS.keep == 14 and BACKUPS.keep_offsite == 60  # a fortnight on the server, two months off it
    copy = tmp_path / "copy.db"
    copy.write_bytes(gzip.decompress(out.read_bytes()))
    con = sqlite3.connect(copy)
    assert con.execute("SELECT value FROM wts_kv WHERE key = 'something'").fetchone() == ("kept",)
    assert con.execute("SELECT value FROM wts_kv WHERE key = 'site'").fetchone() == ("wcs",)  # marked as this site's
    con.close()


def test_a_backup_from_before_the_move_is_still_restored_and_its_rows_brought_across(settings, tmp_path):
    """Every backup on the server and off it when the site moves was made by the old code: no ``wts_`` tables, no
    mark. A restore takes one (it has the two tables every backup of this site has had), and the next start
    brings its rows across, as the first start after the move did."""
    old = tmp_path / "old.db"
    con = sqlite3.connect(old)
    con.executescript((shared.GOLDEN / "old-state.sql").read_text(encoding="utf-8"))
    con.close()
    archive = make_backup(old, tmp_path / "b", BACKUPS, clock=FixedClock("2026-11-12T04:20:00+00:00"))
    open_state(settings).set("visitor_salt:2026-11-14", "newer")  # the database now on the server, to be replaced
    kept = restore_backup(archive, settings.state_db, BACKUPS)
    assert kept == settings.state_db.with_name("state.db.before-restore")
    db = open_state(settings, FixedClock(shared.START))
    with db.connect() as con:
        assert con.execute("SELECT count(*) FROM wts_spending").fetchone()[0] == 10
        assert con.execute("SELECT count(*) FROM wts_briefings").fetchone()[0] == 3
    assert db.get("site") == "wcs"


def test_a_sister_sites_backup_is_refused(settings, tmp_path):
    """New with wts-core 0.2.0: a state database is marked as its site's when it is opened, and a restore refuses
    one marked as another's. Where Councils Stand's backups hold tables of the same names as this site's, so the
    tables alone could not tell them apart."""
    from wts_core.state import StateDB

    from wcs.state import migrations

    other = tmp_path / "councils" / "state.db"
    other.parent.mkdir()
    con = sqlite3.connect(other)
    con.executescript("CREATE TABLE usage (id INTEGER PRIMARY KEY); CREATE TABLE briefings (id TEXT PRIMARY KEY);")
    con.close()
    StateDB(other, migrations=migrations(), site="councils")
    archive = make_backup(other, tmp_path / "b", BACKUPS, clock=FixedClock("2026-11-14T04:20:00+00:00"))
    open_state(settings).set("mine", "kept")
    with pytest.raises(RestoreRefused) as refused:
        restore_backup(archive, settings.state_db, BACKUPS)
    assert refused.value.why == "other-site"
    assert open_state(settings).get("mine") == "kept"  # nothing was replaced
    result = CliRunner().invoke(cli.app, ["restore", str(archive), "--yes"])
    assert result.exit_code == 1 and result.output.startswith("Not restored: ")


def test_a_stopped_backup_makes_the_update_need_attention_but_not_the_public_status(tmp_path):
    from wcs.update import Report, backups

    folder = tmp_path / "backups"
    folder.mkdir()
    out = make_backup(shared.a_state_database(tmp_path / "s.db"), folder, BACKUPS,
                      clock=FixedClock("2026-01-01T04:20:00+00:00"))
    os.utime(out, (0, 0))
    report = Report(started="2026-11-06T05:41:00+00:00")
    backups(report, folder)
    assert report.ok and report.needs_attention
    assert report.to_dict()["needs_attention"] is True
    assert "is wcs-backup.timer running?" in report.backups[0]["text"]
