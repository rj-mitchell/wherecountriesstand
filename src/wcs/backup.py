"""Nightly backup of the one thing that cannot be rebuilt from public sources: the state database (spending against
the caps, saved briefings, problem reports). Everything else comes back with ``wcs bootstrap``.

A backup is a consistent copy made with SQLite's online backup (safe while the site is running), checked, then
compressed into ``wcs-state-YYYY-MM-DD.sqlite.gz``, readable by its owner only. The newest ``keep`` are kept.
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
