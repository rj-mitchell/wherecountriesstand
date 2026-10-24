"""Recent UN verbatim records, straight from the UN: discovery, download, parsing and storage.

The academic corpora stop at the end of their latest release (the Security Council's in December 2023), so newer
meetings come from the UN itself (docs/DECISIONS.md, 4). Built to keep working as the UN's websites change:

* **Discovery, two ways.** The UN Digital Library's search (MARC XML export) lists meeting records with their date,
  agenda items, whether the record is still provisional, and a link to each language's PDF. Meeting numbers are
  also sequential (S/PV.10235, S/PV.10236 ...), so the Official Document System (ODS) is probed for numbers beyond
  the last one known, which finds records the Library has not catalogued yet. Either alone is enough.
* **Download, two ways.** The Library's file link first, then the ODS symbol service. A response is accepted only if
  it is a PDF (ODS answers a missing symbol with an HTML error page and status 200).
* **A raw cache and a manifest** (data/raw/undocs/): every PDF is kept, with its checksum, source, the Library's
  last-modified stamp and the parser version. Parsing again after a parser change needs no downloads.
* **Corrections.** Provisional records are reissued once corrected. Records still marked provisional, or changed in
  the Library since they were fetched, are fetched again (at most monthly) for a year.
* **Gaps.** A missing number (a closed meeting has only a communiqué; some records appear late) is checked again on
  later runs for 60 days before it is given up.
* **Checks.** Every meeting parsed is checked (speeches found, countries recognised, a date); problems are reported
  by the update job (``wcs.update``) rather than passing silently into the index.

Records are stored as source "sc" for the years from ``[live] sc_from`` on, alongside the corpus's earlier years,
with the same record format. General Assembly records (the General Debate, ``wcs.sources.unga``) go through the same
client, cache and parser, with a manifest of their own.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from wcs.config import Settings
from wcs.reference.countries import Countries
from wcs.sources.pdftext import read_lines
from wcs.sources.speech import language, normalise_text, on_behalf_of
from wcs.sources.unsc import meeting_label, symbol_of, symbol_url
from wcs.sources.verbatim import PARSER_VERSION, Meeting, parse, split_chair, summary

log = logging.getLogger(__name__)

LIBRARY = "https://digitallibrary.un.org"
ODS = "https://documents.un.org/api/symbol/access"
USER_AGENT = "WhereCountriesStand/0.1 (independent research; https://github.com/rj-mitchell/wherecountriesstand)"
REFETCH_PROVISIONAL_DAYS = 365      # keep checking provisional records for corrections this long
REFETCH_EVERY_DAYS = 30             # at most this often
GIVE_UP_MISSING_DAYS = 60           # a number not found for this long is a closed meeting or never issued
PROBE_MISSES = 12                   # consecutive numbers not found before the probe stops
MAX_ATTEMPTS = 5                    # a record that will not parse is reported, then left alone after this many tries


# ---- the Digital Library's catalogue -----------------------------------------------------------------------------

def parse_marc(xml: str) -> list[dict[str, Any]]:
    """Meeting records from a Digital Library MARC XML search export."""
    out = []
    for rec in re.findall(r"<record>(.*?)</record>", xml, re.S):
        fields: dict[str, list[list[tuple[str, str]]]] = {}
        for tag, body in re.findall(r'<datafield tag="(\d+)"[^>]*>(.*?)</datafield>', rec, re.S):
            fields.setdefault(tag, []).append(re.findall(r'<subfield code="(.)">([^<]*)</subfield>', body))
        control = dict(re.findall(r'<controlfield tag="(\d+)">([^<]*)</controlfield>', rec))

        def first(tag: str, code: str, fields=fields) -> str | None:
            for subs in fields.get(tag, []):
                for c, v in subs:
                    if c == code:
                        return _unescape(v)
            return None

        symbol = first("191", "a")
        if not symbol:
            continue
        title = " ".join(x for x in (first("245", "a"), first("245", "b")) if x)
        kinds = [v for subs in fields.get("989", []) for c, v in subs if c in "bc"]
        files = {_unescape(dict(s).get("y", "")): _unescape(dict(s).get("u", "")) for s in fields.get("856", [])}
        agenda = []
        for subs in fields.get("991", []):
            d = dict(subs)
            item = d.get("c") or (d.get("d") or "").capitalize()
            if item and item not in agenda:
                agenda.append(_unescape(item).rstrip("."))
        out.append({
            "symbol": symbol_of(symbol.replace(" ", "")) if symbol.startswith("S/PV") else symbol,
            "record": control.get("001"),
            "modified": control.get("005"),
            "date": first("992", "a") or first("269", "a"),
            "title": title,
            "provisional": first("091", "a") == "PRO",
            # a closed meeting's communiqué is catalogued among the verbatim records too: the title tells them apart
            "verbatim": "communiqu" not in title.lower() and (not kinds or "Verbatim Records" in kinds),
            "pdf_en": files.get("English"),
            "agenda": agenda,
        })
    return out


def _unescape(s: str) -> str:
    return s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&apos;", "'")


def meeting_number(symbol: str) -> int | None:
    m = re.match(r"S/PV\.(\d+)", symbol)
    return int(m.group(1)) if m else None


# ---- the cache and manifest ---------------------------------------------------------------------------------------

def cache_dir(settings: Settings) -> Path:
    return settings.raw_dir / "undocs"


def safe_name(symbol: str) -> str:
    return re.sub(r"[^A-Za-z0-9.]+", "_", symbol).strip("_")


@dataclass
class Manifest:
    path: Path
    entries: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def load(cls, settings: Settings, body: str = "sc") -> Manifest:
        """The Security Council's manifest (manifest.json) or the General Assembly's (manifest-ga.json)."""
        path = cache_dir(settings) / ("manifest.json" if body == "sc" else f"manifest-{body}.json")
        return cls(path, json.loads(path.read_text()) if path.exists() else {})

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.entries, indent=1, sort_keys=True, default=str))
        tmp.replace(self.path)

    def last_number(self) -> int:
        nums = [n for s in self.entries if (n := meeting_number(s))]
        return max(nums, default=0)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _days_since(stamp: str | None) -> float:
    if not stamp:
        return 1e9
    return (datetime.now(UTC) - datetime.fromisoformat(stamp)).total_seconds() / 86400


# ---- the client ----------------------------------------------------------------------------------------------------

class UNDocs:
    """Talks to the Digital Library and ODS, politely (one request at a time, a pause between them)."""

    def __init__(self, settings: Settings, client: httpx.Client | None = None, pause: float = 1.0):
        self.settings = settings
        self.client = client or httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=120, follow_redirects=True)
        self.pause = pause
        self._last = 0.0

    def _get(self, url: str, **params: Any) -> httpx.Response:
        wait = self.pause - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        for attempt in range(4):
            try:
                r = self.client.get(url, params=params or None)
                self._last = time.monotonic()
                if r.status_code in (429, 502, 503, 504):
                    raise httpx.HTTPStatusError("busy", request=r.request, response=r)
                return r
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                if attempt == 3:
                    raise
                log.info("retrying %s (%s)", url, exc)
                time.sleep(2 ** (attempt + 1))
        raise RuntimeError("unreachable")

    def search(self, query: str, rows: int = 200) -> list[dict[str, Any]]:
        r = self._get(f"{LIBRARY}/search", ln="en", p=query, of="xm", rg=rows)
        r.raise_for_status()
        return parse_marc(r.text)

    def library_meetings(self, after: int) -> list[dict[str, Any]]:
        """Security Council meeting records numbered above ``after``, from the Library's catalogue, searched by the
        hundreds the numbers fall in (S/PV.102*, S/PV.103* ...) until a block comes back empty."""
        found: dict[str, dict[str, Any]] = {}
        block = after // 100
        while True:
            items = [i for i in self.search(f"191__a:S/PV.{block}*")
                     if (n := meeting_number(i["symbol"])) and n > after and n // 100 == block]
            for i in items:
                found[i["symbol"]] = i
            if not items and block > after // 100:
                break
            block += 1
        return sorted(found.values(), key=lambda i: (meeting_number(i["symbol"]) or 0, i["symbol"]))

    def library_record(self, symbol: str) -> dict[str, Any] | None:
        items = [i for i in self.search(f'191__a:"{symbol}"', rows=5) if i["symbol"] == symbol]
        return items[0] if items else None

    def pdf(self, symbol: str, library_url: str | None = None) -> tuple[bytes, str] | None:
        """The English PDF of a document: from the Library's file link, else the ODS symbol service."""
        tries = [library_url] if library_url else []
        tries.append(f"{ODS}?s={symbol.replace(' ', '')}&l=en&t=pdf")
        for url in tries:
            try:
                r = self._get(url)
            except httpx.HTTPError as exc:
                log.warning("download failed for %s from %s: %s", symbol, url, exc)
                continue
            if r.status_code == 200 and r.content[:5] == b"%PDF-":
                return r.content, str(r.url)
        return None

    def exists(self, symbol: str) -> bool:
        """Whether ODS has an English PDF for a symbol (a HEAD-like check that downloads it)."""
        return self.pdf(symbol) is not None


# ---- parsed meetings to records ------------------------------------------------------------------------------------

CHAIR = re.compile(r"^The (?:Acting )?President\b")
UN_SPEAKERS = re.compile(r"^The (?:Deputy )?Secretary-General\b")


def records_for(meeting: Meeting, meta: dict[str, Any], countries: Countries, settings: Settings) -> list[dict[str, Any]]:
    """Stored records (``store.parquet.RECORD_SCHEMA``) for one parsed Security Council meeting."""
    symbol = meeting.symbol
    d = meeting.meeting_date or (date.fromisoformat(meta["date"]) if re.fullmatch(r"\d{4}-\d{2}-\d{2}",
                                                                                   meta.get("date") or "") else None)
    if d is None:
        return []
    members = {c for c in (countries.resolve(x) for x in meeting.members) if c}
    president_code = countries.resolve(meeting.president_country)
    undocs = settings.sources.get("undocs", "https://undocs.org/en/")
    out: list[dict[str, Any]] = []

    def add(text: str, speech, code: str | None, kind: str, participant: str, label: str | None,
            behalf: str | None) -> None:
        text = normalise_text(text)
        if not text.strip():
            return
        n = len(out) + 1
        out.append({
            "record_id": f"sc:{symbol}:{n:04d}", "source": "sc", "body": "Security Council", "date": d,
            "date_precision": "day", "year": d.year, "session": None, "meeting": meeting_label(symbol),
            "symbol": symbol, "speech_number": n, "agenda": speech.agenda or (meta.get("agenda") or [None])[0],
            "agenda_area": None, "speaker_name": speech.speaker, "speaker_post": None, "speaker_role": "",
            "participant": participant, "country_code": code, "country_label": label, "entity_kind": kind,
            "on_behalf_of": behalf, "language": language(speech.attribution), "ocr": False, "text": text,
            "url": symbol_url(undocs, symbol),
        })

    for sp in meeting.speeches:
        if CHAIR.match(sp.speaker):
            for part, text in split_chair(sp.text):
                if part == "national" and president_code:
                    add(text, sp, president_code, countries.kind(president_code), "president",
                        meeting.president_country, _group(on_behalf_of(text), countries))
                else:
                    add(text, sp, None, "chair", "president", "The President", on_behalf_of(text, chair=True))
            continue
        code = countries.resolve(sp.label)
        if code:
            add(sp.text, sp, code, countries.kind(code), "member" if code in members or code == president_code
                else "guest", sp.label, _group(on_behalf_of(sp.text), countries))
        else:
            post = meeting.briefers.get(sp.speaker.split()[-1]) if not sp.label else None
            kind, label = briefer(sp.speaker, sp.label, post)
            n0 = len(out)
            add(sp.text, sp, None, kind, "guest", label, on_behalf_of(sp.text))
            if post and len(out) > n0:
                out[-1]["speaker_post"] = post
    return out


UN_POSTS = re.compile(r"Secretary-General|United Nations|\bUN\b|High Commissioner|Special Representative|"
                      r"Special Envoy|Special Coordinator|Emergency Relief|Force Commander|Police Adviser|"
                      r"Head of (?:the )?UN|UNICEF|UNHCR|UNRWA|UNDP|UNFPA|UN-Women|World Food Programme|"
                      r"Office for the Coordination of Humanitarian Affairs|Peacekeeping|Peacebuilding", re.I)


def briefer(speaker: str, label: str | None, post: str | None) -> tuple[str, str | None]:
    """(entity_kind, label) for a speaker who is not a country: a UN official, or an organisation named by their
    label or their post ("Commissioner ... of the African Union" -> "African Union")."""
    if UN_SPEAKERS.match(speaker) or (label or "").startswith(("United Nations", "UN ")) or \
            (post and UN_POSTS.search(post)):
        return "un", label or "United Nations"
    if label:
        return "organisation", label
    if post:
        m = re.search(r"\bof (?:the )?([A-Z][^,;]+)$", post)
        return "organisation", (m.group(1).strip() if m else post)
    return "organisation", None


ORG_WORDS = re.compile(r"\b(?:Union|Organi[sz]ation|League|Committee|Bank|Fund|Programme|Office|Commission|Council|"
                       r"Court|Agency|Mission|Society|Network|Forum|Institute|Group|Community|Movement|Nations|"
                       r"Association|Centre|Center|Foundation|Alliance|Coalition|Red Cross|Red Crescent|Watch|"
                       r"International|Women|Youth|University|Party|Front|Federation of|Representative|Envoy)\b", re.I)


def looks_like_country(label: str | None) -> bool:
    """A short proper name of capitalised words ("Democratic People's Republic of Korea"), not an organisation."""
    if not label or ORG_WORDS.search(label) or re.search(r"\d", label):
        return False
    words = label.replace("(", " ").replace(")", " ").split()
    return 0 < len(words) <= 7 and all(w[:1].isupper() or w in ("of", "and", "the", "da", "de") for w in words)


def _group(behalf: str | None, countries: Countries) -> str | None:
    return None if behalf and countries.resolve(behalf) else behalf


def check(meeting: Meeting, records: list[dict[str, Any]]) -> list[str]:
    """Problems worth reporting for one parsed meeting."""
    problems = list(meeting.warnings)
    if meeting.detection != "bold":
        problems.append("speakers found by wording, not typography: the record's template may have changed")
    if meeting.speeches and not records:
        problems.append("speeches found but no records made (no date?)")
    # a label that looks like a country's name but matched none (a spelling the reference data lacks)
    unknown = sorted({r["country_label"] for r in records if r["entity_kind"] == "organisation" and
                      looks_like_country(r["country_label"])})
    if unknown:
        problems.append("labels that look like countries but matched none: " + ", ".join(unknown[:5]))
    if meeting.members and len(meeting.members) not in (14, 15):
        problems.append(f"{len(meeting.members)} members listed (expected 14 besides the President)")
    return problems


# ---- one meeting, end to end ---------------------------------------------------------------------------------------

def process(settings: Settings, countries: Countries, manifest: Manifest, docs: UNDocs, meta: dict[str, Any],
            force: bool = False, body: str = "sc") -> list[dict[str, Any]]:
    """Fetch (if needed), parse and store one meeting record; returns its records."""
    symbol = meta["symbol"]
    entry = manifest.entries.setdefault(symbol, {"first_seen": _now()})
    entry.update({k: meta[k] for k in ("record", "modified", "provisional", "title", "agenda") if meta.get(k)
                  is not None})
    if not meta.get("verbatim", True):
        entry["status"] = "communique"  # a closed meeting: only a communiqué, no speeches
        return []
    pdf_path = cache_dir(settings) / "pdf" / f"{safe_name(symbol)}.pdf"
    if force or not pdf_path.exists():
        got = docs.pdf(symbol, meta.get("pdf_en"))
        if got is None:
            entry.setdefault("missing_since", _now())
            entry["status"] = "missing"
            return []
        content, url = got
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        pdf_path.write_bytes(content)
        entry.update({"sha256": hashlib.sha256(content).hexdigest(), "url": url, "fetched_at": _now(),
                      "provisional": meta.get("provisional", "/pro/" in url)})
        entry.pop("missing_since", None)
    return parse_cached(settings, countries, manifest, symbol, meta, body)


def parse_cached(settings: Settings, countries: Countries, manifest: Manifest, symbol: str,
                 meta: dict[str, Any] | None = None, body: str = "sc") -> list[dict[str, Any]]:
    """Parse a cached record into stored records. ``body`` "ga": a General Assembly plenary meeting, of which only
    the General Debate statements are kept (a meeting on other business is recorded as "other", not a failure)."""
    from wcs.sources import unga

    entry = manifest.entries.setdefault(symbol, {"first_seen": _now()})
    pdf_path = cache_dir(settings) / "pdf" / f"{safe_name(symbol)}.pdf"
    try:
        lines = read_lines(pdf_path)
        if body == "ga" and len(lines) < 20:  # a scanned record without a text layer (before the late 1990s)
            entry.update({"status": "no_text", "parser_version": PARSER_VERSION})
            return []
        if body == "sc" and is_communique(lines):
            entry.update({"status": "communique", "parser_version": PARSER_VERSION})
            return []
        meeting = parse(lines, symbol)
    except Exception as exc:  # a broken or unexpected PDF must not stop the run
        log.exception("could not parse %s", symbol)
        entry.update({"status": "failed", "error": f"{type(exc).__name__}: {exc}", "parser_version": PARSER_VERSION,
                      "attempts": entry.get("attempts", 0) + 1})
        return []
    if body == "ga":
        records = unga.records_for(meeting, meta or entry, countries, settings)
        problems = unga.check(meeting, records, meta or entry)
        debate = unga.debate_meeting(meeting, meta or entry)
        status = "parsed" if records else ("failed" if debate or not meeting.speeches else "other")
        entry["concludes_debate"] = unga.concludes_debate(meeting)
        entry["replies"] = sum(unga.is_reply(r) for r in records)
        entry["statements"] = len(records) - entry["replies"]
    else:
        records = records_for(meeting, meta or entry, countries, settings)
        problems = check(meeting, records)
        status = "parsed" if records else "failed"
    if status == "failed":
        entry["attempts"] = entry.get("attempts", 0) + 1
    entry.update({"status": status, "parser_version": PARSER_VERSION, "stored": False,
                  "date": meeting.meeting_date.isoformat() if meeting.meeting_date else entry.get("date"),
                  "speeches": len(records), "problems": problems, "parsed": summary(meeting)})
    out = cache_dir(settings) / "parsed" / f"{safe_name(symbol)}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(records, default=str))
    return records


def is_communique(lines) -> bool:
    """A closed meeting's communiqué: "the following communiqué was issued ... in place of a verbatim record"."""
    first = " ".join(ln.text for ln in lines if ln.page == 0)
    return bool(re.search(r"Official communiqu[eé]|in place of a verbatim record", first, re.I))


def load_parsed(settings: Settings, symbol: str) -> list[dict[str, Any]]:
    path = cache_dir(settings) / "parsed" / f"{safe_name(symbol)}.json"
    if not path.exists():
        return []
    rows = json.loads(path.read_text())
    for r in rows:
        r["date"] = date.fromisoformat(r["date"])
    return rows


# ---- what to do on a run -------------------------------------------------------------------------------------------

def due_for_refetch(entry: dict[str, Any], meta: dict[str, Any] | None) -> bool:
    """A provisional record under a year old not fetched for a month, or one the Library has changed since."""
    if entry.get("status") != "parsed":
        return False
    if meta and meta.get("modified") and entry.get("modified") and meta["modified"] > entry["modified"] and \
            _days_since(entry.get("fetched_at")) > 1:
        return True
    fresh = entry.get("date") and (date.today() - date.fromisoformat(entry["date"])) < timedelta(
        days=REFETCH_PROVISIONAL_DAYS)
    return bool(entry.get("provisional") and fresh and _days_since(entry.get("fetched_at")) > REFETCH_EVERY_DAYS)


def still_looking(entry: dict[str, Any]) -> bool:
    return entry.get("status") == "missing" and _days_since(entry.get("missing_since")) < GIVE_UP_MISSING_DAYS
