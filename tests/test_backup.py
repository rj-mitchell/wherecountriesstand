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
