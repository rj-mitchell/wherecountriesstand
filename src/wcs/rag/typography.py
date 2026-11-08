"""Curly quotation marks for the model's text, to match the site's typography (the records themselves print “ ” ‘ ’).

The model writes straight quotes ("..." and '...'). Each one is turned the right way by what comes before it: after
the start, a space, an opening bracket, a dash or another opening quote it opens; otherwise it closes. A straight
apostrophe in a word ("don't", "States' rights", "the '90s") becomes ’. Quotes inside a web address, `code` or an
HTML tag are left alone, since changing them would change what they say. Every mark is replaced by one character,
so the positions of the marks on a briefing's segments (judgemental words, misquotations) stay where they were.
"""

from __future__ import annotations

import re
from typing import Any

# web addresses, `code` and HTML tags (with their attributes): their quotes are part of what they say
_PROTECTED = re.compile(r"(?:https?://|www\.)\S+|`[^`\n]*`|<[A-Za-z/!][^<>\n]*>")
_OPENS_AFTER = set(" \t\n\r ([{—–-/“‘")
_DECADE = re.compile(r"'\d\d(?:s\b|\b)")  # "the '90s", "class of '68"


def curl_quotes(text: str) -> str:
    """``text`` with its straight quotes and apostrophes curled."""
    if "'" not in text and '"' not in text:
        return text
    protected = [False] * len(text)
    for m in _PROTECTED.finditer(text):
        protected[m.start():m.end()] = [True] * (m.end() - m.start())
    out = list(text)
    for i, ch in enumerate(text):
        if ch not in "'\"" or protected[i]:
            continue
        j = i - 1
        while j >= 0 and text[j] == "*":  # "**" around bold text is not a character of the sentence
            j -= 1
        before = out[j] if j >= 0 else " "
        opens = before in _OPENS_AFTER
        if ch == '"':
            out[i] = "“" if opens and text[i + 1:i + 2] not in ("", " ", "\n") else "”"
        elif opens and not _DECADE.match(text, i) and text[i + 1:i + 2] not in ("", " ", "\n"):
            out[i] = "‘"
        else:
            out[i] = "’"
    return "".join(out)


def curl_answer(answer: dict[str, Any] | None) -> dict[str, Any] | None:
    """A briefing with its quotes curled. The segments are curled as one text, so a quotation that runs across
    segments opens and closes the right way."""
    segs = (answer or {}).get("segments")
    if not segs:
        return answer
    full = curl_quotes("".join(s.get("text") or "" for s in segs))
    out, pos = [], 0
    for s in segs:
        n = len(s.get("text") or "")
        out.append({**s, "text": full[pos:pos + n]} if n else s)
        pos += n
    curled = {**answer, "segments": out}
    if isinstance(answer.get("markdown"), str):
        curled["markdown"] = curl_quotes(answer["markdown"])
    return curled


def curl_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A briefing's events with the model's text curled: the plan's interpretation and the briefing."""
    return [curl_event(e) for e in events]


def curl_event(e: dict[str, Any]) -> dict[str, Any]:
    if e.get("type") == "final" and isinstance(e.get("answer"), dict):
        return {**e, "answer": curl_answer(e["answer"])}
    if e.get("type") == "plan" and isinstance(e.get("plan"), dict) and e["plan"].get("interpretation"):
        return {**e, "plan": {**e["plan"], "interpretation": curl_quotes(e["plan"]["interpretation"])}}
    return e
