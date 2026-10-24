"""The UN General Debate Corpus (Jankin, Baturo and Dasandi; CC0).

Harvard Dataverse, doi:10.7910/DVN/0TJX8Y: every statement in the General Assembly's annual General Debate from
1946, one text file per statement, named ``<code>_<session>_<year>.txt`` (for example ``GBR_78_2023.txt``), in
``UNGDC_1946-2025.tar.gz``, with a list of speakers and their posts (``Speakers_by_session.xlsx``). Dataverse asks
for a short guestbook entry before the download, so the files are fetched by hand into data/raw/ungd/.

The day of each statement is not recorded, so a statement is dated to its session (``date_precision`` "session")
and shown with its session, never with a day.
"""

from __future__ import annotations

import logging
import re
import tarfile
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

from wcs.config import Settings
from wcs.reference.countries import Countries
from wcs.reference.roles import classify
from wcs.sources.speech import language, normalise_text, on_behalf_of, split_attribution

log = logging.getLogger(__name__)

NAME = re.compile(r"(?:^|/)(?P<code>[A-Z]{2,3})_(?P<session>\d{1,2})_(?P<year>\d{4})\.txt$")
# Codes the corpus uses for speakers that are not countries.
ORGANISATIONS = {"EU": "European Union"}


def raw_dir(settings: Settings) -> Path:
    return settings.raw_dir / "ungd"


def archive_path(settings: Settings) -> Path | None:
    found = sorted(raw_dir(settings).glob("UNGDC*.tar.gz")) + sorted(raw_dir(settings).glob("*.tar.gz"))
    return found[0] if found else None


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def read_speakers(path: Path | None) -> dict[tuple[str, int], dict[str, str]]:
    """Speaker name and post for each (code, session), from Speakers_by_session.xlsx (if present and readable).

    The columns are found by their headings ("ISO Code", "Session", "Name of Person Speaking", "Post"), so a
    re-ordered sheet still reads correctly."""
    if path is None or not path.exists():
        return {}
    try:
        from openpyxl import load_workbook
    except ImportError:
        log.warning("openpyxl is not installed: speakers' names and posts are left out")
        return {}
    ws = load_workbook(path, read_only=True, data_only=True).worksheets[0]
    rows = ws.iter_rows(values_only=True)
    header = [str(h or "").strip().lower() for h in next(rows, [])]

    def col(*words: str) -> int | None:
        for i, h in enumerate(header):
            if all(w in h for w in words):
                return i
        return None

    ci, si, ni, pi = col("iso"), col("session"), col("name"), col("post")
    if ci is None or si is None:
        log.warning("speaker list headings not recognised: %s", header)
        return {}
    out = {}
    for r in rows:
        try:
            key = (str(r[ci]).strip(), int(r[si]))
        except (TypeError, ValueError):
            continue
        out[key] = {"name": str(r[ni]).strip() if ni is not None and r[ni] else "",
                    "post": str(r[pi]).strip() if pi is not None and r[pi] else ""}
    return out


def record_for(name: str, text: str, speaker: dict[str, str] | None, countries: Countries,
               born_digital_from: int) -> dict[str, Any] | None:
    m = NAME.search(name)
    if not m:
        return None
    raw_code, session, year = m.group("code"), int(m.group("session")), int(m.group("year"))
    d = date(year, 9, 1)  # stands for the session: the day is not recorded
    if raw_code in ORGANISATIONS:
        code, label, kind = None, ORGANISATIONS[raw_code], "organisation"
    else:
        code = countries.resolve(raw_code) or raw_code
        label = countries.name_on(code, d)
        kind = countries.kind(code)
    text = normalise_text(text)
    attribution, speech = split_attribution(text)
    post = (speaker or {}).get("post") or ""
    behalf = on_behalf_of(speech)
    return {
        "record_id": f"gd:{session}:{raw_code}",
        "source": "gd",
        "body": "General Assembly",
        "date": d,
        "date_precision": "session",
        "year": year,
        "session": session,
        "meeting": f"General Debate, {_ordinal(session)} session",
        "symbol": None,
        "speech_number": None,
        "agenda": "General debate",
        "agenda_area": None,
        "speaker_name": (speaker or {}).get("name") or None,
        "speaker_post": post or None,
        "speaker_role": classify(post),
        "participant": "",
        "country_code": code,
        "country_label": label,
        "entity_kind": kind,
        "on_behalf_of": None if countries.resolve(behalf or "") else behalf,
        "language": language(attribution),
        "ocr": year < born_digital_from,
        "text": speech,
        "url": f"https://doi.org/10.7910/DVN/0TJX8Y#{raw_code}_{session}_{year}",
    }


def iter_records(settings: Settings, countries: Countries, years: set[int] | None = None) -> Iterator[dict[str, Any]]:
    path = archive_path(settings)
    if path is None:
        raise FileNotFoundError(f"no General Debate archive in {raw_dir(settings)}: see docs/DATA.md")
    speakers = read_speakers(next(iter(sorted(raw_dir(settings).glob("Speakers_by_session*.xlsx"))), None))
    with tarfile.open(path) as tar:
        for member in tar:
            m = NAME.search(member.name) if member.isfile() else None
            if not m or (years and int(m.group("year")) not in years):
                continue
            fh = tar.extractfile(member)
            if fh is None:
                continue
            text = fh.read().decode("utf-8", errors="replace")
            rec = record_for(Path(member.name).name, text, speakers.get((m.group("code"), int(m.group("session")))),
                             countries, settings.born_digital_from)
            if rec:
                yield rec
