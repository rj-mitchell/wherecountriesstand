"""Write a made-up topic briefing into a data folder's state database, so that a site started with no data has a
topic page to check (CI's accessibility check, decision 27). Usage: python scripts/sample_topic.py /tmp/wcs-data

It is the same made-up briefing scripts/a11y.mjs opens as a reader's saved briefing, with each kind of mark: a record
quotation found word for word, one shown as its source's by the display rule, a sentence with no source, one whose
only citation failed the check, a quotation not found word for word and a judgemental word. Its words must match
the constants at the top of scripts/a11y.mjs, which checks they are all marked on the topic's page too
(tests/test_topics.py checks the two agree). It is kept as the newest version of the first topic in
config/topics.toml, labelled as a sample, and never goes near the live site's data.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from wts_core.usage import BriefingArchive, token_id

from wcs.config import load_settings
from wcs.state import open_state
from wcs.topics import keep_version, load_topics, topics_file

ID = "a11ytopic01"
CHECKED = "adaptation finance must reach the most vulnerable"
BESIDE = "climate finance is far too slow"
UNSUPPORTED = "Most delegations agreed."
INVALID = "Kenya called for a fund in 2019."
MISQUOTE = "we will never accept this"
LOADED = "regime"
LEAD = f'Several States spoke of slow finance, as when Kenya said it was "{BESIDE}"'
SOURCE = {"n": 1, "kind": "record", "title": "Mr. Kimani (Kenya)", "url": "https://undocs.org/en/S/PV.9999",
          "record_id": "sc:S/PV.9999:0003", "date": "2026-03-12", "date_precision": "day", "body": "Security Council",
          "meeting": "9999th meeting", "symbol": "S/PV.9999", "agenda": "Maintenance of international peace and security",
          "speaker_name": "Mr. Kimani", "country_code": "KEN", "country_name": "Kenya", "entity_kind": "member_state",
          "group": "African States",
          "text": f"I thank the President. In our view {CHECKED}, and {BESIDE} for the small island States and the "
                  "least developed countries. We call for a fund that answers to them."}


def events(question: str) -> list[dict]:
    segments = [
        {"text": f'Kenya said "{CHECKED}"', "sources": [1], "cited": [], "status": "cited"},
        {"text": ". ", "sources": [], "cited": [], "status": "connective"},
        {"text": LEAD[:LEAD.index('"')], "sources": [], "cited": [], "status": "unsupported"},
        {"text": f'"{BESIDE}"', "sources": [1], "cited": [], "status": "cited", "beside": True},
        {"text": " in March 2026", "sources": [1], "cited": [], "status": "cited"},
        {"text": ". ", "sources": [], "cited": [], "status": "connective"},
        {"text": UNSUPPORTED, "sources": [], "cited": [], "status": "unsupported"},
        {"text": " ", "sources": [], "cited": [], "status": "connective"},
        {"text": INVALID, "sources": [], "cited": [], "status": "invalid"},
        {"text": f' It said "{MISQUOTE}" of the proposal', "sources": [1], "cited": [], "status": "cited",
         "misquotes": [{"start": 9, "end": 36, "text": MISQUOTE}]},
        {"text": ".", "sources": [], "cited": [], "status": "connective"},
        {"text": f" The {LOADED} said so.", "sources": [], "cited": [], "status": "unsupported",
         "loaded": [{"start": 5, "end": 5 + len(LOADED), "word": LOADED}]},
    ]
    return [
        {"type": "plan", "plan": {"question": question, "interpretation": "A sample briefing for the accessibility "
                                  "check.", "filters": {"sources": ["sc"], "date_from": "2022-01-01",
                                                        "countries": [], "groups": []}}, "usage": None},
        {"type": "sources", "sources": [SOURCE], "candidates": 1, "semantic": False,
         "evidence": {"records": 1, "countries": 1, "meetings": 1, "date_from": "2026-03-12",
                      "date_to": "2026-03-12", "votes": 0}},
        {"type": "text", "text": "".join(s["text"] for s in segments)},
        {"type": "final", "briefing_id": ID, "usage": None, "answer": {
            "segments": segments, "no_records": False, "citations_total": 4, "citations_invalid": 1,
            "grounded_ratio": 0.6, "unsupported_segments": 3, "loaded_words": 1, "quotes_total": 3,
            "quotes_unverified": 1}},
    ]


def write(data: Path) -> str:
    os.environ["WCS_DATA_DIR"] = str(data)
    settings = load_settings()
    topic = load_topics(topics_file(settings))[0]
    db = open_state(settings)
    archive = BriefingArchive(db, new_id=lambda: token_id(8))
    saved = archive.get(ID) or archive.save(topic.question, {"mode": "summary", "topic": topic.slug},
                                            events(topic.question), id=ID, extra={"topic": topic.slug, "sample": True})
    keep_version(db, topic.slug, saved.id, saved.created_at, "2026-03-12")
    return topic.path


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: python scripts/sample_topic.py <data folder>")
    print(f"wrote a sample briefing for {write(Path(sys.argv[1]))}")
