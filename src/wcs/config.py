"""The site's settings: config/settings.toml, read by the Where They Stand sites' shared loader (wts-core,
``wts_core.settings``), and the facts about this site that are fixed in its code.

The loader reads config/settings.toml (or the file named by ``WCS_CONFIG``), then ``settings.local.toml`` in the
data folder if there is one (laid over it one level deep), then any overrides given by name
(``**{"usage.daily_spend_cap_usd": 1.0}``). The data folder is ``WCS_DATA_DIR``, else ``[paths] data_dir``.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from importlib import metadata
from pathlib import Path
from typing import Any

from wts_core import settings as core

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = REPO_ROOT / "data_static"
# Raise when the briefing instructions (rag/prompts.py) change: it is part of the answer cache's key, so a briefing
# written under the old instructions is written again, never served as if written under the new.
INSTRUCTIONS_VERSION = 3


def _version() -> str:
    try:
        return metadata.version("wcs")
    except metadata.PackageNotFoundError:  # run from the source without being installed
        return "0"


SITE = core.Site(
    name="Where Countries Stand",
    package="wcs",
    env_prefix="WCS",  # WCS_DATA_DIR, WCS_CONFIG, WCS_ANTHROPIC_API_KEY, WCS_B2_BUCKET, WCS_CHECK_TOKEN…
    root=REPO_ROOT,
    hosts=("wherecountriesstand.com", "www.wherecountriesstand.com"),
    state_file="state.db",
    version=_version(),
    instructions_version=str(INSTRUCTIONS_VERSION),
)


class Settings(core.Settings):
    """The shared package's settings (``raw``, ``data_dir``, ``config_path``, ``section``, ``answer``, ``usage``,
    ``state_db``), with this site's own accessors."""

    @property
    def archive(self) -> dict[str, Any]:
        return self.section("archive")

    @property
    def start_date(self) -> date:
        """Start of the main collection (1992)."""
        return date.fromisoformat(self.archive.get("start_date", "1992-01-01"))

    @property
    def archive_start(self) -> date:
        """Start of the archive (1946): the General Debate and General Assembly votes."""
        return date.fromisoformat(self.archive.get("archive_start_date", "1946-01-01"))

    @property
    def born_digital_from(self) -> int:
        return int(self.archive.get("born_digital_from", 1994))

    @property
    def ranking(self) -> dict[str, Any]:
        return self.section("ranking")

    @property
    def embedding(self) -> dict[str, Any]:
        return self.section("embedding")

    @property
    def sources(self) -> dict[str, Any]:
        return self.section("sources")

    # Data layout -------------------------------------------------------------
    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def parquet_dir(self) -> Path:
        return self.data_dir / "parquet"

    @property
    def index_dir(self) -> Path:
        return self.data_dir / "index"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    @property
    def logs_dir(self) -> Path:
        """Each update's report (``update-*.json``) and the structured log of briefings (``briefings.jsonl``: the
        stages each reached and how it ended; never a reader's words)."""
        return self.data_dir / "logs"

    @property
    def reports_dir(self) -> Path:
        """The weekly reports, written by ``wcs weekly-report``."""
        return self.data_dir / "reports"


def load_settings(config_path: str | Path | None = None, env: Mapping[str, str] | None = None,
                  **overrides: Any) -> Settings:
    return core.load_settings(Settings, SITE, config_path, env, **overrides)
