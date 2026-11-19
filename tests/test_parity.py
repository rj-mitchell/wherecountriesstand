"""The site on wts-core gives what its own code gave: each golden output (tests/golden/, recorded by
tests/golden/record.py on the code as it was) compared with what the site gives now, on the same inputs.

A difference is either a fault to mend or a known difference of the move, and then it is asserted here, by name,
beside the comparison it belongs to. Nothing is skipped.
"""

from __future__ import annotations

import re

import pytest
import shared

from wcs.config import REPO_ROOT, load_settings


def by_name(rows):
    return {row["name"]: row for row in rows}


# Settings


# Keys written into config/settings.toml with the move, each with the value the code had as its fallback or the
# package's behaviour as it always was, so nothing changes by their being there (the code's fallbacks were
# written into settings.toml with the values they had).
ADDED = {
    ("answer", "cache_instructions"): "short",  # prompt caching becomes a setting, on as before
    # the structured log's limits, which wts-core's ADR 0012 gives and leaves to each site to state
    ("logs", "max_bytes"): 3000000, ("logs", "keep"): 15, ("logs", "days"): 14,
    # for the weekly report's themes: how alike two questions must be, for this embedding model
    ("embedding", "theme_similarity"): 0.8,
    # what an evaluation run with each briefing written may spend, and what it is held to
    ("eval", "max_cost_usd"): 3.0,
    ("eval", "thresholds"): {"max_invalid_citations": 0, "max_unverified_quotations": 0, "max_errors": 0},
}

# Values in config/settings.toml changed on purpose since the recording, each (as it was, as it is): fewer new
# briefings a day for each visitor, each briefing in progress counted at the dearest briefings' cost, and medium
# effort, not low, for the question reader (a reading costs a fiftieth of a penny).
CHANGED = {("usage", "per_visitor_daily_questions"): (40, 8), ("usage", "briefing_estimate_usd"): (0.1, 0.25),
           ("answer", "query_parse_effort"): ("low", "medium"), ("usage", "daily_spend_cap_usd"): (3.0, 25.0)}

BRIEFING_MODEL = "claude-sonnet-5-5"


def without_the_added(gives: dict) -> dict:
    """What a case gives, with the keys added at the move taken out of the sections they were added to (checked
    to hold what ``ADDED`` says, where the case reads the real settings file)."""
    import copy

    out = copy.deepcopy(gives)
    if out["raw"].get("alerts") == {}:  # [alerts]: there, and empty until the owner sets a threshold (ADR 0012)
        del out["raw"]["alerts"]
    for holder in (out["raw"], out):
        # strict prices (the review of the adoption): the briefing model has a table of its own, which holds the
        # base prices it was costed at before
        models = holder.get("usage", {}).get("models", {})
        if BRIEFING_MODEL in models and "price_input_per_mtok" in holder["usage"]:
            assert models.pop(BRIEFING_MODEL) == {k: holder["usage"][k] for k in (
                "price_input_per_mtok", "price_output_per_mtok", "price_cache_read_per_mtok")}
    for (section, key), (was, now) in CHANGED.items():
        for holder in (out["raw"], out):
            if holder.get(section, {}).get(key) == now:
                holder[section][key] = was
    for (section, key), value in ADDED.items():
        for holder in (out["raw"], out):
            if key in holder.get(section, {}):
                assert holder[section].pop(key) == value, (section, key)
                if not holder[section] and holder is out["raw"]:  # a section the move added whole
                    del holder[section]
    return out


def test_settings_resolve_as_they_did(tmp_path, monkeypatch):
    """The real settings file and samples, with a local file, overrides and each environment variable: the whole
    of what was read, the data folder, and every accessor of the site's own. The one difference is the keys the
    move wrote into the file (``ADDED``)."""
    monkeypatch.setattr("os.environ", dict(__import__("os").environ))  # the cases set and clear WCS_ variables
    before = by_name(shared.read("settings.json"))
    now = by_name(shared.settings_cases(tmp_path / "settings", REPO_ROOT,
                                        lambda path, **overrides: load_settings(path, **overrides)))
    assert now.keys() == before.keys() and len(before) == len(shared.SETTINGS_CASES)
    for name in before:
        assert without_the_added(now[name]["gives"]) == before[name]["gives"], name
    real = now["the real settings file"]["gives"]["raw"]
    assert all(real[section][key] == value for (section, key), value in ADDED.items())


# The checks on briefings


# wts-core 0.3.2, version 2 of the checking rules: a short uncited line that is a sentence of its own is a claim to
# check ("unsupported"), where version 1 let it pass as a join ("connective"). Each passage named here is a claim no
# source supports, so it is right that it is marked; nothing else in any report changes, and no flag is lost.
RULES_2_CLAIMS = {
    "a cited claim, a lead-in and an uncited claim": [" This was a turning point for the Council."],
    "an honorific's full stop ends no sentence": [" Dr. Prasad disagreed. St. Lucia was not heard."],
}


def as_under_rules_2(name: str, answer: dict) -> dict:
    """A report recorded under version 1 of the checking rules as version 2 gives it: the passages
    ``RULES_2_CLAIMS`` names for the case marked as claims, and the two counts that follow from the statuses
    worked out again. Every other passage and figure is as it was recorded."""
    claims = RULES_2_CLAIMS.get(name, [])
    if not claims:
        return answer
    assert [s["text"] for s in answer["segments"] if s["text"] in claims] == claims, name  # each, once
    segments = []
    for segment in answer["segments"]:
        if segment["text"] in claims:
            assert segment["status"] == "connective", name
            segment = {**segment, "status": "unsupported"}
        segments.append(segment)
    cited = sum(len(s["text"]) for s in segments if s["status"] == "cited")
    unsupported = sum(len(s["text"]) for s in segments if s["status"] in ("unsupported", "invalid"))
    return {**answer, "segments": segments,
            "unsupported_segments": sum(1 for s in segments if s["status"] == "unsupported"),
            "grounded_ratio": round(1.0 if not cited + unsupported else cited / (cited + unsupported), 3)}


def test_the_checks_report_what_they_reported():
    """Each test briefing through every check, as the pipeline runs them. Two differences, each listed: an answer
    now carries the version of the checking rules that wrote it, as a new key, ``rules`` (2 since wts-core 0.3.2);
    and the short uncited claims in ``RULES_2_CLAIMS`` are marked as unsupported. Everything else is what it was:
    never a flag fewer."""
    from wts_core.checks import RULES_VERSION

    from wcs.rag.checks import checked
    from wcs.rag.sources import Source

    assert RULES_VERSION == 2
    sources = [Source(**s) for s in shared.SOURCES]
    before = by_name(shared.read("checks.json")["written"])
    assert before.keys() == {case["name"] for case in shared.CHECK_CASES} >= RULES_2_CLAIMS.keys()
    for case in shared.CHECK_CASES:
        answer = dict(checked(case["blocks"], sources))
        assert answer.pop("rules") == RULES_VERSION, case["name"]
        assert answer == as_under_rules_2(case["name"], before[case["name"]]["answer"]), case["name"]


def test_saved_briefings_are_shown_again_as_they_were():
    """Saved and cached briefings from before the move carry no stamp of the rules, so each is judged again when it
    is shown, with curly quotes: the same statuses, counts and text as the site's own recheck gave."""
    import json

    from wcs.rag.checks import rechecked

    before = by_name(shared.read("checks.json")["shown_again"])
    assert before.keys() == {case["name"] for case in shared.SAVED_BRIEFINGS}
    for case in shared.SAVED_BRIEFINGS:
        assert rechecked(json.loads(json.dumps(case["events"]))) == before[case["name"]]["events"], case["name"]


def test_the_evidence_note_rule_is_the_wide_one_it_was():
    """This site takes a sentence with "was supplied" or "were retrieved" in it for a note about the evidence, which
    is loose ("Gas was supplied to two million homes." is not marked). It is kept as it is here, so that nothing
    changes with the move; narrowing it is a separate change for the owner, with an evaluation run before and
    after."""
    from wts_core.checks import EVIDENCE_NOTES_STRICT, EVIDENCE_NOTES_WIDE, Verifier

    from wcs.rag.checks import VERIFIER
    from wcs.rag.sources import Source

    sources = [Source(**s) for s in shared.SOURCES]
    case = next(c for c in shared.CHECK_CASES if c["name"] == "notes about the evidence, wide and narrow")
    wide = Verifier(no_records="No records found.", evidence_notes=EVIDENCE_NOTES_WIDE)
    strict = Verifier(no_records="No records found.", evidence_notes=EVIDENCE_NOTES_STRICT)
    ours = VERIFIER.verify(case["blocks"], sources).to_dict()
    assert ours == wide.verify(case["blocks"], sources).to_dict()
    assert ours["unsupported_segments"] < strict.verify(case["blocks"], sources).to_dict()["unsupported_segments"]


# /api/ask: the bytes sent and the rows written


def new_side(settings, pipeline, clock, monkeypatch):
    """The site as it is now, for one telling of a scenario: its real web app over its state database, with the
    clock, the day's visitor secret and the briefings' ids fixed as they were when the goldens were recorded."""
    import sqlite3
    import types

    from fastapi.testclient import TestClient

    from wcs.api import app as web
    from wcs.state import open_state

    db = open_state(settings, clock)
    db.set(f"visitor_salt:{shared.DAY}", shared.SALT)
    ids = (f"briefing{n:03d}" for n in range(1, 100))
    monkeypatch.setattr(web, "new_briefing_id", lambda chance: next(ids))
    app = web.create_app(settings, pipeline=pipeline, state=db, clock=clock)

    def stream(body):
        route = next(r.endpoint for r in app.routes if getattr(r, "path", "") == "/api/ask")
        with monkeypatch.context() as patch:
            patch.setattr(web, "StreamingResponse", lambda content, **kwargs: content)
            request = types.SimpleNamespace(client=types.SimpleNamespace(host="testclient"), headers={}, cookies={})
            return route(web.AskBody(**body), request)

    side = shared.Side(client=TestClient(app), pipeline=pipeline, connect=lambda: sqlite3.connect(db.path), new=True,
                       stream=stream, another_client=lambda: TestClient(app))
    side.db, side.app = db, app
    return side


def settings_in(folder, monkeypatch, **usage):
    monkeypatch.setenv("WCS_DATA_DIR", str(folder / "data"))
    settings = load_settings(**shared.SETTINGS_FOR_TESTS)
    settings.raw["usage"] = {**settings.raw["usage"], **usage}
    # the recordings were made with the question read at low effort, since raised to medium (CHANGED): played with
    # the effort they were made with, so that everything else the model is sent is checked whole
    settings.raw["answer"] = {**settings.raw["answer"], "query_parse_effort": "low"}
    return settings


def records_of(side):
    """The record of each briefing the desk wrote (new with the move: nothing is compared with it)."""
    con = side.connect()
    con.row_factory = __import__("sqlite3").Row
    try:
        return [dict(r) for r in con.execute("SELECT * FROM wts_briefing_log ORDER BY rowid")]
    finally:
        con.close()


# Changes made on purpose since the recordings, applied to them here (``as_now``):
# - the token counts of a plan or a final event are blanked before they are sent, saved or cached: they are for the
#   spending record alone, which still has them;
# - the ask API takes no search plan, so a briefing's saved options no longer have "plan" (the cache's key keeps
#   it, empty, so that keys stay the same);
# - of the earlier questions sent with a follow-up, only the words are taken: an earlier answer sent with them is
#   not, and is not put in the model's prompt.
# - the model that reads a question is asked whether it is about anything the UN discusses ("off_topic"): one that
#   is not is "No records found", with no briefing written.
FOLLOW_UP_ANSWER = re.compile(r"\nA \(summary\): [^\n]*")
READING_RULES = [  # (as the instructions for reading a question were, as they are)
    ("""If the topic is not one the UN would discuss, keep just the question's words:
  "No records found" is then the right answer.""", """If the topic is not one the UN would discuss, keep just the question's words
  and set off_topic: "No records found" is then the right answer."""),
    ("""- wants_votes: true if the question is about how countries voted.
""", """- wants_votes: true if the question is about how countries voted.
- off_topic: true only when the question is about nothing that countries discuss at the United Nations (a recipe,
  a sports result, a film). Then no briefing is written and the reader is told no records were found, so when in
  doubt (a question that could touch on the UN's work, however loosely) leave it false.
"""),
]


def as_now(recorded):
    """A recording with the changes above made to it: in every event, row and request, whether held as JSON, as
    JSON in text, or as server-sent events."""
    import json

    if isinstance(recorded, list):
        return [as_now(v) for v in recorded]
    if isinstance(recorded, dict):
        out = {k: as_now(v) for k, v in recorded.items()}
        if out.get("type") in ("plan", "final") and out.get("usage"):
            out["usage"] = None
        if "sources_only" in out and "plan" in out and out["plan"] is None:
            del out["plan"]
        if set(out) == {"question", "answer"} and isinstance(out["answer"], str):
            del out["answer"]
        return out
    if not isinstance(recorded, str):
        return recorded
    if recorded.startswith("event: ") and "\ndata: " in recorded:
        return "\n".join(f"data: {json.dumps(as_now(json.loads(line[6:])), default=str)}"
                         if line.startswith("data: ") else line for line in recorded.split("\n"))
    if recorded[:1] in "[{" and recorded:
        try:
            value = json.loads(recorded)
        except ValueError:
            return recorded
        if json.dumps(value, default=str) == recorded:  # written as the site writes it: rewritten the same way
            return json.dumps(as_now(value), default=str)
    if recorded.startswith("You convert research questions"):
        for was, now in READING_RULES:
            assert was in recorded
            recorded = recorded.replace(was, now)
    return FOLLOW_UP_ANSWER.sub("", recorded)


SCRIPTED = as_now(shared.read("ask-scripted.json"))
# Cases in which a briefing ends without an answer once its model has been called, each with where its row is in
# the spending record (wts-core 0.3.2: such a briefing is charged, in a row of kind "unfinished")
UNFINISHED = {"the pipeline raising mid-stream": 1, "the reader leaving while the briefing is written": 1}


@pytest.mark.parametrize("name", list(shared.SCRIPTED))
def test_the_flow_of_a_question_sends_and_writes_what_it_did(name, tmp_path, monkeypatch):
    """Every parity case of wts-core's docs/adr/0009, played through the site's real web app over a scripted
    pipeline: the status and the exact bytes of every answer, the rows written to the spending record, the answer
    cache, the saved briefings and the notes of failures, and what was asked of the pipeline. One difference, and
    it is listed: a briefing that ends without an answer after its model was called is charged (``UNFINISHED``)."""
    from wts_core.testing import FixedClock

    from wcs.reference.countries import countries

    made = []

    def make(has_model=True, **usage):
        clock = FixedClock(shared.START)
        settings = settings_in(tmp_path / f"side-{len(made)}", monkeypatch, **usage)
        pipeline = shared.ScriptedPipeline(clock.advance, settings.answer["model"], countries(), has_model=has_model)
        made.append(new_side(settings, pipeline, clock, monkeypatch))
        made[-1].settings = settings
        return made[-1]

    now = shared.plain(shared.scripted_asks(make, only=name), tmp=tmp_path)[name]
    before = SCRIPTED[name]
    assert len(now["sent"]) == len(before["sent"])
    for (status, body), (was_status, was_body) in zip(now["sent"], before["sent"], strict=True):
        assert status == was_status
        assert shared.events_of(body) == shared.events_of(was_body)  # the readable comparison first
        assert body == was_body  # then every byte
    if name in UNFINISHED:
        # wts-core 0.3.2: the briefing that broke off is charged when it ends, in a row of its own, and counts
        # towards the visitor's daily limit. The scripted pipeline's model reports nothing of what it used, so the
        # row is charged what a briefing in progress is reserved at, with no token counts. Taken out, the rows are
        # what they were.
        spending, was = now["rows"]["spending"], before["rows"]["spending"]
        (at, *row), = [r for r in spending if r[1] == "unfinished"]
        visitor = was[0][3]
        estimate = made[0].settings.usage["briefing_estimate_usd"]
        assert row == ["unfinished", BRIEFING_MODEL, visitor, 0, 0, 0, 0, estimate] and estimate == 0.25  # CHANGED
        # written when the briefing ended: after its question was read, before the next question was
        assert spending.index([at, *row]) == UNFINISHED[name] and was[0][0] <= at <= was[1][0]
        assert made[0].app.state.parts.spending.briefings_today(visitor=visitor) == 2  # it and the next one
        now["rows"]["spending"] = [r for r in spending if r[1] != "unfinished"]
    for table in ("spending", "cache", "briefings", "failures"):
        assert now["rows"][table] == before["rows"][table], table
    assert now["calls"] == before["calls"]
    # new with the move: one record for every question the desk was handed, and never the visitor's code in it
    written = records_of(made[0])
    assert len(written) == sum(1 for status, _ in now["sent"] if status == 200)
    codes = {row[3] for row in now["rows"]["spending"] if row[3]}
    assert not any(code in repr(written) for code in codes)


AFRESH = "with no hours to keep an answer, every question is written afresh"  # the golden case to compare with


@pytest.mark.parametrize("taken_down", ["withdrawn", "hidden by its own address"])
def test_a_refused_replay_sends_and_writes_what_a_briefing_written_afresh_does(taken_down, tmp_path, monkeypatch):
    """wts-core 0.3.0, where `Hooks.on_cached` is asked before anything is sent and may refuse the replay: the site
    answers None for the cached copy of a briefing that has since been withdrawn, or hidden by its own
    address, where the commands did not reach the copy. The question is then one with no cached answer.

    Three tellings of "the question, the briefing taken down, the question again", each through the site's real
    web app over the scripted pipeline. In the first the copy is still in the cache when the question comes
    again, and the site refuses it. In the second the copy was dropped when the briefing was taken down, as
    `wcs withdraw` and `wcs hide` do, so the question is a plain miss. The third is the golden: what the site's
    own code sent, before the package, for a second briefing written afresh. Every byte the reader is sent is
    the same in all three; every row written is the same in the first two, with each model call in the spending
    record once; and the place in progress is given back."""
    from wts_core.testing import FixedClock

    from wcs.reference.countries import countries

    def telling(name: str, copy_dropped: bool):
        clock = FixedClock(shared.START)
        settings = settings_in(tmp_path / name, monkeypatch, briefings_at_once_per_visitor=1, briefings_at_once=1)
        pipeline = shared.ScriptedPipeline(clock.advance, settings.answer["model"], countries())
        side = new_side(settings, pipeline, clock, monkeypatch)
        parts, replies = side.app.state.parts, side.app.state.replies
        sent = [side.ask(shared.Q)]
        (key, _, cached), = side.rows()["cache"]
        assert "briefing001" in cached
        if taken_down == "withdrawn":
            assert parts.archive.withdraw("briefing001", "It misattributed a statement.")
        else:
            replies.hide("/b/briefing001", "Hidden while a concern about it is looked into.")
        if copy_dropped:
            assert parts.cache.delete_briefing("briefing001") == 1
        assert len(side.rows()["cache"]) == (0 if copy_dropped else 1)
        sent.append(side.ask(shared.Q))
        after = side.rows()
        today = parts.spending.briefings_today(visitor=after["spending"][0][3])
        spent = parts.spending.spent_since(shared.DAY)
        # the place in progress was given back: with one place for the visitor and one for the site, another
        # question is written, where a place still held would have refused it as busy
        assert dict(parts.in_progress.running) == {}
        sent.append(side.ask(shared.Q2))
        assert dict(parts.in_progress.running) == {} and "briefing003" in sent[-1][1]
        return shared.plain({"sent": sent, "rows": after, "calls": side.calls(), "key": key,
                             "record": [(r["outcome"], r["briefing_id"]) for r in records_of(side)],
                             "today": today, "spent": spent}, tmp=tmp_path)

    refused = telling("refused", copy_dropped=False)
    dropped = telling("dropped", copy_dropped=True)
    golden = SCRIPTED[AFRESH]["sent"]

    # what the reader is sent: a briefing written afresh, byte for byte
    status, body = refused["sent"][1]
    assert status == 200 and '"briefing_id": "briefing002"' in body
    assert "withdrawn" not in body and "Hidden while" not in body and '"note"' not in body
    assert shared.events_of(body) == shared.events_of(dropped["sent"][1][1]) == shared.events_of(golden[1][1])
    assert body == dropped["sent"][1][1] == golden[1][1]
    assert refused["sent"][0] == dropped["sent"][0] == golden[0]  # as was the first, before anything was taken down
    assert refused["sent"][2] == dropped["sent"][2]
    # what is written: the same rows either way, and the pipeline asked once for each briefing
    assert refused["rows"] == dropped["rows"] and refused["calls"] == dropped["calls"] == ["written"] * 3
    rows = refused["rows"]
    # spending: each briefing's two model calls (the question read, the briefing written) once, and no more
    assert [row[1] for row in rows["spending"]] == ["parse", "answer", "parse", "answer"]
    assert rows["spending"][2][4:] == rows["spending"][0][4:] and rows["spending"][3][4:] == rows["spending"][1][4:]
    assert refused["spent"] == pytest.approx(sum(row[-1] for row in rows["spending"]))
    assert refused["today"] == dropped["today"] == 2  # two briefings against the visitor's daily limit, not three
    # the cache: one copy under the question's key, the new briefing's, stored over the one that was refused
    (key, _, cached), = rows["cache"]
    assert key == refused["key"] and "briefing002" in cached and "briefing001" not in cached
    # saved briefings: both, the first as it was taken down; and nothing noted as a failure
    assert [row[0] for row in rows["briefings"]] == ["briefing001", "briefing002"] and rows["failures"] == []
    # the record of each briefing says "written", twice (and then the third question), never "from the cache"
    assert refused["record"] == dropped["record"] == [["completed", "briefing001"], ["completed", "briefing002"],
                                                      ["completed", "briefing003"]]


@pytest.fixture(scope="module")
def small_index(tmp_path_factory):
    """A data folder with the two fixture speeches indexed and embedded, built once and copied for each telling."""
    import os

    from wts_core.adapters.hashing import HashingEmbedder

    from wcs.index.build import Indexer, embed_pending, refresh_indices
    from wcs.reference.countries import countries
    from wcs.store.parquet import write_by_year

    folder = tmp_path_factory.mktemp("small-index")
    before = os.environ.get("WCS_DATA_DIR")
    os.environ["WCS_DATA_DIR"] = str(folder / "data")
    try:
        settings = load_settings(**shared.SETTINGS_FOR_TESTS)
        write_by_year(settings.parquet_dir, "sc", shared.SPEECHES)
        idx = Indexer(settings, countries())
        idx.index_all()
        embed_pending(settings, idx.index, idx.state, HashingEmbedder(64))
        refresh_indices(idx.index, idx.state)
    finally:
        if before is None:
            del os.environ["WCS_DATA_DIR"]
        else:
            os.environ["WCS_DATA_DIR"] = before
    return folder


def without_the_stamp(value):
    """``value`` with the one key the move added to a written answer taken out (asserted to be today's version of
    the checking rules wherever it is)."""
    from wts_core.checks import RULES_VERSION

    if isinstance(value, dict):
        if "segments" in value and "rules" in value:
            assert value["rules"] == RULES_VERSION
        return {k: without_the_stamp(v) for k, v in value.items() if not (k == "rules" and "segments" in value)}
    if isinstance(value, list):
        return [without_the_stamp(v) for v in value]
    return value


def unstamped_events(body):
    return [without_the_stamp(e) for e in shared.events_of(body)]


def unstamped_rows(rows):
    """Rows whose saved or cached events are compared as events, without the stamp (see ``without_the_stamp``)."""
    import json

    return {"spending": rows["spending"], "failures": rows["failures"],
            "cache": [[key, at, without_the_stamp(json.loads(events))] for key, at, events in rows["cache"]],
            "briefings": [[*row[:4], without_the_stamp(json.loads(row[4])), *row[5:]] for row in rows["briefings"]]}


REAL = as_now(shared.read("ask-real.json"))  # with the changes made since (``as_now``)
PLAN_SENT = {"no records", "an edited plan"}  # cases that sent a search plan with the question


@pytest.mark.parametrize("name", list(shared.REAL))
def test_the_real_pipeline_sends_and_writes_what_it_did(name, small_index, tmp_path, monkeypatch):
    """The same through the site's real pipeline, over a small index and a faked SDK: what the model is sent,
    whole (its instructions, its documents, the question, the model and how hard it is asked to think), what the
    reader is sent, what is written, a saved briefing opened again, the search and the status. One difference, and
    it is listed: a written answer carries ``rules``, the version of the checking rules that wrote it (2 since
    wts-core 0.3.2). With that key taken out, nothing differs."""
    import os
    import shutil

    from conftest import model_of
    from wts_core.adapters.hashing import HashingEmbedder
    from wts_core.checks import RULES_VERSION
    from wts_core.testing import FixedClock

    from wcs.engine import Engine
    from wcs.rag.pipeline import Pipeline

    for key in ("WCS_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    made = []

    def make(sdk, **usage):
        clock = FixedClock(shared.START)
        folder = tmp_path / f"real-{len(made)}"
        shutil.copytree(small_index, folder)
        settings = settings_in(folder, monkeypatch, **usage)
        os.utime(settings.index_dir / "state.json", (shared.STAMP, shared.STAMP))
        engine = Engine(settings, embedder=HashingEmbedder(64), today=shared.TODAY)
        side = new_side(settings, Pipeline(settings, engine, client=model_of(sdk), today=shared.TODAY), clock,
                        monkeypatch)
        made.append(side)

        def get(path):
            r = side.client.get(path)
            return [r.status_code, r.json()]

        return side, get

    now = shared.plain(shared.real_asks(make, only=name), tmp=tmp_path)[name]
    if name in PLAN_SENT:
        # recorded with a search plan sent with each question, which the ask API no longer takes: the site reads
        # the question itself, so each case gives exactly what the same questions give without a plan
        make_sdk, requests, usage = shared.REAL[name]
        assert all("plan" in body for body in requests)
        monkeypatch.setitem(shared.REAL, name, (make_sdk, [{k: v for k, v in body.items() if k != "plan"}
                                                           for body in requests], usage))
        without = shared.plain(shared.real_asks(make, only=name), tmp=tmp_path)[name]
        assert now == without and now["calls"] and "parse" in now["calls"][0]
        for _, body in now["sent"]:
            plan = shared.events_of(body)[0]["plan"]
            assert plan["question"] == requests[0]["question"] and plan["parser"] == "model"
        return
    before = REAL[name]
    assert now["calls"] == before["calls"]  # what the model was asked, whole
    assert len(now["sent"]) == len(before["sent"])
    stamped = 0
    for (status, body), (was_status, was_body) in zip(now["sent"], before["sent"], strict=True):
        assert status == was_status
        assert unstamped_events(body) == shared.events_of(was_body)
        stamped += body != was_body
        # every byte, once the stamp is taken out of the text as it was sent
        assert body.replace(f', "rules": {RULES_VERSION}}}', "}") == was_body
    assert unstamped_rows(now["rows"]) == unstamped_rows(before["rows"])
    assert without_the_stamp(now["saved"]) == before["saved"] and now["search"] == before["search"]
    assert now["status"] == before["status"]
    written = sum(1 for _, body in now["sent"] for e in shared.events_of(body)
                  if isinstance(e, dict) and e.get("type") == "final" and (e.get("answer") or {}).get("quotes_total")
                  is not None)
    assert stamped == written  # only an answer the checks wrote differs, and only by its stamp


# Limits, cost, caps, cache keys


def test_the_limit_on_requests_a_minute_decides_as_it_did(tmp_path, monkeypatch):
    """The scripted two minutes, through the limiter the site's web app is built with."""
    from wts_core.testing import FixedClock

    from wcs.reference.countries import countries

    before = shared.read("limiter.json")
    clock = FixedClock(shared.START)
    settings = settings_in(tmp_path, monkeypatch, requests_per_minute=before["per_minute"])
    side = new_side(settings, shared.ScriptedPipeline(clock.advance, "m", countries()), clock, monkeypatch)
    limiter, at, allowed = side.app.state.parts.limiter, 0.0, []
    for t, who in before["script"]:
        clock.advance(t - at)
        at = t
        allowed.append(limiter.allow(who))
    assert allowed == before["allowed"]


def test_every_call_costs_what_it_cost():
    """A table of token counts, in both of the forms the site has stored, for each model: the same figure to the
    last decimal, and the same counts stored."""
    from wts_core.ports import Usage
    from wts_core.usage import PriceTable

    prices = PriceTable.from_settings(load_settings().usage)
    before = shared.read("costs.json")
    assert len(before) == len(shared.USAGES) * len(shared.MODELS)
    for row in before:
        usage = Usage.from_mapping(row["usage"])
        assert prices.cost(usage if row["usage"] else None, row["model"]) == row["cost_usd"], row
        assert usage.as_stored() == row["stored_as"], row


def same(value) -> str:
    return __import__("json").dumps(value, sort_keys=True)


def test_the_answer_caches_key_is_what_it_was(tmp_path, monkeypatch):
    """So that the week's cache survives the move: the key for each question and each choice, a follow-up having
    none, and the key changing with the model, the data's stamp and the instructions' version."""
    from wcs.api import app as web

    settings = settings_in(tmp_path / "keys", monkeypatch)
    before = shared.read("cache-keys.json")
    assert len(before) == len(shared.KEY_BODIES) * len(shared.KEY_WORLDS)
    for row in before:
        world = row["world"]
        with monkeypatch.context() as patch:
            used = settings
            if "model" in world:
                used = load_settings(**{"answer.model": world["model"]})
            if "data" in world:
                patch.setattr(web, "data_stamp", lambda s, stamp=world["data"]: stamp)
            if "instructions" in world:
                patch.setattr(web, "INSTRUCTIONS_VERSION", world["instructions"])
            key = web.answer_key(used, web.AskBody(**row["body"]), model=True, cache_hours=168)
        if "plan" in row["body"]:
            # the ask API no longer takes a search plan: a question sent with one is the question without it
            plain = {k: v for k, v in row["body"].items() if k != "plan"}
            assert key == next(r["key"] for r in before if r["body"] == plain and r["world"] == world), row
            continue
        assert key == row["key"], row
        assert key is None or len(key) == 64
    # a follow-up has no key; nor has anything when there is no model or no cache
    by_body = {(same(r["body"]), same(r["world"])): r["key"] for r in before}
    assert by_body[(same({"question": shared.Q2, "history": shared.EARLIER}), "{}")] is None
    body = web.AskBody(question=shared.Q)
    assert web.answer_key(settings, body, model=False, cache_hours=168) is None
    assert web.answer_key(settings, body, model=True, cache_hours=0) is None
    # a field the pages do not send is ignored, as it always was, and is no part of the key
    assert web.answer_key(settings, web.AskBody(question=shared.Q, follows="Briefing01", anything="else"),
                          model=True, cache_hours=168) == by_body[(same({"question": shared.Q}), "{}")]


@pytest.mark.parametrize("world", [w for w in shared.KEY_WORLDS if w])
def test_the_key_changes_with_the_model_the_instructions_and_the_data(world):
    """wts-core's ADR 0009 leaves these three to each site, since the site makes the key: a change to any of them
    must mean an answer written before is not served again."""
    before = shared.read("cache-keys.json")
    plain = {repr(r["body"]): r["key"] for r in before if not r["world"]}
    changed = {repr(r["body"]): r["key"] for r in before if r["world"] == world}
    assert len(changed) == len(shared.KEY_BODIES)
    for body, key in plain.items():
        assert (changed[body] is None) if key is None else (changed[body] != key), (world, body)


def test_the_caps_and_the_places_in_progress_decide_as_they_did(tmp_path, monkeypatch):
    """A scripted month of spending and questions: each refusal, word for word, at each moment; and the places in
    progress, taken and given back."""
    from wts_core.testing import FixedClock
    from wts_core.usage import BriefingsInProgress, busy_refusal

    from wcs.messages import MESSAGES
    from wcs.reference.countries import countries

    before = shared.read("caps.json")
    clock = FixedClock(shared.START)
    settings = settings_in(tmp_path, monkeypatch, **before["limits"])
    parts = new_side(settings, shared.ScriptedPipeline(clock.advance, "m", countries()), clock,
                     monkeypatch).app.state.parts
    said = []
    for when, what, *rest in before["script"]:
        clock.set(when)
        if what == "spend":
            kind, model, usage, visitor = rest
            said.append(parts.spending.add(kind, model, usage, visitor=visitor))
        else:
            visitor, pending = rest
            refusal = parts.caps.refusal(visitor=visitor, pending_usd=pending)
            said.append(None if refusal is None else refusal.message)
    assert said == before["said"]
    flight, places = BriefingsInProgress(**before["in_progress"]), []
    for what, arg in before["in_progress_script"]:
        if what == "start":
            busy = flight.start(arg)
            places.append(None if busy is None else busy_refusal(busy, MESSAGES).message)
        elif what == "finish":
            flight.finish(arg)
            places.append(dict(flight.running))
        else:
            places.append(flight.pending_usd(besides=arg))
    assert places == before["places"]


# Reports, statements, saved briefings: what the forms are answered and what the owner's commands print

# Every difference from what the site's own code gave, each a known difference of the move, or found here and said
# so. Whatever is not named here is
# compared whole: the same status, the same JSON, the same lines printed, the same exit code.
REPORT_OF_SPACES = "a report of spaces"            # found here: it was kept as an empty report; it is refused now
TWO_FAULTS = "a statement with two things wrong: the name and the words"  # the package's order of the checks
TAKEDOWN_FIRST = ("the open reports", "every report")                    # takedown requests listed first
THE_EXACT_REASON = {                                                      # the owner now sees the reason
    "close it again": "error: no open report 1\n",
    "close a report there is not": "error: no open report 99\n",
    "publish with an empty note": "error: say how you confirmed who sent it (checked), for the log\n",
    "publish it again": "error: statement 1 is published, not pending\n",
    "publish one there is not": "error: no statement 99\n",
    "decline one already declined": "error: statement 2 is declined, not pending\n",
    "update with nothing to change": "error: nothing to change: give new words, a name, a role, a link or whom it "
                                     "speaks for\n",
    "update a declined statement": "error: statement 2 is declined, not pending or published\n",
    "withdraw it again": "error: statement 3 is withdrawn, not pending or published\n",
}
CORRECTIONS = ("the corrections log", "the corrections log, with two withdrawn")
WAS_READ_BY_THE_PAGE = ("id", "at", "question", "withdrawn_at", "reason")  # what corrections.js reads of an entry


def test_reports_statements_and_the_owners_commands_answer_as_they_did(tmp_path, monkeypatch):
    import sqlite3

    from typer.testing import CliRunner
    from wts_core.testing import FixedClock

    from wcs import cli
    from wcs.reference.countries import countries

    clock = FixedClock(shared.START)
    monkeypatch.setattr(cli, "CLOCK", clock)
    settings = settings_in(tmp_path, monkeypatch)
    side = new_side(settings, shared.ScriptedPipeline(clock.advance, "m", countries()), clock, monkeypatch)

    noticed = set()

    def run(args):
        """What a command gave: its status and everything it printed. The scenario runs the owner's commands under
        the names they had when the goldens were recorded. From wts-core 0.3.0 (`RepliesCommands.also`) a command run under such a name
        first says, on standard error, what it is called now. That one line is checked here and taken off; what
        is left is compared with what the old code printed, whole, as before."""
        result = CliRunner().invoke(cli.app, args)
        output = result.output
        key = cli.REPLIES_COMMANDS.also.get(args[0])
        if key is not None:
            notice = cli.OLD_NAME.format(old=args[0], new=getattr(cli.REPLIES_COMMANDS, key)) + "\n"
            if result.exit_code == 2:  # the command line itself was refused (an option missing): nothing was run
                assert notice not in output
            else:
                assert output.startswith(notice) and result.stderr.startswith(notice), args
                assert notice not in result.stdout  # standard output is exactly what it was
                output = output[len(notice):]
                noticed.add(args[0])
        return result.exit_code, output

    def kept():
        con = sqlite3.connect(side.db.path)
        con.row_factory = sqlite3.Row
        try:
            return {"reports": [dict(r) for r in con.execute("SELECT * FROM wts_reports ORDER BY id")],
                    "statements": [dict(r) for r in con.execute("SELECT * FROM wts_statements ORDER BY id")],
                    "log": [dict(r) for r in con.execute("SELECT * FROM wts_statement_log ORDER BY id")],
                    "briefings": [dict(r) for r in con.execute(
                        "SELECT id, created_at, question, withdrawn_at, withdrawn_reason FROM wts_briefings "
                        "ORDER BY id")]}
        finally:
            con.close()

    now = shared.plain(shared.replies_scenario(
        client=side.client, run=run, tick=clock.advance, kept=kept,
        save=lambda bid, question, options, events: side.app.state.parts.archive.save(question, options, events,
                                                                                       id=bid)), tmp=tmp_path)
    before = shared.read("replies.json")
    assert noticed == set(cli.REPLIES_COMMANDS.also) and len(noticed) == 6  # every older name was run, and said so
    listed = {REPORT_OF_SPACES, TWO_FAULTS, *TAKEDOWN_FIRST, *THE_EXACT_REASON, *CORRECTIONS}
    for part in ("http", "cli", "after"):
        assert [step["case"] for step in now[part]] == [step["case"] for step in before[part]]
        for step, was in zip(now[part], before[part], strict=True):
            if " · Where Countries Stand</title>" in was.get("title", ""):
                # a page's title is "Page | Site name" in the family's words (GLOSSARY, "Page titles"), where its
                # separator was a middle dot
                was = {**was, "title": was["title"].replace(" · Where Countries Stand", " | Where Countries Stand")}
            if "og_title" in step and "Briefing from UN records" in step.get("title", ""):
                # a saved briefing's title and link preview are the site's words and the day it was written, where
                # they were the reader's question (decision 7)
                assert re.fullmatch(r"<title>Briefing from UN records, \d{1,2} [A-Z][a-z]+ \d{4} \| Where "
                                    r"Countries Stand</title>", step["title"])
                assert step["og_title"] == [step["title"].replace("<title>", '<meta property="og:title" content="')
                                            .replace(" | Where Countries Stand</title>", '">')]
                was = {**was, "title": step["title"], "og_title": step["og_title"]}
            if step["case"] not in listed:
                assert step == was, step["case"]
    steps = {step["case"]: step for part in ("http", "cli", "after") for step in now[part]}
    were = {step["case"]: step for part in ("http", "cli", "after") for step in before[part]}

    # a report of nothing but spaces was kept, empty; it is refused, in the family's words, the same on every Where
    # They Stand site: the owner asked for the family's wording to be the same (16 November 2026), where these were
    # the site's own, "Say what is wrong, in a few words at least."
    assert were[REPORT_OF_SPACES]["status"] == 200
    assert steps[REPORT_OF_SPACES] == {"case": REPORT_OF_SPACES, "status": 422, "json": {
        "detail": "Please say what is wrong, in a few words."}}
    # a sender with two things wrong is told of the words first, where the name came first
    assert were[TWO_FAULTS]["json"] == {"detail": "Give your name, as it should be shown."}
    assert steps[TWO_FAULTS]["status"] == 422 and steps[TWO_FAULTS]["json"] == {
        "detail": "A statement can be up to 150 words; this one is 151."}
    # the owner's listing of reports: the same lines for each report, takedown requests first (and no empty one)
    for case in TAKEDOWN_FIRST:
        def blocks(text):
            return ["#" + block for block in ("\n" + text).split("\n#") if block]
        was_blocks = [b for b in blocks(were[case]["output"]) if not b.startswith("#7 ")]
        now_blocks = blocks(steps[case]["output"])
        assert sorted(now_blocks) == sorted(was_blocks) and steps[case]["exit"] == 0
        assert now_blocks[0].startswith("#5 ") and "takedown" in now_blocks[0]
        assert [b for b in now_blocks[1:]] == [b for b in was_blocks if not b.startswith("#5 ")]
    # what could not be done: the exact reason where there was one fixed line; exit 1 as before
    for case, line in THE_EXACT_REASON.items():
        assert were[case]["exit"] == 1 and steps[case] == {**were[case], "output": line}, case
    # the corrections log: each entry holds what it held, under the same names, in the same order (and more: the
    # log can now also carry what the owner says publicly when closing a report)
    for case in CORRECTIONS:
        assert steps[case]["status"] == 200
        assert [{k: entry[k] for k in WAS_READ_BY_THE_PAGE} for entry in steps[case]["json"]] == were[case]["json"]
        assert all(entry["kind"] == "withdrawn" for entry in steps[case]["json"])

    # what is kept: the same reports and statements, under the package's names for the columns
    for stage in ("kept_after_the_forms", "kept"):
        old, new = before[stage], now[stage]
        old_reports = [r for r in old["reports"] if r["id"] != 7]  # the report of spaces, no longer kept
        assert [(r["id"], r["at"], r["kind"], r["page"] or None, r["message"], r["contact"] or None, r["closed_at"])
                for r in old_reports] == [(r["id"], r["at"], r["kind"], r["page"], r["message"], r["contact"],
                                           r["closed_at"]) for r in new["reports"]]
        assert [r["briefing_id"] for r in new["reports"]] == ["Briefing01", None, None, None, "Briefing02", None]
        for was, kept_now in zip(old["statements"], new["statements"], strict=True):
            # text cleaning now closes runs of blank lines (and line endings are made plain); an empty
            # link or address is none
            assert kept_now["text"] == re.sub(r"\n{3,}", "\n\n", was["text"].replace("\r\n", "\n"))
            assert (kept_now["id"], kept_now["at"], kept_now["item"], kept_now["about"], kept_now["name"],
                    kept_now["role"], kept_now["link"], kept_now["email"], kept_now["status"], kept_now["closed_at"],
                    kept_now["reason"]) == (
                was["id"], was["at"], was["page"], was["country"], was["name"], was["role"], was["link"] or None,
                was["email"] or None, was["status"], was["ended_at"], was["reason"])
            dated = max(kept_now["published_at"] or "", kept_now["updated_at"] or "")[:10] or None
            assert dated == was["shown_on"]  # the date of the words shown
            log = [e for e in new["log"] if e["statement"] == was["id"]]
            assert [(e["at"], e["action"]) for e in log] == [(e["at"], e["action"]) for e in was["log"]]
            # the note of a publishing (how the sender was confirmed), a decline and a withdrawal are kept as they
            # were; an update's note named what changed and still does
            assert [e["note"] for e in log if e["action"] != "updated"] == [
                e.get("note") for e in was["log"] if e["action"] != "updated"]
        assert [(b["id"], b["created_at"], b["question"], b["withdrawn_at"], b["withdrawn_reason"])
                for b in new["briefings"]] == [(b["id"], b["at"], b["question"], b["withdrawn_at"],
                                                b["withdrawn_reason"]) for b in old["briefings"]]


# Backups, restores, off-site copies and their alerts


def test_backups_restores_and_their_alerts_are_what_they_were(tmp_path, monkeypatch):
    """`wcs backup`, `wcs restore` and `wcs offsite`, line for line: the files made and what is in them, who may
    read them, which are deleted, every refusal of a restore, and the alert for each state of the backups' folder.
    No difference."""
    from typer.testing import CliRunner
    from wts_core.adapters import b2
    from wts_core.ops import backup_alerts
    from wts_core.testing import FixedClock

    from wcs import cli
    from wcs.config import SITE
    from wcs.state import BACKUP_ALERTS, BACKUPS

    def run(args):
        result = CliRunner().invoke(cli.app, args)
        return result.exit_code, result.output

    def environment(data_dir, **more):
        monkeypatch.setenv("WCS_DATA_DIR", str(data_dir))
        for name in ("WCS_B2_BUCKET", "WCS_B2_KEY_ID", "WCS_B2_APPLICATION_KEY", "WCS_CONFIG"):
            monkeypatch.delenv(name, raising=False)
        for name, value in more.items():
            monkeypatch.setenv(name, value)

    def alerts(folder, env, now):
        found = backup_alerts(folder, BACKUPS, offsite_set_up=b2.configured(SITE, env) is not None,
                              wording=BACKUP_ALERTS, clock=FixedClock(now))
        return [{"level": alert.level, "text": alert.text} for alert in found]

    now = shared.backups_scenario(
        tmp_path / "backups", run=run, environment=environment, alerts=alerts,
        set_day=lambda day: monkeypatch.setattr(cli, "CLOCK", FixedClock(f"{day}T04:20:00+00:00")))
    before = shared.read("backups.json")
    assert [c["case"] for c in now["commands"]] == [c["case"] for c in before["commands"]]
    for command, was in zip(now["commands"], before["commands"], strict=True):
        assert command == was, command["case"]
    assert now["alerts"] == before["alerts"]
    assert now["restored"] == before["restored"] and now["after"] == before["after"]


def test_off_site_copies_are_what_they_were(tmp_path):
    """A copy stored, old copies deleted, a key that cannot delete, an upload that keeps failing, a refused key, a
    key for another bucket, sites sharing a bucket: what is returned, what is in the bucket, and offsite.json."""
    from wts_core.adapters import b2
    from wts_core.ops import OffsiteError, check_offsite, send_offsite
    from wts_core.testing import FixedClock

    from wcs.config import SITE
    from wcs.state import BACKUPS

    clock = FixedClock("2026-11-03T04:20:05+00:00")
    now = shared.offsite_scenario(
        tmp_path, error=OffsiteError,
        send=lambda archive, keep, env, client: send_offsite(archive, b2.configured(SITE, env, client=client),
                                                             BACKUPS, keep=keep, clock=clock),
        check=lambda env, client: check_offsite(b2.configured(SITE, env, client=client), BACKUPS,
                                                how_to_set_up=b2.how_to_set_up(SITE)))
    before = shared.read("offsite.json")
    assert [step["case"] for step in now] == [step["case"] for step in before]
    for step, was in zip(now, before, strict=True):
        assert step == was, step["case"]


def test_the_real_pipeline_never_sends_the_three_shapes_the_desk_and_the_old_flow_disagreed_on(small_index, tmp_path,
                                                                                              monkeypatch):
    """wts-core's independent review left three event shapes to each site, where the desk and this site's old flow
    would differ and no pipeline was known to send them. This site's own ``on_event`` deals with one (an ``error``
    with keys of its own is sent as its message alone: the scripted case above). The other two cannot come from
    its pipeline, which is checked here on every way it ends: a ``final`` always has its ``usage`` key (the old
    flow stored ``usage: null`` for one without; the desk would store none), and nothing follows a ``final`` (the
    old flow cached a briefing whose ``final`` was followed by a ``citation``; the desk would not)."""
    import shutil

    from conftest import model_of
    from wts_core.adapters.hashing import HashingEmbedder

    from wcs.engine import Engine
    from wcs.rag.pipeline import AskRequest, Pipeline

    for key in ("WCS_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    shutil.copytree(small_index, tmp_path / "shapes")
    settings = settings_in(tmp_path / "shapes", monkeypatch)
    engine = Engine(settings, embedder=HashingEmbedder(64), today=shared.TODAY)
    endings = set()
    for sdk in (shared.FakeSDK(shared.READING), shared.FakeSDK(None), shared.FakeSDK(shared.READING, refuse=True),
                None):
        pipeline = Pipeline(settings, engine, client=model_of(sdk), today=shared.TODAY)
        for request in (AskRequest(shared.Q), AskRequest(shared.Q_NOTHING, plan=shared.PLAN_NOTHING),
                        AskRequest(shared.Q2, history=shared.EARLIER)):
            sent = list(pipeline.run(request))
            kinds = [e["type"] for e in sent]
            assert kinds[:2] == ["plan", "sources"] and kinds[-1] in ("final", "error")
            assert kinds.count("final") + kinds.count("error") == 1  # one ending, and it is the last event
            if kinds[-1] == "final":
                assert "usage" in sent[-1]
            else:
                assert set(sent[-1]) == {"type", "message", "usage", "model"}
            endings.add((kinds[-1], bool(sent[-1].get("answer"))))
    assert endings == {("final", True), ("final", False), ("error", False)}
