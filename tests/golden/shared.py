"""What the golden recorder (record.py, run on the site's code as it was before wts-core) and the parity tests
(tests/test_parity.py and its neighbours, run on the code as it is now) both need: the tables of inputs, the
scripted pipeline, a simulated Backblaze B2 and the scenarios themselves. Nothing here imports the site's code, so
it reads the same under either. Whatever must touch the site (its settings, its web app, its state database) is
handed in by the caller.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import sqlite3
import stat
import threading
from collections.abc import Callable, Iterator
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace as NS
from typing import Any

import httpx

GOLDEN = Path(__file__).parent
START = "2026-11-13T12:00:00+00:00"   # where every scenario's clock starts
DAY = START[:10]
SALT = "5a" * 16                       # the day's visitor secret, so a visitor's code is the same in every run
TODAY = date(2026, 10, 24)             # the pipeline's "today" in the scenarios with a real index
STAMP = 1_763_000_000                  # the index's state.json is given this time, so the cache key is the same


def read(name: str) -> Any:
    return json.loads((GOLDEN / name).read_text(encoding="utf-8"))


def write(out: Path, name: str, value: Any) -> None:
    (out / name).write_text(json.dumps(value, indent=1, ensure_ascii=False, sort_keys=True, default=str) + "\n",
                            encoding="utf-8")


def plain(value: Any, **places: Path | str) -> Any:
    """``value`` as JSON would hold it, with each folder's path replaced by its name in angle brackets."""
    text = json.dumps(value, default=str)
    for name, path in sorted(places.items(), key=lambda kv: -len(str(kv[1]))):
        text = text.replace(str(path), f"<{name}>")
    return json.loads(text)


# ---- settings ------------------------------------------------------------------------------------------------------

SAMPLE_CONFIG = """\
[paths]
data_dir = "{data_dir}"

[archive]
start_date = "2000-01-01"
default_years = 5

[answer]
model = "sample-model"

[usage]
daily_spend_cap_usd = 1.5
"""
LOCAL = """\
[usage]
daily_spend_cap_usd = 9.0
per_visitor_daily_questions = 7

[site]
url = "https://example.test"

[brand_new]
key = "value"
"""
SETTINGS_CASES: list[dict[str, Any]] = [
    {"name": "the real settings file", "config": "real", "env": {}},
    {"name": "the real file, the data folder from the environment (absolute)", "config": "real",
     "env": {"WCS_DATA_DIR": "<tmp>/elsewhere"}},
    {"name": "the real file, the data folder from the environment (relative)", "config": "real",
     "env": {"WCS_DATA_DIR": "other/data"}},
    {"name": "the real file, a local file layered over it", "config": "real", "env": {"WCS_DATA_DIR": "<tmp>/local"},
     "local": LOCAL},
    {"name": "the real file, overrides as the tests give them", "config": "real", "env": {"WCS_DATA_DIR": "<tmp>/o"},
     "overrides": {"embedding.backend": "hashing", "embedding.dim": 64, "live.gd_bundled": "", "new.key": 1}},
    {"name": "the real file, a local file and overrides", "config": "real", "env": {"WCS_DATA_DIR": "<tmp>/both"},
     "local": LOCAL, "overrides": {"usage.daily_spend_cap_usd": 0.0}},
    {"name": "a sample file named by WCS_CONFIG", "config": SAMPLE_CONFIG, "data_dir": "<tmp>/sample-data",
     "env": {}},
    {"name": "a sample file with a relative data folder", "config": SAMPLE_CONFIG, "data_dir": "var/data", "env": {}},
    {"name": "a sample file given as an argument, the data folder from the environment", "config": SAMPLE_CONFIG,
     "data_dir": "var/data", "env": {"WCS_DATA_DIR": "<tmp>/from-env"}, "as_argument": True},
    {"name": "an empty WCS_DATA_DIR counts as unset", "config": "real", "env": {"WCS_DATA_DIR": ""}},
]
ACCESSORS = ("archive", "start_date", "archive_start", "born_digital_from", "ranking", "answer", "embedding",
             "sources", "usage", "raw_dir", "parquet_dir", "index_dir", "models_dir")


def settings_cases(tmp: Path, root: Path, load: Callable[..., Any]) -> list[dict[str, Any]]:
    """Each case resolved by ``load(config_path_or_None, **overrides)``, with the environment set as the case says."""
    out = []
    for n, case in enumerate(SETTINGS_CASES):
        for key in [k for k in os.environ if k.startswith("WCS_")]:
            del os.environ[key]
        os.environ.update({k: v.replace("<tmp>", str(tmp)) for k, v in case["env"].items()})
        argument = None
        if case["config"] != "real":
            path = tmp / f"sample-{n}.toml"
            path.write_text(case["config"].format(data_dir=case["data_dir"].replace("<tmp>", str(tmp))))
            if case.get("as_argument"):
                argument = path
            else:
                os.environ["WCS_CONFIG"] = str(path)
        if case.get("local"):
            folder = Path(os.environ["WCS_DATA_DIR"])
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "settings.local.toml").write_text(case["local"])
        s = load(argument, **case.get("overrides", {}))
        gives = {"raw": s.raw, "data_dir": str(s.data_dir), "config_path": str(s.config_path),
                 "overrides": s.overrides, "site": s.section("site"), "no_such_section": s.section("no_such_section"),
                 **{name: getattr(s, name) for name in ACCESSORS}}
        out.append({"name": case["name"], "gives": plain(gives, tmp=tmp, root=root)})
    for key in [k for k in os.environ if k.startswith("WCS_")]:
        del os.environ[key]
    return out


# ---- the limit on requests a minute ------------------------------------------------------------------------------

A, B = "visitor-a", "visitor-b"
LIMITER_PER_MINUTE = 3
LIMITER_SCRIPT = (
    [[0.0, A]] * 4 + [[0.0, B]] + [[30.0, A], [59.9, A], [60.0, A], [60.1, A], [60.1, A], [60.1, A], [60.1, A]]
    + [[60.2, B]] * 4 + [[120.2, B], [120.3, B], [120.3, B], [200.0, A], [200.0, B]]
)


# ---- cost ----------------------------------------------------------------------------------------------------------

MODELS = ["claude-sonnet-5-5", "claude-haiku-5-5", "claude-haiku-5-5-20260101", "claude-opus-9", None]
USAGES: list[dict[str, Any] | None] = [
    None,
    {},
    {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0},
    {"input_tokens": 1000, "output_tokens": 200},
    {"input_tokens": 9000, "output_tokens": 700, "cache_read_tokens": 3000, "cache_write_tokens": 2000},
    {"input_tokens": 1200, "output_tokens": 80, "cache_read_tokens": 0, "cache_write_tokens": 0},
    {"input_tokens": 123_457, "output_tokens": 15_999, "cache_read_tokens": 77_777, "cache_write_tokens": 3_333},
    # the SDK's own names: input_tokens does not include the cache writes
    {"input_tokens": 7000, "output_tokens": 700, "cache_read_input_tokens": 3000, "cache_creation_input_tokens": 2000},
    {"input_tokens": 100, "output_tokens": 50, "cache_read_tokens": 0, "cache_read_input_tokens": 999},
    {"input_tokens": None, "output_tokens": None, "cache_read_tokens": None, "cache_write_tokens": None},
    {"input_tokens": 1_000_000, "output_tokens": 1_000_000, "cache_read_tokens": 1_000_000,
     "cache_write_tokens": 1_000_000},
]


# ---- cache keys ----------------------------------------------------------------------------------------------------

Q = "What has been said about climate finance since 2020?"
Q2 = "And what did the small island States say?"
EARLIER = [{"question": Q, "answer": "Kenya said that climate finance was too slow."}]
KEY_BODIES: list[dict[str, Any]] = [
    {"question": Q},
    {"question": "what has been said about  CLIMATE finance since 2020?"},
    {"question": Q, "mode": "comprehensive"},
    {"question": Q, "recency": 0.7},
    {"question": Q, "role_weight": 1.5},
    {"question": Q, "plan": {"search_text": "climate finance", "filters": {"date_from": "2020-01-01"}}},
    {"question": Q, "sources_only": True},
    {"question": Q2},
    {"question": Q2, "history": EARLIER},
    {"question": "Où est la bibliothèque — “déjà vu”?"},
]
# what else a key depends on: each of these must change it
KEY_WORLDS: list[dict[str, Any]] = [
    {},
    {"model": "claude-sonnet-9"},
    {"data": "1763009999"},
    {"instructions": 4},
]


# ---- caps, and briefings in progress ---------------------------------------------------------------------------

CAP_LIMITS = {"per_visitor_daily_questions": 2, "daily_spend_cap_usd": 1.0, "monthly_spend_cap_usd": 2.0}
# (when, what): spend(kind, model, usage, visitor) or check(visitor or None, pending)
CAPS_SCRIPT: list[list[Any]] = [
    ["2026-10-31T23:59:59+00:00", "spend", "answer", "claude-sonnet-5-5", {"input_tokens": 1_000_000}, "old"],
    ["2026-11-01T00:00:00+00:00", "spend", "answer", "claude-sonnet-5-5", {"input_tokens": 450_000}, "v0"],
    ["2026-11-12T23:59:59+00:00", "spend", "answer", "claude-sonnet-5-5", {"input_tokens": 50_000}, "v1"],
    ["2026-11-13T00:00:00+00:00", "check", "v1", 0.0],
    ["2026-11-13T00:00:00+00:00", "spend", "answer", "claude-sonnet-5-5", {"input_tokens": 100_000}, "v1"],
    ["2026-11-13T00:00:01+00:00", "check", "v1", 0.0],
    ["2026-11-13T09:00:00+00:00", "spend", "parse", "claude-haiku-5-5", {"input_tokens": 100_000}, "v1"],
    ["2026-11-13T09:00:01+00:00", "check", "v1", 0.0],
    ["2026-11-13T09:00:02+00:00", "spend", "answer", "claude-sonnet-5-5", {"input_tokens": 100_000}, "v1"],
    ["2026-11-13T09:00:03+00:00", "check", "v1", 0.0],
    ["2026-11-13T09:00:03+00:00", "check", "v2", 0.0],
    ["2026-11-13T09:00:03+00:00", "check", None, 0.0],
    ["2026-11-13T09:00:03+00:00", "check", "v2", 0.58],
    ["2026-11-13T09:00:03+00:00", "check", "v2", 0.59],
    ["2026-11-13T09:00:04+00:00", "spend", "answer", "claude-sonnet-5-5", {"input_tokens": 295_000}, "v3"],
    ["2026-11-13T09:00:05+00:00", "check", "v2", 0.0],
    ["2026-11-13T09:00:06+00:00", "spend", "answer", "claude-sonnet-5-5", {"input_tokens": 5_000}, "v3"],
    ["2026-11-13T09:00:07+00:00", "check", "v2", 0.0],
    ["2026-11-14T00:00:00+00:00", "check", "v1", 0.0],
    ["2026-11-14T00:00:00+00:00", "check", "v1", 0.39],
    ["2026-11-14T00:00:00+00:00", "check", "v1", 0.40],
    ["2026-11-14T00:00:01+00:00", "spend", "answer", "claude-sonnet-5-5", {"input_tokens": 200_000}, "v9"],
    ["2026-11-14T00:00:02+00:00", "check", "v1", 0.0],
    ["2026-12-01T00:00:00+00:00", "check", "v1", 0.0],
]
# (start or finish, key) or (pending, besides), for places of 2 a visitor, 3 in all, at $0.25
IN_PROGRESS = {"per_key": 2, "total": 3, "estimate_usd": 0.25}
IN_PROGRESS_SCRIPT: list[list[Any]] = [
    ["start", "a"], ["start", "a"], ["start", "a"], ["pending", 0], ["pending", 1], ["start", "b"], ["start", "c"],
    ["pending", 1], ["finish", "a"], ["start", "c"], ["start", "d"], ["finish", "a"], ["finish", "b"],
    ["finish", "c"], ["pending", 0], ["pending", 5], ["start", "d"], ["finish", "d"],
]


# ---- the checks: briefings as the model writes them, and saved briefings shown again ---------------------------

KENYA = "Kenya says climate finance for adaptation is too slow. We call on the developed countries to honour " \
        "their pledges."
FIJI = "Fiji warns that climate finance must reach small islands. The organization of this work has been slow."
SOURCES = [
    {"n": 1, "kind": "record", "title": "Mr. Kimani (Kenya), Security Council, 9000th meeting, 1 March 2023",
     "url": "https://undocs.org/en/S/PV.9000", "blocks": [KENYA],
     "meta": {"heading": "Security Council, 9000th meeting — Climate and security"}},
    {"n": 2, "kind": "record", "title": "Mr. Prasad (Fiji), Security Council, 9000th meeting, 1 March 2023",
     "url": "https://undocs.org/en/S/PV.9000", "blocks": [FIJI], "meta": {}},
]


def cite(n: int, text: str) -> dict[str, Any]:
    return {"type": "search_result_location", "search_result_index": n, "cited_text": text, "source": "x"}


CHECK_CASES: list[dict[str, Any]] = [
    {"name": "a cited claim, a lead-in and an uncited claim", "blocks": [
        {"text": "Kenya said that "},
        {"text": "climate finance for adaptation is too slow.", "citations": [cite(0, KENYA)]},
        {"text": " This was a turning point for the Council."}]},
    {"name": "a quotation found word for word, and one that is not", "blocks": [
        {"text": "Kenya's delegate called it \"too slow\"", "citations": [cite(0, KENYA)]},
        {"text": " and Fiji said it was \"a moral outrage\".", "citations": [cite(1, FIJI)]}]},
    {"name": "a citation to a source that is not there", "blocks": [
        {"text": "Tuvalu agreed.", "citations": [cite(7, "Tuvalu agrees")]},
        {"text": " Fiji warned of delay.", "citations": [cite(1, FIJI)]}]},
    {"name": "judgemental words in the site's own voice, and inside a quotation", "blocks": [
        {"text": "Kenya slammed the regime, and the West was blamed.", "citations": [cite(0, KENYA)]},
        {"text": " Farms in the west of the country were mentioned. Kenya claimed that it was \"a scandal\"."},
        {"text": " The benefits claimed were large.", "citations": [cite(1, FIJI)]}]},
    {"name": "American spelling in the site's words, and in a quotation", "blocks": [
        {"text": "Fiji criticized the organization of the work, saying \"The organization of this work has been "
                 "slow.\"", "citations": [cite(1, FIJI)]}]},
    {"name": "an opener about the records, then the briefing", "blocks": [
        {"text": "Across the records retrieved, two positions emerge. "},
        {"text": "Kenya wants faster finance.", "citations": [cite(0, KENYA)]}]},
    {"name": "notes about the evidence, wide and narrow", "blocks": [
        {"text": "Kenya wants faster finance.", "citations": [cite(0, KENYA)]},
        {"text": " Gas was supplied to two million homes. The votes on the resolution were not supplied. No vote "
                 "records were retrieved for 2021. Only two records were retrieved. The minister said that aid was "
                 "supplied late."}]},
    {"name": "an honorific's full stop ends no sentence", "blocks": [
        {"text": "Mr. Kimani said "},
        {"text": "the developed countries should honour their pledges.", "citations": [cite(0, KENYA)]},
        {"text": " Dr. Prasad disagreed. St. Lucia was not heard."}]},
    {"name": "headings, a list and a lead-in in pieces", "blocks": [
        {"text": "## African States\n\n"},
        {"text": "- Kenya: "},
        {"text": "finance is too slow", "citations": [cite(0, KENYA)]},
        {"text": "\n- Fiji: "},
        {"text": "finance must reach small islands", "citations": [cite(1, FIJI)]},
        {"text": "\n\nIn short:"}]},
    {"name": "no records", "blocks": [
        {"text": "No records found. The archive holds nothing on this subject between those dates."}]},
    {"name": "nothing at all", "blocks": []},
    {"name": "quotes and apostrophes to curl", "blocks": [
        {"text": "Kenya's delegate said \""},
        {"text": "climate finance for adaptation is too slow", "citations": [cite(0, KENYA)]},
        {"text": "\" in 2023, and it's 'urgent'."}]},
]


def an_answer() -> dict[str, Any]:
    """A checked briefing as the site's verifier reports one: a cited sentence with a quotation, a sentence marked
    for checking that today's rules clear when it is shown again (it only leads in to the citation after it), one
    that stays marked, and a judgemental-word mark that the list does not hold any more."""
    segments = [
        {"text": "Kenya said that climate finance was \"too slow\".", "sources": [1],
         "cited": [{"n": 1, "text": "climate finance for adaptation is too slow"}], "status": "cited", "loaded": [],
         "misquotes": []},
        {"text": " In the debate that followed:", "sources": [], "cited": [], "status": "unsupported", "loaded": [],
         "misquotes": []},
        {"text": " Fiji warned that finance must reach small islands.", "sources": [2],
         "cited": [{"n": 2, "text": "climate finance must reach small islands"}], "status": "cited", "loaded": [],
         "misquotes": []},
        {"text": " This was a turning point for every delegation in the room.", "sources": [], "cited": [],
         "status": "unsupported", "loaded": [{"start": 12, "end": 19, "word": "turning"}], "misquotes": []},
    ]
    return {"segments": segments, "markdown": "".join(s["text"] for s in segments), "no_records": False,
            "citations_total": 2, "citations_invalid": 0, "grounded_ratio": 0.5, "unsupported_segments": 2,
            "loaded_words": 1, "quotes_total": 1, "quotes_unverified": 0}


def _saved(answer: Any, **more: Any) -> list[dict[str, Any]]:
    return [{"type": "plan", "plan": {"question": Q, "interpretation": "The Council's debates on \"climate\"."},
             "usage": None},
            {"type": "sources", "sources": [{"n": 1, "title": "Kenya's \"statement\""}]},
            {"type": "text", "text": "Kenya said \"no\"."},
            {"type": "final", "answer": answer, "usage": None, **more}]


_REGIME = 'Kenya called it "too slow" and the Council\'s regime was mentioned.'
_LEAD = {"segments": [{"text": 'He said "no." Mr Smith said ', "status": "unsupported"},
                      {"text": "we will act", "status": "cited", "sources": [1]}], "unsupported_segments": 1}
SAVED_BRIEFINGS: list[dict[str, Any]] = [
    {"name": "marks made by rules since corrected", "events": _saved({
        "markdown": "Kenya said climate finance is slow. The savings claimed were large.",
        "segments": [{"text": "Kenya said ", "status": "unsupported"},
                     {"text": "climate finance is slow.", "status": "cited", "sources": [1]},
                     {"text": " The savings claimed were large.", "status": "cited", "sources": [1],
                      "loaded": [{"start": 13, "end": 20, "word": "claimed"}]}],
        "unsupported_segments": 1, "grounded_ratio": 0.5, "loaded_words": 1})},
    {"name": "saved before quotes were curled, with a mark that stands", "events": _saved({
        "segments": [{"text": _REGIME, "status": "cited", "sources": [1],
                      "loaded": [{"start": _REGIME.index("regime"), "end": _REGIME.index("regime") + 6,
                                  "word": "regime"}]}], "loaded_words": 1})},
    {"name": "a claim before a quotation, saved straight", "events": _saved(_LEAD)},
    {"name": "a claim before a quotation, saved curled", "events": _saved({
        **_LEAD, "segments": [{"text": "He said “no.” Mr Smith said ", "status": "unsupported"},
                              _LEAD["segments"][1]]})},
    {"name": "a short claim stays a point to check", "events": _saved({
        "segments": [{"text": "Kenya agreed.", "status": "unsupported"},
                     {"text": " Fiji said so too.", "status": "cited", "sources": [2]}],
        "unsupported_segments": 1, "grounded_ratio": 0.6})},
    {"name": "the scripted briefing", "events": _saved(an_answer(), model="claude-sonnet-5-5",
                                                       stop_reason="end_turn", briefing_id="briefing001")},
    {"name": "no records", "events": _saved({
        "segments": [{"text": "No records found. The archive contains no records matching this question and these "
                              "filters.", "sources": [], "cited": [], "status": "connective"}],
        "markdown": "No records found.", "no_records": True, "citations_total": 0, "citations_invalid": 0,
        "grounded_ratio": 1.0, "unsupported_segments": 0})},
    {"name": "no briefing was written", "events": _saved(None, note="Sources only: no briefing was written.")},
    {"name": "an answer with no segments", "events": _saved({"markdown": "x", "segments": []})},
]


# ---- a scripted pipeline, in the site's own shapes ---------------------------------------------------------------

PARSE_USAGE = {"input_tokens": 1200, "output_tokens": 80, "cache_read_tokens": 0, "cache_write_tokens": 0}
ANSWER_USAGE = {"input_tokens": 9000, "output_tokens": 700, "cache_read_tokens": 3000, "cache_write_tokens": 2000}
DECLINED = "The model declined to answer this question."
NO_KEY_NOTE = "Showing the evidence only (no Anthropic API key set)."
CARDS = [
    {"n": 1, "title": "Security Council, 9000th meeting", "group": "KEN", "voted_on": date(2024, 12, 17)},
    {"n": 2, "title": "General Assembly, 79th session", "group": "FJI"},
    {"n": 3, "title": "Security Council, 9001st meeting", "group": "KEN"},
]
NO_RECORDS = {"segments": [{"text": "No records found. The archive contains no records matching this question and "
                                    "these filters.", "sources": [], "cited": [], "status": "connective"}],
              "markdown": "No records found.", "no_records": True, "citations_total": 0, "citations_invalid": 0,
              "grounded_ratio": 1.0, "unsupported_segments": 0}


class FakePlan:
    def __init__(self, question: str, parser: str) -> None:
        self.question, self.parser, self.parse_error, self.group_by = question, parser, None, "region"

    def to_dict(self) -> dict[str, Any]:
        return {"question": self.question, "parser": self.parser, "filters": {"date_from": "2020-01-01"},
                "interpretation": "Debates on \"climate finance\" since 2020."}


class FakeSource:
    def __init__(self, card: dict[str, Any]) -> None:
        self._card, self.kind = card, "record"

    def card(self) -> dict[str, Any]:
        return dict(self._card)


class ScriptedPipeline:
    """In place of the site's ``Pipeline``: ``run`` sends the events of the next behaviour in ``plays`` (or of
    ``"written"``), in the site's own shapes, moving the clock as a real one takes time. ``find`` is what the
    site's sources-only search asks of it; the question is read there by the site's real built-in rules, with
    ``countries`` (the site's reference data) and ``today``."""

    def __init__(self, tick: Callable[[float], None], model: str, countries: Any, *, has_model: bool = True) -> None:
        self.tick, self.model, self.model_on = tick, model, has_model
        self.today = date(2026, 11, 13)
        self.engine = NS(countries=countries)
        self.plays: list[str] = []
        self.calls: list[str] = []
        self.entered, self.gate, self.closed = threading.Event(), threading.Event(), threading.Event()

    def has_model(self) -> bool:
        return self.model_on

    def find(self, plan: Any, req: Any) -> tuple[Any, ...]:
        result = NS(candidates=12, semantic_available=True, evidence={"records": 3}, timings={}, records=[1, 2, 3])
        return result, ["a vote"], [FakeSource(c) for c in CARDS]

    def _plan(self, question: str, **more: Any) -> dict[str, Any]:
        return {"type": "plan", "plan": FakePlan(question, "model" if self.model_on else "offline").to_dict(),
                "usage": dict(PARSE_USAGE) if self.model_on else None, **more}

    def run(self, req: Any) -> Iterator[dict[str, Any]]:
        play = self.plays.pop(0) if self.plays else "written"
        self.calls.append(play)
        tick = self.tick
        tick(0.4)
        yield self._plan(req.question, **({"parse_error": "AuthenticationError: bad key"} if play == "unread" else
                                          {"parse_error": ""} if play == "unread_empty" else {}))
        tick(0.3)
        yield {"type": "sources", "sources": [dict(c) for c in CARDS], "candidates": 12, "semantic": True,
               "evidence": {"records": 3, "votes": 1}}
        if play == "no_records":
            yield {"type": "final", "answer": json.loads(json.dumps(NO_RECORDS)), "usage": None}
            return
        if not self.model_on:
            yield {"type": "final", "answer": None, "usage": None, "note": NO_KEY_NOTE}
            return
        if play == "held":  # a briefing still being written while another question arrives
            self.entered.set()
            assert self.gate.wait(20)
        tick(1.5)
        # as the model really sends them: a citation comes before the text of the block it belongs to
        yield {"type": "citation", "search_result_index": 0, "cited_text": "climate finance for adaptation is too slow"}
        yield {"type": "text", "text": "Kenya said that climate finance "}
        if play == "raises":
            raise RuntimeError("the model's stream broke")
        if play == "left":  # the reader goes while the briefing is being written
            try:
                self.entered.set()
                assert self.gate.wait(20)
                for word in ("was ", "\"too ", "slow\"."):
                    tick(0.1)
                    yield {"type": "text", "text": word}
            finally:
                self.closed.set()
            raise AssertionError("the pipeline was not stopped when the reader left")
        yield {"type": "text", "text": "was \"too slow\"."}
        yield {"type": "citation", "search_result_index": 1, "cited_text": "climate finance must reach small islands"}
        yield {"type": "text", "text": " Fiji warned that finance must reach small islands."}
        tick(2.0)
        if play == "declined":  # the call was still paid for, so its token counts go with the event to be recorded
            yield {"type": "error", "message": DECLINED, "usage": dict(ANSWER_USAGE), "model": self.model}
            return
        if play == "declined_with_more":  # an error event with keys of its own: the site sends only the message
            yield {"type": "error", "message": DECLINED, "usage": dict(ANSWER_USAGE), "model": self.model,
                   "stop_reason": "refusal", "detail": {"why": "policy"}}
            return
        tick(0.6)
        yield {"type": "final", "answer": an_answer(), "usage": dict(ANSWER_USAGE), "model": self.model,
               "stop_reason": "end_turn"}


# ---- the two tellings of /api/ask ------------------------------------------------------------------------------


class Side:
    """One telling of a scenario: the site's web app (as it was, or as it is), a pipeline and the state database.

    ``client`` is a ``fastapi.testclient.TestClient``; ``stream(body)`` gives the answer's pieces as the web server
    would take them, so that it can be closed part-way; ``connect()`` opens the state database; ``new`` says which
    tables to read (the site's own before wts-core, the package's after)."""

    def __init__(self, *, client: Any, pipeline: Any, connect: Callable[[], sqlite3.Connection], new: bool,
                 stream: Callable[[dict[str, Any]], Iterator[bytes]], another_client: Callable[[], Any],
                 calls: Callable[[], list[Any]] | None = None) -> None:
        self.client, self.pipeline, self.connect, self.new = client, pipeline, connect, new
        self.stream, self.another_client = stream, another_client
        self._calls = calls

    def ask(self, question: str, play: str = "written", **body: Any) -> list[Any]:
        if play != "written":
            self.pipeline.plays.append(play)
        response = self.client.post("/api/ask", json={"question": question, **body})
        return [response.status_code, response.content.decode()]

    def calls(self) -> list[Any]:
        return self._calls() if self._calls else list(self.pipeline.calls)

    def rows(self) -> dict[str, list[list[Any]]]:
        return state_rows(self.connect(), self.new)


def state_rows(con: sqlite3.Connection, new: bool) -> dict[str, list[list[Any]]]:
    """What the flow of a briefing wrote, in one shape whichever tables hold it."""
    t = {"usage": "wts_spending", "cache": "wts_answer_cache", "briefings": "wts_briefings",
         "failures": "wts_failures", "at": "created_at"} if new else \
        {"usage": "usage", "cache": "answer_cache", "briefings": "briefings", "failures": "failures", "at": "at"}
    try:
        return {
            "spending": [list(r) for r in con.execute(
                "SELECT at, kind, model, visitor, input_tokens, output_tokens, cache_read_tokens, "
                f"cache_write_tokens, cost_usd FROM {t['usage']} ORDER BY id")],
            "cache": [list(r) for r in con.execute(f"SELECT key, created_at, events FROM {t['cache']} ORDER BY key")],
            "briefings": [list(r) for r in con.execute(
                f"SELECT id, {t['at']}, question, options, events, withdrawn_at, withdrawn_reason "
                f"FROM {t['briefings']} ORDER BY id")],
            "failures": [list(r) for r in con.execute(f"SELECT at, kind, question, error FROM {t['failures']} "
                                                      "ORDER BY id")],
        }
    finally:
        con.close()


def while_one_is_written(side: Side, then: Callable[[], list[Any]]) -> list[Any]:
    """Start a briefing that stays in progress, do ``then`` while it is, and let it finish. What was sent: the
    held briefing's answer first, then whatever ``then`` sent."""
    side.pipeline.plays.append("held")
    held: list[Any] = []
    other = side.another_client()
    thread = threading.Thread(target=lambda: held.append(other.post("/api/ask", json={"question": Q})))
    thread.start()
    assert side.pipeline.entered.wait(20)
    try:
        sent = then()
    finally:
        side.pipeline.gate.set()
        thread.join(30)
    return [[held[0].status_code, held[0].content.decode()], *sent]


def the_reader_leaves(side: Side) -> list[Any]:
    side.pipeline.plays.append("left")
    stream = side.stream({"question": Q})
    received = b""
    for chunk in stream:
        received += chunk
        if chunk.startswith(b"event: text"):
            break
    stream.close()  # the reader has gone: the server closes the response
    side.pipeline.gate.set()
    assert side.pipeline.closed.wait(20)  # the pipeline was stopped, and with it the model
    for thread in threading.enumerate():
        if thread.name == "sse":
            thread.join(20)
    return [[200, received.decode()], side.ask(Q)]


# Every case of wts-core's docs/adr/0009 "Parity", and the event shapes its independent review left to each site.
# name -> (what is done, has a model, [usage] settings for the telling)
SCRIPTED: dict[str, tuple[Callable[[Side], list[Any]], bool, dict[str, Any]]] = {
    "a fresh question, and the same again from the cache": (
        lambda s: [s.ask(Q), s.ask("what has been said about  CLIMATE finance since 2020?")], True, {}),
    "with no hours to keep an answer, every question is written afresh": (
        lambda s: [s.ask(Q), s.ask(Q)], True, {"answer_cache_hours": 0}),
    "a follow-up is never cached": (
        lambda s: [s.ask(Q), s.ask(Q2, history=EARLIER), s.ask(Q2, history=EARLIER), s.ask(Q2)], True, {}),
    "a question the model could not read": (lambda s: [s.ask(Q, "unread"), s.ask(Q)], True, {}),
    "a question whose reading failed with nothing said": (lambda s: [s.ask(Q, "unread_empty")], True, {}),
    "sources only": (lambda s: [s.ask(Q, sources_only=True), s.ask(Q), s.ask(Q, sources_only=True)], True, {}),
    "sources only, with the site's budget spent": (
        lambda s: [s.ask(Q, sources_only=True)], True, {"daily_spend_cap_usd": 0.0}),
    "the visitor's limit stops new briefings, and cached ones still load": (
        lambda s: [s.ask(Q), s.ask(Q2), s.ask(Q)], True, {"per_visitor_daily_questions": 1}),
    "the daily cap": (lambda s: [s.ask(Q)], True, {"daily_spend_cap_usd": 0.0}),
    "the monthly cap": (lambda s: [s.ask(Q)], True, {"monthly_spend_cap_usd": 0.0}),
    "too many requests in a minute": (lambda s: [s.ask(Q), s.ask(Q2)], True, {"requests_per_minute": 1}),
    "one place too many for the visitor": (
        lambda s: [*while_one_is_written(s, lambda: [s.ask(Q2)]), s.ask(Q2)], True,
        {"briefings_at_once_per_visitor": 1}),
    "one place too many for the site": (
        lambda s: [*while_one_is_written(s, lambda: [s.ask(Q2)]), s.ask(Q2)], True,
        {"briefings_at_once_per_visitor": 5, "briefings_at_once": 1}),
    "the second check of the caps counts the others in progress": (
        lambda s: while_one_is_written(s, lambda: [s.ask(Q2)]), True, {"daily_spend_cap_usd": 0.05}),
    "the pipeline raising mid-stream": (
        lambda s: [s.ask(Q, "raises"), s.ask(Q)], True, {"briefings_at_once_per_visitor": 1}),
    "the model refusing": (lambda s: [s.ask(Q, "declined"), s.ask(Q)], True, {}),
    "an error event with keys of its own": (lambda s: [s.ask(Q, "declined_with_more")], True, {}),
    "no records": (lambda s: [s.ask(Q, "no_records"), s.ask(Q)], True, {}),
    "no API key": (lambda s: [s.ask(Q), s.ask(Q), s.ask(Q, sources_only=True)], False, {}),
    "the reader leaving while the briefing is written": (
        the_reader_leaves, True, {"briefings_at_once_per_visitor": 1}),
    "a question too short, and one too long": (
        lambda s: [s.ask("Hi"), s.ask("x" * 501), s.ask(Q, mode="everything")], True, {}),
}


def scripted_asks(make: Callable[..., Side], only: str | None = None) -> dict[str, Any]:
    """Each scripted case played on a side of its own: what was sent, what was written, what the pipeline did.
    ``make(has_model=…, **usage)`` gives the side."""
    out = {}
    for name, (scenario, has_model, usage) in SCRIPTED.items():
        if only and name != only:
            continue
        side = make(has_model=has_model, **usage)
        out[name] = {"sent": scenario(side), "rows": side.rows(), "calls": side.calls()}
    return out


# ---- the same through the site's real pipeline, over a small index -------------------------------------------------

D = date(2023, 3, 1)


def speech(record_id: str, d: date, code: str | None, text: str, **kw: Any) -> dict[str, Any]:
    """A stored speech record with sensible defaults (tests/conftest.py's, kept here so the recorder can run on
    its own)."""
    source = kw.pop("source", "sc")
    return {
        "record_id": record_id, "source": source,
        "body": "Security Council" if source == "sc" else "General Assembly",
        "date": d, "date_precision": "day" if source == "sc" else "session", "year": d.year,
        "session": None if source == "sc" else d.year - 1945, "meeting": kw.pop("meeting", "9000th meeting"),
        "symbol": kw.pop("symbol", "S/PV.9000" if source == "sc" else None), "speech_number": kw.pop("n", 1),
        "agenda": kw.pop("agenda", "Maintenance of international peace and security"), "agenda_area": "Thematic",
        "speaker_name": kw.pop("speaker", "Mr. Smith"), "speaker_post": None, "speaker_role": kw.pop("role", ""),
        "participant": kw.pop("participant", "member"), "country_code": code,
        "country_label": kw.pop("label", code), "entity_kind": kw.pop("kind", "member_state"),
        "on_behalf_of": kw.pop("behalf", None), "language": kw.pop("language", None), "ocr": False,
        "text": text, "url": "https://undocs.org/en/S/PV.9000", **kw,
    }


SPEECHES = [
    speech("sc:S/PV.9000:0005", D, "KEN", "Kenya says climate finance for adaptation is too slow.", n=5),
    speech("sc:S/PV.9000:0006", D, "FJI", "Fiji warns climate finance must reach small islands.",
           participant="guest", n=6, language="French"),
]
SETTINGS_FOR_TESTS = {"embedding.backend": "hashing", "embedding.dim": 64, "live.gd_bundled": ""}


class FakeStream:
    """The SDK's stream of one message, as the site's own tests fake it."""

    def __init__(self, blocks: list[dict[str, Any]], stop_reason: str = "end_turn") -> None:
        self.blocks, self.stop_reason = blocks, stop_reason

    def __enter__(self) -> FakeStream:
        return self

    def __exit__(self, *a: Any) -> bool:
        return False

    def __iter__(self) -> Iterator[Any]:
        for b in self.blocks:
            yield NS(type="content_block_delta", delta=NS(type="text_delta", text=b["text"]))
            for c in b.get("citations", []):
                yield NS(type="content_block_delta", delta=NS(type="citations_delta", citation=NS(**c)))

    def get_final_message(self) -> Any:
        content = [NS(type="text", text=b["text"], citations=[NS(type="search_result_location", **c)
                                                              for c in b.get("citations", [])])
                   for b in self.blocks]
        return NS(content=content, stop_reason=self.stop_reason, model="claude-sonnet-5-5",
                  usage=NS(input_tokens=1000, output_tokens=200, cache_read_input_tokens=400,
                           cache_creation_input_tokens=300))


class FakeSDK:
    """In place of the Anthropic SDK's client. It reads a question as ``reading`` says (or fails to, when that is
    None), and answers by citing the first block of the first source it is given, in straight quotes, then adds a
    sentence with a judgemental word. What it was asked is kept in ``calls`` (briefings) and ``parses``."""

    def __init__(self, reading: dict[str, Any] | None = None, refuse: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.parses: list[dict[str, Any]] = []
        self.reading, self.refuse = reading, refuse
        self.beta = NS(messages=NS(stream=self._stream))
        self.messages = NS(stream=self._stream, parse=self._parse)

    def _parse(self, **kw: Any) -> Any:
        self.parses.append(kw)
        if self.reading is None:
            raise RuntimeError("force offline parse in tests")
        return NS(stop_reason="end_turn", parsed_output=kw["output_format"](**self.reading),
                  usage=NS(input_tokens=1500, output_tokens=90, cache_read_input_tokens=0,
                           cache_creation_input_tokens=1100))

    def _stream(self, **kw: Any) -> FakeStream:
        self.calls.append(kw)
        if self.refuse:
            return FakeStream([], stop_reason="refusal")
        first = next(b for b in kw["messages"][0]["content"] if b["type"] == "search_result")
        quote = first["content"][0]["text"]
        return FakeStream([
            {"text": "Kenya's delegate said \""},
            {"text": quote[:44], "citations": [{"search_result_index": 0, "cited_text": quote, "source": "x"}]},
            {"text": "\" in 2023. The regime was mentioned."},
        ])


READING = {"search_text": "climate finance", "date_from": "2020-01-01", "date_to": None, "sources": [],
           "countries": ["Kenya", "Atlantis"], "groups": [], "group_by": "region", "wants_votes": False,
           "interpretation": "Statements on \"climate finance\" since 2020, with the color of each country's view."}
Q_NOTHING = "What has been said about deep-sea mining in 1994?"
PLAN_NOTHING = {"search_text": "deep-sea mining", "filters": {"date_from": "1994-01-01", "date_to": "1994-12-31"}}

# name -> (the SDK, has a model, the requests, [usage] settings)
REAL: dict[str, tuple[Callable[[], FakeSDK | None], list[dict[str, Any]], dict[str, Any]]] = {
    "a briefing, the same again, a follow-up, sources only": (
        lambda: FakeSDK(READING),
        [{"question": Q}, {"question": Q}, {"question": Q2, "history": EARLIER},
         {"question": Q, "sources_only": True}, {"question": Q, "mode": "comprehensive", "recency": 0.9}], {}),
    "the model cannot read the question": (lambda: FakeSDK(None), [{"question": Q}, {"question": Q}], {}),
    "the model declines": (lambda: FakeSDK(READING, refuse=True), [{"question": Q}, {"question": Q}], {}),
    "no records": (lambda: FakeSDK(READING), [{"question": Q_NOTHING, "plan": PLAN_NOTHING},
                                              {"question": Q_NOTHING, "plan": PLAN_NOTHING}], {}),
    "an edited plan": (lambda: FakeSDK(READING), [
        {"question": Q, "plan": {"search_text": "climate finance", "filters": {"countries": ["FJI"]}}}], {}),
    "no API key": (lambda: None, [{"question": Q}, {"question": Q, "sources_only": True}], {}),
    "the daily cap": (lambda: FakeSDK(READING), [{"question": Q}], {"daily_spend_cap_usd": 0.0}),
}


def sdk_calls(sdk: FakeSDK | None) -> list[Any]:
    """What the model was sent, whole: a change to a request is a change a reader could notice."""
    if sdk is None:
        return []
    return [{"parse": {k: (v.__name__ if k == "output_format" else v) for k, v in kw.items()}} for kw in sdk.parses] \
        + [{"stream": kw} for kw in sdk.calls]


def real_asks(make: Callable[..., tuple[Side, Callable[[str], Any]]], only: str | None = None) -> dict[str, Any]:
    """Each case through the site's real pipeline. ``make(sdk, **usage)`` gives the side and a function that opens
    a saved briefing as ``GET /api/briefings/<id>`` and the other public routes do."""
    out = {}
    for name, (make_sdk, requests, usage) in REAL.items():
        if only and name != only:
            continue
        sdk = make_sdk()
        side, get = make(sdk, **usage)
        sent = []
        for body in requests:
            r = side.client.post("/api/ask", json=body)
            sent.append([r.status_code, r.content.decode()])
        saved = [row[0] for row in side.rows()["briefings"]]
        out[name] = {"sent": sent, "rows": side.rows(), "calls": sdk_calls(sdk),
                     "saved": {bid: get(f"/api/briefings/{bid}") for bid in saved},
                     "search": get_post(side.client, "/api/search", requests[0]),
                     "status": {k: v for k, v in get("/api/status")[1].items()
                                if k in ("briefings", "briefings_note")}}
    return out


def get_post(client: Any, path: str, body: dict[str, Any]) -> list[Any]:
    r = client.post(path, json=body)
    return [r.status_code, r.json()]


def events_of(body: str) -> list[Any]:
    out = []
    for chunk in body.split("\n\n"):
        data = [line[6:] for line in chunk.splitlines() if line.startswith("data: ")]
        out.append(json.loads(data[0]) if data else chunk)
    return out


# ---- a simulated Backblaze B2 --------------------------------------------------------------------------------------


class FakeB2:
    def __init__(self, bucket: str = "wts-backups", fail_uploads: int = 0, can_delete: bool = True) -> None:
        self.bucket, self.fail_uploads, self.can_delete = bucket, fail_uploads, can_delete
        self.files: dict[str, bytes] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("b2_authorize_account"):
            if request.headers.get("authorization") != httpx.BasicAuth("kid", "secret")._auth_header:
                return httpx.Response(401)
            return httpx.Response(200, json={"accountId": "acc", "apiUrl": "https://api.b2.test",
                                             "authorizationToken": "tok",
                                             "allowed": {"bucketId": "b1", "bucketName": self.bucket}})
        assert request.headers.get("authorization") in ("tok", "up"), "every call carries a token"
        body = json.loads(request.content or b"{}") if path.startswith("/b2api") else {}
        if path.endswith("b2_get_upload_url"):
            return httpx.Response(200, json={"uploadUrl": "https://up.b2.test/upload", "authorizationToken": "up"})
        if path == "/upload":
            if self.fail_uploads:
                self.fail_uploads -= 1
                return httpx.Response(503)
            assert request.headers["x-bz-content-sha1"] == hashlib.sha1(request.content).hexdigest()  # noqa: S324
            self.files[request.headers["x-bz-file-name"]] = request.content
            return httpx.Response(200, json={"fileName": request.headers["x-bz-file-name"]})
        if path.endswith("b2_list_file_names"):
            return httpx.Response(200, json={"files": [{"fileName": n, "fileId": f"id-{n}"} for n in sorted(self.files)
                                                       if n.startswith(body["prefix"])]})
        if path.endswith("b2_delete_file_version"):
            if not self.can_delete:
                return httpx.Response(401, json={"code": "unauthorized"})
            del self.files[body["fileName"]]
            return httpx.Response(200, json={})
        return httpx.Response(404)


B2_ENV = {"WCS_B2_BUCKET": "wts-backups", "WCS_B2_KEY_ID": "kid", "WCS_B2_APPLICATION_KEY": "secret"}


def b2_client(b2: FakeB2) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(b2))


def offsite_scenario(tmp: Path, *, send: Callable[..., dict[str, Any]], check: Callable[..., dict[str, Any]],
                     error: type[Exception]) -> list[dict[str, Any]]:
    """Off-site copies: ``send(archive, keep, env, client)`` and ``check(env, client)``, at a fixed time."""
    out: list[dict[str, Any]] = []

    def archive(folder: Path, day: str) -> Path:
        folder.mkdir(parents=True, exist_ok=True)
        p = folder / f"wcs-state-{day}.sqlite.gz"
        p.write_bytes(f"backup {day}".encode())
        return p

    def step(name: str, folder: Path, do: Callable[[], Any], b2: FakeB2 | None = None) -> None:
        try:
            result: Any = do()
        except error as exc:
            result = {"raised": str(exc)}
        status = folder / "offsite.json"
        out.append({"case": name, "result": result, "stored": sorted(b2.files) if b2 else None,
                    "offsite.json": json.loads(status.read_text()) if status.exists() else None})

    f = tmp / "not-set-up"
    step("not set up: nothing is sent", f, lambda: send(archive(f, "2026-11-01"), 60, {}, None))
    step("not set up: the check says how", f, lambda: check({}, None))
    b2, f = FakeB2(fail_uploads=2), tmp / "retries"
    for day in ("2026-11-01", "2026-11-02", "2026-11-03"):
        step(f"a copy is stored and the newest two kept ({day})", f,
             lambda day=day, b2=b2, f=f: send(archive(f, day), 2, B2_ENV, b2_client(b2)), b2)
    step("the check counts the copies", f, lambda: check(B2_ENV, b2_client(b2)), b2)
    b2, f = FakeB2(can_delete=False), tmp / "no-delete"
    for day in ("2026-11-01", "2026-11-02"):
        step(f"a key that cannot delete still stores ({day})", f,
             lambda day=day, b2=b2, f=f: send(archive(f, day), 1, B2_ENV, b2_client(b2)), b2)
    b2, f = FakeB2(fail_uploads=5), tmp / "fails"
    step("an upload that keeps failing is reported and recorded", f,
         lambda: send(archive(f, "2026-11-01"), 60, B2_ENV, b2_client(b2)), b2)
    step("a key that is refused", f,
         lambda: send(archive(f, "2026-11-02"), 60, {**B2_ENV, "WCS_B2_KEY_ID": "wrong"}, b2_client(b2)), b2)
    step("a key for another bucket", f,
         lambda: send(archive(f, "2026-11-03"), 60, {**B2_ENV, "WCS_B2_BUCKET": "another"}, b2_client(b2)), b2)
    b2, f = FakeB2(), tmp / "shared"
    b2.files["councils-state-2026-11-01.sqlite.gz"] = b"another site's"
    for day in ("2026-11-01", "2026-11-02"):
        step(f"sites sharing a bucket keep their own ({day})", f,
             lambda day=day, b2=b2, f=f: send(archive(f, day), 1, B2_ENV, b2_client(b2)), b2)
    step("the check counts this site's only", f, lambda: check(B2_ENV, b2_client(b2)), b2)
    return out


# ---- backups -------------------------------------------------------------------------------------------------------


def listing(folder: Path) -> list[dict[str, Any]]:
    """Each file in a folder: its name, who may read it, and what is inside (a gzipped database's tables and rows,
    or its text)."""
    out = []
    for p in sorted(folder.iterdir()) if folder.is_dir() else []:
        entry: dict[str, Any] = {"name": p.name, "mode": oct(stat.S_IMODE(p.stat().st_mode))}
        if p.name.endswith(".sqlite.gz"):
            try:
                entry["holds"] = database_holds(gzip.decompress(p.read_bytes()), folder)
            except (OSError, EOFError, sqlite3.DatabaseError) as exc:
                entry["holds"] = type(exc).__name__
        elif p.suffix == ".json":
            entry["holds"] = json.loads(p.read_text())
        out.append(entry)
    return out


def database_holds(data: bytes, scratch: Path) -> dict[str, Any]:
    tmp = scratch / ".reading.db"
    tmp.write_bytes(data)
    con = sqlite3.connect(tmp)
    try:
        tables = sorted(r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT "
                                                  "LIKE 'sqlite_%' AND name NOT LIKE 'wts_%'"))
        return {t: [list(r) for r in con.execute(f"SELECT * FROM {t} ORDER BY 1")] for t in tables}
    finally:
        con.close()
        tmp.unlink()


def a_state_database(path: Path, cost: float = 0.5) -> Path:
    """A state database with the two tables every backup of this site has had, and one row to tell it by."""
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript("CREATE TABLE IF NOT EXISTS usage (id INTEGER PRIMARY KEY, cost REAL);"
                      "CREATE TABLE IF NOT EXISTS briefings (id TEXT PRIMARY KEY);")
    con.execute("DELETE FROM usage")
    con.execute("INSERT INTO usage (cost) VALUES (?)", (cost,))
    con.commit()
    con.close()
    return path


def backups_scenario(tmp: Path, *, run: Callable[[list[str]], tuple[int, str]],
                     set_day: Callable[[str], None], environment: Callable[..., None],
                     alerts: Callable[[Path, dict[str, str], str], list[dict[str, str]]]) -> dict[str, Any]:
    """The owner's backup commands, word for word, and the alerts for each state of the backups' folder.
    ``run(args)`` runs the site's command line; ``set_day("2026-11-01")`` sets the day a backup is named by;
    ``environment(data_dir, **more)`` sets the site's environment; ``alerts(folder, env, now)`` gives the alerts."""
    out: dict[str, Any] = {"commands": [], "alerts": []}
    data, to = tmp / "data", tmp / "backups"

    def command(case: str, args: list[str]) -> None:
        code, text = run(args)
        out["commands"].append({"case": case, "args": plain(args, tmp=tmp), "exit": code,
                                "output": plain(text, tmp=tmp), "backups": plain(listing(to), tmp=tmp)})

    environment(data)
    set_day("2026-11-01")
    command("a new site has nothing to back up", ["backup", "--to", str(to)])
    command("the off-site check, not set up", ["offsite"])
    a_state_database(data / "state.db", 0.5)
    for n, day in enumerate(("2026-11-01", "2026-11-02", "2026-11-02", "2026-11-03")):
        set_day(day)
        a_state_database(data / "state.db", 0.5 + n)
        command(f"a backup on {day}, keeping two", ["backup", "--to", str(to), "--keep", "2"])
    command("a backup with nothing said of how many to keep", ["backup", "--to", str(to)])
    # restoring
    a_state_database(data / "state.db", 99.0)
    command("restore, refused at the question", ["restore", str(to / "wcs-state-2026-11-02.sqlite.gz")])
    command("restore a backup", ["restore", str(to / "wcs-state-2026-11-02.sqlite.gz"), "--yes"])
    out["restored"] = plain({"state.db": database_holds((data / "state.db").read_bytes(), tmp),
                             "kept": database_holds((data / "state.db.before-restore").read_bytes(), tmp),
                             "files": sorted(p.name for p in data.iterdir())}, tmp=tmp)
    command("a second restore never writes over the kept copy",
            ["restore", str(to / "wcs-state-2026-11-03.sqlite.gz"), "--yes"])
    (data / "state.db.before-restore").unlink()
    command("a backup that is not there", ["restore", str(to / "missing.sqlite.gz"), "--yes"])
    (to / "damaged.sqlite.gz").write_bytes(b"not gzip at all")
    command("a damaged backup", ["restore", str(to / "damaged.sqlite.gz"), "--yes"])
    (to / "text.sqlite.gz").write_bytes(gzip.compress(b"plainly not a database, however long it goes on " * 40))
    command("a backup that is not a database", ["restore", str(to / "text.sqlite.gz"), "--yes"])
    other = sqlite3.connect(tmp / "other.db")
    other.execute("CREATE TABLE something (x)")
    other.commit()
    other.close()
    (to / "foreign.sqlite.gz").write_bytes(gzip.compress((tmp / "other.db").read_bytes()))
    command("another database altogether", ["restore", str(to / "foreign.sqlite.gz"), "--yes"])
    command("an uncompressed database is taken too", ["restore", str(tmp / "other.db"), "--yes"])
    out["after"] = plain({"state.db": database_holds((data / "state.db").read_bytes(), tmp),
                          "files": sorted(p.name for p in data.iterdir())}, tmp=tmp)
    for extra in ("damaged.sqlite.gz", "text.sqlite.gz", "foreign.sqlite.gz"):
        (to / extra).unlink()

    # the alerts, for each state of the folder
    now = "2026-11-06T12:00:00+00:00"
    seconds = datetime.fromisoformat(now).timestamp()

    def alert(case: str, folder: Path, env: dict[str, str]) -> None:
        out["alerts"].append({"case": case, "alerts": alerts(folder, env, now)})

    alert("no backups folder", tmp / "none", {})
    folder = tmp / "alerts"
    folder.mkdir()
    alert("an empty folder, no off-site copy set up", folder, {})
    alert("an empty folder, off-site set up", folder, B2_ENV)
    newest = folder / "wcs-state-2026-11-01.sqlite.gz"
    newest.write_bytes(b"x")
    os.utime(newest, (seconds - 3 * 86400, seconds - 3 * 86400))
    alert("a backup three days old, no off-site copy yet", folder, B2_ENV)
    alert("a backup three days old, on this server only", folder, {})
    os.utime(newest, (seconds - 50 * 3600, seconds - 50 * 3600))
    alert("a backup exactly fifty hours old", folder, B2_ENV)
    os.utime(newest, (seconds - 3 * 3600, seconds - 3 * 3600))
    (folder / "offsite.json").write_text(json.dumps({"ok": False, "when": "2026-11-06T03:00:00+00:00",
                                                     "error": "Backblaze did not accept the key"}))
    alert("the last off-site copy failed", folder, B2_ENV)
    (folder / "offsite.json").write_text(json.dumps({"ok": True, "when": "2026-11-02T03:00:00+00:00"}))
    alert("the last off-site copy is four days old", folder, B2_ENV)
    (folder / "offsite.json").write_text(json.dumps({"ok": True, "when": "2026-11-06T03:00:00+00:00"}))
    alert("all is well", folder, B2_ENV)
    (folder / "offsite.json").write_text("not json")
    alert("a record of the off-site copy that cannot be read", folder, B2_ENV)
    return out


# ---- reports, statements, saved briefings: what is sent, and what the owner types ------------------------------

WORDS = "Our delegation spoke on behalf of the group, and the record of the meeting shows the whole statement."
STATEMENT = {"page": "/b/Briefing01", "name": "  Amina   Example ", "role": "Permanent Representative of Kenya to "
             "the United Nations", "text": f"  {WORDS}\r\n\r\n\r\nSecond paragraph.  ", "link": "", "email":
             " mission@example.org "}
LONG_WORDS = " ".join(["word"] * 151)
LONG_CHARS = " ".join(["extraordinarily"] * 140)   # within 150 words, but over 1,500 characters

HTTP_STEPS: list[dict[str, Any]] = [
    # reports
    {"case": "a report of an error", "post": "/api/report",
     "json": {"kind": "error", "page": " /b/Briefing01 ", "message": "  The date is wrong.  ",
              "contact": " reader@example.org "}},
    {"case": "a report with no kind is an error", "post": "/api/report", "json": {"message": "Something is wrong."}},
    {"case": "a report of unfairness", "post": "/api/report", "json": {"kind": "unfair", "message": "A loaded word."}},
    {"case": "a report under the old kind", "post": "/api/report",
     "json": {"kind": "problem", "message": "From an old link."}},
    {"case": "a takedown request", "post": "/api/report",
     "json": {"kind": "takedown", "page": "https://wherecountriesstand.com/b/Briefing02",
              "message": "Please remove my name.", "contact": "named@example.org"}},
    {"case": "another kind of report", "post": "/api/report", "json": {"kind": "other", "message": "A thought."}},
    {"case": "a report of a kind there is not", "post": "/api/report", "json": {"kind": "praise", "message": "Lovely."}},
    {"case": "a report too short", "post": "/api/report", "json": {"kind": "error", "message": "No"}},
    {"case": "a report of spaces", "post": "/api/report", "json": {"kind": "error", "message": "          "}},
    {"case": "a report too long", "post": "/api/report", "json": {"kind": "error", "message": "x" * 4001}},
    {"case": "a report with a contact too long", "post": "/api/report",
     "json": {"kind": "error", "message": "Something is wrong.", "contact": "c" * 201}},
    {"case": "a report with a page too long", "post": "/api/report",
     "json": {"kind": "error", "message": "Something is wrong.", "page": "p" * 301}},
    # statements
    {"case": "a statement", "post": "/api/statements", "json": STATEMENT},
    {"case": "a statement by the briefing's full address, with a link", "post": "/api/statements",
     "json": {**STATEMENT, "page": "https://www.wherecountriesstand.com/b/Briefing01/", "name": "Second Sender",
              "link": " https://undocs.org/en/S/PV.9000 ", "text": "One word."}},
    {"case": "a statement by the test server's own address", "post": "/api/statements",
     "json": {**STATEMENT, "page": "http://testserver/b/Briefing02", "name": "Third Sender"}},
    {"case": "a statement with no address", "post": "/api/statements", "json": {**STATEMENT, "page": "  "}},
    {"case": "a statement beside another site's page", "post": "/api/statements",
     "json": {**STATEMENT, "page": "https://example.org/b/Briefing01"}},
    {"case": "a statement beside something that is not a briefing", "post": "/api/statements",
     "json": {**STATEMENT, "page": "/about.html"}},
    {"case": "a statement beside a briefing there is not", "post": "/api/statements",
     "json": {**STATEMENT, "page": "/b/NoSuch0001"}},
    {"case": "a statement beside a withdrawn briefing", "post": "/api/statements",
     "json": {**STATEMENT, "page": "/b/Withdrawn1"}},
    {"case": "a statement with no name", "post": "/api/statements", "json": {**STATEMENT, "name": " A "}},
    {"case": "a statement with no role", "post": "/api/statements", "json": {**STATEMENT, "role": ""}},
    {"case": "a statement with no words", "post": "/api/statements", "json": {**STATEMENT, "text": "   "}},
    {"case": "a statement of 151 words", "post": "/api/statements", "json": {**STATEMENT, "text": LONG_WORDS}},
    {"case": "a statement within 150 words and too many characters", "post": "/api/statements",
     "json": {**STATEMENT, "text": LONG_CHARS}},
    {"case": "a statement with a link that is not a web address", "post": "/api/statements",
     "json": {**STATEMENT, "link": "javascript:alert(1)"}},
    {"case": "a statement with a link with a quote in it", "post": "/api/statements",
     "json": {**STATEMENT, "link": "https://example.org/\"onmouseover=\"x"}},
    {"case": "a statement with no email address", "post": "/api/statements", "json": {**STATEMENT, "email": "nobody"}},
    {"case": "a statement with two things wrong: the address and the words", "post": "/api/statements",
     "json": {**STATEMENT, "page": "/about.html", "text": LONG_WORDS}},
    {"case": "a statement with two things wrong: the name and the words", "post": "/api/statements",
     "json": {**STATEMENT, "name": "", "text": LONG_WORDS}},
    {"case": "a statement with a name too long for the form", "post": "/api/statements",
     "json": {**STATEMENT, "name": "n" * 201}},
    {"case": "a statement with a field missing", "post": "/api/statements",
     "json": {k: v for k, v in STATEMENT.items() if k != "email"}},
    # what the public may read, before anything is published
    {"case": "no statement is shown while it waits", "get": "/api/statements?page=/b/Briefing01"},
    {"case": "statements asked for with nothing said", "get": "/api/statements"},
    {"case": "statements of a country that is not a code", "get": "/api/statements?country=Kenya"},
    {"case": "statements beside something that is not a briefing", "get": "/api/statements?page=/about.html"},
    {"case": "a saved briefing", "get": "/api/briefings/Briefing01"},
    {"case": "a withdrawn briefing", "get": "/api/briefings/Withdrawn1"},
    {"case": "a briefing there is not", "get": "/api/briefings/NoSuch0001"},
    {"case": "the corrections log", "get": "/api/corrections"},
    {"case": "a saved briefing's page", "get": "/b/Briefing01", "page": True},
    {"case": "a withdrawn briefing's page", "get": "/b/Withdrawn1", "page": True},
    {"case": "the page of a briefing there is not", "get": "/b/NoSuch0001", "page": True},
]

# The owner's commands as they were typed before wts-core, in order, on the rows the steps above left.
CLI_STEPS: list[dict[str, Any]] = [
    {"case": "the open reports", "args": ["reports"]},
    {"case": "close a report", "args": ["close-report", "1"]},
    {"case": "close it again", "args": ["close-report", "1"]},
    {"case": "close a report there is not", "args": ["close-report", "99"]},
    {"case": "every report", "args": ["reports", "--all"]},
    {"case": "the statements waiting or shown", "args": ["statements"]},
    {"case": "publish with nothing said of how the sender was confirmed", "args": ["publish-statement", "1"]},
    {"case": "publish with an empty note", "args": ["publish-statement", "1", "--checked", "  "]},
    {"case": "publish for a country that is not a code", "args": [
        "publish-statement", "1", "--checked", "Reply from the mission's published address.", "--country", "Kenya"]},
    {"case": "publish", "args": [
        "publish-statement", "1", "--checked", "Reply  from the mission's published address, 14 November.",
        "--country", "ken"]},
    {"case": "publish it again", "args": ["publish-statement", "1", "--checked", "Again."]},
    {"case": "publish one there is not", "args": ["publish-statement", "99", "--checked", "x"]},
    {"case": "decline", "args": ["decline-statement", "2", "--reason", "It is about another delegation's conduct."]},
    {"case": "decline with no reason", "args": ["decline-statement", "3"]},
    {"case": "decline one already declined", "args": ["decline-statement", "2", "--reason", "Again."]},
    {"case": "update the words of a published statement", "args": [
        "update-statement", "1", "--text", "New words, as the author sent them."]},
    {"case": "update with too many words", "args": ["update-statement", "1", "--text", LONG_WORDS]},
    {"case": "update the name, the role, the link and the country", "args": [
        "update-statement", "1", "--name", "Amina B. Example", "--role", "Ambassador", "--link",
        "https://undocs.org/en/S/PV.9001", "--country", "FJI"]},
    {"case": "update with a bad link", "args": ["update-statement", "1", "--link", "ftp://example.org/x"]},
    {"case": "take the link off", "args": ["update-statement", "1", "--link", ""]},
    {"case": "update with nothing to change", "args": ["update-statement", "1"]},
    {"case": "update a declined statement", "args": ["update-statement", "2", "--name", "Someone Else"]},
    {"case": "publish the third", "args": ["publish-statement", "3", "--checked", "Telephoned the mission."]},
    {"case": "withdraw a statement, at its author's request", "args": ["withdraw-statement", "3"]},
    {"case": "withdraw it again", "args": ["withdraw-statement", "3"]},
    {"case": "the statements waiting or shown, now", "args": ["statements"]},
    {"case": "every statement", "args": ["statements", "--all"]},
    {"case": "the same under the sister site's name", "args": ["list-statements", "--all"]},
    {"case": "withdraw a briefing", "args": ["withdraw", "Briefing02", "--reason", "It misattributed a statement."]},
    {"case": "withdraw a briefing there is not", "args": ["withdraw", "NoSuch0001", "--reason", "x"]},
    {"case": "withdraw a briefing with no reason", "args": ["withdraw", "Briefing01"]},
]

AFTER_STEPS: list[dict[str, Any]] = [
    {"case": "the published statement beside its briefing", "get": "/api/statements?page=/b/Briefing01"},
    {"case": "the same by the briefing's full address", "get":
     "/api/statements?page=https://wherecountriesstand.com/b/Briefing01"},
    {"case": "a country's statements", "get": "/api/statements?country=fji"},
    {"case": "another country's", "get": "/api/statements?country=KEN"},
    {"case": "a briefing with no statement shown", "get": "/api/statements?page=/b/Briefing02"},
    {"case": "the corrections log, with two withdrawn", "get": "/api/corrections"},
    {"case": "the briefing withdrawn just now", "get": "/api/briefings/Briefing02"},
]


def usage_error(code: int, text: str) -> str:
    """A command's output; for one the command line itself refused (exit 2: an option missing), only what it says is
    missing, since the box it is drawn in is the terminal library's and follows its version and the terminal."""
    if code != 2:
        return text
    return " ".join(line.strip("│╭╰─╮╯ ") for line in text.splitlines() if "Missing" in line)


def close_hits(rows: list[dict[str, Any]], score: str, keep: int = 8) -> list[list[Any]]:
    """The best ``keep`` of a search's rows as [passage, score], in a settled order. Rows that tie with the last
    one kept are left out with it: which of them a search returns first is not fixed."""
    hits = sorted(([r["passage_id"], round(r[score], 4)] for r in rows),
                  key=lambda h: (-h[1] if score == "_score" else h[1], h[0]))
    if len(hits) > keep:
        edge = hits[keep][1]
        hits = [h for h in hits[:keep] if h[1] != edge]
    return hits


def a_saved_briefing(question: str) -> list[dict[str, Any]]:
    return [{"type": "plan", "plan": {"question": question}, "usage": None},
            {"type": "sources", "sources": [dict(c) for c in CARDS[1:]]},
            {"type": "text", "text": "Kenya said that climate finance was \"too slow\"."},
            {"type": "final", "answer": an_answer(), "usage": None, "model": "claude-sonnet-5-5",
             "stop_reason": "end_turn"}]


def replies_scenario(*, client: Any, run: Callable[[list[str]], tuple[int, str]], tick: Callable[[float], None],
                     save: Callable[[str, str, dict[str, Any], list[dict[str, Any]]], Any],
                     kept: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """What the forms are answered and what the owner's commands print, step by step, an hour apart.
    ``save(id, question, options, events)`` keeps a briefing; ``kept()`` gives the rows of reports and statements."""
    for bid, question in (("Briefing01", Q), ("Briefing02", Q2), ("Withdrawn1", "An older question?")):
        save(bid, question, {"mode": "summary"}, a_saved_briefing(question))
        tick(60)
    code, text = run(["withdraw", "Withdrawn1", "--reason", "It quoted the wrong meeting."])
    assert code == 0, text

    def http(step: dict[str, Any]) -> dict[str, Any]:
        tick(3600)
        r = client.get(step["get"]) if "get" in step else client.post(step["post"], json=step["json"])
        if step.get("page"):  # only what a saved briefing's page changes: its status, title and whether to index it
            html = r.text
            title = html[html.index("<title>"):html.index("</title>") + 8]
            return {"case": step["case"], "status": r.status_code, "title": title,
                    "noindex": '<meta name="robots" content="noindex">' in html,
                    "og_title": [ln.strip() for ln in html.splitlines() if 'property="og:title"' in ln]}
        return {"case": step["case"], "status": r.status_code, "json": r.json()}

    out: dict[str, Any] = {"http": [http(step) for step in HTTP_STEPS]}
    out["kept_after_the_forms"] = kept()
    out["cli"] = []
    for step in CLI_STEPS:
        tick(3600)
        code, text = run(step["args"])
        out["cli"].append({"case": step["case"], "args": step["args"], "exit": code, "output": usage_error(code, text)})
    out["after"] = [http(step) for step in AFTER_STEPS]
    out["kept"] = kept()
    return out
