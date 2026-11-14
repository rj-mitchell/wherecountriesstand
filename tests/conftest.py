from __future__ import annotations

import os

# A test that reads a command's help must have Typer's colours off before Typer is imported: GitHub Actions makes
# Typer colour its help, which splits an option's name in two.
os.environ["_TYPER_FORCE_DISABLE_TERMINAL"] = "1"
os.environ["NO_COLOR"] = "1"

import sys  # noqa: E402
from datetime import date  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

from wcs.config import load_settings  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent / "golden"))  # tests/golden/shared.py: the scenarios the goldens hold


@pytest.fixture
def settings(tmp_path, monkeypatch):
    """Settings with a temporary data folder and the offline hashing embedder."""
    monkeypatch.setenv("WCS_DATA_DIR", str(tmp_path / "data"))
    return load_settings(**{"embedding.backend": "hashing", "embedding.dim": 64, "live.gd_bundled": ""})


def speech(record_id: str, d: date, code: str | None, text: str, **kw) -> dict:
    """A stored speech record with sensible defaults."""
    source = kw.pop("source", "sc")
    return {
        "record_id": record_id, "source": source,
        "body": "Security Council" if source == "sc" else "General Assembly",
        "date": d, "date_precision": "day" if source == "sc" else "session", "year": d.year,
        "session": None if source == "sc" else d.year - 1945, "meeting": kw.pop("meeting", "9000th meeting"),
        "symbol": kw.pop("symbol", "S/PV.9000" if source == "sc" else None), "speech_number": kw.pop("n", 1),
        "agenda": kw.pop("agenda", "Maintenance of international peace and security"), "agenda_area": "Thematic",
        "speaker_name": kw.pop("speaker", "Mr. Smith"), "speaker_post": None, "speaker_role": kw.pop("role", ""),
        "participant": kw.pop("participant", "member"), "country_code": code,
        "country_label": kw.pop("label", code), "entity_kind": kw.pop("kind", "member_state"),
        "on_behalf_of": kw.pop("behalf", None), "language": kw.pop("language", None), "ocr": False,
        "text": text, "url": "https://undocs.org/en/S/PV.9000", **kw,
    }
