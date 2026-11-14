"""`wcs evaluate` on a small store (no model): the questions file and the results folder are arguments, and both
are read and written as UTF-8 (as Where Councils Stand's tests/test_evaluate.py)."""

import json

from test_pipeline import TODAY, indexed  # noqa: F401  (the fixture)
from wts_core.adapters.hashing import HashingEmbedder

from wcs import evaluate as ev
from wcs.engine import Engine
from wcs.rag.pipeline import Pipeline


def test_evaluate_scores_questions_and_evidence(indexed, tmp_path):  # noqa: F811
    path = tmp_path / "q.toml"
    path.write_text('today = "2026-10-24"\n'
                    '[[q]]\nid = "kenya-finance"\nquestion = "What has Kenya said about climate finance since 2020?"\n'
                    'category = "country"\nexpect = { date_from = "2020", countries = ["KEN"] }\ninclude = ["KEN"]\n'
                    '[[q]]\nid = "côte"\nquestion = "What did Côte d’Ivoire say about climate finance in 1993?"\n'
                    'category = "dates"\nexpect = { date_from = "1993", date_to = "1993", countries = ["CIV"] }\n'
                    'no_records = true\n', encoding="utf-8")
    eng = Engine(indexed, embedder=HashingEmbedder(64), today=TODAY)
    results = ev.run(indexed, pipeline=Pipeline(indexed, eng, today=TODAY), path=path)
    assert [(r.id, r.plan_ok, r.evidence_ok) for r in results] == [("kenya-finance", True, True), ("côte", True, True)]
    s = ev.summary(results)
    assert s["plans_right"] == 2 and s["evidence_right"] == 2
    saved = ev.save(results, False, tmp_path / "runs")
    assert saved.parent == tmp_path / "runs"
    kept = json.loads(saved.read_text(encoding="utf-8"))
    assert kept["summary"]["plans_right"] == 2 and kept["results"][1]["question"].startswith("What did Côte d’Ivoire")


def test_the_test_set_is_read_from_its_own_file_by_default():
    today, questions = ev.load_questions()
    assert today.isoformat() == "2026-10-25" and len({q["id"] for q in questions}) == len(questions) > 10


# ---- the same test set on the shared harness (wts-core, ``wts_core.eval``), with each briefing written -------------


def test_the_free_scores_over_the_whole_test_set_are_what_they_were(indexed, monkeypatch):  # noqa: F811
    """`wcs evaluate` over the real eval/questions.toml and the two fixture speeches: every question's filters, the
    evidence found and the summary, as the code gave them before the move (tests/golden/evaluation.json)."""
    from dataclasses import asdict

    import shared

    today, _ = ev.load_questions()
    monkeypatch.delenv("WCS_ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    pipeline = Pipeline(indexed, Engine(indexed, embedder=HashingEmbedder(64), today=today), today=today)
    results = ev.run(indexed, pipeline=pipeline)
    before = shared.read("evaluation.json")
    assert json.loads(json.dumps([asdict(r) for r in results], default=str)) == before["results"]
    assert ev.summary(results) == before["summary"]


def test_the_harness_is_given_the_sites_own_scores(indexed, monkeypatch):  # noqa: F811
    """``CountriesEval.score`` is the same scoring, question by question, from what a reader would be sent (the plan
    and the source cards): each filter right or wrong as `wcs evaluate` has it, and the evidence."""
    import shared

    for name in ("WCS_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    site = ev.CountriesEval(indexed)
    site._pipeline = Pipeline(indexed, Engine(indexed, embedder=HashingEmbedder(64), today=site.today),
                              today=site.today)
    before = {r["id"]: r for r in shared.read("evaluation.json")["results"]}
    cases = site.cases()
    assert [c.id for c in cases] == list(before) and len(cases) == 55
    for case in cases:
        sent = {e["type"]: e for e in site.ask(case, {})}
        met = dict(site.score(case, sent["plan"]["plan"], sent["sources"]["sources"]))
        evidence = met.pop("no_records", met.pop("include", None))
        was = before[case.id]
        assert met == was["fields"], case.id
        # the old score counted votes as evidence found; a reader is sent them as source cards, so it is the same
        assert evidence == was["evidence_ok"], case.id
        assert site.group_of({"group": "African States"}) == "African States"


def test_a_run_with_each_briefing_written_and_the_checks_run_again_over_it(indexed, tmp_path):  # noqa: F811
    """`wcs eval run` on the two fixture speeches with a scripted model (no key, no call): each briefing is written
    and checked, the run is saved, held to the thresholds and noted as passed; the briefings it kept are then
    checked again from the files alone (`wcs eval replay`), and give the same reports."""
    from test_privacy import scripted_model
    from wts_core.eval import (
        assert_never_weaker,
        check_thresholds,
        has_passed,
        load_fixture,
        mark_passed,
        replay,
        run,
    )
    from wts_core.observe import config_id
    from wts_core.testing import FixedClock
    from wts_core.usage import PriceTable

    from wcs.api.app import configuration
    from wcs.rag.checks import LOADED_WORDS, VERIFIER

    path = tmp_path / "q.toml"
    path.write_text('today = "2026-10-24"\n'
                    '[[q]]\nid = "kenya-finance"\nquestion = "What has Kenya said about climate finance since 2020?"\n'
                    'category = "country"\nexpect = { date_from = "2020", countries = ["KEN"] }\ninclude = ["KEN"]\n'
                    '[[q]]\nid = "nothing"\nquestion = "What did Fiji say about climate finance in 1993?"\n'
                    'category = "dates"\nexpect = { date_from = "1993", date_to = "1993", countries = ["FJI"] }\n'
                    'no_records = true\n', encoding="utf-8")
    model = scripted_model()
    model.structured_answer = None  # it cannot read a question, so the built-in rules read each one
    pipeline = Pipeline(indexed, Engine(indexed, embedder=HashingEmbedder(64), today=TODAY), client=model, today=TODAY)
    site = ev.CountriesEval(indexed, pipeline=pipeline, path=path)
    config = configuration(indexed, None).settings
    thresholds = indexed.section("eval")["thresholds"]
    done = run(site, config, config_id=config_id(config), pricing=PriceTable.from_settings(indexed.usage),
               clock=FixedClock("2026-11-14T09:00:00+00:00"), record_fixtures=tmp_path / "fixtures", max_cost_usd=1.0)
    by_id = {r.id: r for r in done.results}
    assert by_id["kenya-finance"].outcome == "completed" and by_id["kenya-finance"].citations_invalid == 0
    assert by_id["nothing"].outcome == "no_records" and by_id["nothing"].expect_met["no_records"] is True
    assert by_id["kenya-finance"].cost_usd > 0 and by_id["kenya-finance"].spread  # the cost, and the sources by group
    assert check_thresholds(done, thresholds) == []
    runs = tmp_path / "runs"
    assert not has_passed(runs, done.config_id, thresholds)
    assert mark_passed(done, thresholds, runs).name == "passed.jsonl" and has_passed(runs, done.config_id, thresholds)
    # the briefing it kept, checked again from the file alone: the same report, so nothing weaker
    kept = sorted((tmp_path / "fixtures").glob("*.jsonl"))
    assert len(kept) == 1 and len(model.requests) == 1
    differences = replay([load_fixture(p) for p in kept], VERIFIER, LOADED_WORDS, curled=True)
    assert differences == []
    assert_never_weaker(differences)


def test_the_checks_give_the_old_codes_reports_on_replay():
    """The harness's replay over a dozen briefings as the model writes them, each with the report the site's own
    checks gave before the move (tests/golden/checks.json): no difference, and so nothing weaker. This is the
    before-and-after of the checks that needs no model; one on the server's whole record needs `wcs eval run`."""
    import shared
    from wts_core.eval import Fixture, assert_never_weaker, replay
    from wts_core.events import Block, Citation
    from wts_core.ports import Document

    from wcs.rag.checks import LOADED_WORDS, VERIFIER

    documents = tuple(Document(number=s["n"], title=s["title"], source=s["url"], passages=tuple(s["blocks"]))
                      for s in shared.SOURCES)
    headings = tuple(s["meta"].get("heading", "") for s in shared.SOURCES)
    before = {c["name"]: c["answer"] for c in shared.read("checks.json")["written"]}
    fixtures = [Fixture(name=c["name"], question=shared.Q, documents=documents, headings=headings,
                        blocks=tuple(Block(b["text"], tuple(Citation(x["search_result_index"], x["cited_text"])
                                                            for x in b.get("citations", []))) for b in c["blocks"]),
                        report=before[c["name"]]) for c in shared.CHECK_CASES]
    differences = replay(fixtures, VERIFIER, LOADED_WORDS, curled=True)
    assert len(fixtures) == 12 and differences == []
    assert_never_weaker(differences)


def test_the_eval_commands_are_there_and_the_note_of_a_passed_run_is_kept_by_git():
    import subprocess
    from pathlib import Path

    import typer

    from wcs.cli import app

    commands = typer.main.get_command(app).commands
    assert "evaluate" in commands and set(commands["eval"].commands) == {"run", "compare", "replay"}
    root = Path(__file__).parents[1]
    ignored = subprocess.run(["git", "check-ignore", "-q", "eval/runs/passed.jsonl"], cwd=root).returncode
    assert ignored == 1  # not ignored: it must reach the server
    assert subprocess.run(["git", "check-ignore", "-q", "eval/runs/20261114T090000.json"], cwd=root).returncode == 0
    assert "COPY eval ./eval" in (root / "Dockerfile").read_text()
