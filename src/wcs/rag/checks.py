"""The checks every briefing passes through before a reader sees it, with this site's own words and rules.

The checks themselves are the Where They Stand sites' shared ones (wts-core, ``wts_core.checks``): every citation
is looked up in its source, a sentence with no citation is marked as a point to check, a quotation is looked for
word for word, American spellings in the site's own words are made British, and judgemental words in the site's own
voice are highlighted (never hidden, and never inside a quotation). What is this site's own is passed in here:

* the words a "no records" answer opens with (``rag/prompts.py`` tells the model to use them);
* the judgemental words: the family's common list, and this site's labels for governments and blocs
  (docs/DECISIONS.md, 7): "regime" (say "government"), "rogue state", "puppet", and "the West" as a bloc, which is
  matched with its capital letter so "the west of the country" is not marked;
* what counts as a note about the evidence, which is not a claim to check. This site has the wider of the family's
  two forms, as it always has: a sentence saying something "was supplied" or "were retrieved" counts as one;
* curly quotation marks: this site shows its briefings with them, put in last, once the checks have read the text
  as the model wrote it.
"""

from __future__ import annotations

import re
from typing import Any

from wts_core.checks import (
    COMMON_HONORIFICS,
    COMMON_LOADED_WORDS,
    EVIDENCE_NOTES_WIDE,
    LoadedWords,
    Verifier,
    check_briefing,
    curl_events,
)
from wts_core.events import Answer, Event

from wcs.rag.prompts import NO_RECORDS

LOADED_WORDS = LoadedWords(re.compile(
    rf"\b({COMMON_LOADED_WORDS}|"
    r"regime|regimes|rogue state|rogue states|puppet government|puppet governments|puppet state|puppet states)\b"
    # the West as a bloc, only with its capital letter
    r"|\b(?-i:the West)\b", re.IGNORECASE))

VERIFIER = Verifier(no_records=NO_RECORDS, evidence_notes=EVIDENCE_NOTES_WIDE, honorifics=COMMON_HONORIFICS)


def checked(blocks: list[dict[str, Any]], sources: list[Any]) -> Answer:
    """The model's finished blocks as the briefing a reader is sent: every check, in the family's one order
    (citations, openers about the records dropped, spelling, judgemental words, quotations), then curly quotes."""
    return check_briefing(blocks, sources, VERIFIER, LOADED_WORDS, curled=True)


def rechecked(events: list[Event]) -> list[Event]:
    """A saved or cached briefing as shown today: its points to check and its judgemental-word marks judged again
    by the current rules, so a mark since found to be wrong is not shown. Nothing is ever newly flagged, and a
    briefing written under today's rules is left as it is. Its text is read with straight quotes, as the checks
    first read it, and comes back as it was saved; then its quotes are curled, as a new briefing's are, for one
    saved before they were."""
    return curl_events([{**e, "answer": LOADED_WORDS.still_loaded(VERIFIER.recheck(e["answer"], curled=True))}
                        if e.get("type") == "final" and isinstance(e.get("answer"), dict) else e for e in events])
