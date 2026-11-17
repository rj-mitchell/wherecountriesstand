"""The UN Security Council Debates corpus (Schoenfeld, Eckhard, Patz, van Meegdenburg and others; CC0).

Harvard Dataverse, doi:10.7910/DVN/KGVSYH: every speech in the Council's public meetings from January 1992 to
December 2023, one text file per speech (``speeches.tar``), with a table of speakers (``speaker.tsv``) giving each
speech's date, meeting symbol, speaker, country or organisation and whether the speaker sat on the Council.

Who a speech is attributed to:

* a Council member or invited country speaking for itself: its country;
* the President presiding (welcomes, procedure, statements read for the Council): the chair (``entity_kind``
  "chair"), never the President's country; statements "on behalf of the Council" are the Security Council's;
* the President "in my capacity as the representative of X": that country;
* a statement on behalf of a group: the speaker's country, with the group in ``on_behalf_of``;
* UN officials, other organisations and civil society briefers: ``entity_kind`` "un" or "organisation".
"""

from __future__ import annotations

import csv
import logging
import re
import tarfile
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from wcs.config import Settings
from wcs.reference.countries import Countries
from wcs.sources.speech import (
    in_national_capacity,
    language,
    normalise_text,
    on_behalf_of,
    split_attribution,
)

log = logging.getLogger(__name__)

FILES = {"meta.tsv": "sc_meta", "speaker.tsv": "sc_speakers", "speeches.tar": "sc_speeches"}
# Meeting records (S/PV.nnnn) and, for the videoconferences of 2020 and 2021, the letters that recorded them
# (S/2020/751)
NAME = re.compile(r"(?:^|/)UNSC_(?P<year>\d{4})_(?P<symbol>S[._]?PV[._]\d+(?:[._]?Resumption\d+)?|S_\d{4}_\d+)"
                  r"_spch(?P<n>\d+)\.txt$")
PARTICIPANT = {"the president": "president", "mentioned": "member", "guest": "guest"}
UN_LABELS = {"un", "united nations", "un secretariat"}


def raw_dir(settings: Settings) -> Path:
    return settings.raw_dir / "unsc"


def fetch(settings: Settings, force: bool = False) -> list[Path]:
    """Download the corpus files from Harvard Dataverse (about 510 MB) into data/raw/unsc/."""
    out = raw_dir(settings)
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, key in FILES.items():
        path = out / name
        paths.append(path)
        if path.exists() and not force:
            continue
        tmp = path.with_suffix(path.suffix + ".part")
        log.info("downloading %s", name)
        with httpx.stream("GET", settings.sources[key], follow_redirects=True, timeout=600) as r:
            r.raise_for_status()
            with open(tmp, "wb") as fh:
                for chunk in r.iter_bytes(1 << 20):
                    fh.write(chunk)
        tmp.replace(path)
    return paths


def symbol_of(raw: str) -> str:
    """"SPV.7565" in a file name -> "S/PV.7565"; "S_2020_751" -> "S/2020/751"; a resumed meeting
    ("S/PV.9220Resumption1") -> "S/PV.9220 (Resumption 1)", as the UN writes it."""
    m = re.match(r"S[._/]?PV[._](\d+)\s*\(?(?:[._]?Resumption\s*(\d+))?\)?$", raw)
    if m:
        return f"S/PV.{m.group(1)}" + (f" (Resumption {m.group(2)})" if m.group(2) else "")
    m = re.fullmatch(r"S_(\d{4})_(\d+)", raw)
    return f"S/{m.group(1)}/{m.group(2)}" if m else raw


def meeting_label(symbol: str) -> str:
    """"7565th meeting"; for a videoconference recorded in a letter, "videoconference (recorded in S/2020/751)"."""
    m = re.fullmatch(r"S/PV\.(\d+)(?: \(Resumption (\d+)\))?", symbol)
    if not m:
        return f"videoconference (recorded in {symbol})"
    return f"{_ordinal(int(m.group(1)))} meeting" + (f" (resumption {m.group(2)})" if m.group(2) else "")


def symbol_url(undocs: str, symbol: str) -> str:
    """The address of a document: undocs.org writes a resumed meeting as S/PV.9220(Resumption1)."""
    return undocs + re.sub(r" \(Resumption (\d+)\)", r"(Resumption\1)", symbol)


def read_speakers(path: Path) -> dict[str, dict[str, str]]:
    """Speaker rows keyed by speech file name. Rows broken in the source (fields shifted) are skipped."""
    csv.field_size_limit(1 << 30)
    out = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            name = row.get("filename") or ""
            if NAME.search(name) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", row.get("date") or ""):
                out[name] = row
    return out


def _clean(v: str | None) -> str | None:
    v = (v or "").strip()
    return None if v in ("", "NA") else v


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def record_for(name: str, text: str, row: dict[str, str] | None, countries: Countries, born_digital_from: int,
               undocs: str) -> dict[str, Any] | None:
    m = NAME.search(name)
    if not m:
        return None
    row = row or {}
    d = date.fromisoformat(row["date"]) if row.get("date") else None
    if d is None:
        return None
    symbol = symbol_of(_clean(row.get("document_symbol")) or m.group("symbol"))
    attribution, speech = split_attribution(normalise_text(text))
    participant = PARTICIPANT.get((row.get("participanttype") or "").strip().lower(), "")
    label = _clean(row.get("country_org"))
    speaker = _clean(row.get("speaker")) or (attribution.split("(")[0].strip() if attribution else None)
    chair = participant == "president" or (attribution or "").lower().startswith("the president")
    behalf = on_behalf_of(speech, chair=chair)
    if behalf and countries.resolve(behalf):
        behalf = None  # "on behalf of the United States": a country speaking for itself
    code = countries.resolve(label)
    if chair and not in_national_capacity(speech):
        kind, code = "chair", None
    elif code is None:
        kind = "un" if (label or "").strip().lower() in UN_LABELS or (label or "").startswith("UN ") else "organisation"
    else:
        kind = countries.kind(code)
    number = int(m.group("n"))
    return {
        "record_id": f"sc:{symbol}:{number:04d}",
        "source": "sc",
        "body": "Security Council",
        "date": d,
        "date_precision": "day",
        "year": d.year,
        "session": None,
        "meeting": meeting_label(symbol),
        "symbol": symbol,
        "speech_number": number,
        "agenda": _clean(row.get("topic")),
        "agenda_area": _clean(row.get("agenda_item1")),
        "speaker_name": speaker,
        "speaker_post": _clean(row.get("role")),
        "speaker_role": "",
        "participant": participant,
        "country_code": code,
        "country_label": label,
        "entity_kind": kind,
        "on_behalf_of": behalf,
        "language": language(attribution),
        "ocr": d.year < born_digital_from,
        "text": speech,
        "url": symbol_url(undocs, symbol),
    }


def iter_records(settings: Settings, countries: Countries, years: set[int] | None = None) -> Iterator[dict[str, Any]]:
    """Every speech in the corpus as a record (optionally only some years)."""
    d = raw_dir(settings)
    speakers = read_speakers(d / "speaker.tsv")
    with tarfile.open(d / "speeches.tar") as tar:
        for member in tar:
            if not member.isfile():
                continue
            m = NAME.search(member.name)
            if not m or (years and int(m.group("year")) not in years):
                continue
            base = Path(member.name).name
            fh = tar.extractfile(member)
            if fh is None:
                continue
            text = fh.read().decode("utf-8", errors="replace")
            rec = record_for(base, text, speakers.get(base), countries, settings.born_digital_from,
                             settings.sources.get("undocs", "https://undocs.org/en/"))
            if rec is None:
                log.warning("skipped %s (no speaker row)", base)
                continue
            yield rec
