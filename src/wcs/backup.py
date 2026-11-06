"""Nightly backup of the one thing that cannot be rebuilt from public sources: the state database (spending against
the caps, saved briefings, problem reports). Everything else comes back with ``wcs bootstrap``.

A backup is a consistent copy made with SQLite's online backup (safe while the site is running), checked, then
compressed into ``wcs-state-YYYY-MM-DD.sqlite.gz``, readable by its owner only. The newest ``keep`` are kept.
``restore_backup`` puts one back (``wcs restore``), as Where MPs Stand's ``backup.py`` does.
"""

from __future__ import annotations

import gzip
import shutil
import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path

PREFIX = "wcs-state-"


def make_backup(state_db: Path, dest: Path, keep: int = 14, day: str | None = None) -> Path | None:
    """The backup written, or None when there is no state database yet (a new site)."""
    if not state_db.exists():
        return None
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / f"{PREFIX}{day or datetime.now(UTC).strftime('%Y-%m-%d')}.sqlite.gz"
    with tempfile.TemporaryDirectory(dir=dest) as tmp:
        copy = Path(tmp) / "state.db"
        src, dst = sqlite3.connect(state_db), sqlite3.connect(copy)
        try:
            src.backup(dst)
            ok = dst.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            dst.close()
            src.close()
        if ok != "ok":
            raise RuntimeError(f"the backup copy failed its integrity check: {ok}")
        partial = Path(tmp) / out.name
        with open(copy, "rb") as f, gzip.open(partial, "wb") as g:
            shutil.copyfileobj(f, g)
        partial.chmod(0o600)  # it holds readers' problem reports: its owner only
        partial.replace(out)
    for old in sorted(dest.glob(f"{PREFIX}*.sqlite.gz"))[:-keep] if keep > 0 else []:
        old.unlink()
    return out


def restore_backup(archive: Path, state_db: Path) -> Path | None:
    """Put a backup's state database back as ``state_db``. Stop the site first. The backup is unpacked and checked
    before anything is replaced; the database it replaces is kept beside it as ``state.db.before-restore``, which is
    returned (None when there was none)."""
    archive, state_db = Path(archive), Path(state_db)
    state_db.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=state_db.parent) as tmp:
        db = Path(tmp) / "state.db"
        opener = gzip.open if archive.suffix == ".gz" else open
        try:
            with opener(archive, "rb") as f, open(db, "wb") as out:
                shutil.copyfileobj(f, out)
        except (OSError, EOFError) as exc:  # gzip's own errors are OSErrors
            raise RuntimeError(f"{archive.name} could not be read: {exc}") from exc
        con = sqlite3.connect(db)
        try:
            ok = con.execute("PRAGMA integrity_check").fetchone()[0]
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        except sqlite3.DatabaseError as exc:
            raise RuntimeError(f"{archive.name} is not a database: {exc}") from exc
        finally:
            con.close()
        if ok != "ok":
            raise RuntimeError(f"the backup's database failed its integrity check: {ok}")
        if not {"usage", "briefings"} <= tables:
            raise RuntimeError(f"{archive.name} is not a backup of this site's state database")
        kept = None
        if state_db.exists():
            kept = state_db.with_name(state_db.name + ".before-restore")
            shutil.copy2(state_db, kept)
        for suffix in ("-wal", "-shm", "-journal"):  # left from the database being replaced, they would corrupt it
            Path(f"{state_db}{suffix}").unlink(missing_ok=True)
        db.chmod(0o600)
        db.replace(state_db)
    return kept
