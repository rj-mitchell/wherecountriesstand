"""Question -> plan -> evidence -> grounded briefing -> verification.

``Pipeline.run`` yields events as it goes (the plan, the sources, text as it is written, then the verified answer),
so a web page can show each step; ``answer_once`` collects them into one result for the command line and tests.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from wcs.config import Settings
from wcs.engine import Engine
from wcs.rag.british import britishise_segments, to_british
from wcs.rag.neutral import flag_loaded
from wcs.rag.prompts import NO_RECORDS
from wcs.rag.query_parse import QueryPlan, parse_offline, parse_with_model
from wcs.rag.sources import build_sources
from wcs.rag.typography import curl_answer, curl_event
from wcs.rag.verify import check_quotes, drop_preambles, verify
from wcs.votes import VoteFinder

log = logging.getLogger(__name__)


@dataclass
class AskRequest:
    question: str
    mode: str = "summary"                       # summary | comprehensive
    plan: dict[str, Any] | None = None          # an edited plan from the reader, used instead of parsing
    recency: float | None = None
    role_weight: float | None = None
    history: list[dict[str, str]] = field(default_factory=list)


def plan_note(plan: QueryPlan, engine: Engine) -> str:
    """The filters, in words, for the model (so it knows what the evidence was limited to)."""
    f = plan.filters
    c = engine.countries
    bits = []
    if f.sources:
        bits.append("bodies: " + ", ".join({"sc": "Security Council", "gd": "General Assembly General Debate"}[s]
                                           for s in f.sources))
    if f.countries:
        bits.append("countries: " + ", ".join(c.name_on(x) for x in f.countries))
    if f.groups:
        bits.append("groups: " + ", ".join(c.label(g) for g in f.groups))
    if f.date_from or f.date_to:
        bits.append("dates: " + (f.date_from.isoformat() if f.date_from else "the start") + " to " +
                    (f.date_to.isoformat() if f.date_to else "today"))
    return "; ".join(bits)


VOTES_ASKED = 6  # resolutions shown when a question asks about votes


class Pipeline:
    def __init__(self, settings: Settings, engine: Engine | None = None, client=None, today: date | None = None):
        self.settings = settings
        self.engine = engine or Engine(settings, today=today)
        self.votes = VoteFinder(settings, self.engine.countries)
        self._client = client
        self.today = today

    @property
    def client(self):
        if self._client is None:
            from wcs.rag.answer import make_client

            self._client = make_client()
        return self._client

    def has_model(self) -> bool:
        if self._client is not None:
            return True
        import os

        from wcs.rag.answer import api_key

        return bool(api_key() or os.environ.get("ANTHROPIC_AUTH_TOKEN"))

    def make_plan(self, req: AskRequest) -> tuple[QueryPlan, dict[str, int] | None]:
        if req.plan:
            return QueryPlan.from_dict({"question": req.question, **req.plan}), None
        today = self.today or date.today()
        default_years = int(self.settings.archive.get("default_years", 10))
        offline = parse_offline(req.question, self.engine.countries, today, default_years, self.settings.start_date)
        if not self.has_model():
            return offline, None
        try:
            from wcs.rag.answer import usage_dict

            plan, usage = parse_with_model(self.client, self.settings, req.question, self.engine.countries, today)
            plan.interpretation = to_british(plan.interpretation)
            return plan, usage_dict(usage)
        except Exception as exc:
            log.warning("model query parsing failed (%s); using offline rules", exc)
            offline.parse_error = f"{type(exc).__name__}: {exc}"
            return offline, None

    def find(self, plan: QueryPlan, req: AskRequest):
        """The evidence for a plan: (search result, votes, numbered sources). Free: no model call."""
        limit = int(self.settings.answer.get(
            "comprehensive_sources" if req.mode == "comprehensive" else "summary_sources", 24))
        result = self.engine.search(plan.search_text, plan.filters, req.recency, req.role_weight, limit,
                                    diversify=True, group_by=plan.group_by)
        votes = []
        if not plan.filters.sources or "gd" in plan.filters.sources:
            # resolution titles are matched on the question's own subject words first: the model's search words add
            # debate vocabulary ("territorial integrity, sovereignty") that titles such as "Aggression against
            # Ukraine" lack
            n = VOTES_ASKED if plan.wants_votes else 1
            own = parse_offline(req.question, self.engine.countries, self.today or date.today()).search_text
            strict = not plan.wants_votes
            votes = self.votes.find(own, plan.filters, n, strict) or \
                self.votes.find(plan.search_text, plan.filters, n, strict)
        return result, votes, build_sources(result.records, votes, plan.group_by)

    def run(self, req: AskRequest) -> Iterator[dict[str, Any]]:
        plan, parse_usage = self.make_plan(req)
        ev: dict[str, Any] = {"type": "plan", "plan": plan.to_dict(), "usage": parse_usage}
        if plan.parse_error:
            ev["parse_error"] = plan.parse_error
        yield curl_event(ev)  # the interpretation in curly quotes, as the briefing
        result, votes, sources = self.find(plan, req)
        yield {"type": "sources", "sources": [s.card() for s in sources], "candidates": result.candidates,
               "semantic": result.semantic_available, "evidence": {**result.evidence, "votes": len(votes)}}
        if not result.records and not votes:
            yield {"type": "final", "answer": {"segments": [{"text": NO_RECORDS + " The archive contains no records "
                   "matching this question and these filters.", "sources": [], "cited": [], "status": "connective"}],
                   "markdown": NO_RECORDS, "no_records": True, "citations_total": 0, "citations_invalid": 0,
                   "grounded_ratio": 1.0, "unsupported_segments": 0}, "usage": None}
            return
        if not self.has_model():
            yield {"type": "final", "answer": None, "usage": None,
                   "note": "Showing the evidence only (no Anthropic API key set)."}
            return
        from wcs.rag.answer import Answerer

        final = None
        for event in Answerer(self.settings, self.client).stream(
                plan.question, sources, req.mode, plan_note(plan, self.engine), req.history,
                self.today or date.today(), plan.group_by):
            if event["type"] == "final":
                final = event
            else:
                yield event
        if final is None:
            raise RuntimeError("the model returned no answer")
        if final.get("stop_reason") == "refusal":
            # the model declined: an error for the reader, never an empty briefing shown as fully grounded (as Where
            # MPs Stand). The call was still paid for, so its token counts go with the event to be recorded.
            yield {"type": "error", "message": "The model declined to answer this question.",
                   "usage": final["usage"], "model": final.get("model")}
            return
        report = verify(final["blocks"], sources)
        drop_preambles(report.segments)       # "Across the records retrieved, ..." openers
        britishise_segments(report.segments)  # the model's own words only; quotations stay verbatim
        loaded = flag_loaded(report.segments)  # judgemental words in its own voice are highlighted, not hidden
        check_quotes(report, sources)         # quotations not found word for word in their sources are marked
        # curly quotes and apostrophes, last, once the checks have read the text as the model wrote it
        yield {"type": "final", "answer": curl_answer({**report.to_dict(), "loaded_words": loaded}),
               "usage": final["usage"], "model": final.get("model"), "stop_reason": final.get("stop_reason")}


def answer_once(pipeline: Pipeline, req: AskRequest) -> dict[str, Any]:
    """Run a question to the end and collect the plan, sources and answer."""
    out: dict[str, Any] = {}
    for ev in pipeline.run(req):
        if ev["type"] in ("plan", "sources", "final", "error"):
            out[ev["type"]] = ev
    return out
