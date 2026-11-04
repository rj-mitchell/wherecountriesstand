"""The UN's General Debate website (gadebate.un.org): each statement within days, until the official record arrives.

The General Assembly's verbatim records of the debate are published about a year after it (``wcs.sources.unga``).
The UN's General Debate website has every statement within days: a page per speaker with the speaker's name, post
and date, the statement as the delegation issued it (a PDF, in English for most), and an AI transcript of the UN's
English interpretation. Its sitemap lists every statement page.

The website puts automated clients through a browser check, so this client presents itself as a browser (the
owner's decision, docs/DECISIONS.md, 12). It reads only what robots.txt allows, one request at a time with a pause,
the sitemap at most once a day, and each page again only when the sitemap says it has changed. If the site blocks
it, the update reports a warning and the official records still arrive in time.

Which text. The delegation's English text if there is one ("text_source" "delegation": as prepared, so it can
differ from what was said), else the AI transcript of the English interpretation ("transcript": as delivered, but
machine-made). Both are labelled as not yet the official record wherever they are shown, and the official record
replaces them, statement by statement, when it is published (the record ids match: ``gd:<session>:<code>``).
"""

from __future__ import annotations

import html
import json
import logging
import re
import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx

from wcs.config import Settings
from wcs.reference.countries import Countries
from wcs.reference.roles import classify
from wcs.sources.speech import normalise_text

log = logging.getLogger(__name__)

SITE = "https://gadebate.un.org"
BROWSER_USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 "
                      "Safari/537.36")
CLEANER_VERSION = 3         # raise when clean_statement or clean_transcript change: cached files are cleaned again
PAUSE = 2.0                 # seconds between requests
SITEMAP_EVERY_HOURS = 20    # the sitemap (about 5 MB) is read at most this often
RETRY_NO_TEXT_DAYS = 30     # a page without an English text or transcript yet is looked at again for this long
LANGUAGES = {"english": None, "french": "French", "spanish": "Spanish", "russian": "Russian", "arabic": "Arabic",
             "chinese": "Chinese", "portuguese": "Portuguese"}
# the first words of a statement, after a cover page or the President's introduction
SALUTATION = re.compile(r"^\s*(?:Mr\.?|Madam|Madame|Mister|Mme|M\.|Your (?:Excellenc|Majest|Highness|Holiness)|"
                        r"Excellenc|Honou?rable|Distinguished|Dear|President of the (?:General )?Assembly|"
                        r"Co-?Chairs?|Secretary-General|Thank you|Allow me|It is (?:a|an|my)|I (?:am|have|wish|would)|"
                        r"At the outset)", re.I)
INTRO = re.compile(r"Assembly will (?:now )?hear|request protocol|escort|give the floor|I (?:now )?(?:invite|call)|"
                   r"have the honou?r to welcome|^\w+\.$", re.I)
OUTRO = re.compile(r"^(?:On behalf of the General Assembly, I (?:wish to )?thank|I thank the|The Assembly will|"
                   r"I (?:now )?(?:give the floor|call|invite))", re.I)
# the President's thanks, run on into the speaker's last line in a transcript
THANKS = re.compile(r"\s*On behalf of the (?:General )?Assembly,? I (?:wish to |would like to )?thank\b.*$", re.I | re.S)
# lines a delegation's PDF repeats or prints for the web page it came from
PDF_FURNITURE = re.compile(r"https?://\S+|www\.\S+|\d{1,3}\s*/\s*\d{1,3}|©.*|All content is available under.*|"
                           r"Check against delivery\.?|Please check against delivery\.?", re.I)
MIN_STATEMENT_WORDS = 300   # a delegation's PDF shorter than this (scanned, or mostly images): use the transcript
COMMON = {"the", "and", "of", "to", "in", "a", "is", "that", "for", "we", "our", "on", "with", "as", "are", "it"}
# where a web page's own furniture begins after a speech printed from it
WEB_TAIL = re.compile(r"^(?:Press contacts?|Media contacts?|Visit the meeting page|For more information|Related links?|"
                      r"Share this page)\b", re.I | re.M)
# the speech's last words; anything short after them is the web page or document it was printed from
CLOSING = re.compile(r"\b(?:Thank you(?: very much)?(?: indeed)?|I thank you(?: for your attention)?|"
                     r"Long live [^.!\n]{2,60})[.!]?", re.I)
MAX_TAIL_WORDS = 120


def readable(text: str) -> bool:
    """English a reader can use: PDFs with unmapped fonts come out as "(cid:12)" codes or shifted letters, in
    which the commonest English words are missing."""
    words = re.findall(r"[A-Za-z']+", text.lower())
    return len(words) >= MIN_STATEMENT_WORDS and sum(w in COMMON for w in words) / len(words) > 0.08
# speakers on the website that are not States or the EU: the corpus and the records leave them out too
NOT_A_SPEAKER = re.compile(r"President of the General Assembly|Secretary-General", re.I)
ORGANISATION_KEYS = {"European Union": "EU"}


class Blocked(Exception):
    """The website refused the request (its browser check)."""


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _hours_since(stamp: str | None) -> float:
    if not stamp:
        return 1e9
    return (datetime.now(UTC) - datetime.fromisoformat(stamp)).total_seconds() / 3600


# ---- reading pages ------------------------------------------------------------------------------------------------

def sitemap_pages(xml: str) -> list[str]:
    return re.findall(r"<sitemap>\s*<loc>([^<]+)</loc>", xml)


def sitemap_statements(xml: str, sessions: set[int]) -> dict[str, str]:
    """English statement pages of the given sessions in a sitemap page: url -> last modified."""
    out = {}
    for block in re.findall(r"<url>(.*?)</url>", xml, re.S):
        loc, mod = re.search(r"<loc>([^<]+)</loc>", block), re.search(r"<lastmod>([^<]+)</lastmod>", block)
        m = re.match(re.escape(SITE) + r"/en/(\d+)/([a-z0-9-]+)$", loc.group(1)) if loc else None
        if m and int(m.group(1)) in sessions:
            out[loc.group(1)] = mod.group(1) if mod else ""
    return out


def _field(page: str, name: str) -> str | None:
    m = re.search(r'field--name-field-' + name + r'\b[^>]*>(?:\s*<div[^>]*>)*\s*([^<]+?)\s*<', page)
    return html.unescape(m.group(1)).strip() if m else None


def parse_page(page: str, url: str) -> dict[str, Any]:
    """The speaker, date and texts on a statement page."""
    title = re.search(r'<meta property="og:title" content="([^"]*)"', page)
    label = html.unescape(title.group(1)).split("|")[0].strip() if title else None
    when = re.search(r"<time\s+datetime=\"(\d{4}-\d{2}-\d{2})", page)
    files: dict[str, str] = {}
    for lang, body in re.findall(r'field--name-field-([a-z-]+)-statement\b(.*?)</div>\s*</div>', page, re.S):
        link = re.search(r'href="([^"]+\.pdf)"', body)
        if link and lang not in files:
            files[lang] = SITE + link.group(1) if link.group(1).startswith("/") else link.group(1)
    prepare = re.search(r'data-prepare-url="([^"]+/transcript/en/prepare-download)"', page)
    m = re.match(re.escape(SITE) + r"/en/(\d+)/([a-z0-9-]+)$", url)
    return {
        "url": url, "session": int(m.group(1)) if m else None, "slug": m.group(2) if m else None,
        "label": _field(page, "speaker-list") or label,
        "honorific": _field(page, "speaker-title"), "name": _field(page, "speaker-name"),
        "post": _field(page, "speaker-function-2"),
        "date": when.group(1) if when else None, "files": files,
        "transcript": SITE + prepare.group(1) if prepare else None,
    }


def clean_statement(text: str) -> str:
    """A delegation's PDF as text: the cover page (letterhead, title, "Check against delivery") and page numbers
    dropped."""
    lines = [ln.strip() for ln in text.replace("\x0c", "\n").splitlines()]
    counts: dict[str, int] = {}
    for ln in lines:
        if ln and len(ln.split()) <= 12:
            counts[ln] = counts.get(ln, 0) + 1
    lines = [ln for ln in lines if not re.fullmatch(r"\d{1,3}|-\s*\d{1,3}\s*-|Page \d+( of \d+)?", ln)
             and not PDF_FURNITURE.fullmatch(ln) and counts.get(ln, 0) < 3]  # running headers and footers
    start = next((i for i, ln in enumerate(lines[: max(60, len(lines) // 3)]) if SALUTATION.match(ln)), 0)
    body = "\n".join(lines[start:])
    body = re.sub(r"\n\s*\n+", "\n\n", body)
    tail = [m for m in WEB_TAIL.finditer(body) if m.start() > len(body) * 0.8]
    if tail:
        body = body[:tail[0].start()]
    closings = list(CLOSING.finditer(body))
    if closings and len(body[closings[-1].end():].split()) <= MAX_TAIL_WORDS:
        body = body[:closings[-1].end()]
    return normalise_text(body)


def clean_transcript(text: str) -> str:
    """An AI transcript: the header, the President's introduction and thanks dropped."""
    lines = [ln.strip() for ln in text.splitlines()]
    if any(ln.startswith("[Auto-generated") for ln in lines[:10]):
        lines = lines[next(i for i, ln in enumerate(lines) if ln.startswith("[Auto-generated")) + 1:]
    lines = [ln for ln in lines if ln]
    head = lines[:15]
    start = next((i for i, ln in enumerate(head) if SALUTATION.match(ln) and not INTRO.search(ln)), None)
    if start is None:
        start = next((i for i, ln in enumerate(head) if not INTRO.search(ln)), 0)
    lines = lines[start:]
    tail = next((i for i in range(len(lines) - 1, max(len(lines) - 12, 0), -1) if OUTRO.match(lines[i])), None)
    if tail is not None:
        lines = lines[:tail]
    text = "\n\n".join(" ".join(lines[i:i + 6]) for i in range(0, len(lines), 6))
    tail = THANKS.search(text, max(0, len(text) - 600))
    return text[:tail.start()].rstrip() if tail else text


def record_for(info: dict[str, Any], text: str, text_source: str, countries: Countries) -> dict[str, Any] | None:
    session = info["session"]
    if not info.get("date") or not session or not text.strip():
        return None
    if NOT_A_SPEAKER.search(info.get("label") or ""):
        return None
    d = date.fromisoformat(info["date"])
    code = countries.resolve(info.get("label"))
    original = next((LANGUAGES.get(k) for k in info.get("files", {}) if LANGUAGES.get(k)), None)
    post = info.get("post") or ""
    return {
        "record_id": f"gd:{session}:{code or ORGANISATION_KEYS.get(info.get('label') or '') or (info.get('slug') or '').upper()}",
        "source": "gd",
        "body": "General Assembly", "date": d, "date_precision": "day", "year": d.year, "session": session,
        "meeting": f"General Debate, {_ordinal(session)} session", "symbol": None, "speech_number": None,
        "agenda": "General debate", "agenda_area": None, "speaker_name": info.get("name"),
        "speaker_post": post or None, "speaker_role": classify(post), "participant": "", "country_code": code,
        "country_label": countries.name_on(code, d) if code else info.get("label"),
        "entity_kind": countries.kind(code) if code else "organisation", "on_behalf_of": None,
        # the transcript is of the English interpretation; a delegation's English text is its own
        "language": original if text_source == "transcript" and "english" not in info.get("files", {}) else None,
        "ocr": False, "text": text, "url": info["url"], "text_source": text_source,
    }


# ---- the client and cache -----------------------------------------------------------------------------------------

def cache_dir(settings: Settings) -> Path:
    return settings.raw_dir / "gadebate"


class Website:
    def __init__(self, client: httpx.Client | None = None, pause: float = PAUSE):
        self.client = client or httpx.Client(headers={"User-Agent": BROWSER_USER_AGENT}, timeout=60,
                                             follow_redirects=True)
        self.pause = pause
        self._last = 0.0

    def _request(self, method: str, url: str) -> httpx.Response:
        wait = self.pause - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        r = self.client.request(method, url)
        self._last = time.monotonic()
        if r.status_code in (403, 405) or "Human Verification" in r.text[:3000]:
            raise Blocked(f"{r.status_code} from {url}")
        r.raise_for_status()
        return r

    def get(self, url: str) -> httpx.Response:
        return self._request("GET", url)

    def transcript(self, prepare_url: str) -> str | None:
        """The AI transcript: asked for (POST), then downloaded from the link returned."""
        r = self._request("POST", prepare_url)
        link = (r.json() or {}).get("url") if r.headers.get("content-type", "").startswith("application/json") \
            else None
        if not link:
            return None
        t = self.get(SITE + link if link.startswith("/") else link)
        return t.text if t.headers.get("content-type", "").startswith("text/plain") else None


def load_state(settings: Settings) -> dict[str, Any]:
    path = cache_dir(settings) / "state.json"
    return json.loads(path.read_text()) if path.exists() else {"pages": {}}


def save_state(settings: Settings, state: dict[str, Any]) -> None:
    path = cache_dir(settings) / "state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, sort_keys=True))
    tmp.replace(path)


def records_path(settings: Settings, session: int) -> Path:
    return cache_dir(settings) / f"records-{session}.json"


def load_records(settings: Settings, session: int) -> list[dict[str, Any]]:
    path = records_path(settings, session)
    if not path.exists():
        return []
    rows = json.loads(path.read_text())
    for r in rows:
        r["date"] = date.fromisoformat(r["date"])
    return rows


def text_from_cache(settings: Settings, info: dict[str, Any]) -> tuple[str, str]:
    """The best text for a page from its cached files: the delegation's PDF, else the transcript."""
    from pdfminer.high_level import extract_text

    folder = cache_dir(settings) / str(info["session"])
    pdf, txt = folder / f"{info['slug']}.pdf", folder / f"{info['slug']}.txt"
    if pdf.exists():
        text = clean_statement(extract_text(str(pdf)))
        if readable(text):
            return text, "delegation"
    if txt.exists():
        return clean_transcript(txt.read_text()), "transcript"
    return "", ""


def reclean(settings: Settings, countries: Countries, state: dict[str, Any],
            by_session: dict[int, dict[str, dict[str, Any]]]) -> set[int]:
    """Records cleaned by an older CLEANER_VERSION, made again from the cached files (no downloads)."""
    changed: set[int] = set()
    for url, seen in state["pages"].items():
        info = seen.get("info")
        if not info or seen.get("cleaner_version", 0) >= CLEANER_VERSION or info.get("session") not in by_session \
                or NOT_A_SPEAKER.search(info.get("label") or ""):
            continue
        text, source = text_from_cache(settings, info)
        rec = record_for(info, text, source, countries) if text else None
        for rid in [k for k, r in by_session[info["session"]].items() if r.get("url") == url]:
            del by_session[info["session"]][rid]
        if rec:
            by_session[info["session"]][rec["record_id"]] = rec
        seen.update({"cleaner_version": CLEANER_VERSION, "text_source": source or None,
                     "status": "ok" if rec else seen.get("status")})
        if rec is None:
            seen["lastmod"] = None  # the cache is not enough (a transcript never fetched): fetch it on the next run
        changed.add(info["session"])
    for s in changed:
        save_records(settings, s, by_session[s].values())
    return changed


def last_check(settings: Settings, sessions: set[int] | None = None) -> dict[str, Any]:
    """What the last reading of the website found, and how many statements are kept from it, for the status page:
    the website is read at most every SITEMAP_EVERY_HOURS, so a failure must be visible between readings too."""
    state = load_state(settings)
    out = dict(state.get("last_check") or {})
    if sessions is None:
        sessions = {int(p.stem.split("-")[1]) for p in cache_dir(settings).glob("records-*.json")}
    out["statements_kept"] = {str(s): len(load_records(settings, s)) for s in sorted(sessions)}
    return out


def _sitemap(site: Website, url: str) -> str:
    """A sitemap page, or Blocked if what came back isn't one (a browser check served with status 200)."""
    r = site.get(url)
    if "<urlset" not in r.text[:3000] and "<sitemapindex" not in r.text[:3000]:
        raise Blocked(f"{url} came back as {r.headers.get('content-type', '?')}, not a sitemap: "
                      f"{' '.join(r.text[:120].split())!r}")
    return r.text


def update(settings: Settings, countries: Countries, sessions: set[int], site: Website | None = None,
           skip: set[str] | None = None, force: bool = False) -> tuple[set[int], list[str]]:
    """Fetch new and changed statement pages of the given sessions. ``skip``: record ids the official record already
    has. ``force``: read the sitemap now, however recently it was read. Returns the sessions whose records changed,
    and problems to report. What it found is kept in the state ("last_check") for the status page."""
    try:
        return _update(settings, countries, sessions, site, skip, force)
    except Exception as exc:
        state = load_state(settings)
        state["last_check"] = {"at": _now(), "ok": False, "error": f"{type(exc).__name__}: {exc}"[:500]}
        save_state(settings, state)
        raise


def _update(settings: Settings, countries: Countries, sessions: set[int], site: Website | None,
            skip: set[str] | None, force: bool) -> tuple[set[int], list[str]]:
    state = load_state(settings)
    by_session: dict[int, dict[str, dict[str, Any]]] = {
        s: {r["record_id"]: r for r in load_records(settings, s)} for s in sessions}
    recleaned = reclean(settings, countries, state, by_session)
    if recleaned:
        state["unstored"] = sorted(set(state.get("unstored", [])) | recleaned)
        save_state(settings, state)
    # sessions with records saved but not yet stored (an interrupted run) are returned again until stored
    unstored = {int(s) for s in state.get("unstored", [])} & sessions
    if not force and _hours_since(state.get("sitemap_checked")) < SITEMAP_EVERY_HOURS:
        return unstored, []
    site = site or Website()
    problems: list[str] = []
    pages: dict[str, str] = {}
    index = _sitemap(site, f"{SITE}/sitemap.xml")
    for sub in sitemap_pages(index) or [f"{SITE}/sitemap.xml"]:
        pages.update(sitemap_statements(_sitemap(site, sub) if sub != f"{SITE}/sitemap.xml" else index, sessions))
    listed = {s: sum(1 for u in pages if u.split("/")[4] == str(s)) for s in sessions}
    if not any(listed.values()):
        problems.append(f"the website's sitemap lists no statements for sessions {sorted(sessions)}")
    changed: set[int] = set()
    for url, mod in sorted(pages.items()):
        seen = state["pages"].get(url, {})
        retry = seen.get("status") == "no_text" and _hours_since(seen.get("first_seen")) < RETRY_NO_TEXT_DAYS * 24
        if seen and seen.get("lastmod") == mod and not retry:
            continue
        try:
            info = parse_page(site.get(url).text, url)
            if skip and any(rid.startswith(f"gd:{info['session']}:") and rid.split(":")[2] ==
                            (countries.resolve(info.get("label")) or "") for rid in skip):
                state["pages"][url] = {**seen, "lastmod": mod, "status": "official"}
                continue
            folder = cache_dir(settings) / str(info["session"])
            folder.mkdir(parents=True, exist_ok=True)
            cached = (folder / f"{info['slug']}.pdf").exists()
            if "english" in info["files"] and (seen or not cached):  # a changed page may have a new PDF
                pdf = site.get(info["files"]["english"]).content
                if pdf[:5] == b"%PDF-":
                    (folder / f"{info['slug']}.pdf").write_bytes(pdf)
            text, source = text_from_cache(settings, info)
            if source != "delegation" and info.get("transcript") and (seen or not (folder / f"{info['slug']}.txt").exists()):
                got = site.transcript(info["transcript"])
                if got:
                    (folder / f"{info['slug']}.txt").write_text(got)
                    text, source = text_from_cache(settings, info)
            rec = record_for(info, text, source, countries) if text else None
        except Blocked:
            raise
        except Exception as exc:  # one odd page must not stop the rest
            log.exception("could not read %s", url)
            problems.append(f"{url}: {type(exc).__name__}: {exc}")
            continue
        status = "ok" if rec else ("not_a_state" if NOT_A_SPEAKER.search(info.get("label") or "") else "no_text")
        state["pages"][url] = {"lastmod": mod, "status": status, "fetched_at": _now(),
                               "first_seen": seen.get("first_seen") or _now(), "text_source": source or None,
                               "info": info, "cleaner_version": CLEANER_VERSION}
        if rec:
            if rec["country_code"] is None and rec["record_id"].split(":")[2] not in ORGANISATION_KEYS.values():
                problems.append(f"website statement not matched to a country: {info.get('label')}")
            by_session[rec["session"]][rec["record_id"]] = rec
            changed.add(rec["session"])
            save_records(settings, rec["session"], by_session[rec["session"]].values())  # each page, so a stop
            state["unstored"] = sorted(set(state.get("unstored", [])) | {rec["session"]})  # loses nothing
        save_state(settings, state)
    state["sitemap_checked"] = _now()
    state["last_check"] = {"at": _now(), "ok": not problems, "pages_listed": {str(s): n for s, n in listed.items()},
                           "pages_failed": sum(1 for p in problems if p.startswith(SITE)),
                           "problems": problems[:5]}
    save_state(settings, state)
    return changed | unstored, problems


def import_records(settings: Settings, sources: list[str]) -> dict[int, int]:
    """Statements read from the website elsewhere (records-<session>.json files, as this module saves them, from a
    path or a web address), for a server the website refuses. They are kept beside any this server read itself, and
    the next ``wcs update`` stores them without reading the website first. Returns statements per session."""
    state = load_state(settings)
    out: dict[int, int] = {}
    for src in sources:
        raw = httpx.get(src, timeout=120, follow_redirects=True).raise_for_status().text \
            if src.startswith(("http://", "https://")) else Path(src).read_text()
        rows = json.loads(raw)
        m = re.search(r"records-(\d+)\.json$", src)
        sessions = {int(r["record_id"].split(":")[1]) for r in rows}
        if not rows or len(sessions) != 1 or (m and int(m.group(1)) not in sessions) or \
                not all(r.get("record_id", "").startswith("gd:") and r.get("text") for r in rows):
            raise ValueError(f"{src}: not a records file of one session")
        session = sessions.pop()
        for r in rows:
            r["date"] = date.fromisoformat(str(r["date"])[:10])
        mine = {r["record_id"]: r for r in load_records(settings, session)}
        merged = {r["record_id"]: r for r in rows} | mine     # what this server read itself wins
        save_records(settings, session, merged.values())
        out[session] = len(rows)
    state = load_state(settings)
    state["unstored"] = sorted(set(state.get("unstored", [])) | set(out))
    state["sitemap_checked"] = _now()       # store them at the next update, before trying the website again
    state.setdefault("last_check", {})["imported"] = {"at": _now(), "statements": {str(k): v for k, v in out.items()}}
    save_state(settings, state)
    return out


def save_records(settings: Settings, session: int, records) -> None:
    path = records_path(settings, session)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(sorted(records, key=lambda r: r["record_id"]), default=str))
    tmp.replace(path)


def mark_stored(settings: Settings, sessions: set[int]) -> None:
    state = load_state(settings)
    if state.get("unstored"):
        state["unstored"] = [s for s in state["unstored"] if s not in sessions]
        save_state(settings, state)
