"""Settings loaded from config/settings.toml, with environment overrides."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = REPO_ROOT / "config" / "settings.toml"
STATIC_DIR = REPO_ROOT / "data_static"


@dataclass(frozen=True)
class Settings:
    raw: dict[str, Any]
    data_dir: Path
    config_path: Path
    overrides: dict[str, Any] = field(default_factory=dict)

    def section(self, name: str) -> dict[str, Any]:
        return self.raw.get(name, {})

    # Convenience accessors -------------------------------------------------
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
    def answer(self) -> dict[str, Any]:
        return self.section("answer")

    @property
    def embedding(self) -> dict[str, Any]:
        return self.section("embedding")

    @property
    def sources(self) -> dict[str, Any]:
        return self.section("sources")

    @property
    def usage(self) -> dict[str, Any]:
        return self.section("usage")

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


def _resolve(path: str | Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else REPO_ROOT / p


def load_settings(config_path: str | Path | None = None, **overrides: Any) -> Settings:
    path = _resolve(config_path or os.environ.get("WCS_CONFIG") or DEFAULT_CONFIG)
    with open(path, "rb") as fh:
        raw = tomllib.load(fh)
    data_dir = _resolve(os.environ.get("WCS_DATA_DIR") or raw.get("paths", {}).get("data_dir", "data"))
    local = data_dir / "settings.local.toml"
    if local.exists():
        with open(local, "rb") as fh:
            for section, values in tomllib.load(fh).items():
                raw.setdefault(section, {}).update(values)
    for dotted, value in overrides.items():
        section, _, key = dotted.partition(".")
        raw.setdefault(section, {})[key] = value
    return Settings(raw=raw, data_dir=data_dir, config_path=path, overrides=overrides)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()
