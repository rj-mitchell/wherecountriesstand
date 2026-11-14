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
