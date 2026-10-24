"""Flag loaded or judgemental words in the model's own voice (a safety net behind prompt rule 9).

Briefings must report in neutral words; loaded words may appear only inside a quotation from the speaker who
used them. Flags are shown to the reader, like unsupported text: the words stay, highlighted, so nothing is
silently rewritten. Ambiguous words with common neutral senses ("claims" for benefits, "extreme weather",
"lie ahead") are deliberately not on the list, and "claimed" counts only as a verb of saying.

Where Countries Stand adds labels for governments and blocs (docs/DECISIONS.md, 7): "regime" (say
"government"), "rogue state", "puppet", and "the West" as a bloc, which is matched with its capital letter so
"the west of the country" is not flagged.
"""

from __future__ import annotations

import re
from typing import Any

from wcs.rag.british import outside_quotes

LOADED = re.compile(
    r"\b(lied|liar|liars|misled|misleading|dishonest|dishonesty|hypocrisy|hypocrite|hypocrites|hypocritical|"
    r"u-turn|u-turns|u-turned|backtracked|backtracking|flip-flopped|flip-flop|flip-flops|admitted|conceded|"
    # "claimed" only as a verb of saying ("claimed that", "claimed the plan had failed"), not of benefits or
    # savings claimed for something, which is neutral
    r"claimed(?=\s*(?:that|to|the|a|an|it|there|this|these|he|she|they|we)\b|\s*[,:\"“]|\s*$)|"
    r"attacked|slammed|blasted|dodged|evaded|evasive|refused to answer|gaffe|gaffes|scandal|scandals|"
    r"scandalous|disgraceful|shameful|incompetent|incompetence|"
    r"regime|regimes|rogue state|rogue states|puppet government|puppet governments|puppet state|puppet states)\b"
    # the West as a bloc, only with its capital letter
    r"|\b(?-i:the West)\b",
    re.IGNORECASE,
)


def still_loaded(answer: dict[str, Any] | None) -> dict[str, Any] | None:
    """A saved briefing's judgemental-word marks, judged again by the current list (a word since recognised as
    neutral in its use, such as "the benefits claimed", is no longer marked). Nothing is ever newly marked."""
    segs = (answer or {}).get("segments")
    if not segs or not any(x.get("loaded") for x in segs):
        return answer

    def keep(seg: dict[str, Any], mark: dict[str, Any]) -> bool:
        m = LOADED.match(seg["text"], mark["start"])
        return bool(m) and m.end() == mark["end"]

    segs = [{**x, "loaded": [m for m in x["loaded"] if keep(x, m)]} if x.get("loaded") else x for x in segs]
    return {**answer, "segments": segs, "loaded_words": sum(len(x.get("loaded") or []) for x in segs)}


def flag_loaded(segments: list[Any]) -> int:
    """Set ``segment.loaded`` to the [start, end] spans of loaded words outside quotations; return the count."""
    total = 0
    for i, start, text in outside_quotes(segments):
        seg = segments[i]
        for m in LOADED.finditer(text):
            seg.loaded.append({"start": start + m.start(), "end": start + m.end(), "word": m.group(0)})
            total += 1
    return total
