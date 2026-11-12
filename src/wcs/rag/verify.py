"""Post-generation verification of a cited answer.

Claude's citations API returns, for each cited span, the exact ``cited_text``
and the index of the search result it came from. This module re-checks every
citation against our own copy of the source text and classifies every
uncited span, so the UI can show the reader exactly what is and is not
supported. Unsupported text is never silently passed off as sourced.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from wcs.rag.prompts import NO_RECORDS
from wcs.rag.sources import Source

_WS = re.compile(r"\s+")
_CONNECTIVE_MAX = 60


def _norm(text: str) -> str:
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return _WS.sub(" ", text).strip().lower()


def _compact(text: str) -> str:
    """For checking a citation: the characters without any spacing. A citation that runs across two paragraphs of
    a source comes back with them joined directly ("and(b) implementation"), not with the space between them."""
    return _WS.sub("", _norm(text))


@dataclass
class Segment:
    text: str
    sources: list[int] = field(default_factory=list)       # 1-based source numbers
    cited: list[dict[str, Any]] = field(default_factory=list)
    status: str = "cited"                                   # cited | connective | unsupported | invalid
    loaded: list[dict[str, Any]] = field(default_factory=list)  # judgemental words outside quotes (neutral.py)
    misquotes: list[dict[str, Any]] = field(default_factory=list)  # quotations not found in their sources

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "sources": self.sources, "cited": self.cited, "status": self.status,
                "loaded": self.loaded, "misquotes": self.misquotes}


@dataclass
class Verification:
    segments: list[Segment]
    no_records: bool
    citations_total: int
    citations_invalid: int
    unsupported_chars: int
    cited_chars: int
    quotes_total: int = 0
    quotes_unverified: int = 0

    @property
    def grounded_ratio(self) -> float:
        total = self.cited_chars + self.unsupported_chars
        return 1.0 if total == 0 else self.cited_chars / total

    def markdown(self) -> str:
        """Answer text with [Source n] markers after each cited span."""
        out = []
        for s in self.segments:
            out.append(s.text)
            if s.sources:
                out.append(" " + "".join(f"[Source {n}]" for n in s.sources))
        return "".join(out)

    def to_dict(self) -> dict[str, Any]:
        return {
            "segments": [s.to_dict() for s in self.segments],
            "markdown": self.markdown(),
            "no_records": self.no_records,
            "citations_total": self.citations_total,
            "citations_invalid": self.citations_invalid,
            "grounded_ratio": round(self.grounded_ratio, 3),
            "unsupported_segments": sum(1 for s in self.segments if s.status == "unsupported"),
            "loaded_words": sum(len(s.loaded) for s in self.segments),
            "quotes_total": self.quotes_total,
            "quotes_unverified": self.quotes_unverified,
        }


# the date of the contribution just cited, after its citation: "[Source 2] (16 September 2025).",
# "on 12 November 2025.", "(written answer, 12 October 2026)."
_DATE_ONLY = re.compile(r"^\W*(?:on\s+)?\(?\s*(?:(?:written|oral)\s+(?:answer|question|statement)s?,?\s*)?"
                        r"(?:\d{1,2}\s+)?(?:[A-Z][a-z]+\s+)?\d{4}\s*\)?\W*$")


def _is_connective(text: str, short: bool = True) -> bool:
    """A date, a heading, a line ending in a colon or, with ``short``, a join or short transition."""
    t = text.strip()
    if not t or _DATE_ONLY.match(t):
        return True
    if t.startswith("#") or t.endswith(":"):
        return True
    # bullet markers, joins between cited spans, short transitions
    stripped = re.sub(r"^[\s\-*•\d.)]+", "", t)
    return short and len(stripped) <= _CONNECTIVE_MAX and not re.search(r"\d{4}|£|%", stripped)


def recheck_flags(answer: dict[str, Any] | None) -> dict[str, Any] | None:
    """A saved briefing's points to check, judged again by the current rules: a passage since recognised as a date,
    a label, a lead-in or a note about the evidence is no longer one. Being short is never reason enough: a flagged
    sentence may be a claim that verify() split off a lead-in. Nothing is ever newly flagged."""
    segs = (answer or {}).get("segments")
    if not segs:
        return answer

    def cleared(i: int) -> bool:
        # as in verify(): short uncited pieces of a lead-in do not stop it leading in to the citation after them
        j = i + 1
        while j < len(segs) and segs[j].get("status") != "cited" and _is_lead_in_piece(segs[j].get("text") or ""):
            j += 1
        nxt = j < len(segs) and segs[j].get("status") == "cited"
        # flagged=True: verify() marks a whole sentence split off a lead-in as a claim however short it is, and that
        # is not known here, so a point to check is never cleared only for being short
        return all(x.status == "connective" for x in _classify_uncited(segs[i]["text"], nxt, flagged=True))

    fixed = {i for i, x in enumerate(segs) if x.get("status") == "unsupported" and cleared(i)}
    if not fixed:
        return answer
    segs = [{**x, "status": "connective"} if i in fixed else x for i, x in enumerate(segs)]
    cited = sum(len(x["text"]) for x in segs if x.get("status") == "cited")
    unsupported = sum(len(x["text"]) for x in segs if x.get("status") in ("unsupported", "invalid"))
    return {**answer, "segments": segs,
            "unsupported_segments": sum(1 for x in segs if x.get("status") == "unsupported"),
            "grounded_ratio": round(1.0 if not cited + unsupported else cited / (cited + unsupported), 3)}


_HONORIFIC_DOT = re.compile(r"\b(Mr|Mrs|Ms|Dr|Prof|Sr|Jr|St)\.")


def _is_lead_in_piece(text: str) -> bool:
    """A short piece of a lead-in ("Delattre said "): no sentence ends in it."""
    t = _HONORIFIC_DOT.sub(lambda m: m.group(1), text).strip()
    return bool(t) and len(t) <= 80 and "\n" not in t and not re.search(r"[.!?][\"')\]]*$", t) and \
        not _SENTENCE_END.search(t)


_LABEL = re.compile(r"^\s*(#+ .*|\*\*[^*]+\*\*:?)\s*$")
# statements about the evidence itself (what was or was not retrieved), not about what anyone said
_EVIDENCE_NOTE = re.compile(
    r"\b(records? (retrieved|supplied|shown)|(retrieved|supplied) (records|results|sources)|in the records|"
    r"no (division|voting) records|no (recorded[- ]vote|vote|voting) (data|records|tables)|"
    r"(votes?|data) (were|was) (not )?supplied|\b(are|were|is|was) (not )?(supplied|retrieved)\b|evidence (base|is|set)|gaps?\b|caveats?\b|cannot be (reported|attributed)|"
    r"(were|was) not relevant|been excluded|the records (are|were|contain|include|cover)|little (was|is) (found|said)|"
    r"nothing (was|is) found|no stated position|written questions only|"
    r"decides? (many|most|some)? ?(of )?(these |the |such )?(subjects|matters|areas|policies) for ((its|their) own "
    r"nations?|wales|scotland|northern ireland)|(is|are) (a )?separate (parliament|legislature)s?\b|decides? (different|separate|their own) "
    r"(matters|subjects|policies)\b|"
    r"^\W*(about the evidence|notes?)\b\W*:)", re.I)
# a note about the evidence that reports what someone said ("Smith said", "he argued") is a claim, not a note;
# "what was said" or "nothing said by Benn" is not
_SOMEONE_SAID = re.compile(r"\b(?:[A-Z][\w'-]+|[Hh]e|[Ss]he|[Tt]hey)\s+(?:also\s+)?"
                           r"(?:said|argued|claimed|stated|told|described|called)\b")
_SENTENCE_END = re.compile(r"[.!?][\"')\]]*\s+\S")
_ATTRIBUTION_MAX = 220


def _classify_uncited(text: str, next_is_cited: bool, flagged: bool = False) -> list[Segment]:
    """Split an uncited block into lines and label each one.

    * headings, bold group labels, bullets and joins are ``connective``;
    * the lead-in to a cited quotation ("- Keir Mather (Selby), 10 August 2026, said ") is ``connective``
      when the very next span is cited and the lead-in states nothing on its own;
    * notes about the evidence itself ("No division records were supplied") are ``connective``;
    * anything else (a claim with no citation) is ``unsupported``.

    ``flagged`` is for judging again a passage already marked as a point to check (``recheck_flags``): there, being
    short does not make it a join.
    """
    # an honorific's full stop ("Mr. Delattre") ends no sentence: the checks read a copy with it masked (the same
    # length, so positions carry over), and the text kept is the original
    masked = _HONORIFIC_DOT.sub(lambda m: m.group(1) + "\u2024", text)
    lines = text.splitlines(keepends=True)
    mlines = masked.splitlines(keepends=True)
    claims: set[int] = set()  # whole sentences split off a lead-in: never a mere transition, however short
    if next_is_cited and lines:
        # "Some answers said the data was not held. Stephen Kinnock said on 24 March that " + quotation: the
        # last sentence leads in to the citation that follows; only the sentence before it is a claim
        ends = list(_SENTENCE_END.finditer(mlines[-1]))
        if ends:
            cut = ends[-1].end() - 1
            tail = mlines[-1][cut:]
            if len(tail.strip()) <= _ATTRIBUTION_MAX and not tail.rstrip().endswith("."):
                lines[-1:] = [lines[-1][:cut], lines[-1][cut:]]
                mlines[-1:] = [mlines[-1][:cut], tail]
                if re.search(r"\w", lines[-2]):  # a bare full stop closing the citation before is no claim
                    claims.add(len(lines) - 2)
    out: list[Segment] = []
    for i, line in enumerate(lines):
        last = i == len(lines) - 1
        body = re.sub(r"^[\s\-*•\d.)]+", "", mlines[i]).strip()
        lead_in = (last and next_is_cited and len(body) <= _ATTRIBUTION_MAX
                   and not _SENTENCE_END.search(body.rstrip()) and not body.endswith("."))
        if ((_is_connective(line, short=not flagged) and i not in claims) or _LABEL.match(line) or lead_in
                or (_EVIDENCE_NOTE.search(body) and not _SOMEONE_SAID.search(body))):
            status = "connective"
        else:
            status = "unsupported"
        if out and out[-1].status == status:
            out[-1].text += line
        else:
            out.append(Segment(line, status=status))
    return out or [Segment(text, status="connective")]


# "Across the records retrieved, ..." and the like: an opener about the records rather than the substance
_PREAMBLE = re.compile(
    r"(^|\n|[.!?] )([-*•]\s+)?(?:across|in|from|based on|on the basis of|according to|within)\s+"
    r"(?:all\s+)?(?:the\s+|these\s+)?(?:retrieved\s+|supplied\s+)?(?:records?|evidence|search results|results|"
    r"sources|contributions)(?:\s+(?:retrieved|supplied|provided|shown|available|here|above))?\s*[,:]\s*"
    r"([a-z]?)", re.I)


def drop_preambles(segments: list[Segment]) -> int:
    """Remove openers that talk about the records instead of the substance ("Across the records retrieved, most
    ..." becomes "Most ..."); returns how many were removed."""
    removed = 0
    for seg in segments:
        new, n = _PREAMBLE.subn(lambda m: m.group(1) + (m.group(2) or "") + m.group(3).upper(), seg.text)
        if n:
            seg.text, removed = new, removed + n
    return removed


def verify(blocks: list[dict[str, Any]], sources: list[Source]) -> Verification:
    """``blocks``: text blocks as returned by the API (``text`` and optional ``citations``)."""
    by_index = {s.n - 1: s for s in sources}
    segments: list[Segment] = []
    total = invalid = 0
    for i, b in enumerate(blocks):
        text = b.get("text") or ""
        cites = b.get("citations") or []
        if not cites:
            # a lead-in can come in pieces ("..., as when France's Mr. " + "Delattre said " + the quotation): short
            # uncited pieces in between do not stop it leading in to the citation after them
            j = i + 1
            while j < len(blocks) and not blocks[j].get("citations") and _is_lead_in_piece(blocks[j].get("text") or ""):
                j += 1
            next_is_cited = j < len(blocks) and bool(blocks[j].get("citations"))
            segments.extend(_classify_uncited(text, next_is_cited))
            continue
        nums, kept = [], []
        for c in cites:
            total += 1
            idx = c.get("search_result_index", c.get("document_index"))
            src = by_index.get(idx)
            cited_text = c.get("cited_text") or ""
            if src is None or _compact(cited_text) not in _compact("\n\n".join(src.blocks)):
                invalid += 1
                continue
            if src.n not in nums:
                nums.append(src.n)
            kept.append({"source": src.n, "cited_text": cited_text})
        segments.append(Segment(text, nums, kept, "cited" if nums else "invalid"))
    full = "".join(s.text for s in segments).strip()
    unsupported = sum(len(s.text) for s in segments if s.status in ("unsupported", "invalid"))
    cited = sum(len(s.text) for s in segments if s.status == "cited")
    return Verification(segments, full.startswith(NO_RECORDS), total, invalid, unsupported, cited)


# ---- quotations ------------------------------------------------------------------------------------------------
# Words the briefing puts in quotation marks must appear, word for word, in the record it attributes them to.
# A citation proves its own cited passage exists; this also checks the quotation written around it.

_QUOTE = re.compile(r'["\u201c]([^"\u201c\u201d\n]{6,}?)["\u201d]|\u2018([^\u2018\u2019\n]{6,}?)\u2019(?![a-z])')
# gaps a quotation may contain: an ellipsis, or an editorial insertion in square brackets ("welcome[d]")
_ELLIPSIS = re.compile(r"\s*(?:\.\.\.|\u2026|\[[^\]]{0,40}\])\s*")
_WORDS = re.compile(r"[^a-z0-9]+")


_REPEATED = re.compile(r"\b(\w+)(?: \1\b)+")


def _words(text: str) -> str:
    """Lower-case words only: punctuation, quote marks and spacing are not what a quotation is checked on, nor a
    word printed twice in a row in the record ("the positioning of of that company")."""
    words = _WORDS.sub(" ", text.lower().replace("\u2019", "'").replace("'", "")).strip()
    return " " + _REPEATED.sub(r"\1", words) + " "


def _found(quote: str, haystack: str) -> bool:
    parts = [p for p in _ELLIPSIS.split(quote) if len(_words(p).split()) >= 2]
    # the last word may run on in the record ("Gentleman" quoted from "Gentleman's")
    return all(_words(p).rstrip() in haystack for p in parts) if parts else True


def check_quotes(report: Verification, sources: list[Source]) -> int:
    """Mark quotations that cannot be found in the sources cited for them; returns how many were not found."""
    # the speech and its title line (debate headings are quoted too)
    texts = {s.n: _words("\n\n".join([s.title, str(s.meta.get("heading") or ""), *s.blocks])) for s in sources}
    everything = " ".join(texts.values())
    starts, pos = [], 0
    for seg in report.segments:
        starts.append(pos)
        pos += len(seg.text)
    full = "".join(seg.text for seg in report.segments)
    total = bad = 0
    for m in _QUOTE.finditer(full):
        quote = m.group(1) or m.group(2)
        if len(_words(quote).split()) < 3:  # a word or two in quotes is a term, not a quotation
            continue
        total += 1
        a, b = m.span()
        # the sources cited for the quotation: segments it overlaps, or the citation straight after it
        cited: set[int] = set()
        for i, seg in enumerate(report.segments):
            s0, s1 = starts[i], starts[i] + len(seg.text)
            if s0 < b and a < s1 or (s0 >= b and s0 - b <= 3 and seg.sources):
                cited.update(seg.sources)
        haystack = " ".join(texts[n] for n in cited if n in texts) or everything
        if _found(quote, haystack):
            continue
        bad += 1
        for i, seg in enumerate(report.segments):
            s0, s1 = starts[i], starts[i] + len(seg.text)
            if s0 < b and a < s1:
                seg.misquotes.append({"start": max(a, s0) - s0, "end": min(b, s1) - s0, "text": quote})
    report.quotes_total, report.quotes_unverified = total, bad
    return bad
