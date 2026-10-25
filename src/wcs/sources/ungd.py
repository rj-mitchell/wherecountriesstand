"""The UN General Debate Corpus (Jankin, Baturo and Dasandi; CC0).

Harvard Dataverse, doi:10.7910/DVN/0TJX8Y: every statement in the General Assembly's annual General Debate from
1946, one text file per statement, named ``<code>_<session>_<year>.txt`` (for example ``GBR_78_2023.txt``), in
``UNGDC_1946-2025.tar.gz``, with a list of speakers and their posts (``Speakers_by_session.xlsx``). Dataverse asks
for a short guestbook entry before the download; ``fetch`` makes it through Dataverse's API, with the name and email
from the environment (``WCS_GUESTBOOK_NAME``, ``WCS_GUESTBOOK_EMAIL``), and takes the latest version's files, so a new
session added by the authors (``UNGDC_1946-2026.tar.gz``) is found without a change here.

The corpus does not say which language a statement was given in. ``add_languages`` fills it in from the UN's records
of the same statements, read for the rights of reply (from 2000), so translations are marked as the UN's.

The day of each statement is not recorded, so a statement is dated to its session (``date_precision`` "session")
and shown with its session, never with a day.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import tarfile
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from wcs.config import Settings
from wcs.reference.countries import Countries
from wcs.reference.roles import classify
from wcs.sources.speech import general_debate_behalf, language, normalise_text, split_attribution

log = logging.getLogger(__name__)

NAME = re.compile(r"(?:^|/)(?P<code>[A-Z]{2,3})_(?P<session>\d{1,2})_(?P<year>\d{4})\.txt$")
# Older records number their paragraphs ("81.<tab>This fifteenth session ..."); the corpus keeps the numbers.
# The number is followed by a tab, which nothing else in the texts is; some files have old Mac line endings, or the
# number in the middle of a line.
PARAGRAPH_NUMBER = re.compile(r"[ \t\r\n]*\b\d{1,3}\.\t[ \t]*")
# Codes the corpus uses for speakers that are not countries.
ORGANISATIONS = {"EU": "European Union"}


def raw_dir(settings: Settings) -> Path:
    return settings.raw_dir / "ungd"


def archive_path(settings: Settings) -> Path | None:
    found = sorted(raw_dir(settings).glob("UNGDC*.tar.gz")) + sorted(raw_dir(settings).glob("*.tar.gz"))
    return found[0] if found else None


DATAVERSE = "https://dataverse.harvard.edu"
DATASET = "doi:10.7910/DVN/0TJX8Y"
WANTED = (re.compile(r"^UNGDC_\d{4}-\d{4}\.tar\.gz$"), re.compile(r"^Speakers_by_session\.xlsx$"))


def guestbook_entry(settings: Settings, guestbook: dict[str, Any]) -> dict[str, Any]:
    """The answers Dataverse's guestbook asks for: name and email from the environment, the rest from
    ``[sources.ungd_guestbook]``; a multiple-choice question is answered with the configured option, if offered."""
    conf = settings.sources.get("ungd_guestbook") or {}
    name, email = os.environ.get("WCS_GUESTBOOK_NAME"), os.environ.get("WCS_GUESTBOOK_EMAIL")
    if not (name and email):
        raise RuntimeError("the General Debate corpus asks for a guestbook entry: set WCS_GUESTBOOK_NAME and "
                           "WCS_GUESTBOOK_EMAIL (deploy/.env), or download it by hand (docs/DATA.md)")
    answers = []
    for q in guestbook.get("customQuestions") or []:
        options = [o["value"] for o in q.get("optionValues") or []]
        want = conf.get("purpose", "Research")
        value = want if not options or want in options else options[-1]
        answers.append({"id": q["id"], "value": value})
    return {"name": name, "email": email, "institution": conf.get("institution", ""),
            "position": os.environ.get("WCS_GUESTBOOK_POSITION") or conf.get("position", ""), "answers": answers}


def fetch(settings: Settings, force: bool = False, client: httpx.Client | None = None) -> list[Path]:
    """Download the latest version of the corpus (about 71 MB) and its speaker list into data/raw/ungd/, making the
    guestbook entry Dataverse asks for; checksums are verified. Files already there are kept unless ``force``."""
    out = raw_dir(settings)
    out.mkdir(parents=True, exist_ok=True)
    http = client or httpx.Client(timeout=600, follow_redirects=True)
    meta = http.get(f"{DATAVERSE}/api/datasets/:persistentId/", params={"persistentId": DATASET})
    meta.raise_for_status()
    data = meta.json()["data"]
    files = [f["dataFile"] for f in data["latestVersion"]["files"]
             if any(w.match(f["dataFile"]["filename"]) for w in WANTED)]
    if len(files) < len(WANTED):
        raise RuntimeError(f"the corpus's files have new names: {[f['dataFile']['filename'] for f in data['latestVersion']['files']]}")
    entry = None
    paths = []
    for f in files:
        path = out / f["filename"]
        paths.append(path)
        if path.exists() and not force:
            continue
        url = f"{DATAVERSE}/api/access/datafile/{f['id']}"
        if data.get("guestbookId"):
            if entry is None:
                gb = http.get(f"{DATAVERSE}/api/guestbooks/{data['guestbookId']}")
                gb.raise_for_status()
                entry = guestbook_entry(settings, gb.json()["data"])
            signed = http.post(url, params={"gbrecs": "true"}, json={"guestbookResponse": entry})
            signed.raise_for_status()
            url = signed.json()["data"]["signedUrl"]
        tmp = path.with_suffix(path.suffix + ".part")
        md5 = hashlib.md5()
        log.info("downloading %s", f["filename"])
        with http.stream("GET", url) as r:
            r.raise_for_status()
            with open(tmp, "wb") as fh:
                for chunk in r.iter_bytes(1 << 20):
                    fh.write(chunk)
                    md5.update(chunk)
        want = (f.get("checksum") or {}).get("value") or f.get("md5")
        if want and md5.hexdigest() != want:
            tmp.unlink()
            raise RuntimeError(f"{f['filename']}: checksum does not match")
        tmp.replace(path)
    for old in out.glob("UNGDC_*.tar.gz"):  # an older version, replaced by the latest
        if old not in paths:
            old.unlink()
    return paths


def add_languages(settings: Settings, first_session: int) -> int:
    """Fill in the language of corpus statements from the UN's records of the same statements (parsed for the
    rights of reply, from 2000). Returns how many statements gained one."""
    from wcs.sources import undocs, unga
    from wcs.store.parquet import read_year, write_year, years

    manifest = undocs.Manifest.load(settings, "ga")
    spoken: dict[tuple[int, str], str] = {}
    for symbol, entry in manifest.entries.items():
        session = unga.session_of(symbol)
        if entry.get("status") != "parsed" or not session or session >= first_session:
            continue
        for r in undocs.load_parsed(settings, symbol):
            if r.get("language") and r.get("country_code") and not unga.is_reply(r):
                spoken[(session, r["country_code"])] = r["language"]
    added = 0
    for year in years(settings.parquet_dir, "gd"):
        rows = read_year(settings.parquet_dir, "gd", year)
        changed = 0
        for r in rows:
            lang = spoken.get((r["session"], r["country_code"])) if r.get("date_precision") == "session" else None
            if lang and not r.get("language"):
                r["language"] = lang
                changed += 1
        if changed:
            write_year(settings.parquet_dir, "gd", year, rows)
            added += changed
    return added


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
    text = normalise_text(PARAGRAPH_NUMBER.sub("\n\n", text).strip())
    attribution, speech = split_attribution(text)
    post = (speaker or {}).get("post") or ""
    behalf = general_debate_behalf(speech)
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
