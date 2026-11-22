"""Security Council votes (decision 30): the resolutions it adopted, and the drafts it did not.

* **Adopted resolutions** come from the UN Dag Hammarskjöld Library's Security Council voting data (UN Digital
  Library record 4055387; copyright United Nations, non-commercial use with attribution): one row per member per
  resolution, from resolution 1 (1946). Its file is found from the record's MARC export, as the General Assembly's
  is (``wcs.sources.votes``).
* **Drafts not adopted** are not in that data. They are read from the meeting records the site holds (the Security
  Council corpus to 2023 and the UN's own records since: the stored ``sc`` records), by ``wcs.sources.vote_result``:
  what was put to the vote, how each member voted, and the President's announcement of the result, in the record's
  words. A draft that failed because a permanent member voted against ("owing to the negative vote of a permanent
  member of the Council") and one that did not get the nine votes it needed ("having failed to obtain the required
  number of votes") are told apart by those words.
* **The Library's list of vetoes** (its research guide, https://research.un.org/en/docs/sc/quick/veto, from 1946)
  is read too. Each veto it lists since the site's records begin must be found in them, with the same permanent
  members against: the update job reports any that is not (``check_vetoes``). A listed veto whose record the site
  holds but whose vote it cannot read (a page lost from a PDF, a record recognised from a scan) is read again from
  the UN's PDF of the record, a few a run. Vetoes from before the records begin are kept as the list gives them: the
  date, the draft, the meeting record, the agenda item and the permanent members voting against. No member's vote
  is ever made up.

Votes on procedural proposals (to adjourn a meeting) have no document and are left out, as are oral amendments.
"""

from __future__ import annotations

import html as htmlmod
import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb
import httpx

from wcs.config import Settings
from wcs.reference.countries import Countries, norm
from wcs.sources import votes as vote_files
from wcs.sources.vote_result import Chunk, Vote, resolve_lists, votes_in
from wcs.store.parquet import council_votes_path, record_path, write_council_votes, years

log = logging.getLogger(__name__)

FOLDER = "council_votes"                 # data/raw/council_votes/: the CSV and the list of vetoes
RECORD_KEY = "council_votes_record"
DEFAULT_RECORD = "https://digitallibrary.un.org/record/4055387"
DEFAULT_VETO_LIST = "https://research.un.org/en/docs/sc/quick/veto"
# the page shows the list in a frame from the Library's own service; this is where it was on 22 November 2026, used
# if the page itself cannot be read
VETO_FRAME = "https://ydsftksff8.execute-api.us-east-1.amazonaws.com/dev/render_meeting_veto/sc_veto_list/EN"
USER_AGENT = "WhereCountriesStand/0.1 (independent research; https://github.com/rj-mitchell/wherecountriesstand)"
MAX_FETCH = 40          # records read again from the UN's PDFs in one run
VERSION = 1             # of how the drafts are read: a higher one reads every meeting again
BODY = "Security Council"
# the permanent members as the list of vetoes names them
P5_LABELS = {"ussr": "SUN", "usa": "USA", "united states": "USA", "united states of america": "USA", "uk": "GBR",
             "united kingdom": "GBR", "france": "FRA", "china": "CHN", "russian federation": "RUS"}


def raw_dir(settings: Settings) -> Path:
    return vote_files.raw_dir(settings, FOLDER)


def record_url(settings: Settings) -> str:
    return str(settings.sources.get(RECORD_KEY) or DEFAULT_RECORD)


def file_url(settings: Settings) -> str:
    return vote_files.file_url(_with_record(settings), RECORD_KEY)


def _with_record(settings: Settings) -> Settings:
    if RECORD_KEY not in settings.sources:
        settings.sources[RECORD_KEY] = DEFAULT_RECORD
    return settings


def fetch(settings: Settings, force: bool = False) -> Path:
    """Download the current CSV (about 16 MB), and delete the ones it supersedes."""
    return vote_files.fetch(_with_record(settings), force, RECORD_KEY, FOLDER)


def latest_csv(settings: Settings) -> Path | None:
    return vote_files.latest_csv(settings, FOLDER)


# The voting data: adopted resolutions

def resolution_rows(csv_path: Path) -> list[dict[str, Any]]:
    """Every row of the voting data, in the stored form (``COUNCIL_VOTE_SCHEMA``)."""
    con = duckdb.connect()

    def num(col: str) -> str:
        return f"TRY_CAST(TRY_CAST({col} AS DOUBLE) AS INTEGER)"

    rows = con.execute(f"""
        SELECT undl_id, CASE ms_code WHEN 'GER' THEN 'DEU' ELSE ms_code END, ms_name, lower(permanent_member) = 'true',
               ms_vote, TRY_CAST(date AS DATE), resolution, draft, meeting, description, agenda, subjects, vote_note,
               {num('total_yes')}, {num('total_no')}, {num('total_abstentions')}, {num('total_non_voting')},
               {num('total_ms')}, modality, undl_link
        FROM read_csv(?, header = true, all_varchar = true)""", [str(csv_path)]).fetchall()
    out = []
    for (undl, code, name, p5, how, on, res, draft, meeting, title, agenda, subjects, note, ty, tn, ta, tx, tm,
         modality, url) in rows:
        out.append({"vote_id": str(undl), "body": BODY, "outcome": "adopted", "reason": None, "what": "resolution",
                    "country_code": code, "country_name": name, "permanent_member": bool(p5), "vote": how,
                    "date": on, "resolution": re.sub(r"\s+\(", "(", res or "") or None, "draft": draft, "amends": None, "meeting": meeting,
                    "title": title, "agenda": agenda, "subjects": subjects, "vote_note": note, "result": None,
                    "total_yes": ty, "total_no": tn, "total_abstentions": ta, "total_non_voting": tx, "total_ms": tm,
                    "modality": modality, "vetoed_by": [], "source": "voting data", "complete": True, "url": url})
    return out


# The Library's list of vetoes

@dataclass
class Veto:
    """One line of the Library's list of vetoes."""
    on: date
    drafts: list[str]            # "S/2024/239"; "S/PV.49" where the list gives the meeting for a proposal
    records: list[str]           # "S/PV.9584"; two letters for a vote taken by letter in 2020
    agenda: str
    members: list[str]           # the permanent members casting a negative vote, as codes
    named: str                   # as the list names them
    note: str | None = None      # the list's own note: "*" (most are vetoes of paragraphs or amendments), or
                                 # "Not identified in private meeting communiqué, but attributed to ..."
    oral: bool = False           # an oral amendment, with no document of its own

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "on": self.on.isoformat()}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Veto:
        return cls(**{**d, "on": date.fromisoformat(d["on"])})


def _cell(text: str) -> str:
    text = re.sub(r"<br\s*/?>", "|", text, flags=re.I)
    return " ".join(htmlmod.unescape(re.sub(r"<[^>]+>", " ", text)).split())


def _day(text: str) -> date | None:
    try:
        return datetime.strptime(text.strip(), "%d %B %Y").date()
    except ValueError:
        return None


def parse_veto_list(page: str) -> list[Veto]:
    """The list's table: date, draft, meeting record, agenda item, permanent members casting a negative vote."""
    out = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S | re.I):
        cells = [_cell(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S | re.I)]
        if len(cells) != 5 or (on := _day(cells[0])) is None:
            continue
        drafts_cell, records_cell, agenda, who = cells[1], cells[2], cells[3], cells[4]
        oral = "oral amendment" in drafts_cell.lower()
        drafts = [d.strip() for d in drafts_cell.split("|") if re.match(r"\s*S/", d)]
        records = [r.strip() for r in records_cell.split("|") if r.strip()]
        note = None
        if who.startswith("*"):
            note = who.lstrip("*").strip()
            who = re.sub(r"^.*attributed to\s+", "", note) if "attributed to" in note else note
            note = note if "attributed" in note else "*"
        members = []
        for part in re.split(r"\|", who):
            code = P5_LABELS.get(part.strip(" .*").lower())
            if code and code not in members:
                members.append(code)
        named = ", ".join(" ".join(p.split()) for p in who.split("|") if p.strip())
        out.append(Veto(on, drafts, records, agenda, members, named, note, oral))
    return out


def veto_frame(page: str) -> str | None:
    """The address of the frame the research guide shows the list in."""
    m = re.search(r'<iframe[^>]+src="([^"]*veto[^"]*)"', page, re.I)
    if not m:
        return None
    src = htmlmod.unescape(m[1])
    return ("https:" + src) if src.startswith("//") else src


def fetch_veto_list(settings: Settings, client: httpx.Client | None = None) -> Path:
    """Read the list of vetoes (two requests: the research guide's page, then the frame it shows the list in), and
    keep it in data/raw/council_votes/veto-list.html."""
    client = client or httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=60, follow_redirects=True)
    page_url = str(settings.sources.get("veto_list") or DEFAULT_VETO_LIST)
    frame = VETO_FRAME
    try:
        r = client.get(page_url)
        r.raise_for_status()
        frame = veto_frame(r.text) or VETO_FRAME
    except httpx.HTTPError as exc:
        log.warning("the research guide's page could not be read (%s); trying the list's own address", exc)
    r = client.get(frame)
    r.raise_for_status()
    if not parse_veto_list(r.text):
        raise RuntimeError(f"no vetoes listed at {frame}: the list's layout may have changed")
    out = raw_dir(settings) / "veto-list.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(r.text, encoding="utf-8")
    return out


def veto_list(settings: Settings) -> list[Veto]:
    path = raw_dir(settings) / "veto-list.html"
    return parse_veto_list(path.read_text(encoding="utf-8")) if path.exists() else []


# Drafts not adopted, from the meeting records

@dataclass
class Found:
    """A vote on a draft not adopted, as a meeting record gives it."""
    meeting: str
    on: date
    vote: Vote
    codes: dict[str, list[str]]
    unknown: list[str] = field(default_factory=list)
    amends: str | None = None
    agenda: str | None = None
    source: str = "meeting record"
    oral: bool = False          # an oral amendment: it has no document, so no page; the list of vetoes names them

    @property
    def draft(self) -> str:
        return self.vote.drafts[0] if self.vote.drafts else ""

    def against_p5(self, countries: Countries) -> list[str]:
        return sorted(c for c in self.codes.get("N", []) if "p5" in countries.groups_on(c, self.on))


def _meetings_with_votes(settings: Settings, year: int) -> list[tuple[str, date, list[Chunk]]]:
    """The year's meetings whose presiding officer announced a result, each as its speeches in order."""
    path = str(record_path(settings.parquet_dir, "sc", year)).replace("'", "''")
    con = duckdb.connect()
    pattern = r"(?i)not\s+been\s+adopted|was\s+not\s+adopted|negative\s+vote|failed\s+to\s+obtain|vote\s+was\s+taken"
    rows = con.execute(f"""
        SELECT symbol, date, speech_number, entity_kind, participant, text, agenda, record_id
        FROM read_parquet('{path}')
        WHERE symbol IN (SELECT DISTINCT symbol FROM read_parquet('{path}')
                         WHERE regexp_matches(coalesce(text, ''), ?))
        ORDER BY symbol, speech_number, record_id""", [pattern]).fetchall()
    meetings: dict[str, tuple[date, list[Chunk]]] = {}
    for symbol, on, _n, kind, participant, text, agenda, _ in rows:
        _, chunks = meetings.setdefault(symbol, (on, []))
        chair = kind == "chair" or (kind is None and participant == "president")
        chunks.append(Chunk(len(chunks), chair, text or "", agenda))
    return [(s, on, chunks) for s, (on, chunks) in meetings.items()]


def resolve(countries: Countries, name: str) -> str | None:
    """A member's name as a list prints it: a name the PDF split ("C olombia") is matched without its spaces."""
    code = countries.resolve(name)
    if code is None:
        compact = re.sub(r"[^a-z]", "", norm(name))
        code = next((c for label, c in countries._labels.items() if label.replace(" ", "") == compact), None)
    return code


def not_adopted(meeting: str, on: date, chunks: list[Chunk], countries: Countries,
                source: str = "meeting record") -> list[Found]:
    """The drafts (and amendments) put to the vote at a meeting and not adopted, with how each member voted."""
    out = []
    every = votes_in(chunks)
    for v in every:
        # a result the record does not give in full (a line lost from a PDF) is kept only if the list of vetoes
        # names the draft (``build``): it is never taken to be either
        if v.adopted is True or (v.adopted is None and not v.drafts):
            continue
        oral = v.what == "proposed amendment" and "oral" in v.words.lower()
        if (not v.drafts and not oral) or v.what in ("proposal", "motion"):
            continue
        codes, unknown = resolve_lists(v, lambda name: resolve(countries, name))
        amends = None
        if v.what == "proposed amendment":
            later = [w for w in every if w.chunk >= v.chunk and w.what == "draft resolution" and w.drafts]
            amends = later[0].drafts[0] if later else None
        out.append(Found(meeting, on, v, codes, unknown, amends, v.agenda, source, oral))
    return out


def from_records(settings: Settings, countries: Countries) -> list[Found]:
    """Every draft not adopted in the stored Security Council records."""
    out = []
    for year in years(settings.parquet_dir, "sc"):
        for symbol, on, chunks in _meetings_with_votes(settings, year):
            out += not_adopted(symbol, on, chunks, countries)
    return out


def chunks_of_pdf(path: Path, symbol: str) -> list[Chunk]:
    """A record read from the UN's PDF: its speeches, or for a letter (a vote taken by letter, 2020 and 2021) or a
    record whose speakers cannot be found (a scan), its first page as the presiding officer's words."""
    from wcs.sources.pdftext import read_lines
    from wcs.sources.verbatim import CHAIR_SPEAKER, parse

    lines = read_lines(path)
    if symbol.startswith("S/PV."):
        m = parse(lines, symbol)
        if m.speeches:
            return [Chunk(i, bool(CHAIR_SPEAKER.match(sp.speaker)), sp.text, sp.agenda)
                    for i, sp in enumerate(m.speeches)]
        return [Chunk(0, True, " ".join(ln.text for ln in lines))]
    return [Chunk(0, True, " ".join(ln.text for ln in lines if ln.page == 0))]


def from_pdf(settings: Settings, countries: Countries, docs: Any, symbol: str, on: date) -> list[Found] | None:
    """The drafts not adopted in one record, read from the UN's PDF (kept in the records' cache, so read once);
    None if it cannot be had."""
    from wcs.sources.undocs import cache_dir, safe_name

    path = cache_dir(settings) / "pdf" / f"{safe_name(symbol)}.pdf"
    if not path.exists():
        if docs is None:
            return None
        got = docs.pdf(symbol)
        if got is None:
            return None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(got[0])
    try:
        return not_adopted(symbol, on, chunks_of_pdf(path, symbol), countries)
    except Exception as exc:  # a PDF that will not parse is reported by the check, not fatal
        log.warning("could not read the votes in %s: %s", symbol, exc)
        return []


# Putting them together

def _same_draft(a: str, b: str) -> bool:
    return re.sub(r"\s+", "", a).lower() == re.sub(r"\s+", "", b).lower()


def match(found: list[Found], v: Veto) -> list[Found]:
    """The drafts found that a line of the list of vetoes is about: the same draft (or, where the list gives a
    meeting for a proposal, the same meeting) on the same day."""
    out = []
    for f in found:
        if abs((f.on - v.on).days) > 1:
            continue
        if v.oral or f.oral:  # "S/2023/1029 | oral amendment": an oral amendment to that draft
            if v.oral and f.oral and any(_same_draft(f.amends or "", d) for d in v.drafts):
                out.append(f)
            continue
        if any(_same_draft(f.draft, d) for d in v.drafts) or (
                not v.drafts and any(_same_draft(f.meeting, r) for r in v.records)):
            out.append(f)
    return out


def records_begin(settings: Settings) -> date | None:
    """The first day the site's Security Council records cover."""
    ys = years(settings.parquet_dir, "sc")
    if not ys:
        return None
    path = str(record_path(settings.parquet_dir, "sc", ys[0])).replace("'", "''")
    return duckdb.connect().execute(f"SELECT min(date) FROM read_parquet('{path}')").fetchone()[0]


def check_vetoes(found: list[Found], listed: list[Veto], countries: Countries, since: date | None) -> list[str]:
    """Every veto on the Library's list since the records begin, found in them with the same permanent members
    against. A private meeting's veto (only a communiqué, which names no one) and an oral amendment (no document) are
    expected not to be found, and are not problems."""
    problems = []
    if since is None:
        return problems
    for v in listed:
        if v.on < since:
            continue
        if v.note and "communiqu" in v.note.lower():
            continue
        got = match(found, v)
        where = f"{', '.join(v.drafts) or 'no draft'} ({v.on.day} {v.on:%B %Y}, {', '.join(v.records)})"
        if not got:
            problems.append(f"{where}: on the list of vetoes, not found in the records")
            continue
        against = sorted({c for f in got for c in f.against_p5(countries)})
        if against != sorted(v.members):
            problems.append(f"{where}: the record has {', '.join(against) or 'no permanent member'} against; the list "
                            f"of vetoes has {', '.join(sorted(v.members)) or 'none'}")
    return problems


def _vote_id(draft: str) -> str:
    return f"draft:{draft}"


def draft_rows(found: list[Found], listed: list[Veto], countries: Countries, since: date | None,
               undocs: str = "https://undocs.org/en/") -> list[dict[str, Any]]:
    """The stored rows for the drafts not adopted: each member's vote where the record lists it, and for a veto
    the records cannot give (before they begin, or not readable), one row from the list of vetoes."""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    by_found: dict[int, Veto] = {}
    for v in listed:
        for f in match(found, v):
            by_found[id(f)] = v
    for f in found:
        if f.oral:
            continue  # no document, so no page of its own
        v = f.vote
        key = _vote_id(f.draft)
        n = 2
        while key in seen:
            key, n = f"{_vote_id(f.draft)}:{n}", n + 1
        seen.add(key)
        listed_as = by_found.get(id(f))
        reason = v.reason or ("veto" if listed_as else None)
        p5 = f.against_p5(countries)
        vetoed_by = p5 if reason == "veto" else []
        if reason == "veto" and not vetoed_by and listed_as:
            vetoed_by = listed_as.members
        agenda = f.agenda or (listed_as.agenda if listed_as else None)
        base = {"vote_id": key, "body": BODY, "outcome": "not adopted", "reason": reason, "what": v.what,
                "date": f.on, "resolution": None, "draft": f.draft, "amends": f.amends, "meeting": f.meeting,
                "title": None, "agenda": agenda, "subjects": None, "vote_note": None,
                "result": re.sub(r"\s+([,.;:])", r"\1", v.words) if v.adopted is False else None,
                "total_yes": v.totals.get("Y"), "total_no": v.totals.get("N"), "total_abstentions": v.totals.get("A"),
                "total_non_voting": v.totals.get("X"), "total_ms": None, "modality": "Vote", "vetoed_by": vetoed_by,
                "source": f.source, "complete": v.complete and not f.unknown, "url": undocs + f.draft}
        members = [(code, how) for how, codes in f.codes.items() for code in codes]
        if not members:
            rows.append({**base, "country_code": None, "country_name": None, "permanent_member": None, "vote": None})
        for code, how in members:
            rows.append({**base, "country_code": code, "country_name": countries.name_on(code, f.on),
                         "permanent_member": "p5" in countries.groups_on(code, f.on), "vote": how})
    # vetoes the records do not give: as the list gives them
    taken = {id(v) for v in by_found.values()}
    for v in listed:
        if id(v) in taken or v.oral or not v.drafts and not v.records:
            continue
        for d in (v.drafts or v.records[:1]):
            key = _vote_id(d)
            n = 2
            while key in seen:
                key, n = f"{_vote_id(d)}:{n}", n + 1
            seen.add(key)
            rows.append({"vote_id": key, "body": BODY, "outcome": "not adopted", "reason": "veto",
                         "what": "draft resolution" if d.startswith("S/") and "/PV." not in d else "proposal",
                         "country_code": None, "country_name": None, "permanent_member": None, "vote": None,
                         "date": v.on, "resolution": None, "draft": d, "amends": None,
                         "meeting": v.records[0] if v.records else None, "title": None, "agenda": v.agenda,
                         "subjects": None, "vote_note": v.note if v.note and v.note != "*" else None, "result": None,
                         "total_yes": None, "total_no": None, "total_abstentions": None, "total_non_voting": None,
                         "total_ms": None, "modality": "Vote", "vetoed_by": v.members, "source": "veto list",
                         "complete": False, "url": undocs + d})
    return rows


@dataclass
class Built:
    resolutions: int = 0
    drafts: int = 0
    vetoes_found: int = 0
    vetoes_listed: int = 0
    vetoes_from_list: int = 0
    failed_for_votes: int = 0
    oral_vetoes: int = 0
    from_pdfs: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    first_record: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build(settings: Settings, countries: Countries, docs: Any = None, max_fetch: int = MAX_FETCH) -> Built:
    """Write data/parquet/council_votes.parquet from the voting data, the records and the list of vetoes; and check
    the vetoes found against the list. ``docs`` (``undocs.UNDocs``): read again from the UN's PDFs the records of
    listed vetoes whose vote the stored records do not give in full, at most ``max_fetch`` a run (each kept)."""
    out = Built()
    csv_path = latest_csv(settings)
    rows = resolution_rows(csv_path) if csv_path else []
    out.resolutions = len({r["vote_id"] for r in rows})
    listed = veto_list(settings)
    since = records_begin(settings)
    out.first_record = since.isoformat() if since else None
    found = from_records(settings, countries)
    # listed vetoes the stored records do not give in full: read from the UN's PDF of the record
    fetched = 0
    cache = _pdf_cache(settings)
    for v in listed:
        if since is None or v.on < since or not v.records or (v.note and "communiqu" in v.note.lower()):
            continue
        got = match(found, v)
        if got and all(f.vote.complete and not f.unknown for f in got) and \
                sorted({c for f in got for c in f.against_p5(countries)}) == sorted(v.members):
            continue
        record = v.records[0]
        if record in cache:
            extra = [_found_from_dict(d) for d in cache[record]]
        else:
            if fetched >= max_fetch:
                continue
            fetched += 1
            extra = from_pdf(settings, countries, docs, record, v.on)
            if extra is None:
                continue
            cache[record] = [_found_to_dict(f) for f in extra]
            out.from_pdfs.append(record)
        drafts = {f.draft for f in extra}
        found = [f for f in found if not (f.meeting == record and f.draft in drafts)] + extra
    _save_pdf_cache(settings, cache)
    # a vote whose result could not be read is kept only where the list of vetoes names it
    unread = [f for f in found if f.vote.adopted is None and not any(match([f], v) for v in listed)]
    found = [f for f in found if f not in unread]
    out.problems = check_vetoes(found, listed, countries, since)
    out.problems += [f"{f.meeting}, {f.draft}: the result of the vote could not be read" for f in unread]
    for f in found:
        # a vote taken by letter (2020 and 2021) names only some members: each member's vote is in its annexes
        if f.meeting.startswith("S/PV."):
            out.problems += [f"{f.meeting}, {f.draft or 'a vote'}: {p}" for p in f.vote.problems if "lists printed" in p]
        if f.unknown:
            out.problems.append(f"{f.meeting}, {f.draft}: names in the lists that match no country: "
                                + ", ".join(f.unknown[:5]))
    drafts = draft_rows(found, listed, countries, since, str(settings.sources.get("undocs", "https://undocs.org/en/")))
    ids = {r["vote_id"]: r for r in drafts}
    out.oral_vetoes = len([f for f in found if f.oral and f.vote.reason == "veto"])
    out.drafts = len(ids)
    out.vetoes_listed = len([v for v in listed if since and v.on >= since])
    out.vetoes_found = len([r for r in ids.values() if r["reason"] == "veto" and r["source"] != "veto list"])
    out.vetoes_from_list = len([r for r in ids.values() if r["source"] == "veto list"])
    out.failed_for_votes = len([r for r in ids.values() if r["reason"] == "votes"])
    if rows or drafts:
        write_council_votes(settings.parquet_dir, rows + drafts)
    report = raw_dir(settings) / "built.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({**out.to_dict(), "version": VERSION}, indent=1))
    return out


def _pdf_cache(settings: Settings) -> dict[str, list[dict[str, Any]]]:
    path = raw_dir(settings) / "from-pdfs.json"
    try:
        data = json.loads(path.read_text()) if path.exists() else {}
    except ValueError:
        return {}
    return data.get("records", {}) if data.get("version") == VERSION else {}


def _save_pdf_cache(settings: Settings, cache: dict[str, list[dict[str, Any]]]) -> None:
    path = raw_dir(settings) / "from-pdfs.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": VERSION, "records": cache}, indent=1, default=str))


def _found_to_dict(f: Found) -> dict[str, Any]:
    return {"meeting": f.meeting, "on": f.on.isoformat(), "vote": asdict(f.vote), "codes": f.codes,
            "unknown": f.unknown, "amends": f.amends, "agenda": f.agenda, "source": f.source, "oral": f.oral}


def _found_from_dict(d: dict[str, Any]) -> Found:
    return Found(d["meeting"], date.fromisoformat(d["on"]), Vote(**d["vote"]), d["codes"], d["unknown"],
                 d.get("amends"), d.get("agenda"), d.get("source", "meeting record"), d.get("oral", False))


def stamp(settings: Settings) -> str:
    """What the drafts are built from: the CSV, the list of vetoes and the stored Security Council records (their
    times and sizes). The update builds again only when it changes."""
    parts = [f"v{VERSION}"]
    for p in [latest_csv(settings), raw_dir(settings) / "veto-list.html",
              *[record_path(settings.parquet_dir, "sc", y) for y in years(settings.parquet_dir, "sc")]]:
        if p is not None and p.exists():
            st = p.stat()
            parts.append(f"{p.name}:{st.st_mtime_ns}:{st.st_size}")
    return "|".join(parts)


def built_stamp(settings: Settings) -> str | None:
    path = raw_dir(settings) / "stamp.txt"
    return path.read_text() if path.exists() and council_votes_path(settings.parquet_dir).exists() else None


def mark_built(settings: Settings) -> None:
    path = raw_dir(settings) / "stamp.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(stamp(settings))
