import gzip
import sqlite3

from typer.testing import CliRunner

from wcs.backup import make_backup
from wcs.cli import app


def test_backup_copies_the_state_database_and_keeps_the_newest(tmp_path):
    db = tmp_path / "state.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE usage (cost REAL)")
    con.execute("INSERT INTO usage VALUES (0.12)")
    con.commit()
    con.close()
    dest = tmp_path / "backups"
    for day in ("2026-10-01", "2026-10-02", "2026-10-03"):
        out = make_backup(db, dest, keep=2, day=day)
    assert sorted(p.name for p in dest.iterdir()) == ["wcs-state-2026-10-02.sqlite.gz", "wcs-state-2026-10-03.sqlite.gz"]
    assert out.stat().st_mode & 0o777 == 0o600
    restored = tmp_path / "restored.db"
    restored.write_bytes(gzip.decompress(out.read_bytes()))
    assert sqlite3.connect(restored).execute("SELECT cost FROM usage").fetchone() == (0.12,)


def test_a_new_site_has_nothing_to_back_up(settings, tmp_path):
    r = CliRunner().invoke(app, ["backup", "--to", str(tmp_path / "b")])
    assert r.exit_code == 0 and "nothing to back up" in r.output


def state_db(path, cost=0.12):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE IF NOT EXISTS usage (cost REAL)")
    con.execute("CREATE TABLE IF NOT EXISTS briefings (id TEXT)")
    con.execute("INSERT INTO usage VALUES (?)", (cost,))
    con.commit()
    con.close()
    return path


def test_restore_puts_a_backup_back_and_keeps_the_database_it_replaces(tmp_path):
    from wcs.backup import restore_backup

    out = make_backup(state_db(tmp_path / "old.db", 0.12), tmp_path / "backups", day="2026-10-01")
    (tmp_path / "data").mkdir()
    live = state_db(tmp_path / "data" / "state.db", 9.99)
    (tmp_path / "data" / "state.db-journal").write_bytes(b"left over")
    kept = restore_backup(out, live)
    assert sqlite3.connect(live).execute("SELECT cost FROM usage").fetchall() == [(0.12,)]
    assert sqlite3.connect(kept).execute("SELECT cost FROM usage").fetchall() == [(9.99,)]
    assert not (tmp_path / "data" / "state.db-journal").exists()


def test_restore_refuses_a_damaged_or_foreign_file_and_changes_nothing(tmp_path):
    import pytest

    from wcs.backup import restore_backup

    live = state_db(tmp_path / "state.db", 9.99)
    bad = tmp_path / "wcs-state-2026-10-01.sqlite.gz"
    bad.write_bytes(gzip.compress(b"not a database at all" * 100))
    with pytest.raises(RuntimeError, match="not a database"):
        restore_backup(bad, live)
    truncated = tmp_path / "cut.sqlite.gz"
    truncated.write_bytes(gzip.compress(b"x" * 1000)[:20])
    with pytest.raises(RuntimeError, match="could not be read"):
        restore_backup(truncated, live)
    other = tmp_path / "other.db"
    sqlite3.connect(other).execute("CREATE TABLE something (x)").connection.commit()
    foreign = make_backup(other, tmp_path / "b", day="2026-10-02")
    with pytest.raises(RuntimeError, match="not a backup of this site"):
        restore_backup(foreign, live)
    assert sqlite3.connect(live).execute("SELECT cost FROM usage").fetchall() == [(9.99,)]


def test_the_restore_command(settings, tmp_path):
    out = make_backup(state_db(tmp_path / "old.db", 0.5), tmp_path / "b", day="2026-10-01")
    r = CliRunner().invoke(app, ["restore", str(out), "--yes"])
    assert r.exit_code == 0, r.output
    assert sqlite3.connect(settings.data_dir / "state.db").execute("SELECT cost FROM usage").fetchone() == (0.5,)
    r = CliRunner().invoke(app, ["restore", str(tmp_path / "missing.sqlite.gz"), "--yes"])
    assert r.exit_code == 1 and "Not restored" in r.output


def test_backup_alerts(tmp_path):
    import json
    import os
    from datetime import UTC, datetime, timedelta

    from wcs.offsite import backup_alerts

    now = datetime(2026, 11, 6, 12, tzinfo=UTC)
    b2 = {"WCS_B2_BUCKET": "b", "WCS_B2_KEY_ID": "k", "WCS_B2_APPLICATION_KEY": "a"}
    assert backup_alerts(tmp_path / "none", {}, now) == []  # no backups folder: nothing to say
    folder = tmp_path / "backups"
    folder.mkdir()
    assert [a["text"] for a in backup_alerts(folder, {}, now)] == [
        "no backups have been made yet", "backups are kept on this server only: no off-site copy is set up"]
    out = make_backup(state_db(tmp_path / "s.db"), folder, day="2026-11-01")
    old = (now - timedelta(days=3)).timestamp()
    os.utime(out, (old, old))
    alerts = backup_alerts(folder, b2, now)
    assert alerts[0]["level"] == "error" and "over two days ago (wcs-state-2026-11-01" in alerts[0]["text"]
    assert alerts[1]["level"] == "warning" and "no off-site copy has been made yet" in alerts[1]["text"]
    fresh = (now - timedelta(hours=3)).timestamp()
    os.utime(out, (fresh, fresh))
    (folder / "offsite.json").write_text(json.dumps({"ok": False, "when": "2026-11-06T03:00:00+00:00",
                                                     "error": "Backblaze did not accept the key"}))
    assert backup_alerts(folder, b2, now) == [{"level": "error", "text": "the last off-site copy failed "
                                               "(2026-11-06T03:00): Backblaze did not accept the key"}]
    (folder / "offsite.json").write_text(json.dumps({"ok": True, "when": "2026-11-02T03:00:00+00:00"}))
    assert "over two days ago (2026-11-02)" in backup_alerts(folder, b2, now)[0]["text"]
    (folder / "offsite.json").write_text(json.dumps({"ok": True, "when": "2026-11-06T03:00:00+00:00"}))
    assert backup_alerts(folder, b2, now) == []


def test_a_stopped_backup_makes_the_update_need_attention_but_not_the_public_status(tmp_path):
    from wcs.update import Report, backups

    folder = tmp_path / "backups"
    folder.mkdir()
    out = make_backup(state_db(tmp_path / "s.db"), folder, day="2026-01-01")
    import os

    os.utime(out, (0, 0))
    report = Report(started="2026-11-06T05:41:00+00:00")
    backups(report, folder)
    assert report.ok and report.needs_attention
    assert report.to_dict()["needs_attention"] is True
