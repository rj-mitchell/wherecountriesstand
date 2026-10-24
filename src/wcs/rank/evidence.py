"""Choose a balanced evidence set for synthesis, and summarise it.

A briefing that says "permanent members argued X, African States Y, small island States Z" needs evidence from each
of those groups, not the twelve highest-scoring speeches (which are often six speeches by one country). Selection is
deterministic:

1. drop candidates far weaker than the best match (``min_relevance_ratio``);
2. cap contributions per speaker and per country (``max_per_speaker``, ``max_per_country``);
3. take the best remaining record from each group in turn (groups ordered by their best score), then fill any
   remaining places by score.

How records are grouped (``group_by``):

* ``region`` (default): the country's geographic region (UN M49); group statements form their own group, and
  organisations, UN officials and the presiding chair theirs;
* ``council``: at a Security Council meeting, permanent members, elected members and invited countries;
* ``bloc``: the European Union, NATO, the African Union, small island developing States and least developed
  countries, each country counted in the first of these it belongs to, others by region.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from wcs.rank.composite import Scored

GROUP_LABELS = {
    "region:africa": "African States",
    "region:americas": "States of the Americas",
    "region:asia": "Asian States",
    "region:europe": "European States",
    "region:oceania": "States of Oceania",
    "p5": "Permanent members of the Security Council",
    "council_elected": "Elected members of the Security Council",
    "council_guest": "Invited non-members of the Security Council",
    "eu": "European Union member States",
    "nato": "NATO members",
    "au": "African Union member States",
    "sids": "Small island developing States",
    "ldc": "Least developed countries",
    "group_statement": "Statements on behalf of groups",
    "organisation": "Organisations and other speakers",
    "un": "UN officials",
    "chair": "The presiding chair",
    "other": "Other",
}
BLOC_ORDER = ("eu", "nato", "au", "sids", "ldc")
GROUP_BY = ("region", "council", "bloc")


def evidence_group(rec: dict[str, Any], group_by: str = "region") -> str:
    """Group key for a record (as returned by the engine, or a passage row)."""
    if rec.get("on_behalf_of") or rec.get("group_statement"):
        return "group_statement"
    kind = rec.get("entity_kind")
    if kind in ("organisation", "un", "chair"):
        return kind
    groups = set(rec.get("groups") or [])
    if group_by == "council":
        for g in ("p5", "council_elected", "council_guest"):
            if g in groups:
                return g
    if group_by == "bloc":
        for g in BLOC_ORDER:
            if g in groups:
                return g
    for g in sorted(groups):
        if g.startswith("region:"):
            return g
    return "other"


def select_evidence(scored: list[Scored], k: int, *, group_by: str = "region", max_per_speaker: int = 2,
                    max_per_country: int = 2, min_relevance_ratio: float = 0.25) -> list[Scored]:
    if not scored:
        return []
    best_rel = max(s.relevance for s in scored)
    pool = [s for s in scored if s.relevance >= best_rel * min_relevance_ratio]
    groups: dict[str, list[Scored]] = defaultdict(list)
    for s in pool:  # already in score order
        groups[evidence_group(s.candidate.data, group_by)].append(s)
    order = sorted(groups, key=lambda g: -groups[g][0].score)
    chosen: list[Scored] = []
    taken: set[str] = set()
    per_speaker: Counter[str] = Counter()
    per_country: Counter[str] = Counter()

    def take(s: Scored) -> bool:
        d = s.candidate.data
        who = f"{d.get('country_code') or d.get('country_name')}|{d.get('speaker_name')}"
        country = d.get("on_behalf_of") or d.get("country_code") or d.get("country_name") or s.candidate.record_id
        if s.candidate.record_id in taken or per_speaker[who] >= max_per_speaker or \
                per_country[country] >= max_per_country:
            return False
        chosen.append(s)
        taken.add(s.candidate.record_id)
        per_speaker[who] += 1
        per_country[country] += 1
        return True

    progress = True
    while len(chosen) < k and progress:
        progress = False
        for g in order:
            for s in groups[g]:
                if take(s):
                    progress = True
                    break
            if len(chosen) >= k:
                break
    for s in pool:  # fill by score if the groups ran dry
        if len(chosen) >= k:
            break
        take(s)
    chosen.sort(key=lambda s: (-s.score, s.candidate.record_id))
    return chosen


def evidence_summary(records: list[dict[str, Any]], group_by: str = "region") -> dict[str, Any]:
    """Deterministic description of the evidence base (shown to the reader, not model-written)."""
    groups: Counter[str] = Counter()
    bodies: Counter[str] = Counter()
    countries: set[str] = set()
    meetings: set[str] = set()
    translated = ocr = 0
    dates = []
    for r in records:
        groups[GROUP_LABELS.get(evidence_group(r, group_by), "Other")] += 1
        bodies[r.get("body") or ""] += 1
        countries.add(r.get("on_behalf_of") or r.get("country_code") or r.get("country_name") or r["record_id"])
        meetings.add(r.get("symbol") or r.get("meeting") or r["record_id"])
        translated += bool(r.get("language"))
        ocr += bool(r.get("ocr"))
        dates.append(r["date"])
    return {
        "records": len(records),
        "countries": len(countries),
        "meetings": len(meetings),
        "date_from": min(dates) if dates else None,
        "date_to": max(dates) if dates else None,
        "groups": dict(groups.most_common()),
        "bodies": dict(bodies.most_common()),
        "translated": translated,   # speeches given in another language: the English text is the UN's translation
        "ocr": ocr,                 # speeches from scanned records, which may contain recognition errors
    }
