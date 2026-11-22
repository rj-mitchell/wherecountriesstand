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
from dataclasses import dataclass
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
    recorded = mismatched = 0
    for i, (name, post) in sorted(intros.items()):
        sp = meeting.speeches[i]
        if not in_debate(sp.agenda, debate) or NOT_A_STATEMENT.search(f"{sp.speaker} {name} {post}"):
            continue
        # a heading or invitation that names another country than the speaker who follows belongs to another speech:
        # in some records the pages come out of order (2020: India's reply under Tonga's Prime Minister's heading)
        said, named = countries.resolve(sp.label or ""), country_in(post, countries) if post else None
        if said and named and said != named:
            mismatched += 1
            continue
        # "Address by His Serene Highness Prince Albert II of Monaco": no post, the country is in the name
        code = said or named or country_in(name, countries)
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
    taken = {r["record_id"] for r in out}
    for annex in meeting.annexes:   # recorded statements, printed after the meeting (2020, partly 2021)
        rec = annex_record(annex, symbol, session, d, countries, undocs)
        if rec is None or rec["record_id"] in taken:
            continue
        out.append(rec)
        taken.add(rec["record_id"])
        recorded -= 1
    if mismatched:
        meeting.warnings.append(f"{mismatched} introductions name another country than the speaker who follows "
                                f"(the record's pages come out of order?): left out")
    if recorded > 0:
        meeting.warnings.append(f"{recorded} statements shown as recorded videos and printed in annexes could not be "
                                f"read from the annexes: only their introductions are in the record, so they are left "
                                f"out")
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


ORIGINAL = re.compile(r"\[Original:\s*(?P<lang>[A-Za-z]+)")


def annex_record(annex: Any, symbol: str, session: int, d: date, countries: Countries,
                 undocs: str) -> dict[str, Any] | None:
    """A statement printed in an annex (``verbatim.read_annexes``) as a stored record. The country comes from its
    heading's post, or its name (the Pope speaks for the Holy See); the European Council's President speaks for the
    European Union. None if neither gives one."""
    m = ADDRESS.match(annex.heading or "")
    if not m or not annex.text.strip():
        return None
    name, post = split_who(m.group("who"))
    code = country_in(post, countries) or country_in(name, countries) or \
        ("VAT" if re.search(r"\bPope\b|\bHoly See\b", f"{name} {post}") else None)
    label = None
    if code is None:
        last = re.split(r"\bof\s+(?:the\s+)?", post)[-1].strip(" .,")
        label = ORGANISATION_LABELS.get(last, last if last in ORGANISATION_KEYS else None)
        if label is None:
            return None
    lang = ORIGINAL.search(annex.original or "")
    language_spoken = lang.group("lang").capitalize() if lang and lang.group("lang").lower() != "english" else None
    text = normalise_text(annex.text)
    key = code or ORGANISATION_KEYS.get(label or "")
    return {
        "record_id": f"gd:{session}:{key}", "source": "gd", "body": "General Assembly", "date": d,
        "date_precision": "day", "year": d.year, "session": session,
        "meeting": f"General Debate, {_ordinal(session)} session", "symbol": symbol, "speech_number": None,
        "agenda": "General debate", "agenda_area": None, "speaker_name": name, "speaker_post": post or None,
        "speaker_role": classify(post), "participant": "", "country_code": code,
        "country_label": countries.name_on(code, d) if code else label,
        "entity_kind": countries.kind(code) if code else "organisation",
        "on_behalf_of": general_debate_behalf(text), "language": language_spoken, "ocr": False, "text": text,
        "url": symbol_url(undocs, symbol), "text_source": "annex",
    }


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


# The rest of the plenary record (decision 31): every statement at a plenary meeting that is not the General Debate's,
# stored as source "ga", with explanations of vote linked to the recorded votes they explain.

PLENARY_VERSION = 1   # of how plenary statements and explanations of vote are read: a higher one reads them again
PLENARY = re.compile(r"^A/(?:(?P<session>\d+)|ES-(?P<es>\d+)|S-(?P<special>\d+))/PV\.(?P<n>\d+)$")

# The President's words before explanations of vote (and of position, given on a decision taken without a vote)
EOV_CALL = re.compile(r"explanations? of (?P<what>vote or position|vote|position)(?P<rest>[^.]{0,120})", re.I)
EOV_BEFORE = re.compile(r"before (?:the )?(?:vot(?:e|ing)|action|the Assembly takes|a decision|we take)", re.I)
EOV_AFTER = re.compile(r"after (?:the )?(?:vot(?:e|ing)|adoption|action)|just adopted|on the (?:resolutions?|decisions?)"
                       r" (?:just )?adopted", re.I)
EOV_END = re.compile(r"heard the (?:last|only) speakers? in explanation of (?:vote|position)", re.I)
# a resolution as a speech names it ("resolution 79/81", "A/RES/ES-11/1") and a draft ("A/79/L.23")
NAMED_RESOLUTION = re.compile(r"\b(?:A/RES/)?((?:ES-\d+|S-\d+|\d{2,3})/\d+(?:\s?[A-Z]\b)?)")
# a vote's list that the parser took for a speech (2005: "13 Abstaining:", where a page number and the list's heading
# ran together): the presiding officer's words, never a statement
LIST_FRAGMENT = re.compile(r"(?:^\d|\b(?:In favour|Against|Abstaining)\s*$)")
NAMED_DRAFT = re.compile(r"\bA/(?:ES-\d+/|S-\d+/)?(?:C\.\d/)?\d{0,3}/?L\.\d+(?:/Rev\.\d)?")


def plenary_session(symbol: str) -> tuple[str, int | None] | None:
    """("80th session", 80), ("11th emergency special session", None) or ("27th special session", None) for a
    plenary record's symbol; None for another."""
    m = PLENARY.match(symbol or "")
    if not m:
        return None
    if m["session"]:
        return f"{_ordinal(int(m['session']))} session", int(m["session"])
    if m["es"]:
        return f"{_ordinal(int(m['es']))} emergency special session", None
    return f"{_ordinal(int(m['special']))} special session", None


def plenary_label(symbol: str) -> str:
    """"80th session, 45th plenary meeting": what the site calls a plenary meeting."""
    found = plenary_session(symbol)
    m = PLENARY.match(symbol or "")
    if not found or not m:
        return symbol
    return f"{found[0]}, {_ordinal(int(m['n']))} plenary meeting"


def resolution_symbol(named: str) -> str:
    """"79/81" or "ES-11/1" as a resolution's symbol: "A/RES/79/81"."""
    return "A/RES/" + re.sub(r"\s+", "", named)


@dataclass
class Explained:
    """What one explanation of vote is linked to, and how sure the link is."""
    when: str                      # "before", "after" (a vote), "position" (a decision taken without a vote)
    votes: list[str]               # the resolutions (or drafts) it explains: "A/RES/79/81"
    certain: bool


def presiding(sp: Any) -> bool:
    """The presiding officer's words: the President's, or a vote's list the parser took for a speech."""
    return bool(CHAIR_SPEAKER.match(sp.speaker)) or (not sp.label and bool(LIST_FRAGMENT.search(sp.attribution)))


def explanations(meeting: Meeting, votes: list[Any] | None = None) -> dict[int, Explained]:
    """For each speech made in explanation of vote (or of position): the recorded votes of the same meeting and
    agenda item it explains, where that is certain: the drafts or resolutions it names, or else the one vote taken in
    its round of voting (the President's announcement names it). Otherwise no vote, and ``certain`` is False."""
    from wcs.sources.vote_result import Chunk, votes_in

    chunks = [Chunk(i, presiding(sp), sp.text, sp.item or sp.agenda) for i, sp in enumerate(meeting.speeches)]
    if votes is None:
        votes = [v for v in votes_in(chunks) if v.adopted is not None]
    # each speech's round: a new round starts with a statement that is not an explanation, a new agenda item, or a
    # call for explanations before the vote once a vote has been taken in the round
    mode: str | None = None
    round_no, voted_in_round = 0, False
    rounds: dict[int, int] = {}
    modes: dict[int, str] = {}
    vote_round: list[int] = []
    by_chunk: dict[int, list[Any]] = {}
    for v in votes:
        by_chunk.setdefault(v.chunk, []).append(v)
    last_item = None
    for c in chunks:
        if c.agenda != last_item:
            round_no, voted_in_round, mode, last_item = round_no + 1, False, None, c.agenda
        if c.chair:
            events: list[tuple[int, str]] = []
            text = " ".join(c.text.split())
            for m in EOV_CALL.finditer(text):
                what, rest = m["what"].lower(), m["rest"]
                if EOV_END.search(text[max(0, m.start() - 60):m.end()]):
                    events.append((m.start(), "end"))
                elif what == "position" and not EOV_AFTER.search(rest) and not EOV_BEFORE.search(rest):
                    events.append((m.start(), "position"))
                elif EOV_BEFORE.search(rest):
                    events.append((m.start(), "before"))
                elif EOV_AFTER.search(rest) or what == "position":
                    events.append((m.start(), "after" if what != "position" else "position"))
                elif re.search(r"\b(?:give|call|calling)\b[^.]{0,80}$", text[max(0, m.start() - 120):m.start()]):
                    events.append((m.start(), "unqualified"))
            # where in the chunk each vote was announced
            for v in by_chunk.get(c.index, []):
                words = " ".join(v.words.split())[:60]
                at = text.find(words) if words else -1
                events.append((at if at >= 0 else len(text), "vote"))
            for _, kind in sorted(events):
                if kind == "vote":
                    voted_in_round = True
                    vote_round.append(round_no)
                    if mode == "before":
                        mode = None
                elif kind == "end":
                    mode = None
                elif kind == "before":
                    if voted_in_round:
                        round_no, voted_in_round = round_no + 1, False
                    mode = "before"
                elif kind == "unqualified":
                    mode = "after" if voted_in_round else "before"
                else:
                    mode = kind
            continue
        if mode is None:
            round_no, voted_in_round = round_no + 1, False
            continue
        opening = " ".join(c.text.split()[:60])
        if POINT_OF_ORDER.search(opening) or re.search(r"\brule \d+\b", opening):
            continue  # a point of order in the middle of the explanations, not one of them
        modes[c.index] = mode
        rounds[c.index] = round_no
    ordered = sorted(votes, key=lambda v: v.chunk)
    keyed = list(zip(ordered, vote_round if len(vote_round) == len(ordered) else [None] * len(ordered), strict=True))
    out: dict[int, Explained] = {}
    for i, when in modes.items():
        sp = meeting.speeches[i]
        item = sp.item or sp.agenda
        same_item = [v for v in ordered if (chunks[v.chunk].agenda == item)]
        if when == "before":
            candidates = [v for v, r in keyed if r == rounds[i] and v.chunk >= i]
        elif when == "after":
            candidates = [v for v, r in keyed if r == rounds[i] and v.chunk < i]
        else:
            candidates = []
        text = " ".join(sp.text.split())
        named_res = {resolution_symbol(x) for x in NAMED_RESOLUTION.findall(text) if "/" in x}
        named_drafts = set(NAMED_DRAFT.findall(text))
        named = [v for v in same_item if (v.resolution and resolution_symbol(v.resolution) in named_res) or
                 any(d in named_drafts for d in v.drafts)]
        chosen = named or (candidates if len(candidates) == 1 else [])
        keys = [resolution_symbol(v.resolution) if v.resolution else v.drafts[0] for v in chosen
                if v.resolution or v.drafts]
        if when == "position" and chosen:
            # "in explanation of position" on a draft that was then put to a recorded vote: an explanation of vote,
            # before or after it as the record has it
            when = "before" if i < min(v.chunk for v in chosen) else "after"
        out[i] = Explained(when, keys, bool(keys))
    return out


def plenary_records_for(meeting: Meeting, meta: dict[str, Any], countries: Countries,
                        settings: Settings) -> list[dict[str, Any]]:
    """Stored records (``RECORD_SCHEMA``, source "ga") for the statements at one plenary meeting that are not the
    General Debate's: everyone who speaks but the President (whose words are procedure, and are read here for the
    votes and the calls for explanations), with the agenda item as the record prints it. A statement made on behalf
    of a group is the group's (decision 7). An explanation of vote says when it was given and which recorded votes
    it explains, where that is certain (``explanations``)."""
    from wcs.sources.speech import on_behalf_of
    from wcs.sources.undocs import UN_SPEAKERS, briefer

    symbol = meeting.symbol
    session = plenary_session(symbol)
    d = meeting.meeting_date or (date.fromisoformat(meta["date"]) if re.fullmatch(r"\d{4}-\d{2}-\d{2}",
                                                                                   meta.get("date") or "") else None)
    if d is None or session is None:
        return []
    debate = debate_meeting(meeting, meta)
    undocs = settings.sources.get("undocs", "https://undocs.org/en/")
    explained = explanations(meeting)
    out: list[dict[str, Any]] = []
    for i, sp in enumerate(meeting.speeches):
        if presiding(sp) or in_debate(sp.agenda, debate):
            continue
        text = normalise_text(STAGE.sub("", sp.text))
        if not text.strip():
            continue
        code = countries.resolve(sp.label)
        if code:
            kind, label = countries.kind(code), countries.name_on(code, d)
        else:
            post = None
            kind, label = briefer(sp.speaker, sp.label, post)
            if UN_SPEAKERS.match(sp.speaker):
                kind = "un"
        behalf = on_behalf_of(text)
        if behalf and countries.resolve(behalf):
            behalf = None
        title = sp.item_title or sp.agenda
        agenda = f"Agenda item {sp.item}: {title}" if sp.item and title else (title or None)
        e = explained.get(i)
        out.append({
            "record_id": f"ga:{symbol}:{i + 1:04d}", "source": "ga", "body": "General Assembly", "date": d,
            "date_precision": "day", "year": d.year, "session": session[1], "meeting": plenary_label(symbol),
            "symbol": symbol, "speech_number": i + 1, "agenda": agenda, "agenda_area": None,
            "speaker_name": sp.speaker, "speaker_post": None, "speaker_role": "", "participant": "",
            "country_code": code, "country_label": label, "entity_kind": kind, "on_behalf_of": behalf,
            "language": language(sp.attribution), "ocr": False, "text": text, "url": symbol_url(undocs, symbol),
            "text_source": "record", "eov": e.when if e else None,
            "explains": "|".join(e.votes) if e and e.votes else None,
        })
    return out


def eov_counts(records: list[dict[str, Any]]) -> dict[str, int]:
    """How many explanations of vote were linked to a resolution adopted by recorded vote (whose page shows them), how
    many only to a draft or amendment voted on (not in the voting data, so on no page), how many could not be placed
    with certainty, and how many explanations of position (on decisions taken without a vote) there were."""
    eov = [r for r in records if r.get("eov") in ("before", "after")]
    return {"placed": sum(1 for r in eov if "A/RES/" in (r.get("explains") or "")),
            "drafts_only": sum(1 for r in eov if r.get("explains") and "A/RES/" not in r["explains"]),
            "unplaced": sum(1 for r in eov if not r.get("explains")),
            "positions": sum(1 for r in records if r.get("eov") == "position")}
