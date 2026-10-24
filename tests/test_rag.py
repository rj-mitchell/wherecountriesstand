from datetime import date

import pyarrow as pa
import pyarrow.parquet as pq

from wcs.engine import Filters
from wcs.rag.neutral import flag_loaded
from wcs.rag.pipeline import AskRequest, Pipeline, answer_once
from wcs.rag.query_parse import QueryPlan, parse_offline
from wcs.rag.sources import blocks_for, build_sources, record_source, speaker_label
from wcs.rag.verify import Segment, verify
from wcs.reference.countries import countries
from wcs.store.parquet import VOTE_SCHEMA, votes_path
from wcs.votes import VoteFinder, terms

TODAY = date(2026, 10, 24)


def test_offline_plan():
    p = parse_offline("How have small island States spoken about climate finance since 2015?", countries(), TODAY)
    assert p.filters.groups == ["sids"] and p.filters.date_from == date(2015, 1, 1)
    assert "climate finance" in p.search_text and "small island" not in p.search_text
    p = parse_offline("What did France and Brazil say in the Security Council about Haiti?", countries(), TODAY)
    assert set(p.filters.countries) == {"FRA", "BRA"}   # Haiti is the subject, not a speaker
    assert "haiti" in p.search_text
    assert p.filters.sources == ["sc"]
    p = parse_offline("How did EU members vote on Ukraine between 2022 and 2024?", countries(), TODAY)
    assert p.filters.groups == ["eu"] and p.wants_votes
    assert p.filters.date_from == date(2022, 1, 1) and p.filters.date_to == date(2024, 12, 31)
    p = parse_offline("What has been said about peacekeeping?", countries(), TODAY)
    assert p.filters.date_from == date(2016, 1, 1)   # the last ten years by default


def test_short_country_words_need_a_capital():
    p = parse_offline("Did any country chad about reform?", countries(), TODAY)
    assert "TCD" not in p.filters.countries
    p = parse_offline("What has Chad said about the Sahel?", countries(), TODAY)
    assert "TCD" in p.filters.countries


def test_plan_round_trip():
    p = parse_offline("What have African States said about reform since 2020?", countries(), TODAY)
    q = QueryPlan.from_dict({"question": p.question, **p.to_dict()})
    assert q.filters.groups == p.filters.groups and q.filters.date_from == p.filters.date_from


def rec(**kw):
    base = {"record_id": "sc:S/PV.7565:0004", "source": "sc", "body": "Security Council", "date": "2015-11-20",
            "date_precision": "day", "year": 2015, "meeting": "7565th meeting", "symbol": "S/PV.7565",
            "agenda": "Threats to international peace and security", "speaker_name": "Mr. Delattre",
            "speaker_post": None, "speaker_role": "", "country_code": "FRA", "country_name": "France",
            "entity_kind": "member_state", "on_behalf_of": None, "language": "French", "ocr": False,
            "groups": ["region:europe", "p5", "eu", "nato"], "text": "Our action is self-defence.",
            "url": "https://undocs.org/en/S/PV.7565"}
    return {**base, **kw}


def test_source_titles():
    t = record_source(1, rec()).title
    assert t.startswith("Mr. Delattre (France) (spoke in French); France: European States; Permanent members")
    assert "Security Council, 7565th meeting, 20 November 2015" in t and "S/PV.7565" in t
    assert "English text is the UN's translation from French" in t
    g = speaker_label(rec(country_code="EGY", country_name="Egypt", on_behalf_of="Group of 77 and China",
                          language=None, groups=["region:africa", "au"]))
    assert "speaking on behalf of the Group of 77 and China" in g
    chair = speaker_label(rec(entity_kind="chair", speaker_name="The President", on_behalf_of="Security Council"))
    assert chair == "The President, the President of the Security Council, speaking for the Council"
    gd = record_source(2, rec(date_precision="session", meeting="General Debate, 78th session", year=2023,
                              symbol=None, source="gd")).title
    assert "General Debate, 78th session (2023)" in gd and "20 November" not in gd


def test_long_speeches_split_into_citable_blocks():
    text = " ".join(f"This is sentence number {i} of a long speech." for i in range(60))
    blocks = blocks_for(text, 40)
    assert len(blocks) > 3 and all(b.endswith(".") for b in blocks)
    assert " ".join(blocks) == " ".join(text.split())


def test_verification_and_loaded_words():
    sources = build_sources([rec()])
    blocks = [{"text": "France said its action was self-defence", "citations": [
        {"search_result_index": 0, "cited_text": "Our action is self-defence."}]},
        {"text": ", while the regime in question ignored the West.", "citations": []}]
    report = verify(blocks, sources)
    assert report.citations_invalid == 0 and report.segments[0].sources == [1]
    words = [m["word"] for s in report.segments for m in s.loaded] if flag_loaded(report.segments) else []
    assert "regime" in words and "the West" in words
    seg = Segment("Troops moved to the west of the country.")
    assert flag_loaded([seg]) == 0


def test_votes(settings):
    rows = []
    for code, vote in (("GBR", "Y"), ("FRA", "Y"), ("RUS", "N"), ("CHN", "A")):
        rows.append({"undl_id": "1", "country_code": code, "country_name": code, "vote": vote,
                     "date": date(2022, 3, 2), "session": 76, "resolution": "A/RES/ES-11/1", "draft": None,
                     "meeting": "A/ES-11/PV.5", "title": "Aggression against Ukraine : resolution / adopted by the "
                     "General Assembly", "agenda_title": "Letter from Ukraine", "subjects": "UKRAINE--ARMED CONFLICT",
                     "vote_note": None, "total_yes": 141, "total_no": 5, "total_abstentions": 35,
                     "total_non_voting": 12, "total_ms": 193, "url": "https://digitallibrary.un.org/record/1"})
    votes_path(settings.parquet_dir).parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows, schema=VOTE_SCHEMA), votes_path(settings.parquet_dir))
    found = VoteFinder(settings).find("aggression against Ukraine", Filters(date_from=date(2020, 1, 1)))
    assert len(found) == 1 and found[0]["title"] == "Aggression against Ukraine"
    assert found[0]["votes"]["N"] == ["Russian Federation"]
    src = build_sources([], found)[0]
    assert src.kind == "vote" and any(b.startswith("Against (1): Russian Federation") for b in src.blocks)
    assert VoteFinder(settings).find("aggression against Ukraine", Filters(date_to=date(2000, 1, 1))) == []
    assert terms("How did countries vote on nuclear disarmament?") == ["nuclear", "disarmament"]


def test_pipeline_without_a_model_shows_the_evidence(settings, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("WCS_ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    out = answer_once(Pipeline(settings, today=TODAY), AskRequest("What has been said about peacekeeping?"))
    assert out["plan"]["plan"]["parser"] == "offline"
    assert out["final"]["answer"]["no_records"]   # nothing is indexed in an empty data folder
