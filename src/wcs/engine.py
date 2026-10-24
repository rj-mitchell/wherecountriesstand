"""Deterministic retrieval: filters -> BM25 + vector candidates -> composite rank -> balanced records.

Given the same index, query text, filters and slider values, results are identical every time. The only
non-deterministic step, turning a plain-English question into filters, happens before this (``rag.query_parse``)
and its output is shown to the reader as editable filters.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

from wcs.config import Settings
from wcs.embed.base import Embedder, get_embedder
from wcs.index.lance import SearchIndex, date_lit, sql_list, sql_str
from wcs.rank.composite import Candidate, RankParams, Scored, rank
from wcs.rank.evidence import GROUP_BY, evidence_summary, select_evidence
from wcs.reference.countries import Countries, countries
from wcs.store.parquet import read_records

log = logging.getLogger(__name__)

STATE_KINDS = ("member_state", "observer_state", "former_member_state", "other_state")
SPEAKER_KINDS = (*STATE_KINDS, "organisation", "un", "chair")
BODIES = {"sc": "Security Council", "gd": "General Assembly"}


@dataclass
class Filters:
    date_from: date | None = None
    date_to: date | None = None
    sources: list[str] = field(default_factory=list)        # "sc", "gd"
    countries: list[str] = field(default_factory=list)      # codes
    groups: list[str] = field(default_factory=list)         # group keys, e.g. "au", "p5", "region:asia"
    kinds: list[str] = field(default_factory=list)          # speaker kinds; empty: States (and the Council itself)
    group_statements: bool = True                           # include statements on behalf of groups

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for k in ("date_from", "date_to"):
            d[k] = d[k].isoformat() if d[k] else None
        return d


@dataclass
class SearchResult:
    scored: list[Scored]
    records: list[dict[str, Any]]
    no_records: bool
    candidates: int
    semantic_available: bool
    where: str | None
    evidence: dict[str, Any] = field(default_factory=dict)
    timings: dict[str, float] = field(default_factory=dict)


def build_where(f: Filters) -> str:
    clauses = []
    if f.sources:
        clauses.append(f"source IN ({', '.join(sql_str(s) for s in f.sources)})")
    if f.date_from:
        clauses.append(f"date >= {date_lit(f.date_from)}")
    if f.date_to:
        clauses.append(f"date <= {date_lit(f.date_to)}")
    if f.countries:
        clauses.append(f"country_code IN ({', '.join(sql_str(c) for c in f.countries)})")
    if f.groups:
        clauses.append(f"array_has_any(groups, {sql_list(f.groups)})")
    kinds = [k for k in f.kinds if k in SPEAKER_KINDS]
    if kinds:
        clauses.append(f"entity_kind IN ({', '.join(sql_str(k) for k in kinds)})")
    else:
        # States speaking; and the Security Council itself (the President speaking on behalf of the Council)
        clauses.append(f"(entity_kind IN ({', '.join(sql_str(k) for k in STATE_KINDS)}) "
                       "OR (entity_kind = 'chair' AND group_statement = true))")
    if not f.group_statements:
        clauses.append("group_statement = false")
    return " AND ".join(clauses)


class Engine:
    def __init__(self, settings: Settings, embedder: Embedder | None = None, today: date | None = None,
                 reference: Countries | None = None):
        self.settings = settings
        self.index = SearchIndex(settings.index_dir, int(settings.embedding.get("dim", 384)))
        self.countries = reference or countries()
        self._embedder = embedder
        self.today = today
        self._has_vectors = False

    def has_vectors(self) -> bool:
        if not self._has_vectors:
            self._has_vectors = self.index.stats()["vectors"] > 0
        return self._has_vectors

    @property
    def embedder(self) -> Embedder | None:
        if self._embedder is None:
            try:
                self._embedder = get_embedder(self.settings)
            except Exception as exc:  # semantic search degrades to keyword-only
                log.warning("embedder unavailable (%s); using keyword search only", exc)
                return None
        return self._embedder

    def search(self, query: str, filters: Filters | None = None, recency: float | None = None,
               role_weight: float | None = None, limit: int = 12, diversify: bool = True,
               group_by: str = "region") -> SearchResult:
        f = filters or Filters()
        r = self.settings.ranking
        where = build_where(f)
        timings: dict[str, float] = {}
        clock = time.perf_counter()

        def lap(stage: str) -> None:
            nonlocal clock
            now = time.perf_counter()
            timings[stage] = timings.get(stage, 0.0) + now - clock
            clock = now

        def keyword() -> list[dict[str, Any]]:
            return self.index.bm25(query, where, int(r.get("candidates_bm25", 300)))

        with ThreadPoolExecutor(max_workers=1) as pool:
            by_keyword = pool.submit(keyword)
            vec_hits: list[dict[str, Any]] = []
            semantic = self.has_vectors() and self.embedder is not None
            if semantic:
                vec = self.embedder.embed_query(query)
                lap("embed")
                vec_hits = self.index.vector(vec, where, int(r.get("candidates_vector", 300)),
                                             nprobes=r.get("vector_nprobes"),
                                             refine_factor=r.get("vector_refine_factor"))
                lap("meaning")
            bm = by_keyword.result()
            lap("keyword")

        cands: dict[str, Candidate] = {}

        def cand_for(hit: dict[str, Any]) -> Candidate:
            c = cands.get(hit["record_id"])
            if c is None:
                c = Candidate(record_id=hit["record_id"], date=hit["date"], role=hit.get("speaker_role") or "",
                              data={k: hit.get(k) for k in ("source", "year", "country_code", "entity_kind",
                                                            "participant", "group_statement", "groups")}
                              | {"passages": {}})
                c.data["groups"] = list(c.data["groups"] or [])
                cands[hit["record_id"]] = c
            return c

        for h in bm:
            c = cand_for(h)
            c.data["passages"][h["passage_id"]] = {"text": h["text"], "heading": h["heading"]}
            for k in ("speaker_name", "country_name", "on_behalf_of"):
                c.data.setdefault(k, h.get(k))
            if c.bm25 is None or h["_score"] > c.bm25:
                c.bm25 = float(h["_score"])
                c.best_passage_id = h["passage_id"]
        for h in vec_hits:
            c = cand_for(h)
            sim = 1.0 - float(h["_distance"])
            if c.cosine is None or sim > c.cosine:
                c.cosine = sim
                if c.bm25 is None:
                    c.best_passage_id = h["passage_id"]

        floor, ceiling = getattr(self.embedder, "similarity_range", (0.5, 0.85)) if semantic else (0.5, 0.85)
        params = RankParams.from_settings(
            r, recency=recency, role_weight=role_weight,
            semantic_floor=r.get("semantic_floor", floor), semantic_ceiling=r.get("semantic_ceiling", ceiling),
            **({"today": self.today} if self.today else {}))
        scored = rank(list(cands.values()), params)
        lap("rank")
        no_records = not scored
        if diversify and scored:
            top = select_evidence(scored[:max(limit * 8, 150)], limit,
                                  group_by=group_by if group_by in GROUP_BY else "region",
                                  max_per_speaker=int(r.get("max_per_speaker", 2)),
                                  max_per_country=int(r.get("max_per_country", 2)),
                                  min_relevance_ratio=float(r.get("min_relevance_ratio", 0.25)))
        else:
            top = scored[:limit]
        lap("select")
        records = self.records_for(top) if top else []
        lap("records")
        return SearchResult(top, records, no_records, len(cands), semantic, where,
                            evidence_summary(records, group_by), timings)

    # ---- full records for display / grounding ---------------------------------
    def records_for(self, scored: list[Scored]) -> list[dict[str, Any]]:
        by_part: dict[tuple[str, int], set[str]] = {}
        for s in scored:
            by_part.setdefault((s.candidate.data["source"], s.candidate.date.year), set()).add(s.candidate.record_id)
        found: dict[str, dict[str, Any]] = {}
        for (source, year), ids in sorted(by_part.items()):
            for rec in read_records(self.settings.parquet_dir, source, year, ids):
                found[rec["record_id"]] = rec
        missing = [s.candidate.best_passage_id for s in scored
                   if s.candidate.best_passage_id and s.candidate.best_passage_id not in s.candidate.data["passages"]]
        extra = self.index.passages_for(missing)
        out = []
        for s in scored:
            rec = found.get(s.candidate.record_id)
            if rec is None:
                continue
            passage = s.candidate.data["passages"].get(s.candidate.best_passage_id or "") or \
                extra.get(s.candidate.best_passage_id or "", {})
            code = rec.get("country_code")
            out.append({
                **{k: rec[k] for k in ("record_id", "source", "body", "date_precision", "session", "meeting",
                                       "symbol", "agenda", "speaker_name", "speaker_post", "speaker_role",
                                       "participant", "country_code", "country_label", "entity_kind",
                                       "on_behalf_of", "language", "ocr", "text", "url")},
                "text_source": rec.get("text_source"),
                "date": rec["date"].isoformat(),
                "year": rec["date"].year,
                "country_name": self.countries.name_on(code, rec["date"]) if code else rec.get("country_label"),
                "groups": s.candidate.data.get("groups") or [],
                "heading": passage.get("heading"),
                "excerpt": passage.get("text") or rec["text"][:1500],
                "ranking": s.breakdown(),
            })
        return out
