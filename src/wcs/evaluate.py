"""The test set (``eval/questions.toml``, docs/DECISIONS.md 10) and how the site is scored against it.

Two ways of running it.

**Free, with no briefing written** (this module's ``run``):

    wcs evaluate                     # the built-in rules read each question (free)
    wcs evaluate --with-model        # the model reads each question (a fraction of a penny each)

Two scores per question:

1. **Reading the question:** each filter chosen (bodies, countries, groups, dates, whether votes are wanted) compared
   with the filters a careful reader would choose. Dates are compared by year.
2. **The evidence:** for questions where "No records found" is right, whether nothing was found; for questions that
   name countries in ``include``, whether a source from each was found.

**With each briefing written** (``CountriesEval``, for the Where They Stand sites' shared harness, ``wts_core.eval``):

    wcs eval run                     # asks every question of the real pipeline; spends up to [eval] max_cost_usd
    wcs eval compare A.json B.json   # two saved runs side by side
    wcs eval replay eval/fixtures    # the checks run again over briefings a run kept: free, no model

A run reports, for each question and overall, what the checks on briefings found, the spread of sources, the time
and the cost, with the two scores above beside them, and is held to ``[eval.thresholds]`` in the settings.

Results are saved under ``eval/runs/``.
"""

from __future__ import annotations

import json
import tomllib
from collections import defaultdict
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from wts_core.eval import Case, Recording, load_cases, read_as_if
from wts_core.events import Event

from wcs.config import REPO_ROOT, Settings
from wcs.rag.pipeline import AskRequest, Pipeline
from wcs.rag.query_parse import QueryPlan

QUESTIONS = REPO_ROOT / "eval" / "questions.toml"
RUNS = REPO_ROOT / "eval" / "runs"  # where runs are saved, and the note of each run that passed (passed.jsonl)
FIELDS = ("sources", "countries", "groups", "date_from", "date_to", "wants_votes")


@dataclass
class Result:
    id: str
    category: str
    question: str
    fields: dict[str, bool]
    got: dict[str, Any]
    records: int
    votes: int
    evidence_ok: bool | None
    problems: list[str] = field(default_factory=list)

    @property
    def plan_ok(self) -> bool:
        return all(self.fields.values())


def load_questions(path: Path = QUESTIONS) -> tuple[date, list[dict[str, Any]]]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return date.fromisoformat(data["today"]), data["q"]


def _year(value: str | None) -> int | None:
    return int(value[:4]) if value else None


def compare(plan: QueryPlan, expect: dict[str, Any], today: date, default_years: int) -> tuple[dict[str, bool],
                                                                                                 dict[str, Any]]:
    """Each filter right or wrong, and what was chosen."""
    f = plan.filters.as_dict()
    got = {"sources": sorted(f.get("sources") or []), "countries": sorted(f.get("countries") or []),
           "groups": sorted(f.get("groups") or []), "date_from": _year(f.get("date_from")),
           "date_to": _year(f.get("date_to")), "wants_votes": bool(plan.wants_votes)}
    want_from = expect.get("date_from")
    want = {"sources": sorted(expect.get("sources", [])), "countries": sorted(expect.get("countries", [])),
            "groups": sorted(expect.get("groups", [])),
            "date_from": today.year - default_years if want_from == "default" else _year(want_from),
            "date_to": _year(expect.get("date_to")), "wants_votes": bool(expect.get("wants_votes", False))}
    ok = {k: got[k] == want[k] for k in FIELDS}
    for k in FIELDS:  # `<field>_any`: acceptable alternatives
        if f"{k}_any" in expect:
            ok[k] = any(got[k] == (sorted(v) if isinstance(v, list) else v) for v in expect[f"{k}_any"])
    return ok, got


def run(settings: Settings, with_model: bool = False, ids: set[str] | None = None,
        pipeline: Pipeline | None = None, path: Path = QUESTIONS) -> list[Result]:
    today, questions = load_questions(path)
    p = pipeline or Pipeline(settings, today=today)
    default_years = int(settings.archive.get("default_years", 10))
    out: list[Result] = []
    for q in questions:
        if ids and q["id"] not in ids:
            continue
        req = AskRequest(question=q["question"])
        if with_model and p.has_model():
            plan, _ = p.make_plan(req)
        else:
            from wcs.rag.query_parse import parse_offline

            plan = parse_offline(q["question"], p.engine.countries, today, default_years, settings.start_date)
        fields, got = compare(plan, q.get("expect", {}), today, default_years)
        result, votes, _ = p.find(plan, req)
        problems = [f"{k}: chose {got[k]!r}" for k, ok in fields.items() if not ok]
        evidence_ok: bool | None = None
        if q.get("no_records"):
            evidence_ok = not result.records and not votes
            if not evidence_ok:
                problems.append(f"expected no records, found {len(result.records)} and {len(votes)} votes")
        elif q.get("include"):
            found = {r.get("country_code") for r in result.records}
            missing = [c for c in q["include"] if c not in found]
            evidence_ok = not missing
            if missing:
                problems.append("no source from " + ", ".join(missing))
        out.append(Result(q["id"], q.get("category", ""), q["question"], fields, got, len(result.records), len(votes),
                          evidence_ok, problems))
    return out


def summary(results: list[Result]) -> dict[str, Any]:
    by_cat: dict[str, list[Result]] = defaultdict(list)
    for r in results:
        by_cat[r.category].append(r)
    checked = [r for r in results if r.evidence_ok is not None]
    return {
        "questions": len(results),
        "plans_right": sum(r.plan_ok for r in results),
        "fields_right": sum(sum(r.fields.values()) for r in results),
        "fields": len(results) * len(FIELDS),
        "evidence_checked": len(checked),
        "evidence_right": sum(bool(r.evidence_ok) for r in checked),
        "by_category": {c: {"questions": len(rs), "plans_right": sum(r.plan_ok for r in rs)}
                        for c, rs in by_cat.items()},
    }


def save(results: list[Result], with_model: bool, folder: Path = RUNS) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}{'-model' if with_model else ''}.json"
    path.write_text(json.dumps({"summary": summary(results), "results": [asdict(r) for r in results]}, indent=1,
                               default=str), encoding="utf-8")
    return path


class CountriesEval:
    """This site as the shared package's evaluation harness sees it (``wts_core.eval.EvalSite``): its questions, a
    way to ask one of the real pipeline, and the group a source belongs to. It also gives the harness the site's own
    two scores for each question (``score``) and what the model was given and wrote (``recording``), so a briefing
    written in a run can be kept and the checks run over it again later."""

    def __init__(self, settings: Settings, pipeline: Pipeline | None = None, path: Path = QUESTIONS) -> None:
        self.settings = settings
        self.path = path
        self.today = read_as_if(path)  # the date the questions are read as if it were
        self._pipeline = pipeline
        self._kept: dict[str, Recording] = {}

    @property
    def pipeline(self) -> Pipeline:
        if self._pipeline is None:
            self._pipeline = Pipeline(self.settings, today=self.today)
        return self._pipeline

    def cases(self) -> Sequence[Case]:
        return load_cases(self.path)

    def ask(self, case: Case, config: Mapping[str, Any]) -> Iterator[Event]:
        """Ask one question of the pipeline itself: never through the briefing desk, so never from the answer
        cache, never counted against a reader's limits and never saved as a briefing. ``config`` is the
        configuration the settings give, which is what the pipeline runs with."""
        self._kept.pop(case.id, None)

        def keep(documents: Any, blocks: Any, headings: Any) -> None:
            self._kept[case.id] = Recording(documents, blocks, headings)

        return self.pipeline.run(AskRequest(question=case.question), keep=keep)

    def group_of(self, source: Mapping[str, Any]) -> str:
        """The group the evidence was balanced across (a region, a place at the Council, a bloc; "Votes")."""
        return str(source.get("group") or "")

    def score(self, case: Case, plan: Mapping[str, Any] | None,
              sources: Sequence[Mapping[str, Any]]) -> Mapping[str, bool]:
        """The site's own expectations of a question, each met or not: every filter the question was read into
        (as ``compare``), and the evidence (nothing found where nothing should be; a source from each country its
        ``include`` names)."""
        if plan is None:
            return {}
        years = int(self.settings.archive.get("default_years", 10))
        met, _ = compare(QueryPlan.from_dict({"question": case.question, **plan}), dict(case.expect),
                         self.today or date.today(), years)
        if case.expect.get("no_records"):
            met["no_records"] = not sources
        elif case.expect.get("include"):
            met["include"] = set(case.expect["include"]) <= {s.get("country_code") for s in sources}
        return met

    def recording(self, case: Case) -> Recording | None:
        """What the model was given and what it wrote for the question just asked, where a briefing was written."""
        return self._kept.get(case.id)
