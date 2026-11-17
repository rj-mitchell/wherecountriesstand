"""Composite soft ranking: relevance x recency x authority.

Nothing here excludes a record. Recency and role only re-weight relevance, so a much stronger textual match from
2004 still outranks a weak 2025 one. Hard exclusions happen only through filters.

relevance  = w * bm25 / max(bm25) + (1 - w) * calibrated(cosine)
             calibrated(c) = clamp((c - floor) / (ceiling - floor), 0, 1), using the embedding model's similarity
             range, so a weak semantic match is not inflated just because it was the best of a weak set.
recency    = floor + (1 - floor) * 0.5 ** (age_years / half_life)
             slider s in [0, 1]: s = 0 -> recency = 1 for every record; otherwise half_life = min_half_life_years / s
authority  = 1 + (W - 1) * m        W = role slider in [1.0, 2.0]
             m = the speaker's role, where the source gives it: head of state or government 1.0, minister 0.7,
             representative 0.3, not given 0. A group statement counts as its speaker's role.
score      = relevance * recency * authority
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from wts_core.checks import blend, recency_factor

from wcs.reference.roles import ROLE_LABEL


@dataclass
class RankParams:
    recency: float = 0.4
    role_weight: float = 1.3
    bm25_weight: float = 0.5
    recency_floor: float = 0.35
    min_half_life_years: float = 3.0
    m_head: float = 1.0
    m_minister: float = 0.7
    m_representative: float = 0.3
    semantic_floor: float = 0.5
    semantic_ceiling: float = 0.85
    today: date = field(default_factory=date.today)

    @classmethod
    def from_settings(cls, ranking: dict[str, Any], **overrides: Any) -> RankParams:
        keys = {f for f in cls.__dataclass_fields__ if f != "today"}
        values = {k: ranking[k] for k in keys if k in ranking}
        if "default_recency" in ranking:
            values["recency"] = ranking["default_recency"]
        if "default_role_weight" in ranking:
            values["role_weight"] = ranking["default_role_weight"]
        values.update({k: v for k, v in overrides.items() if v is not None})
        p = cls(**values)
        p.recency = min(max(float(p.recency), 0.0), 1.0)
        p.role_weight = min(max(float(p.role_weight), 1.0), 2.0)
        return p


@dataclass
class Candidate:
    record_id: str
    date: date
    bm25: float | None = None
    cosine: float | None = None
    role: str = ""                     # head, minister, representative or ""
    best_passage_id: str | None = None
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class Scored:
    candidate: Candidate
    relevance: float
    recency: float
    authority: float
    authority_m: float
    authority_reason: str
    score: float

    def breakdown(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 4),
            "relevance": round(self.relevance, 4),
            "keyword": None if self.candidate.bm25 is None else round(self.candidate.bm25, 4),
            "semantic": None if self.candidate.cosine is None else round(self.candidate.cosine, 4),
            "recency": round(self.recency, 4),
            "authority": round(self.authority, 4),
            "authority_reason": self.authority_reason,
        }


def authority_m(c: Candidate, p: RankParams) -> tuple[float, str]:
    m = {"head": p.m_head, "minister": p.m_minister, "representative": p.m_representative}.get(c.role)
    if m is None:
        return 0.0, "role not given"
    return m, ROLE_LABEL[c.role]


def rank(candidates: list[Candidate], p: RankParams) -> list[Scored]:
    """The candidates scored, best first. The blend of word and meaning scores and the recency factor are the
    Where They Stand sites' shared arithmetic (wts-core, ``wts_core.checks``), with this site's weights; authority,
    and what a candidate is, are this site's own."""
    if not candidates:
        return []
    relevance = blend([c.bm25 for c in candidates], [c.cosine for c in candidates], word_weight=p.bm25_weight,
                      meaning_floor=p.semantic_floor, meaning_ceiling=p.semantic_ceiling)
    out: list[Scored] = []
    for c, rel in zip(candidates, relevance, strict=True):
        rec = recency_factor(c.date, p.today, recency=p.recency, floor=p.recency_floor,
                             min_half_life_years=p.min_half_life_years)
        m, why = authority_m(c, p)
        auth = 1 + (p.role_weight - 1) * m
        out.append(Scored(c, rel, rec, auth, m, why, rel * rec * auth))
    out.sort(key=lambda s: (-s.score, -s.relevance, s.candidate.record_id))
    return out
