"""British spelling for model-written text: a deterministic safety net behind the prompt rule.

Only the model's own words are changed. Text inside quotation marks is quoted parliamentary material and is
never touched, even when a quotation runs across several answer segments. The word list is deliberately
conservative: only spellings that are unambiguous in British English are converted (``practice``/``practise``,
``license``/``licence``, ``meter``/``metre``, ``draft``, ``judgment`` and similar are left alone). Capitalised
words in mid-sentence are treated as proper names (World Health Organization, Australian Labor Party) and kept.
"""

from __future__ import annotations

import re
from typing import Any

# -ize words that must not become -ise: words built on size, seize or prize, and a few others. An explicit
# list, because endings mislead ("emphasized" ends in "sized").
_IZE_KEEP = {
    f"{stem}{ending}"
    for stem in ("size", "oversize", "outsize", "downsize", "resize", "upsize", "supersize", "capsize")
    for ending in ("", "s", "d")
} | {"sizing", "downsizing", "resizing", "capsizing", "seize", "seizes", "seized", "seizing", "seizure", "prize",
     "prizes", "prized", "prizing", "maize", "baize"}

_WORDS = {
    # -our
    "color": "colour", "colors": "colours", "colored": "coloured", "favor": "favour", "favors": "favours",
    "favored": "favoured", "favorable": "favourable", "favorably": "favourably", "favorite": "favourite",
    "honor": "honour", "honors": "honours", "honored": "honoured", "honorable": "honourable",
    "labor": "labour", "labors": "labours", "neighbor": "neighbour", "neighbors": "neighbours",
    "neighborhood": "neighbourhood", "neighborhoods": "neighbourhoods", "neighboring": "neighbouring",
    "behavior": "behaviour", "behaviors": "behaviours", "behavioral": "behavioural", "harbor": "harbour",
    "harbors": "harbours", "rumor": "rumour", "rumors": "rumours", "humor": "humour", "vigor": "vigour",
    "endeavor": "endeavour", "endeavors": "endeavours", "endeavored": "endeavoured",
    # -re, -ence
    "center": "centre", "centers": "centres", "centered": "centred", "theater": "theatre", "theaters": "theatres",
    "fiber": "fibre", "fibers": "fibres", "liter": "litre", "liters": "litres", "caliber": "calibre",
    "defense": "defence", "defenses": "defences", "offense": "offence", "offenses": "offences",
    "pretense": "pretence",
    # doubled l, single l
    "traveled": "travelled", "traveling": "travelling", "traveler": "traveller", "travelers": "travellers",
    "canceled": "cancelled", "canceling": "cancelling", "labeled": "labelled", "labeling": "labelling",
    "modeled": "modelled", "modeling": "modelling", "fueled": "fuelled", "fueling": "fuelling",
    "signaled": "signalled", "signaling": "signalling", "totaled": "totalled", "counseling": "counselling",
    "counselor": "counsellor", "fulfill": "fulfil", "fulfills": "fulfils", "fulfillment": "fulfilment",
    "enrollment": "enrolment", "installment": "instalment", "installments": "instalments",
    "skillful": "skilful", "willful": "wilful",
    # other
    "program": "programme", "programs": "programmes", "percent": "per cent", "toward": "towards",
    "aging": "ageing", "gray": "grey", "catalog": "catalogue", "dialog": "dialogue", "maneuver": "manoeuvre",
    "maneuvers": "manoeuvres", "pediatric": "paediatric", "orthopedic": "orthopaedic", "jewelry": "jewellery",
    "mold": "mould", "airplane": "aeroplane", "airplanes": "aeroplanes", "aluminum": "aluminium",
}

_WORD = re.compile(r"[A-Za-z]+")
_IZE = re.compile(r"^([a-z]{2,}?)(i|y)z(e|es|ed|ing|ation|ations|er|ers)$")


def _at_sentence_start(before: str) -> bool:
    """True when the text before a word ends a sentence, a line, a heading marker or a bullet."""
    stripped = before.rstrip(" \t*#-•>")
    return not stripped or stripped[-1] in ".!?:\n"


def _british(word: str) -> str | None:
    lower = word.lower()
    if lower in _WORDS:
        new = _WORDS[lower]
    elif lower not in _IZE_KEEP and (m := _IZE.match(lower)):
        new = f"{m.group(1)}{m.group(2)}s{m.group(3)}"
    else:
        return None
    if word.isupper() and len(word) > 1:
        return new.upper()
    return new[0].upper() + new[1:] if word[0].isupper() else new


def _convert_run(text: str, prefix: str) -> str:
    """Convert words in a stretch of the model's own words. ``prefix`` is the text just before it."""
    out, last = [], 0
    for m in _WORD.finditer(text):
        word = m.group(0)
        new = _british(word)
        if new is None:
            continue
        before = prefix + text[:m.start()]
        if word[0].isupper() and not word.isupper() and not _at_sentence_start(before[-80:]):
            continue  # a capitalised word mid-sentence is a name: leave it
        if word.lower() == "program" and before.lower().endswith("computer "):
            continue
        out.append(text[last:m.start()])
        out.append(new)
        last = m.end()
    out.append(text[last:])
    return "".join(out)


class _QuoteState:
    """Tracks whether the next character is inside a quotation, across consecutive pieces of text."""

    def __init__(self) -> None:
        self.inside = False
        self.seen = ""

    def convert(self, text: str) -> str:
        out, run = [], []

        def flush() -> None:
            if run:
                chunk = "".join(run)
                out.append(_convert_run(chunk, self.seen))
                self.seen = (self.seen + chunk)[-80:]
                run.clear()

        for ch in text:
            opening = ch == "“" or (ch == '"' and not self.inside)
            closing = ch == "”" or (ch == '"' and self.inside)
            if self.inside:
                out.append(ch)
                self.seen = (self.seen + ch)[-80:]
                if closing:
                    self.inside = False
            elif opening:
                flush()
                out.append(ch)
                self.seen = (self.seen + ch)[-80:]
                self.inside = True
            else:
                run.append(ch)
        flush()
        return "".join(out)


def outside_quotes(segments: list[Any]) -> list[tuple[int, int, str]]:
    """(segment index, offset, text) for every stretch of the model's own words, outside quotation marks."""
    out: list[tuple[int, int, str]] = []
    inside = False
    for i, seg in enumerate(segments):
        start = None
        for j, ch in enumerate(seg.text):
            opening = ch == "“" or (ch == '"' and not inside)
            closing = ch == "”" or (ch == '"' and inside)
            if inside:
                if closing:
                    inside = False
                continue
            if opening:
                if start is not None:
                    out.append((i, start, seg.text[start:j]))
                    start = None
                inside = True
            elif start is None:
                start = j
        if start is not None:
            out.append((i, start, seg.text[start:]))
    return out


def to_british(text: str) -> str:
    """British spelling for one piece of model-written text (quotations untouched)."""
    return _QuoteState().convert(text) if text else text


def britishise_segments(segments: list[Any]) -> None:
    """Convert verified answer segments in place, carrying quotation state from one segment to the next."""
    state = _QuoteState()
    for s in segments:
        s.text = state.convert(s.text)
