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

from collections import Counter
from typing import Any

from wts_core.checks import round_robin

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
    "oic": "Organisation of Islamic Cooperation member States",
    "nam": "Non-Aligned Movement members",
    "g77": "Group of 77 members",
    "ungroup:african": "African Group (UN regional group)",
    "ungroup:asia_pacific": "Asia-Pacific Group (UN regional group)",
    "ungroup:eastern_european": "Eastern European Group (UN regional group)",
    "ungroup:latin_american_caribbean": "Latin American and Caribbean Group (UN regional group)",
    "ungroup:western_european_other": "Western European and other States (UN regional group)",
    "income:low": "Low-income economy (World Bank, that year)",
    "income:lower_middle": "Lower-middle-income economy (World Bank, that year)",
    "income:upper_middle": "Upper-middle-income economy (World Bank, that year)",
    "income:middle": "Middle-income economies (World Bank)",
    "income:high": "High-income economy (World Bank, that year)",
    "au_suspended": "suspended from the African Union's activities on this date",
    "sids": "Small island developing States",
    "ldc": "Least developed countries",
    "group_statement": "Statements on behalf of groups",
    "organisation": "Organisations and other speakers",
    "un": "UN officials",
    "chair": "The presiding chair",
    "other": "Other",
}
# the smaller and more specific blocs first: most members of the Organisation of Islamic Cooperation and nearly all
# of the African Union are also in the Group of 77 and the Non-Aligned Movement
BLOC_ORDER = ("eu", "nato", "au", "oic", "sids", "ldc", "nam", "g77")
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
    """The balanced set: the turn-taking between groups is the Where They Stand sites' shared one (wts-core,
    ``wts_core.checks.round_robin``); the relevance floor, the grouping and the two caps are this site's."""
    if not scored:
        return []
    best_rel = max(s.relevance for s in scored)
    pool = [s for s in scored if s.relevance >= best_rel * min_relevance_ratio]  # already in score order

    def who(s: Scored) -> str:
        d = s.candidate.data
        return f"{d.get('country_code') or d.get('country_name')}|{d.get('speaker_name')}"

    def country(s: Scored) -> str:
        d = s.candidate.data
        return d.get("on_behalf_of") or d.get("country_code") or d.get("country_name") or s.candidate.record_id

    return round_robin(pool, k, group_of=lambda s: evidence_group(s.candidate.data, group_by),
                       key_of=lambda s: s.candidate.record_id, score_of=lambda s: s.score,
                       caps=[(who, max_per_speaker), (country, max_per_country)])


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
