"""Turn a plain-English question into structured, editable search filters.

This is the only model-driven step before retrieval. Its output is shown to the reader as filters they can change;
retrieval itself is deterministic. Without an API key, :func:`parse_offline` applies simple rules.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field

from wcs.engine import Filters
from wcs.rank.evidence import GROUP_BY
from wcs.reference.countries import Countries, norm

log = logging.getLogger(__name__)

GROUP_KEYS = ("p5", "council_elected", "council_guest", "eu", "nato", "au", "ldc", "lldc", "sids",
              "region:africa", "region:americas", "region:asia", "region:europe", "region:oceania")
GroupKey = Literal["p5", "council_elected", "council_guest", "eu", "nato", "au", "ldc", "lldc", "sids",
                   "region:africa", "region:americas", "region:asia", "region:europe", "region:oceania"]
SourceKey = Literal["sc", "gd"]
GroupBy = Literal["region", "council", "bloc"]

# Phrases that name a group, longest first when matching.
GROUP_PHRASES = {
    "permanent members": "p5", "permanent five": "p5", "p5": "p5", "p-5": "p5",
    "elected members": "council_elected", "non-permanent members": "council_elected",
    "european union": "eu", "eu members": "eu", "eu member states": "eu", "eu countries": "eu",
    "nato": "nato",
    "african union": "au", "au members": "au", "au member states": "au",
    "small island developing states": "sids", "small island states": "sids", "small island": "sids", "sids": "sids",
    "least developed countries": "ldc", "least developed": "ldc", "ldcs": "ldc",
    "landlocked developing countries": "lldc", "landlocked": "lldc",
    "african states": "region:africa", "african countries": "region:africa", "african nations": "region:africa",
    "asian states": "region:asia", "asian countries": "region:asia",
    "european states": "region:europe", "european countries": "region:europe",
    "latin american": "region:americas", "caribbean": "region:americas", "countries of the americas": "region:americas",
    "pacific island": "region:oceania", "pacific states": "region:oceania", "oceania": "region:oceania",
}


@dataclass
class QueryPlan:
    question: str
    search_text: str
    filters: Filters
    wants_votes: bool = False
    interpretation: str = ""
    parser: str = "offline"
    group_by: str = "region"
    unresolved_countries: list[str] = field(default_factory=list)
    parse_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"question": self.question, "search_text": self.search_text, "filters": self.filters.as_dict(),
                "wants_votes": self.wants_votes, "interpretation": self.interpretation, "parser": self.parser,
                "group_by": self.group_by, "unresolved_countries": self.unresolved_countries}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> QueryPlan:
        f = d.get("filters") or {}
        filters = Filters(
            date_from=_iso(f.get("date_from")), date_to=_iso(f.get("date_to")),
            sources=[s for s in f.get("sources") or [] if s in ("sc", "gd")],
            countries=list(f.get("countries") or []),
            groups=[g for g in f.get("groups") or [] if g in GROUP_KEYS],
            kinds=list(f.get("kinds") or []),
            group_statements=bool(f.get("group_statements", True)))
        return cls(question=d.get("question", ""), search_text=d.get("search_text") or d.get("question", ""),
                   filters=filters, wants_votes=bool(d.get("wants_votes")), interpretation=d.get("interpretation", ""),
                   parser=d.get("parser", "edited"),
                   group_by=d.get("group_by") if d.get("group_by") in GROUP_BY else "region")


def _iso(v: Any) -> date | None:
    if not v:
        return None
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


# --------------------------------------------------------------------------- offline rules

_YEAR = r"((?:19|20)\d\d)"


def dates_in(question: str, today: date) -> tuple[date | None, date | None]:
    q = question.lower()
    m = re.search(rf"\bbetween {_YEAR} and {_YEAR}\b|\bfrom {_YEAR} to {_YEAR}\b|\b{_YEAR}\s*[-–]\s*{_YEAR}\b", q)
    if m:
        a, b = [int(x) for x in m.groups() if x]
        return date(min(a, b), 1, 1), date(max(a, b), 12, 31)
    m = re.search(rf"\b(?:since|from|after) {_YEAR}\b", q)
    if m:
        return date(int(m.group(1)), 1, 1), None
    m = re.search(rf"\b(?:before|until|up to) {_YEAR}\b", q)
    if m:
        return None, date(int(m.group(1)) - 1, 12, 31)
    m = re.search(rf"\bin {_YEAR}\b", q)
    if m:
        y = int(m.group(1))
        return date(y, 1, 1), date(y, 12, 31)
    m = re.search(r"\b(?:last|past) (\d+|five|ten|twenty) years\b", q)
    if m:
        n = {"five": 5, "ten": 10, "twenty": 20}.get(m.group(1)) or int(m.group(1))
        return date(today.year - n, 1, 1), None
    return None, None


# A country named after one of these words is the subject ("about Haiti"), not who spoke.
TOPIC_WORDS = {"about", "on", "regarding", "concerning", "towards", "toward", "against", "over", "into", "of",
               "situation", "question", "crisis", "conflict", "war", "invasion"}


def countries_in(question: str, countries: Countries) -> list[tuple[str, str]]:
    """Countries named in the question as speakers: (code, matched words), longest names first, no overlaps. A
    country named as the subject ("what did France say about Haiti") is left to the search words."""
    words = norm(question).split()
    found: list[tuple[str, str]] = []
    used: set[int] = set()
    for size in range(6, 0, -1):
        for i in range(len(words) - size + 1):
            if any(j in used for j in range(i, i + size)):
                continue
            phrase = " ".join(words[i:i + size])
            # short words and everyday words name a country only with a capital letter in the question
            if size == 1 and (len(phrase) < 4 or phrase in ("chad", "niger", "guinea", "jordan", "turkey")) and \
                    not re.search(rf"\b{re.escape(phrase.capitalize())}\b", question):
                continue
            code = countries.resolve(phrase)
            if code and i > 0 and words[i - 1] in TOPIC_WORDS:
                used.update(range(i, i + size))
                continue
            if code and countries.kind(code) != "territory":
                found.append((code, phrase))
                used.update(range(i, i + size))
    return found


def groups_in(question: str) -> list[tuple[str, str]]:
    q = " " + norm(question) + " "
    out: list[tuple[str, str]] = []
    for phrase in sorted(GROUP_PHRASES, key=len, reverse=True):
        p = " " + norm(phrase) + " "
        if p in q:
            out.append((GROUP_PHRASES[phrase], phrase))
            q = q.replace(p, " ")
    return out


def parse_offline(question: str, countries: Countries, today: date | None = None, default_years: int = 10,
                  start: date = date(1992, 1, 1)) -> QueryPlan:
    today = today or date.today()
    ql = question.lower()
    date_from, date_to = dates_in(question, today)
    sources = []
    # "Security Council reform" (or its veto, membership, working methods) is a subject debated everywhere, not a
    # request for the Council's own debates
    council_topic = re.search(r"security council(?:'s)?\s+(?:reform|veto|membership|expansion|enlargement|seats?|"
                              r"working methods|composition)", ql)
    if "security council" in ql and not council_topic:
        sources.append("sc")
    if "general debate" in ql or "general assembly" in ql:
        sources.append("gd")
    named = countries_in(question, countries)
    groups = groups_in(question)
    if date_from is None and date_to is None:
        date_from = date(today.year - default_years, 1, 1)
    elif date_from is None:
        date_from = start
    wants_votes = bool(re.search(r"\bvot(?:e|ed|es|ing)\b", ql))
    group_by = "council" if sources == ["sc"] and re.search(r"\bmembers\b", ql) and not groups else \
        ("bloc" if sum(1 for g, _ in groups if not g.startswith("region:")) >= 2 else "region")
    strip = [w for _, w in named] + [w for _, w in groups]
    text = norm(question)
    for w in sorted(strip, key=len, reverse=True):
        text = re.sub(rf"\b{re.escape(w)}\b", " ", text)
    text = re.sub(rf"\b(?:between|since|from|after|before|until|in|to|and)\s+{_YEAR}\b|\b{_YEAR}\b", " ", text)
    text = " ".join(text.split()) or question
    bits = []
    if named:
        bits.append("countries: " + ", ".join(countries.name_on(c, today) for c, _ in named))
    if groups:
        bits.append("groups: " + ", ".join(countries.label(g) for g, _ in groups))
    if sources:
        bits.append("bodies: " + ", ".join({"sc": "Security Council", "gd": "General Debate"}[s] for s in sources))
    bits.append(f"from {date_from.year}" + (f" to {date_to.year}" if date_to else ""))
    return QueryPlan(question=question, search_text=text,
                     filters=Filters(date_from=date_from, date_to=date_to, sources=sources,
                                     countries=[c for c, _ in named], groups=[g for g, _ in groups]),
                     wants_votes=wants_votes, interpretation="Searching " + "; ".join(bits) + ".",
                     parser="offline", group_by=group_by)


# --------------------------------------------------------------------------- model-based


class ParsedQuery(BaseModel):
    """Schema the model fills in (structured output)."""

    search_text: str = Field(description="Keywords for retrieval, in the vocabulary of UN debates")
    date_from: str | None = Field(description="YYYY-MM-DD or null")
    date_to: str | None = Field(description="YYYY-MM-DD or null")
    sources: list[SourceKey]
    countries: list[str]
    groups: list[GroupKey]
    group_by: GroupBy
    wants_votes: bool
    interpretation: str = Field(description="One sentence: how the question was interpreted")


def system_prompt(today: date, start: date, archive_start: date, default_years: int) -> str:
    return f"""You convert research questions about what countries have said at the United Nations into search filters.
Today's date is {today.isoformat()}. The main collection covers {start.year} to today; the General Debate and General
Assembly votes go back to {archive_start.year}.

Rules:
- Only set a filter when the question clearly asks for it. Never invent constraints.
- search_text: the topic, in the vocabulary used in UN debates (e.g. "climate finance, adaptation, loss and damage";
  "use of force, self-defence, Article 51"). Leave out words that only express filters (dates, country names, groups).
- date_from/date_to: ISO dates for explicit or clearly implied periods ("between 2015 and 2020" -> 2015-01-01 to
  2020-12-31; "since 2022" -> 2022-01-01). When the question gives no period, set date_from to
  {today.year - default_years}-01-01 (the last {default_years} years) and date_to to null.
- sources: "sc" for the Security Council, "gd" for the General Assembly's General Debate. Leave empty for both.
- countries: the countries the question restricts speakers to, by name as written ("France", "Brazil").
  A topic about a country ("the situation in Syria") is not a country filter unless the question asks what that
  country itself said.
- groups: only when the question restricts speakers to a group: p5 (permanent members of the Security Council),
  council_elected (elected members), council_guest (invited non-members), eu, nato, au (African Union), ldc (least
  developed countries), lldc (landlocked developing countries), sids (small island developing States), or a region:
  region:africa, region:americas, region:asia, region:europe, region:oceania. When the question compares groups
  ("how do African and European States differ"), leave groups empty and set group_by.
- group_by: "council" when the question is about positions within the Security Council (permanent vs elected
  members); "bloc" when it compares blocs (EU, NATO, AU, SIDS, LDCs); otherwise "region".
- wants_votes: true if the question is about how countries voted.
- interpretation: one plain sentence describing the filters you chose, in British English."""


def parse_model(settings) -> str:
    return settings.answer.get("query_parse_model") or settings.answer.get("model", "claude-sonnet-5-5")


def parse_with_model(client, settings, question: str, countries: Countries,
                     today: date | None = None) -> tuple[QueryPlan, Any]:
    from wcs.rag.answer import cached_system

    today = today or date.today()
    default_years = int(settings.archive.get("default_years", 10))
    resp = client.messages.parse(
        model=parse_model(settings),
        max_tokens=2000,
        system=cached_system(system_prompt(today, settings.start_date, settings.archive_start, default_years)),
        messages=[{"role": "user", "content": f"Question: {question}"}],
        output_config={"effort": settings.answer.get("query_parse_effort", "low")},
        output_format=ParsedQuery,
    )
    if resp.stop_reason == "refusal" or resp.parsed_output is None:
        raise RuntimeError(f"query parser returned no output (stop_reason={resp.stop_reason})")
    p: ParsedQuery = resp.parsed_output
    codes, unresolved = [], []
    for name in p.countries:
        code = countries.resolve(name)
        (codes if code else unresolved).append(code or name)
    date_to = _iso(p.date_to)
    plan = QueryPlan(
        question=question,
        search_text=p.search_text.strip() or question,
        filters=Filters(date_from=_iso(p.date_from), date_to=None if date_to and date_to >= today else date_to,
                        sources=sorted(set(p.sources)), countries=codes,
                        groups=[g for g in p.groups if g in GROUP_KEYS]),
        wants_votes=p.wants_votes,
        interpretation=p.interpretation,
        parser="model",
        group_by=p.group_by,
        unresolved_countries=unresolved,
    )
    return plan, resp.usage


def plan_json(plan: QueryPlan) -> str:
    return json.dumps(plan.to_dict(), default=str)
