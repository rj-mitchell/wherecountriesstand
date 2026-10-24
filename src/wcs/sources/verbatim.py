"""Parse a UN verbatim record (S/PV, A/PV) into its meeting details and speeches.

Works on positioned lines (``pdftext.Line``), so it can be tested without PDFs. Two stages:

1. **Front matter** (the first page, before "The meeting was called to order"): the date, the President and their
   country, the Council members listed, and the agenda.
2. **Speeches**: paragraphs are rebuilt from text boxes and first-line indents; a paragraph that opens with a bold
   name followed only by bracketed notes and a colon ("**Mr. Delattre** (France) (*spoke in French*):") starts a new
   speech. If a record has no bold type at all (a changed template), the wording of the attribution is used instead,
   and the result says so (``Meeting.detection``), so the update job can report it.

Running headers and footers are dropped by their position and their wording; headings inside the record (agenda
items) are kept as the current agenda item, not as speech text. General Assembly records number their items
("Agenda item 8 (continued)", then "General debate") and give heads of State a heading of their own ("Address by Mr.
Petr Pavel, President of the Czech Republic"), which is kept with the speech it introduces (``Speech.heading``).

``PARSER_VERSION`` is stored with every parsed record: when the parser changes, cached PDFs are parsed again.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from wcs.sources.pdftext import Line

PARSER_VERSION = 2

MONTHS = {m: i for i, m in enumerate(["January", "February", "March", "April", "May", "June", "July", "August",
                                      "September", "October", "November", "December"], 1)}
DATE = re.compile(r"\b(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s+(\d{1,2})\s+(" +
                  "|".join(MONTHS) + r")\s+(\d{4})")
START = re.compile(r"^The meeting (?:was called to order|was resumed|was opened|opened|resumed)\b")
END = re.compile(r"^The meeting (?:rose|was adjourned|was suspended and adjourned)\b")
LEADER = re.compile(r"\s*(?:\.\s?){3,}\s*")
# in the members list, two dots can be all that is left before a long name's representative
MEMBER_LEADER = re.compile(r"\s+(?:\.\s?){2,}\s*")
# header and footer lines, wherever they fall: symbols, dates, page and job numbers, barcodes
FURNITURE = re.compile(r"^(?:[SA]/(?:\d+/)?PV\.\d+(?:\s*\(Resumption\s*\d+\))?|\d{2}/\d{2}/\d{4}|\d+/\d+|"
                       r"\d{2}-\d{5}(?:\s*\([A-Z]\))?|\*\d+\*|Provisional|Official Records)$")
PARENS = re.compile(r"\(((?:[^()]|\([^()]*\))*)\)")
# Wording of an attribution, for records without bold type.
FALLBACK_ATTRIBUTION = re.compile(
    r"^(?P<attr>(?:The (?:Acting )?President|The (?:Deputy )?Secretary-General|"
    r"(?:Mr|Mrs|Ms|Miss|Dr|Sir|Dame|Lord|Lady|Baroness|Archbishop|Cardinal|Monsignor|Sheikh|Sheikha|Prince|Princess|"
    r"King|Queen|President|Prime Minister|Minister|Ambassador|Judge|General)\b\.?[^:()]{0,80}?)"
    r"(?:\s*\((?:[^()]|\([^()]*\))*\))*)\s*:\s+")
CAPACITY = re.compile(r"[^.!?]*\bin\s+(?:my|its)\s+(?:national\s+)?capacity\s+as\s+(?:the\s+)?(?:representative|"
                      r"President)\s+of\b[^.!?]*[.!?]|[^.!?]*\bin\s+my\s+national\s+capacity\b[^.!?]*[.!?]", re.I)
RESUME = re.compile(r"[^.!?]*\bresume\s+(?:my\s+)?functions\s+as\s+(?:the\s+)?President\b[^.!?]*[.!?]", re.I)


@dataclass
class Speech:
    attribution: str
    speaker: str             # "Mr. Delattre", "The President"
    label: str | None        # the bracketed country or organisation: "France"
    language_note: str | None
    text: str
    agenda: str | None = None
    heading: str | None = None  # "Address by Mr. Petr Pavel, President of the Czech Republic"


@dataclass
class Meeting:
    symbol: str
    meeting_date: date | None = None
    president: str | None = None
    president_country: str | None = None
    members: list[str] = field(default_factory=list)
    agenda: list[str] = field(default_factory=list)
    speeches: list[Speech] = field(default_factory=list)
    briefers: dict[str, str] = field(default_factory=dict)  # surname -> post, from the rule 39 invitations
    detection: str = "bold"  # "bold" or "wording" (records without bold type)
    warnings: list[str] = field(default_factory=list)


# ---- front matter ---------------------------------------------------------------------------------------------

def front_matter(lines: list[Line], m: Meeting) -> None:
    # top to bottom, then left to right; heights rounded so a label and its first entry ("Members:", "Albania")
    # printed on one line sort together
    first = sorted((ln for ln in lines if ln.page == 0), key=lambda ln: (-round(ln.y1 / 3), ln.x0))
    state = None
    pres: list[str] = []
    agenda: list[str] = []
    pending: list[str] = []
    for ln in first:
        t = ln.text
        if START.match(t):
            break
        if m.meeting_date is None and (d := DATE.search(t)):
            m.meeting_date = date(int(d.group(3)), MONTHS[d.group(2)], int(d.group(1)))
        if t.startswith("President:"):
            state = "president"
            t = t[len("President:"):].strip()
        elif t.startswith("Members:"):
            state = "members"
            t = t[len("Members:"):].strip()
        elif t == "Agenda" or (t.startswith("Agenda") and ln.bold > 0.8 and len(t) < 12):
            state = "agenda"
            continue
        elif ln.size < 9.5 and state == "agenda":
            state = None  # the boxed notice under the agenda
        if not t:
            continue
        if state == "president":
            pres.append(t)
        elif state == "members":
            if MEMBER_LEADER.search(t):
                country = " ".join([*pending, MEMBER_LEADER.split(t)[0].strip()]).strip()
                pending = []
                if country:
                    m.members.append(country)
            elif not t.startswith(("Mr", "Ms", "Mrs", "Miss", "Dr", "Sir", "Dame")):
                pending.append(t)  # a long name wrapped onto the next line ("United Kingdom of Great Britain and")
        elif state == "agenda" and ln.size >= 9.5 and not FURNITURE.match(t):
            agenda.append(t)
    if pres:
        joined = " ".join(pres)
        m.president = LEADER.split(joined)[0].strip() or None
        found = [p for p in PARENS.findall(joined)]
        m.president_country = " ".join(found[0].split()) if found else None
    m.agenda = join_agenda(agenda)


def join_agenda(lines: list[str]) -> list[str]:
    """Agenda lines as items: a line that continues the one before (lower-case start) joins it."""
    items: list[str] = []
    for t in lines:
        if items and (t[:1].islower() or items[-1].endswith((",", "and", "of", "the", "-"))):
            items[-1] = f"{items[-1]} {t}"
        else:
            items.append(t)
    return [re.sub(r"\s*\(continued\)$", "", i).strip() for i in items if i.strip()]


# ---- body -----------------------------------------------------------------------------------------------------

@dataclass
class Para:
    lines: list[Line]

    @property
    def text(self) -> str:
        out = ""
        for ln in self.lines:
            t = ln.text
            out = (out[:-1] + t) if out.endswith("-") and not out.endswith(" -") else (f"{out} {t}" if out else t)
        return out

    @property
    def bold_prefix(self) -> str:
        return self.lines[0].bold_prefix

    @property
    def heading(self) -> bool:
        """A heading inside the record (an agenda item): bold (a "(continued)" may be in italics), short, no
        attribution colon."""
        return all(ln.bold > 0.9 or (ln.bold >= 0.5 and ln.bold + ln.italic >= 0.9) for ln in self.lines) and \
            ":" not in self.text and len(self.text) < 300


def body_lines(lines: list[Line]) -> list[Line]:
    start = next((i for i, ln in enumerate(lines) if ln.zone == "body" and START.match(ln.text)), None)
    if start is None:
        start = next((i for i, ln in enumerate(lines) if ln.page >= 1), len(lines)) - 1
    out = []
    for ln in lines[start + 1:]:
        if END.match(ln.text):
            break
        if ln.zone != "body" or FURNITURE.match(ln.text):
            continue
        out.append(ln)
    return out


def paragraphs(lines: list[Line]) -> list[Para]:
    """Rebuild paragraphs. A line starts one when it is indented from its column's left edge (the first line of a
    paragraph is indented in these records), or when it opens a new text box after a finished sentence. Text boxes
    alone are not enough: pdfminer sometimes splits one paragraph over two boxes."""
    from wcs.sources.pdftext import column_edges

    edges = column_edges(lines)

    def indented(ln: Line) -> bool:
        lefts = [e for e in edges[ln.page] if e <= ln.x0 + 1]
        return bool(lefts) and ln.x0 > lefts[-1] + 6 or (not lefts)

    paras: list[Para] = []
    prev: Line | None = None
    for ln in lines:
        finished = prev is not None and prev.text.rstrip().endswith((".", ":", "?", "!", "”", '"', "’", ")"))
        new = prev is None or indented(ln) or (ln.box != prev.box and finished) or \
            (bool(ln.bold_prefix) and not prev.bold_prefix and finished)
        # a sentence carried over to the next column or page, or past an indented quotation, continues
        carried = prev is not None and not finished and (ln.text[:1].islower() or ln.text[:1].isdigit()) and \
            not ln.bold_prefix
        # a speaker's bold name alone in its text box, with the brackets or colon in the next one
        carried = carried or (prev is not None and prev.bold > 0.9 and ":" not in prev.text and
                              ln.text.startswith(("(", ":")))
        if new and not carried:
            paras.append(Para([ln]))
        else:
            paras[-1].lines.append(ln)
        prev = ln
    return paras


NAME_PARTICLES = {"de", "da", "das", "do", "dos", "del", "della", "di", "du", "la", "le", "van", "von", "der", "den",
                  "al", "el", "bin", "binti", "ibn", "y", "e", "and", "ter", "zu", "of"}


def attribution_by_font(p: Para) -> tuple[str, str] | None:
    """(attribution, rest) when the paragraph opens with a bold name and bracketed notes before a colon.

    The bold run sometimes breaks inside the name ("**Mr.** Klavinskis"), so up to five more capitalised words may
    follow it before the brackets or the colon."""
    bold = p.bold_prefix.strip().rstrip(":").strip()
    if len(bold) < 2:
        return None
    text = p.text
    if not text.startswith(bold):
        return None
    after = text[len(bold):]
    m = re.match(r"^(?P<more>[^():]{0,60}?)(?P<parens>(?:\s*\((?:[^()]|\([^()]*\))*\))*)\s*:\s*", after)
    if not m:
        return None
    more = m.group("more").split()
    if len(more) > 5 or any(w[:1].islower() and w.lower() not in NAME_PARTICLES for w in more):
        return None
    return (bold + m.group("more") + m.group("parens")).strip(), after[m.end():].strip()


def attribution_by_wording(p: Para) -> tuple[str, str] | None:
    m = FALLBACK_ATTRIBUTION.match(p.text)
    return (m.group("attr").strip(), p.text[m.end():].strip()) if m else None


def split_attr(attr: str) -> tuple[str, str | None, str | None]:
    """"Mr. Delattre (France) (spoke in French)" -> ("Mr. Delattre", "France", "(spoke in French)")."""
    name = attr.split("(", 1)[0].strip()
    label, note = None, None
    for inner in PARENS.findall(attr):
        inner = " ".join(inner.split())
        if re.match(r"(?:spoke in|interpretation from)\b", inner, re.I):
            note = f"({inner})"
        elif label is None:
            label = inner
    return name, label, note


SUBHEADING = re.compile(r"^(?:Address|Statement) by\b")
AGENDA_ITEM = re.compile(r"^Agenda items?\s+\d+(?:\s*(?:,|and)\s*\d+)*(?:\s*\(continued\))?\s*", re.I)
HEADING_OPENER = re.compile(r"^(?:Agenda items?\s+\d|Address by\b|Statement by\b)")
CONNECTOR = re.compile(r"(?:\b(?:of|the|and|for|to|on|in|by|at)|[,\-\u2013\u2014])$")
CHAIR_SPEAKER = re.compile(r"^The (?:Acting |Temporary )?President\b")


def join_headings(texts: list[str]) -> list[str]:
    """Consecutive heading paragraphs as headings: a line that continues the one before (a wrapped "Address by ..."
    heading, a lower-case start, a line ending in "of" or "the") joins it."""
    out: list[str] = []
    # headings printed one after another can come back as one paragraph ("General debate Address by Mr. ...")
    texts = [part for t in texts for part in re.split(r"\s+(?=Agenda items?\s+\d|Address by\b|Statement by\b)", t)]
    for t in texts:
        t = " ".join(t.split())
        if out and not HEADING_OPENER.match(t) and not AGENDA_ITEM.fullmatch(out[-1]) and (
                t[:1].islower() or CONNECTOR.search(out[-1]) or SUBHEADING.match(out[-1])):
            out[-1] = f"{out[-1]} {t}"
        else:
            out.append(t)
    return out


def speeches(lines: list[Line], m: Meeting) -> None:
    paras = paragraphs(body_lines(lines))
    any_bold = any(p.bold_prefix for p in paras)
    detect = attribution_by_font if any_bold else attribution_by_wording
    m.detection = "bold" if any_bold else "wording"
    agenda = m.agenda[0] if m.agenda else None
    current: Speech | None = None
    pending: list[str] = []      # heading paragraphs not yet read
    subheading: str | None = None  # an "Address by ..." heading, for the next speech that is not the chair's

    def read_headings() -> None:
        nonlocal agenda, subheading
        for h in join_headings(pending):
            if SUBHEADING.match(h):
                subheading = h
            elif AGENDA_ITEM.match(h):
                title = AGENDA_ITEM.sub("", h).strip()
                if title:  # "Agenda item 8 (continued)" alone: the title is the next heading
                    agenda = _match_agenda(title, m.agenda) or agenda
            else:
                agenda = _match_agenda(h, m.agenda) or agenda
        pending.clear()

    for p in paras:
        found = detect(p)
        # an attribution the record forgot to print in bold, straight after the President ("Prime Minister Schoof
        # (Kingdom of the Netherlands):"): accepted by its wording when it names a country or body in brackets
        if found is None and any_bold and current is not None and CHAIR_SPEAKER.match(current.speaker) and \
                (w := attribution_by_wording(p)) and "(" in w[0] and not CHAIR_SPEAKER.match(w[0]):
            found = w
        if found is None and p.heading:
            pending.append(p.text)
            continue
        read_headings()
        if found is not None:
            attr, rest = found
            name, label, note = split_attr(attr)
            current = Speech(attr, name, label, note, rest, agenda)
            if subheading and not CHAIR_SPEAKER.match(name):
                current.heading, subheading = subheading, None
            m.speeches.append(current)
        elif current is not None:
            current.text += "\n\n" + p.text
    for sp in m.speeches:
        if sp.speaker.startswith("The President") or sp.speaker.startswith("The Acting President"):
            m.briefers.update(briefers_in(sp.text))
    if not m.speeches:
        m.warnings.append("no speeches found")


# "In accordance with rule 39 ..., I invite the following briefers to participate in this meeting: Ms. Rosemary
# DiCarlo, Under-Secretary-General for Political and Peacebuilding Affairs; and Mr. Bankole Adeoye, Commissioner for
# Political Affairs, Peace and Security of the African Union." or "I invite Mr. X, Special Envoy ..., to participate"
RULE_39 = re.compile(r"rule\s+39\b[^.]*?\binvite\s+(?:the\s+following\s+(?:briefers?|participants?)\s+to\s+"
                     r"participate\s+in\s+(?:this|the)\s+meeting:\s*(?P<list>.+?)\.(?:\s|$)|(?P<one>.+?),?\s+to\s+"
                     r"participate\s+in\s+(?:this|the)\s+meeting)", re.I | re.S)
HONORIFIC = r"(?:Mr|Mrs|Ms|Miss|Dr|Sir|Dame|Lord|Lady|Judge|General|Major General|Lieutenant General|Professor|Prof)"


def briefers_in(text: str) -> dict[str, str]:
    """Surname -> post for the briefers a President invites under rule 39."""
    out: dict[str, str] = {}
    # the full stops of honorifics ("Ms. Rosemary DiCarlo") are not the end of the list
    text = re.sub(r"\b(Mr|Mrs|Ms|Dr|Prof|St|Lt|Gen|Maj|Col|No)\.(?=\s)", lambda m: m.group(1) + "\u00a7", text)
    for m in RULE_39.finditer(text):
        chunk = m.group("list") or m.group("one") or ""
        for item in re.split(r";\s*(?:and\s+)?|\s+and\s+(?=" + HONORIFIC + r"[.\u00a7]?\s)", chunk):
            im = re.match(r"\s*(?:and\s+)?(?:his|her)?\s*(?:Excellency\s+)?(?P<name>" + HONORIFIC +
                          r"[.\u00a7]?\s+[^,]+?),\s*(?P<post>.+?)\s*$", item, re.S)
            if im:
                name = " ".join(im.group("name").replace("\u00a7", ".").split())
                out[name.split()[-1]] = " ".join(im.group("post").split()).rstrip(",;")
    return out


def _match_agenda(heading: str, items: list[str]) -> str | None:
    h = " ".join(heading.split()).lower()
    for item in items:
        if item.lower().startswith(h[:40]) or h.startswith(item.lower()[:40]):
            return item
    return " ".join(heading.split()) if len(heading) > 8 else None


def parse(lines: list[Line], symbol: str) -> Meeting:
    m = Meeting(symbol)
    front_matter(lines, m)
    speeches(lines, m)
    if m.meeting_date is None:
        m.warnings.append("no meeting date on the first page")
    return m


# ---- the President's statements in their national capacity ----------------------------------------------------

def split_chair(text: str) -> list[tuple[str, str]]:
    """A presiding speaker's text as [("chair" | "national", text)]: from "in my capacity as the representative of X"
    to "I now resume my functions as President" is the President's own country speaking."""
    parts: list[tuple[str, str]] = []
    pos = 0
    while True:
        m = CAPACITY.search(text, pos)
        if not m:
            break
        start = m.start() + (len(m.group(0)) - len(m.group(0).lstrip()))
        if text[pos:start].strip():
            parts.append(("chair", text[pos:start].strip()))
        r = RESUME.search(text, m.end())
        end = r.start() + (len(r.group(0)) - len(r.group(0).lstrip())) if r else len(text)
        parts.append(("national", text[start:end].strip()))
        pos = end
    if text[pos:].strip():
        parts.append(("chair", text[pos:].strip()))
    return parts or [("chair", text)]


def summary(m: Meeting) -> dict[str, Any]:
    return {"symbol": m.symbol, "date": m.meeting_date.isoformat() if m.meeting_date else None,
            "president": m.president, "president_country": m.president_country, "members": len(m.members),
            "agenda": m.agenda, "speeches": len(m.speeches), "detection": m.detection, "warnings": m.warnings}
