"""The site on wts-core gives what its own code gave: each golden output (tests/golden/, recorded by
tests/golden/record.py on the code as it was) compared with what the site gives now, on the same inputs.

A difference is either a fault to mend or one of the differences wts-core's docs/AS-BUILT.md and docs/MIGRATION.md
list for this site, and then it is asserted here, by name, beside the comparison it belongs to. Nothing is skipped.
"""

from __future__ import annotations

import re

import pytest
import shared

from wcs.config import REPO_ROOT, load_settings


def by_name(rows):
    return {row["name"]: row for row in rows}


# ---- settings ------------------------------------------------------------------------------------------------------


# Keys written into config/settings.toml with the move, each with the value the code had as its fallback or the
# package's behaviour as it always was, so nothing changes by their being there (wts-core docs/MIGRATION.md 1.1:
# "each site's code fallbacks are written into its settings.toml at adoption with today's values").
ADDED = {
    ("answer", "cache_instructions"): "short",  # MIGRATION 1.5: prompt caching becomes a setting, on as before
    # the structured log's limits, which wts-core's ADR 0012 gives and leaves to each site to state
    ("logs", "max_bytes"): 3000000, ("logs", "keep"): 15, ("logs", "days"): 14,
    # for the weekly report's themes: how alike two questions must be, for this embedding model
    ("embedding", "theme_similarity"): 0.8,
}


def without_the_added(gives: dict) -> dict:
    """What a case gives, with the keys added at the move taken out of the sections they were added to (checked
    to hold what ``ADDED`` says, where the case reads the real settings file)."""
    import copy

    out = copy.deepcopy(gives)
    if out["raw"].get("alerts") == {}:  # [alerts]: there, and empty until the owner sets a threshold (ADR 0012)
        del out["raw"]["alerts"]
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


# ---- the checks on briefings -----------------------------------------------------------------------------------


def test_the_checks_report_what_they_reported():
    """Each test briefing through every check, as the pipeline runs them. One difference, and it is listed
    (wts-core docs/MIGRATION.md 1.4, docs/AS-BUILT.md "checks"): an answer now carries the version of the checking
    rules that wrote it, as a new key, ``rules``. Everything else is what it was: never a flag fewer."""
    from wts_core.checks import RULES_VERSION

    from wcs.rag.checks import checked
    from wcs.rag.sources import Source

    sources = [Source(**s) for s in shared.SOURCES]
    before = by_name(shared.read("checks.json")["written"])
    assert before.keys() == {case["name"] for case in shared.CHECK_CASES}
    for case in shared.CHECK_CASES:
        answer = dict(checked(case["blocks"], sources))
        assert answer.pop("rules") == RULES_VERSION, case["name"]
        assert answer == before[case["name"]]["answer"], case["name"]


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
    after (wts-core docs/ARCHITECTURE.md's table of findings; docs/AS-BUILT.md, "checks")."""
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


# ---- /api/ask: the bytes sent and the rows written -------------------------------------------------------------


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
    return settings


def records_of(side):
    """The record of each briefing the desk wrote (new with the move: nothing is compared with it)."""
    con = side.connect()
    con.row_factory = __import__("sqlite3").Row
    try:
        return [dict(r) for r in con.execute("SELECT * FROM wts_briefing_log ORDER BY rowid")]
    finally:
        con.close()


SCRIPTED = shared.read("ask-scripted.json")


@pytest.mark.parametrize("name", list(shared.SCRIPTED))
def test_the_flow_of_a_question_sends_and_writes_what_it_did(name, tmp_path, monkeypatch):
    """Every case of wts-core's docs/adr/0009 "Parity", played through the site's real web app over a scripted
    pipeline: the status and the exact bytes of every answer, the rows written to the spending record, the answer
    cache, the saved briefings and the notes of failures, and what was asked of the pipeline. No difference."""
    from wts_core.testing import FixedClock

    from wcs.reference.countries import countries

    made = []

    def make(has_model=True, **usage):
        clock = FixedClock(shared.START)
        settings = settings_in(tmp_path / f"side-{len(made)}", monkeypatch, **usage)
        pipeline = shared.ScriptedPipeline(clock.advance, settings.answer["model"], countries(), has_model=has_model)
        made.append(new_side(settings, pipeline, clock, monkeypatch))
        return made[-1]

    now = shared.plain(shared.scripted_asks(make, only=name), tmp=tmp_path)[name]
    before = SCRIPTED[name]
    assert len(now["sent"]) == len(before["sent"])
    for (status, body), (was_status, was_body) in zip(now["sent"], before["sent"], strict=True):
        assert status == was_status
        assert shared.events_of(body) == shared.events_of(was_body)  # the readable comparison first
        assert body == was_body  # then every byte
    for table in ("spending", "cache", "briefings", "failures"):
        assert now["rows"][table] == before["rows"][table], table
    assert now["calls"] == before["calls"]
    # new with the move: one record for every question the desk was handed, and never the visitor's code in it
    written = records_of(made[0])
    assert len(written) == sum(1 for status, _ in now["sent"] if status == 200)
    codes = {row[3] for row in now["rows"]["spending"] if row[3]}
    assert not any(code in repr(written) for code in codes)


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
    the checking rules wherever it is): wts-core docs/MIGRATION.md 1.4."""
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


REAL = shared.read("ask-real.json")


@pytest.mark.parametrize("name", list(shared.REAL))
def test_the_real_pipeline_sends_and_writes_what_it_did(name, small_index, tmp_path, monkeypatch):
    """The same through the site's real pipeline, over a small index and a faked SDK: what the model is sent,
    whole (its instructions, its documents, the question, the model and how hard it is asked to think), what the
    reader is sent, what is written, a saved briefing opened again, the search and the status. One difference, and
    it is listed (wts-core docs/MIGRATION.md 1.4): a written answer carries ``rules``, the version of the checking
    rules that wrote it. With that key taken out, nothing differs."""
    import os
    import shutil

    from conftest import model_of
    from wts_core.adapters.hashing import HashingEmbedder
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
    before = REAL[name]
    assert now["calls"] == before["calls"]  # what the model was asked, whole
    assert len(now["sent"]) == len(before["sent"])
    stamped = 0
    for (status, body), (was_status, was_body) in zip(now["sent"], before["sent"], strict=True):
        assert status == was_status
        assert unstamped_events(body) == shared.events_of(was_body)
        stamped += body != was_body
        # every byte, once the stamp is taken out of the text as it was sent
        assert body.replace(', "rules": 1}', "}") == was_body
    assert unstamped_rows(now["rows"]) == unstamped_rows(before["rows"])
    assert without_the_stamp(now["saved"]) == before["saved"] and now["search"] == before["search"]
    assert now["status"] == before["status"]
    written = sum(1 for _, body in now["sent"] for e in shared.events_of(body)
                  if isinstance(e, dict) and e.get("type") == "final" and (e.get("answer") or {}).get("quotes_total")
                  is not None)
    assert stamped == written  # only an answer the checks wrote differs, and only by its stamp


# ---- limits, cost, caps, cache keys ------------------------------------------------------------------------------


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
        assert key == row["key"], row
        assert key is None or len(key) == 64
    # a follow-up has no key; nor has anything when there is no model or no cache
    by_body = {(same(r["body"]), same(r["world"])): r["key"] for r in before}
    assert by_body[(same({"question": shared.Q2, "history": shared.EARLIER}), "{}")] is None
    body = web.AskBody(question=shared.Q)
    assert web.answer_key(settings, body, model=False, cache_hours=168) is None
    assert web.answer_key(settings, body, model=True, cache_hours=0) is None
    # which briefing a follow-up follows is no part of what an answer depends on
    assert web.answer_key(settings, web.AskBody(question=shared.Q, follows="Briefing01"), model=True,
                          cache_hours=168) == by_body[(same({"question": shared.Q}), "{}")]


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


# ---- reports, statements, saved briefings: what the forms are answered and what the owner's commands print -------

# Every difference from what the site's own code gave, each from a list in wts-core's docs (AS-BUILT.md,
# "replies" and "cli", "At adoption"; MIGRATION.md 1.9), or found here and said so. Whatever is not named here is
# compared whole: the same status, the same JSON, the same lines printed, the same exit code.
REPORT_OF_SPACES = "a report of spaces"            # found here: it was kept as an empty report; it is refused now
TWO_FAULTS = "a statement with two things wrong: the name and the words"  # AS-BUILT: the order of the checks
TAKEDOWN_FIRST = ("the open reports", "every report")                    # AS-BUILT: takedown requests listed first
THE_EXACT_REASON = {                                                      # AS-BUILT: "the owner now sees the reason"
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

    def run(args):
        result = CliRunner().invoke(cli.app, args)
        return result.exit_code, result.output

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
    listed = {REPORT_OF_SPACES, TWO_FAULTS, *TAKEDOWN_FIRST, *THE_EXACT_REASON, *CORRECTIONS}
    for part in ("http", "cli", "after"):
        assert [step["case"] for step in now[part]] == [step["case"] for step in before[part]]
        for step, was in zip(now[part], before[part], strict=True):
            if step["case"] not in listed:
                assert step == was, step["case"]
    steps = {step["case"]: step for part in ("http", "cli", "after") for step in now[part]}
    were = {step["case"]: step for part in ("http", "cli", "after") for step in before[part]}

    # a report of nothing but spaces was kept, empty; it is refused, in words of the site's own
    assert were[REPORT_OF_SPACES]["status"] == 200
    assert steps[REPORT_OF_SPACES] == {"case": REPORT_OF_SPACES, "status": 422, "json": {
        "detail": "Say what is wrong, in a few words at least."}}
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
            # AS-BUILT: "text cleaning now closes runs of blank lines" (and line endings are made plain); an empty
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


# ---- backups, restores, off-site copies and their alerts ---------------------------------------------------------


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
