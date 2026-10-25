"""Turn ranked records and votes into numbered, citable sources.

Each source's title tells the model who spoke and in what capacity, using only facts from the record and the
reference data: the speaker as printed, the country's UN name on the day, the groups it belonged to, whether the
statement was made on behalf of a group, the language spoken and the meeting.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from wcs.rank.evidence import GROUP_LABELS, evidence_group
from wcs.reference.roles import ROLE_LABEL

_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"“(])")
BLOCK_WORDS = 70
TITLE_GROUPS = ("p5", "council_elected", "council_guest", "eu", "nato", "au", "oic", "nam", "g77", "sids", "ldc")
VOTE_LABEL = {"Y": "In favour", "N": "Against", "A": "Abstaining", "X": "Not voting"}


@dataclass
class Source:
    n: int                      # 1-based number shown as [Source n]
    kind: str                   # "record" | "vote"
    title: str
    url: str
    blocks: list[str]           # citable text, verbatim from the record
    meta: dict[str, Any] = field(default_factory=dict)

    def search_result(self) -> dict[str, Any]:
        return {
            "type": "search_result",
            "source": self.url,
            "title": self.title,
            "content": [{"type": "text", "text": b} for b in self.blocks],
            "citations": {"enabled": True},
        }

    def card(self) -> dict[str, Any]:
        return {"n": self.n, "kind": self.kind, "title": self.title, "url": self.url, **self.meta}


def fmt_date(iso: str | date) -> str:
    d = date.fromisoformat(iso) if isinstance(iso, str) else iso
    return f"{d.day} {d.strftime('%B %Y')}"


def blocks_for(text: str, words: int = BLOCK_WORDS) -> list[str]:
    """Paragraphs, with long ones split into runs of whole sentences, so a citation points at a few sentences."""
    out: list[str] = []
    for para in [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]:
        if len(para.split()) <= words:
            out.append(" ".join(para.split()))
            continue
        buf: list[str] = []
        n = 0
        for sent in _SENTENCE.split(para):
            buf.append(" ".join(sent.split()))
            n += len(sent.split())
            if n >= words:
                out.append(" ".join(buf))
                buf, n = [], 0
        if buf:
            out.append(" ".join(buf))
    return out or [text]


def groups_label(r: dict[str, Any]) -> str | None:
    """The groups that may explain a country's position, in plain words."""
    groups = set(r.get("groups") or [])
    names = [GROUP_LABELS[g] for g in TITLE_GROUPS if g in groups]
    region = next((GROUP_LABELS[g] for g in sorted(groups) if g.startswith("region:")), None)
    if region:
        names.insert(0, region)
    return "; ".join(names) or None


def speaker_label(r: dict[str, Any]) -> str:
    """e.g. 'Mr. Delattre (France) (spoke in French), representative; France: European States; Permanent members of
    the Security Council; European Union member States; NATO members'."""
    who = r.get("speaker_name") or "Unnamed speaker"
    kind = r.get("entity_kind")
    if kind == "chair":
        head = f"{who}, the President of the Security Council, speaking for the Council" if r.get("on_behalf_of") \
            else f"{who}, presiding"
        return head
    country = r.get("country_name") or r.get("country_label") or "unknown"
    lang = f" (spoke in {r['language']})" if r.get("language") else ""
    role = ROLE_LABEL.get(r.get("speaker_role") or "")
    post = r.get("speaker_post")
    label = f"{who} ({country}){lang}"
    if post:
        label += f", {post}"
    elif role:
        label += f", {role}"
    if r.get("on_behalf_of"):
        label += f", speaking on behalf of the {r['on_behalf_of']}"
    groups = groups_label(r)
    if groups and kind not in ("organisation", "un"):
        label += f"; {country}: {groups}"
    return label


def when_label(r: dict[str, Any]) -> str:
    if r.get("date_precision") == "session":
        return f"{r['meeting']} ({r['year']})"
    return f"{r['body']}, {r['meeting']}, {fmt_date(r['date'])}"


# Texts published before the official record (the General Debate's records come about a year later).
TEXT_SOURCE_NOTES = {
    "delegation": "text issued by the delegation, as prepared for delivery; not yet the official record",
    "transcript": "AI transcript of the UN's English interpretation, not yet the official record; may contain errors",
}


def record_source(n: int, r: dict[str, Any], group_by: str = "region") -> Source:
    notes = []
    if r.get("language"):
        notes.append(f"English text is the UN's translation from {r['language']}")
    if r.get("ocr"):
        notes.append("text recognised from a scanned record and may contain errors")
    if r.get("text_source") in TEXT_SOURCE_NOTES:
        notes.append(TEXT_SOURCE_NOTES[r["text_source"]])
    ref = r.get("symbol")
    title = " — ".join(x for x in (speaker_label(r), when_label(r), ref, r.get("agenda"),
                                   "; ".join(notes) if notes else None) if x)
    group = evidence_group(r, group_by)
    meta = {
        "record_id": r["record_id"],
        "date": r["date"],
        "date_precision": r.get("date_precision"),
        "body": r.get("body"),
        "meeting": r.get("meeting"),
        "symbol": r.get("symbol"),
        "agenda": r.get("agenda"),
        "heading": r.get("heading"),
        "speaker_name": r.get("speaker_name"),
        "country_code": r.get("country_code"),
        "country_name": r.get("country_name"),
        "entity_kind": r.get("entity_kind"),
        "on_behalf_of": r.get("on_behalf_of"),
        "language": r.get("language"),
        "ocr": r.get("ocr"),
        "text_source": r.get("text_source"),
        "group": GROUP_LABELS.get(group, "Other"),
        "text": r["text"],
        "ranking": r.get("ranking"),
    }
    return Source(n, "record", title, r["url"], blocks_for(r["text"]), meta)


def vote_source(n: int, v: dict[str, Any]) -> Source:
    """A General Assembly recorded vote: what was voted on, the totals, and how each country voted."""
    totals = f"{v['total_yes']} in favour, {v['total_no']} against, {v['total_abstentions']} abstaining"
    title = (f"General Assembly recorded vote on resolution {v['resolution']} — {fmt_date(v['date'])} — "
             f"{v['title']}")
    blocks = [f"Resolution {v['resolution']} was adopted on {fmt_date(v['date'])} by a recorded vote of {totals}"
              + (f", with {v['total_non_voting']} not voting" if v.get("total_non_voting") else "") + "."]
    if v.get("agenda_title"):
        blocks.append(f"Agenda item: {v['agenda_title']}")
    for code in ("Y", "N", "A", "X"):
        names = v["votes"].get(code) or []
        if names:
            blocks.append(f"{VOTE_LABEL[code]} ({len(names)}): {', '.join(names)}.")
    meta = {"resolution": v["resolution"], "date": v["date"], "title": v["title"], "meeting": v.get("meeting"),
            "totals": {"yes": v["total_yes"], "no": v["total_no"], "abstain": v["total_abstentions"],
                       "non_voting": v.get("total_non_voting")},
            "group": "Votes"}
    return Source(n, "vote", title, v["url"], blocks, meta)


def build_sources(records: list[dict[str, Any]], votes: list[dict[str, Any]] | None = None,
                  group_by: str = "region") -> list[Source]:
    out = [record_source(i, r, group_by) for i, r in enumerate(records, 1)]
    out += [vote_source(len(out) + i, v) for i, v in enumerate(votes or [], 1)]
    return out
