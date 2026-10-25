"""The General Debate from the General Assembly's own verbatim records (A/<session>/PV.<n>).

The General Debate corpus (``wcs.sources.ungd``) is released by its authors once a year, behind a guestbook, so newer
sessions come from the UN's records, which share the Security Council's layout and parser (``wcs.sources.verbatim``).
Sessions from ``[live] gd_from_session`` on are read this way; the corpus keeps the sessions before.

What makes a statement. The President introduces each speaker in the debate, either with a heading ("Address by Mr.
Petr Pavel, President of the Czech Republic") or with an invitation ("I now give the floor to His Excellency Mr.
Bassam Sabbagh, Minister for Foreign Affairs and Expatriates of the Syrian Arab Republic."). The speech that follows an
introduction is the country's statement; the introduction gives the speaker's full name and post, and the post gives
the country when the speech's own attribution does not ("President Pavel:").

Rights of reply. At the end of most meetings the President calls those who have asked to speak "in exercise of the
right of reply". These are kept as records of their own (agenda "General debate: right of reply"), never mixed with
the statements: from the President's call until the next speaker is introduced, or wherever a speech opens by saying
it is a reply. Points of order are left out. The corpus has no replies, so for sessions before ``gd_from_session``
they are added from the records on their own (``wcs backfill-replies``).

When the records appear. A session's General Debate is held in late September, but its records are typically
published around a year later, and not in order (in October 2026 the 80th session's meetings 1 to 4 and 18 were
out, its debate was not). So each session's first meetings are looked for in the Digital Library's catalogue, which
names each meeting's agenda items, and on ODS, every week for up to three years, until a record says the debate has
ended. The UN's General Debate website and press releases are published sooner but block automated clients, so they
are not used.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from wcs.config import Settings
from wcs.reference.countries import Countries, norm
from wcs.reference.roles import classify
from wcs.sources.speech import general_debate_behalf, language, normalise_text
from wcs.sources.unsc import symbol_url
from wcs.sources.verbatim import CHAIR_SPEAKER, Meeting

GD_WINDOW = 30            # the debate falls within a session's first 30 plenary meetings (4-17 in 2023 and 2024)
GD_CATALOGUE_WINDOW = 70  # meetings the catalogue says are the debate are taken up to this number (4-29 in 1995;
                          # in 2001 the debate was postponed to November, after 9/11)
CHECK_EVERY_DAYS = 7      # how often numbers not yet published are looked for again
GIVE_UP_DAYS = 3 * 365    # and for how long

INVITATION = re.compile(
    r"(?:invite|call (?:up)?on|give the floor to|recognize)\s+(?:His|Her|Their)\s+"
    r"(?:(?:Excellency|Excellencies|Majesty|Royal Highness|Serene Highness|Highness|Eminence|Holiness|Beatitude)\s+)?"
    r"(?P<who>.+?)(?:,?\s+to address the (?:General )?Assembly|,?\s+to make (?:a|his|her) statement)?\s*[.;]?\s*$",
    re.S)
HEAR_ADDRESS = re.compile(r"will (?:now )?hear an address by (?:the )?(?P<post>[^.]+?)\.?$")
ADDRESS = re.compile(r"^(?:Address|Statement) by\s+(?:His|Her|Their)?\s*(?:(?:Excellency|Majesty|Royal Highness|"
                     r"Serene Highness|Highness|Eminence|Holiness|Beatitude)\s+)?(?P<who>.+)$")
HONORIFIC = re.compile(r"^(?:Mr|Mrs|Ms|Miss|Dr)\.?\s+")
# "We have heard the last speaker in the general debate." (not "... for this meeting"), "As we conclude the general
# debate", "the Assembly has thus concluded its consideration of agenda item 8"
CONCLUDED = re.compile(r"heard the last speaker in the general debate\s*[.;]|as we conclude the general debate|"
                       r"conclude(?:d)? (?:its|the) consideration of agenda item 8\b", re.I)
# "Mr. X, President of Y, was escorted into the General Assembly Hall." printed in italics around an address
STAGE = re.compile(r"(?:^|\n\n)[^\n]{0,300}?\bwas escorted (?:in)?(?:to|from) the (?:General Assembly Hall|Hall|"
                   r"rostrum)\.?\s*(?=\n\n|$)")
# the same, as sentences inside the President's text (where the paragraphs run together)
STAGE_SENTENCE = re.compile(r"\bwas escorted (?:in)?(?:to|from) the (?:General Assembly Hall|Hall|rostrum)\b")


# Speakers in the debate that are not States, keyed as the corpus keys them (``ungd.ORGANISATIONS``)
ORGANISATION_KEYS = {"European Union": "EU"}
ORGANISATION_LABELS = {"European Council": "European Union"}  # its President speaks for the Union


def session_of(symbol: str) -> int | None:
    m = re.match(r"A/(\d+)/PV\.\d+", symbol)
    return int(m.group(1)) if m else None


def meeting_number(symbol: str) -> int | None:
    m = re.match(r"A/\d+/PV\.(\d+)", symbol)
    return int(m.group(1)) if m else None


def current_session(today: date | None = None) -> int:
    """The session open on a date: the 81st opened in September 2026."""
    today = today or date.today()
    return today.year - 1945 - (0 if today.month >= 9 else 1)


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def split_who(who: str) -> tuple[str, str]:
    """"Mr. Joseph R. Biden, Jr., President of the United States of America" -> (name, post)."""
    who = " ".join(who.split()).rstrip(".")
    parts = re.split(r",\s+(?!(?:Jr|Sr|II|III|IV)\b)", who, maxsplit=1)
    name = HONORIFIC.sub("", parts[0]).strip()
    return name, (parts[1].strip() if len(parts) > 1 else "")


def country_in(post: str, countries: Countries) -> str | None:
    """The country a post names: "Minister for Foreign Affairs, Cooperation and Nigeriens Abroad of the Republic of the
    Niger" -> NER. Each "of ..." is tried from the left, whole and up to a following "and" or comma ("President of
    the Republic of Kenya and Commander-in-Chief of the Defence Forces"). A hyphen lost where a heading wrapped
    ("GuineaBissau") is matched by spelling without spaces."""
    compact = {re.sub(r"[^a-z]", "", label): code for label, code in countries._labels.items()}
    for m in re.finditer(r"\bof\s+", post):
        rest = post[m.end():].strip(" .,")
        for cand in (rest, re.split(r",|\s+and\s+", rest)[0]):
            for c in (cand, re.sub(r"^the\s+", "", cand)):
                code = countries.resolve(c) or compact.get(re.sub(r"[^a-z]", "", norm(c)))
                if code:
                    return code
    return None


def introductions(meeting: Meeting) -> dict[int, tuple[str, str]]:
    """For each speech the President introduced: its index -> (name, post), from its heading or from the invitation
    that ends the President's speech before it."""
    out: dict[int, tuple[str, str]] = {}
    for i, sp in enumerate(meeting.speeches):
        if CHAIR_SPEAKER.match(sp.speaker):
            continue
        if sp.heading and (m := ADDRESS.match(sp.heading)):
            out[i] = split_who(m.group("who"))
            continue
        prev = meeting.speeches[i - 1] if i else None
        if prev is not None and CHAIR_SPEAKER.match(prev.speaker):
            sentences = re.split(r"(?<!\bMr\.)(?<!\bMs\.)(?<!\bMrs\.)(?<!\bDr\.)(?<=[.!?])\s+(?=[A-Z])",
                                 " ".join(prev.text.split()))
            spoken = [x for x in sentences if not STAGE_SENTENCE.search(x)]
            last = spoken[-1] if spoken else ""
            if (m := INVITATION.search(last)) and "representative" not in m.group("who")[:30]:
                out[i] = split_who(m.group("who"))
            elif m := HEAR_ADDRESS.search(last):  # "The Assembly will now hear an address by the Prime Minister of X."
                out[i] = (HONORIFIC.sub("", sp.speaker).strip(), m.group("post").strip())
    return out


def concludes_debate(meeting: Meeting) -> bool:
    return any(CHAIR_SPEAKER.match(sp.speaker) and CONCLUDED.search(" ".join(sp.text.split()))
               for sp in meeting.speeches)


def is_general_debate(agenda: str | None) -> bool:
    return bool(agenda) and agenda.strip().lower().startswith("general debate")


def debate_meeting(meeting: Meeting, meta: dict[str, Any]) -> bool:
    """Whether a meeting is (partly) the General Debate. A meeting that continues the debate may print no agenda
    heading at all, opening straight on "Address by ...", so the catalogue's agenda is used, or else the President's
    own words ("the eighth plenary meeting to continue with the general debate")."""
    if any("general debate" in a.lower() for a in meta.get("agenda") or []):
        return True
    return any(is_general_debate(sp.agenda) for sp in meeting.speeches) or any(
        CHAIR_SPEAKER.match(sp.speaker) and "general debate" in " ".join(sp.text.split()).lower()
        for sp in meeting.speeches)


# The Assembly takes up the Secretary-General's annual report as the debate opens, and some opening-day records print
# only its heading (A/72/PV.3, A/76/PV.3): in a meeting that is the debate, speeches under it are the debate's.
OPENING_ITEM = re.compile(r"^Report of the Secretary-General on the work of the Organization", re.I)
# introduced, but not a country's statement: the Secretary-General presenting the report
NOT_A_STATEMENT = re.compile(r"Secretary-General", re.I)


def in_debate(agenda: str | None, debate: bool) -> bool:
    """A speech is in the debate under its heading, or with no heading (or the opening item's) in a meeting that is
    the debate."""
    return is_general_debate(agenda) or (debate and (agenda is None or bool(OPENING_ITEM.match(agenda))))


REPLY_AGENDA = "General debate: right of reply"
REPLY_CALL = re.compile(r"right of reply", re.I)
REPLY_OPENING = re.compile(r"right of (?:reply|appeal)|exercis\w* (?:my|our|its|the) right", re.I)
POINT_OF_ORDER = re.compile(r"point of order", re.I)
MIN_REPLY_WORDS = 30  # shorter interventions are exchanges with the chair ("I shall conclude here.")


def replies(meeting: Meeting, intros: dict[int, tuple[str, str]], debate: bool) -> list[int]:
    """Indexes of the speeches in the debate made in exercise of the right of reply."""
    out: list[int] = []
    calling = point_of_order = False
    for i, sp in enumerate(meeting.speeches):
        if CHAIR_SPEAKER.match(sp.speaker):
            text = " ".join(sp.text.split())
            calling = calling or bool(REPLY_CALL.search(text))
            point_of_order = bool(POINT_OF_ORDER.search(text[-300:]))
            continue
        if i in intros:
            calling = False  # the debate has resumed
            continue
        opening = " ".join(sp.text.split())[:400]
        if in_debate(sp.agenda, debate) and not point_of_order and (calling or REPLY_OPENING.search(opening)) and \
                len(sp.text.split()) >= MIN_REPLY_WORDS:
            out.append(i)
        point_of_order = False
    return out


# In the virtual session of 2020 (and partly 2021) statements were shown as recorded videos: the record holds only a
# delegate's introduction ("I have the honour to introduce the pre-recorded statement by ...") and prints the
# statement itself in an annex at the end. Annexes are not read yet; such introductions are not stored as statements.
RECORDED_INTRO = re.compile(r"\bintroduce\b.{0,120}?\b(?:pre-?recorded|recorded|video)\b", re.I | re.S)
RECORDED_INTRO_WORDS = 150


def records_for(meeting: Meeting, meta: dict[str, Any], countries: Countries, settings: Settings) -> list[dict[str, Any]]:
    """Stored records (``store.parquet.RECORD_SCHEMA``, source "gd") for the General Debate statements and rights of
    reply in one plenary meeting; none for a meeting on other business."""
    symbol = meeting.symbol
    session = session_of(symbol)
    d = meeting.meeting_date or (date.fromisoformat(meta["date"]) if re.fullmatch(r"\d{4}-\d{2}-\d{2}",
                                                                                   meta.get("date") or "") else None)
    if d is None or session is None:
        return []
    undocs = settings.sources.get("undocs", "https://undocs.org/en/")
    out: list[dict[str, Any]] = []
    debate = debate_meeting(meeting, meta)
    intros = introductions(meeting)
    recorded = 0
    for i, (name, post) in sorted(intros.items()):
        sp = meeting.speeches[i]
        if not in_debate(sp.agenda, debate) or NOT_A_STATEMENT.search(f"{sp.speaker} {name} {post}"):
            continue
        # "Address by His Serene Highness Prince Albert II of Monaco": no post, the country is in the name
        code = countries.resolve(sp.label) or country_in(post, countries) or country_in(name, countries)
        if code:
            kind, label = countries.kind(code), countries.name_on(code, d)
        else:
            kind = "organisation"
            last = re.split(r"\bof\s+(?:the\s+)?", post)[-1].strip(" .,")  # "... of the European Union"
            label = sp.label or (last if " of " in f" {post} " and last[:1].isupper() else None)
            label = ORGANISATION_LABELS.get(label or "", label)
        text = normalise_text(STAGE.sub("", sp.text))
        if not text.strip():
            continue
        if len(text.split()) <= RECORDED_INTRO_WORDS and RECORDED_INTRO.search(text):
            recorded += 1  # the statement itself is printed in an annex, which is not read yet
            continue
        behalf = general_debate_behalf(text)
        key = code or ORGANISATION_KEYS.get(label or "") or re.sub(r"[^A-Za-z]+", "-", label or name).strip("-").upper()[:24]
        out.append({
            "record_id": f"gd:{session}:{key}", "source": "gd", "body": "General Assembly", "date": d,
            "date_precision": "day", "year": d.year, "session": session,
            "meeting": f"General Debate, {_ordinal(session)} session", "symbol": symbol, "speech_number": i + 1,
            "agenda": "General debate", "agenda_area": None, "speaker_name": name or sp.speaker,
            "speaker_post": post or None, "speaker_role": classify(post), "participant": "", "country_code": code,
            "country_label": label, "entity_kind": kind,
            "on_behalf_of": None if countries.resolve(behalf or "") else behalf,
            "language": language(sp.attribution), "ocr": False, "text": text, "url": symbol_url(undocs, symbol),
            "text_source": "record",
        })
    if recorded:
        meeting.warnings.append(f"{recorded} statements shown as recorded videos and printed in annexes, which are "
                                f"not read yet: only their introductions are in the record, so they are left out")
    number = meeting_number(symbol)
    for i in replies(meeting, intros, debate):
        sp = meeting.speeches[i]
        code = countries.resolve(sp.label)
        label = countries.name_on(code, d) if code else ORGANISATION_LABELS.get(sp.label or "", sp.label)
        key = code or ORGANISATION_KEYS.get(label or "") or re.sub(r"[^A-Za-z]+", "-", label or sp.speaker).strip(
            "-").upper()[:24]
        text = normalise_text(STAGE.sub("", sp.text))
        out.append({
            "record_id": f"gd:{session}:{key}:reply:{number}.{i + 1:03d}", "source": "gd", "body": "General Assembly",
            "date": d, "date_precision": "day", "year": d.year, "session": session,
            "meeting": f"General Debate, {_ordinal(session)} session", "symbol": symbol, "speech_number": i + 1,
            "agenda": REPLY_AGENDA, "agenda_area": None, "speaker_name": sp.speaker, "speaker_post": None,
            "speaker_role": "", "participant": "", "country_code": code, "country_label": label,
            "entity_kind": countries.kind(code) if code else "organisation", "on_behalf_of": None,
            "language": language(sp.attribution), "ocr": False, "text": text, "url": symbol_url(undocs, symbol),
            "text_source": "record",
        })
    return out


def is_reply(record: dict[str, Any]) -> bool:
    return ":reply:" in record["record_id"]


def check(meeting: Meeting, records: list[dict[str, Any]], meta: dict[str, Any] | None = None) -> list[str]:
    """Problems worth reporting for one General Debate meeting."""
    problems = list(meeting.warnings)
    if meeting.detection != "bold":
        problems.append("speakers found by wording, not typography: the record's template may have changed")
    is_debate = debate_meeting(meeting, meta or {})
    debate = [i for i, sp in enumerate(meeting.speeches) if in_debate(sp.agenda, is_debate)
              and not CHAIR_SPEAKER.match(sp.speaker)]
    if debate and not records:
        problems.append("General Debate speeches found but no statements introduced")
    unplaced = sorted({r["speaker_post"] or r["speaker_name"] for r in records if not is_reply(r)
                       and r["country_code"] is None and r["country_label"] not in ORGANISATION_KEYS})
    if unplaced:
        problems.append("statements not matched to a country: " + "; ".join(unplaced[:5]))
    return problems
