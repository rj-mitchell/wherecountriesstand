"""The votes taken at a meeting, read from its verbatim record: what was put to the vote, how each member voted, and
the result in the record's own words.

A Security Council record prints a vote as the President's words, then the vote in italics, then the President's
announcement of the result::

    I shall put the draft resolution to the vote now.
    A vote was taken by show of hands.
    In favour: Ecuador, France, Japan, ...
    Against: Algeria, China, Russian Federation
    Abstaining: Guyana
    The President: The draft resolution received 11 votes in favour, 3 against and 1 abstention. The draft
    resolution was not adopted, owing to the negative vote of a permanent member of the Council.

Older records announce "The result of the voting is as follows: ..."; during the procedure of 2020 and 2021 a vote
was taken by letter, and the President's letter gives the result, naming the members who voted against or abstained
in brackets ("one against (the United States of America)"). A General Assembly record prints "A recorded vote was
taken.", the same three lists, then "Draft resolution A/ES-11/L.1 was adopted by 141 votes to 5, with 35 abstentions
(resolution ES-11/1)."

A meeting is read as a list of ``Chunk`` (each speech in order, the presiding officer's marked), whether it comes
from a parsed PDF (``wcs.sources.verbatim``) or from the stored records (the Security Council corpus). Each vote is
anchored on the announcement of its result; the lists are the ones printed since the vote before; and what was voted
on is the last document the presiding officer named as a draft, an amendment or a draft decision before the vote.
A vote whose lists do not add up to the announced totals keeps the totals and is marked incomplete: nothing is
filled in that the record does not say.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

NUMBERS = {"no": 0, "none": 0, "nil": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
           "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
           "fifteen": 15}
NUM = r"(?:\d+|no|none|nil|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen)"

# what is voted on, as the record names it
WHAT = (r"(?P<what>draft\s+resolution|proposed\s+(?:oral\s+)?amendments?|draft\s+amendments?|amendments?|"
        r"draft\s+decision|proposal|motion|draft\s+text|text)")
# document symbols of drafts: Security Council (S/2024/239, S/25693, S/17354/Rev.1) and General Assembly
# (A/80/L.12, A/ES-11/L.7, A/S-27/L.1/Rev.1)
SYMBOL = (r"(?:S/\d{4}/\d{1,4}|S/\d{3,5}|A/(?:ES-\d{1,2}/|S-\d{1,2}/)?(?:\d{1,3}/)?L\.\d{1,3})"
          r"(?:/Rev\.\d)?(?:/Add\.\d)?(?:/Corr\.\d)?")
SYMBOL_RE = re.compile(SYMBOL)
DRAFT_WORDS = re.compile(r"draft resolution|amendment|draft decision|draft text", re.I)

# "A vote was taken by show of hands." (Security Council), "A recorded vote was taken." (General Assembly)
TAKEN = re.compile(r"\bA (?:recorded )?vote was\s+taken(?:\s+b[vy] (?:a )?show of hands)?\s*\.", re.I)
# (records recognised from scans write "Asainst")
LISTS = re.compile(r"In favour\s*(?:\(\d+\))?\s*:\s*(?P<Y>.*?)\s*A[gs]ainst\s*(?:\(\d+\))?\s*:\s*(?P<N>.*?)\s*"
                   r"(?:(?:Abstaining|Abstentions?)\s*(?:\(\d+\))?\s*:\s*(?P<A>.*?))?(?:\s*(?:Not participating|"
                   r"Did not participate(?: in the vot(?:e|ing))?|Absent)\s*(?:\(\d+\))?\s*:\s*(?P<X>.*?))?\s*$",
                   re.S | re.I)

# The announcement. Security Council: "The draft resolution received 11 votes in favour, 3 against and 1 abstention."
# or "The result of the voting is as follows: 13 votes in favour, 2 against ..."; the 2020 letters name the members
# in brackets. General Assembly: "Draft resolution A/80/L.12 was adopted by 141 votes to 5, with 35 abstentions".
_COUNTS = (rf"(?P<y>{NUM})(?: votes?)? in favour(?:\s*\((?P<yn>[^()]*)\))?,?\s*(?P<n>{NUM})(?: votes?)? against"
           rf"(?:\s*\((?P<nn>[^()]*)\))?,?\s*(?:and|with)\s+(?P<a>{NUM}) abstentions?(?:\s*\((?P<an>[^()]*)\))?"
           rf"(?:,?\s*(?:and|with)\s+(?P<x>{NUM}) (?:members?|delegations?) not participating(?: in the vot\w+)?)?")
RECEIVED = re.compile(rf"(?:\b{WHAT}[^.]{{0,160}}?\breceived|result of the\s+vot(?:e|ing)\s+(?:is|was)\s+as\s+"
                      rf"follows\s*:?|the vote was as follows\s*:?|\bThere were)\s*{_COUNTS}", re.I | re.S)
NOT_PARTICIPATING = re.compile(rf"\b(?P<x>{NUM}) (?:Council )?members? did not participate in the vot", re.I)
ADOPTED_BY = re.compile(rf"\b(?P<what>draft resolution|draft decision|amendment|proposed amendment|draft amendment|"
                        rf"motion|proposal)(?P<label>(?:\s+(?:{SYMBOL}|[IVXLC]+|[A-Z]))?(?:,\s*as orally "
                        rf"(?:revised|corrected|amended),)?)\s+was (?P<outcome>adopted|rejected) by (?P<y>{NUM}) votes? "
                        rf"to (?P<n>{NUM}),? with (?P<a>{NUM}) abstentions?(?:\s*\((?:resolution|decision) "
                        rf"(?P<res>[^)]+)\))?", re.I | re.S)
VETO = re.compile(r"owing\s+to\s+the\s+negative\s+votes?\s+of\s+(?:a\s+|one\s+|two\s+|three\s+)?permanent members? of the (?:Security )?Council",
                  re.I)
NOT_ENOUGH = re.compile(r"(?:having )?failed to obtain the (?:required )?(?:number of|majority of the) votes|"
                        r"not (?:obtained|received) the required (?:number of |majority of )?votes", re.I)
NOT_ADOPTED = re.compile(r"(?:has|have) not been adopted|(?:was|were) not adopted|is not adopted|"
                         r"(?:was|were|has been) rejected", re.I)
ADOPTED_AS = re.compile(r"(?:has|have) been adopted(?: unanimously)?(?: as (?:resolution|decision) (?P<res>\d+ \(\d{4}\)"
                        r"|\d+\s*\([IVXLC\d]+\)))?|(?:was|is) adopted(?: unanimously)?(?: as resolution (?P<res2>\d+ "
                        r"\(\d{4}\)))?", re.I)


@dataclass
class Chunk:
    """One speech of a meeting, in order: who presided is marked, as what they say carries the votes."""
    index: int
    chair: bool
    text: str
    agenda: str | None = None


@dataclass
class Vote:
    """One vote at a meeting, as the record has it."""
    what: str                         # "draft resolution", "proposed amendment", "draft decision", "proposal"
    drafts: list[str]                 # the document symbols it was taken on, as the record names them
    lists: dict[str, list[str]]       # "Y", "N", "A", "X" -> names as printed
    totals: dict[str, int]            # "Y", "N", "A" (and "X" when the record says so), as announced
    adopted: bool | None
    reason: str | None = None         # for a draft not adopted: "veto" or "votes", in the record's words
    resolution: str | None = None     # "2774 (2025)", "ES-11/1"
    words: str = ""                   # the announcement, in the record's words
    chunk: int = 0                    # the chunk of the announcement
    lists_chunk: int | None = None    # the chunk the lists are printed in
    agenda: str | None = None
    label: str | None = None          # "I" in "Draft resolution I was adopted"
    problems: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        """Whether the record lists every member who voted, as many as the totals it announces."""
        return all(len(self.lists.get(k, [])) == n for k, n in self.totals.items() if k != "X")

    def summary(self) -> dict[str, Any]:
        return {"what": self.what, "drafts": self.drafts, "totals": self.totals, "adopted": self.adopted,
                "reason": self.reason, "resolution": self.resolution, "complete": self.complete,
                "problems": self.problems}


def number(word: str | None) -> int:
    if not word:
        return 0
    w = word.strip().lower()
    return int(w) if w.isdigit() else NUMBERS.get(w, 0)


def names(text: str | None) -> list[str]:
    """The members in a printed list, which wraps across lines ("United Kingdom of Great Britain and" / "Northern
    Ireland"): split at commas, "None" for an empty list. A bracketed list in a letter starts "the"."""
    t = " ".join((text or "").split()).strip(" .;")
    if not t or t.lower() in ("none", "nil", "-", "—"):
        return []
    parts = re.split(r",\s*(?:and\s+)?|\s+and\s+(?=the\s)", t)
    return [re.sub(r"^the\s+", "", p.strip(" .;"), flags=re.I) for p in parts if p.strip(" .;")]


def _list_text(text: str) -> str:
    """The lists printed after "A vote was taken", up to the paragraph that is no longer a list of names: the next
    sentence (the result, or the President speaking on)."""
    out: list[str] = []
    for para in [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]:
        last = out[-1] if out else ""
        if out and re.search(r"[.!?]\)?$", para) and not re.search(r"Abstaining\s*:\s*$", last) and \
                re.search(r"Abstaining\s*:", " ".join(out)) and re.search(r"\b(?:was|were|is|has|have|shall|will|"
                                                                          r"received|adopted)\b", para):
            break
        out.append(para)
        if re.search(r"\b(?:was|were|has been|have been) (?:adopted|rejected|not adopted)\b", para):
            break
    return "\n\n".join(out)


def _split_lists(text: str) -> dict[str, list[str]] | None:
    m = LISTS.search(text)
    if not m:
        return None
    # a list the record leaves out ("Abstaining:" lost with the end of a page) is empty, and the totals then say
    # the lists are incomplete
    out = {k: names(m.group(k)) for k in ("Y", "N", "A")}
    if m.group("X"):
        out["X"] = names(m.group("X"))
    # the last list runs on to the result where both are in one paragraph
    for k in ("A", "X"):
        if out.get(k):
            cut = re.split(r"\s+(?:Draft|The) (?:resolution|decision|draft)\b", " ".join(out[k][-1:]))[0]
            out[k][-1] = cut.strip(" .")
    return out


def _drafts_before(chunks: list[Chunk], start: tuple[int, int], end: tuple[int, int],
                   every: bool = False) -> tuple[list[str], str]:
    """The documents named as what is put to the vote between two points of the meeting (chunk, offset): those the
    presiding officer named next to "draft resolution", "amendment" or "draft decision", the last first. Returns the
    symbols named in the last such passage, and the words used ("draft resolution")."""
    found: list[tuple[str, str]] = []
    for c in chunks:
        if c.index < start[0] or c.index > end[0] or not (c.chair or _letter(c)):
            continue
        lo = start[1] if c.index == start[0] else 0
        hi = end[1] if c.index == end[0] else len(c.text)
        text = c.text[lo:hi]
        for sentence in re.split(r"(?<=[.:;])\s+(?=[A-Z“\"])", text):
            if not DRAFT_WORDS.search(sentence):
                continue
            symbols = list(dict.fromkeys(SYMBOL_RE.findall(sentence)))
            if symbols:
                kind = DRAFT_WORDS.findall(sentence)[-1].lower()
                found.append(("|".join(symbols), kind))
    if not found:
        return [], ""
    if every:
        return list(dict.fromkeys(x for symbols, _ in found for x in symbols.split("|"))), found[-1][1]
    # "the proposed amendments ... to the text of the draft resolution contained in document S/2025/112" names the
    # draft the amendments are to: the sentence that puts one to the vote comes last
    symbols, kind = found[-1]
    return symbols.split("|"), kind


def votes_in(chunks: list[Chunk]) -> list[Vote]:
    """Every vote whose result the record announces, in order."""
    events: list[tuple[int, int, int, re.Match, str]] = []  # (chunk, start, end, match, form)
    for c in chunks:
        if not c.chair and not _letter(c):
            continue
        for m in RECEIVED.finditer(c.text):
            events.append((c.index, m.start(), m.end(), m, "received"))
        for m in ADOPTED_BY.finditer(c.text):
            events.append((c.index, m.start(), m.end(), m, "adopted_by"))
    events.sort(key=lambda e: (e[0], e[1]))
    by_index = {c.index: c for c in chunks}
    out: list[Vote] = []
    taken: set[str] = set()  # documents already voted on
    prev: tuple[int, int] = (chunks[0].index if chunks else 0, 0)
    for ci, s, end, m, form in events:
        c = by_index[ci]
        what = " ".join((m.group("what") or "").lower().split())
        totals = {"Y": number(m.group("y")), "N": number(m.group("n")), "A": number(m.group("a"))}
        if form == "received" and m.group("x"):
            totals["X"] = number(m.group("x"))
        # the lists: printed in the presiding officer's speeches since the vote before, after "A vote was taken"
        lists: dict[str, list[str]] = {}
        lists_chunk, lists_at = None, 0
        for cj in range(ci, prev[0] - 1, -1):
            cc = by_index.get(cj)
            if cc is None or not (cc.chair or _letter(cc)):
                continue
            hi = s if cj == ci else len(cc.text)
            lo = prev[1] if cj == prev[0] else 0
            seg = cc.text[lo:hi]
            t = TAKEN.search(seg)
            fav = re.search(r"In favour\s*(?:\(\d+\))?\s*:", seg)
            if t or fav:
                at = (t or fav).start()
                got = _split_lists(_list_text(seg[at:][t.end() - t.start():] if t else seg[at:]))
                if got:
                    lists, lists_chunk, lists_at = got, cj, lo + at
                break
        if not lists and form == "received":  # a letter names the members in brackets
            for k, g in (("Y", "yn"), ("N", "nn"), ("A", "an")):
                if m.group(g):
                    lists[k] = names(m.group(g))
        drafts, kind = _drafts_before(chunks, prev, (lists_chunk, lists_at) if lists_chunk is not None else (ci, s))
        label = None
        if form == "adopted_by":
            own = SYMBOL_RE.findall(m.group("label") or "")
            if own:
                drafts = own
            elif (lab := (m.group("label") or "").strip(" ,")) and re.fullmatch(r"[IVXLC]+|[A-Z]", lab):
                label = lab
            adopted = m.group("outcome").lower() == "adopted"
            resolution = (m.group("res") or "").strip() or None
            reason = None if adopted else "votes"
            words = " ".join(m.group(0).split()).rstrip(".") + "."
        else:
            # the result is in one of the next few sentences (2001: "One Council member did not participate in the
            # voting." comes between)
            first = _announcement(c.text, s, end, sentences=1)
            outcome, words = "", first
            for k in (2, 3, 4):
                words = _announcement(c.text, s, end, sentences=k)
                outcome = words[len(first):]
                if (x := NOT_PARTICIPATING.search(outcome)) and "X" not in totals:
                    totals["X"] = number(x.group("x"))
                if VETO.search(outcome) or NOT_ENOUGH.search(outcome) or NOT_ADOPTED.search(outcome) or \
                        ADOPTED_AS.search(outcome):
                    break
            else:
                words, outcome = first, ""
            adopted, reason, resolution = None, None, None
            if VETO.search(outcome):
                adopted, reason = False, "veto"
            elif NOT_ENOUGH.search(outcome):
                adopted, reason = False, "votes"
            elif NOT_ADOPTED.search(outcome):
                adopted = False
            elif a := ADOPTED_AS.search(outcome):
                adopted, resolution = True, (a.group("res") or a.group("res2"))
            if not what or what in ("text",):
                what = kind or "draft resolution"
            if "oral" in what:
                drafts = []  # an oral amendment has no document: the one named is the draft it would amend
        v = Vote(what=_what(what or kind), drafts=drafts, lists=lists, totals=totals, adopted=adopted, reason=reason,
                 resolution=resolution, words=words, chunk=ci, lists_chunk=lists_chunk, agenda=c.agenda, label=label)
        if not drafts and v.what not in ("proposal", "motion") and "oral" not in what:
            # nothing named since the vote before (an oral amendment to it came between): the last draft named
            # earlier in the meeting that has not been voted on yet
            earlier, _ = _drafts_before(chunks, (chunks[0].index, 0), (ci, s), every=True)
            left = [d for d in earlier if d not in taken]
            if left:
                v.drafts = drafts = [left[-1]]
        taken.update(drafts)
        if not drafts and v.what not in ("proposal", "motion"):
            v.problems.append("no document named for what was put to the vote")
        if lists and not v.complete:
            v.problems.append("the lists printed do not add up to the totals announced: " + ", ".join(
                f"{k} {len(lists.get(k, []))}/{n}" for k, n in totals.items()))
        out.append(v)
        prev = (ci, end)
    return out


def _announcement(text: str, start: int, end: int, sentences: int = 2) -> str:
    """The announcement in the record's words: the sentence giving the votes, and the one after it giving the
    result."""
    begin = max(text.rfind(". ", 0, start) + 2, text.rfind("\n\n", 0, start) + 2, 0)
    if text.rfind(". ", 0, start) < 0 and text.rfind("\n\n", 0, start) < 0:
        begin = 0
    stop = end
    for _ in range(sentences):
        m = re.compile(r"\.(?=\s|$)").search(text, stop)
        if not m:
            stop = len(text)
            break
        stop = m.end()
    return " ".join(text[begin:stop].split())


def _what(word: str) -> str:
    w = " ".join(word.lower().split()).replace("oral ", "")
    w = w[:-1] if w.endswith("amendments") else w
    return {"amendment": "proposed amendment", "draft amendment": "proposed amendment", "motion": "proposal",
            "draft text": "draft resolution", "text": "draft resolution"}.get(w, w)


def _letter(c: Chunk) -> bool:
    """A letter from the President giving the result of a vote taken by letter (2020 and 2021)."""
    return not c.chair and bool(re.search(r"\bvoting procedure\b|\bwritten procedure\b", c.text[:2000], re.I)) and \
        bool(RECEIVED.search(c.text[:4000]))


def resolve_lists(v: Vote, resolve: Callable[[str], str | None]) -> tuple[dict[str, list[str]], list[str]]:
    """Each list as country codes; and the names that matched no country (reported, never guessed)."""
    out: dict[str, list[str]] = {}
    unknown: list[str] = []
    for k, listed in v.lists.items():
        codes = []
        for name in listed:
            code = resolve(name)
            if code:
                codes.append(code)
            else:
                unknown.append(name)
        out[k] = codes
    return out, unknown
