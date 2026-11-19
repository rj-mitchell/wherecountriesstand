from datetime import date

import pytest
from conftest import speech
from wts_core.adapters.hashing import HashingEmbedder

from wcs.engine import Engine, Filters, build_where
from wcs.index.build import Indexer, chunk_text, embed_pending
from wcs.reference.countries import countries
from wcs.store.parquet import write_by_year

D = date(2023, 3, 1)


@pytest.fixture
def engine(settings):
    recs = [
        speech("sc:S/PV.9000:0001", D, None, "I now give the floor to the representative of France.",
               kind="chair", participant="president", speaker="The President", n=1),
        speech("sc:S/PV.9000:0002", D, "FRA", "France supports climate finance for adaptation in Africa.", n=2),
        speech("sc:S/PV.9000:0003", D, "FRA", "France again supports climate finance and adaptation.", n=3),
        speech("sc:S/PV.9000:0004", D, "FRA", "France calls for climate finance, adaptation and resilience.", n=4),
        speech("sc:S/PV.9000:0005", D, "KEN", "Kenya says climate finance for adaptation is too slow.", n=5),
        speech("sc:S/PV.9000:0006", D, "FJI", "Fiji warns climate finance must reach small islands.",
               participant="guest", n=6),
        speech("sc:S/PV.9000:0007", D, "EGY", "Speaking for the Group, climate finance must double.",
               behalf="Group of 77 and China", n=7),
        speech("sc:S/PV.9000:0008", D, None, "The European Union supports climate finance.", kind="organisation",
               label="European Union", participant="guest", n=8),
        speech("sc:S/PV.3100:0002", date(1992, 6, 1), "FRA", "France on climate finance in 1992.",
               symbol="S/PV.3100", n=2),
        speech("gd:78:GBR", date(2023, 9, 1), "GBR", "The United Kingdom backs climate finance.", source="gd"),
    ]
    write_by_year(settings.parquet_dir, "sc", [r for r in recs if r["source"] == "sc"])
    write_by_year(settings.parquet_dir, "gd", [r for r in recs if r["source"] == "gd"])
    idx = Indexer(settings, countries())
    assert idx.index_all() == len(recs)
    emb = HashingEmbedder(64)
    embed_pending(settings, idx.index, idx.state, emb)
    return Engine(settings, embedder=emb, today=date(2026, 10, 1))


def ids(res):
    return [r["record_id"] for r in res.records]


def test_default_search_is_states_and_group_statements(engine):
    res = engine.search("climate finance", limit=20)
    assert res.semantic_available
    got = ids(res)
    assert "sc:S/PV.9000:0001" not in got          # the presiding chair
    assert "sc:S/PV.9000:0008" not in got          # an organisation
    assert "sc:S/PV.9000:0007" in got              # a group statement
    assert sum(1 for i in got if i.startswith("sc:S/PV.9000") and engine_country(res, i) == "FRA") <= 2


def engine_country(res, record_id):
    return next(r["country_code"] for r in res.records if r["record_id"] == record_id)


def test_filters(engine):
    assert set(ids(engine.search("climate finance", Filters(countries=["KEN"])))) == {"sc:S/PV.9000:0005"}
    assert ids(engine.search("climate finance", Filters(groups=["sids"]))) == ["sc:S/PV.9000:0006"]
    assert ids(engine.search("climate finance", Filters(sources=["gd"]))) == ["gd:78:GBR"]
    early = engine.search("climate finance", Filters(date_to=date(1995, 1, 1)))
    assert ids(early) == ["sc:S/PV.3100:0002"]
    assert "sc:S/PV.9000:0007" not in ids(engine.search("climate finance", Filters(group_statements=False)))
    orgs = engine.search("climate finance", Filters(kinds=["organisation"]))
    assert ids(orgs) == ["sc:S/PV.9000:0008"]


def test_groups_on_the_day_and_council_place(engine):
    res = engine.search("climate finance", Filters(countries=["FJI"]))
    assert "council_guest" in res.records[0]["groups"] and "sids" in res.records[0]["groups"]
    fra = engine.search("climate finance", Filters(countries=["FRA"], date_from=date(2020, 1, 1)))
    assert all("p5" in r["groups"] and "eu" in r["groups"] for r in fra.records)


def test_records_carry_names_and_evidence(engine):
    res = engine.search("climate finance", Filters(countries=["EGY"]))
    r = res.records[0]
    assert r["country_name"] == "Egypt" and r["on_behalf_of"] == "Group of 77 and China"
    assert res.evidence["records"] == 1 and "Statements on behalf of groups" in res.evidence["groups"]


def test_where_clause_quotes_values():
    w = build_where(Filters(countries=["CIV"], groups=["region:africa"], date_from=date(2020, 1, 1)))
    assert "country_code IN ('CIV')" in w and "array_has_any(groups, ['region:africa'])" in w
    assert "DATE '2020-01-01'" in w


def test_chunking():
    text = " ".join(f"Sentence {i} is here." for i in range(200))
    chunks = chunk_text(text, 100)
    assert len(chunks) > 1 and all(len(c.split()) <= 110 for c in chunks)


def test_regroup_updates_groups_in_place(engine, settings):
    from wcs.index.build import regroup
    from wcs.index.lance import PASSAGES, VECTORS, group_rows, set_groups

    index = engine.index
    for name in (PASSAGES, VECTORS):   # as if indexed before the Group of 77 was added
        set_groups(index, name, [{"passage_id": "sc:S/PV.9000:0005#0", "groups": ["region:africa"]}])
    vectors_before = index.vectors().count_rows()
    assert regroup(settings, countries(), index) == {PASSAGES: 1, VECTORS: 1}
    rows = {r["passage_id"]: r["groups"] for r in group_rows(index, VECTORS)}
    assert {"g77", "nam", "au", "council_elected"} <= set(rows["sc:S/PV.9000:0005#0"])
    assert index.vectors().count_rows() == vectors_before
    assert regroup(settings, countries(), index) == {PASSAGES: 0, VECTORS: 0}
    assert ids(engine.search("climate finance", Filters(groups=["g77"])))


def test_a_named_country_is_not_capped_at_two(engine):
    """Found in the first-load rehearsal: two sources per country starved one-country questions."""
    res = engine.search("climate finance", Filters(countries=["FRA"]), limit=12)
    assert len([r for r in res.records if r["country_code"] == "FRA"]) == 4   # all of France's
    assert len([r for r in engine.search("climate finance", Filters(), limit=12).records
                if r["country_code"] == "FRA"]) == 2                                   # balanced otherwise


def test_the_first_check_of_what_is_embedded_is_made_however_lately_the_computer_started(engine, monkeypatch):
    """The answer is kept for ten minutes by the computer's own clock, which counts from when it started. On one
    started less than ten minutes ago the first check was taken for a recent one and never made, and the search
    failed with an error where it should have said nothing relevant was found (found while moving onto wts-core)."""
    import time

    monkeypatch.setattr(time, "monotonic", lambda: 5.0)
    assert engine.mostly_embedded() is True


# The States a question names are heard if they have spoken on the subject (docs/DECISIONS.md, 21)


def scored(record_id, code, region, relevance, **data):
    from wcs.rank.composite import Candidate, Scored

    c = Candidate(record_id=record_id, date=D, data={"country_code": code, "entity_kind": "member_state",
                                                    "groups": [f"region:{region}"], **data})
    return Scored(c, relevance, 1.0, 1.0, 0.0, "role not given", relevance)


def field_of_ten():
    """Ten strong statements from Europe, Africa, the Americas and Asia, then one weaker one from Israel and one far
    weaker from the United States, best first."""
    regions = ["europe", "africa", "americas", "asia"]
    out = [scored(f"r{i:02}", f"C{i:02}", regions[i % 4], 1.0 - i * 0.02) for i in range(10)]
    return out + [scored("isr", "ISR", "asia", 0.4), scored("usa", "USA", "americas", 0.1)]


def test_a_named_state_that_has_spoken_is_included():
    from wcs.rank.evidence import select_evidence

    pool = field_of_ten()
    plain = [s.candidate.record_id for s in select_evidence(pool, 6)]
    assert "isr" not in plain                                  # left out by ranking alone (Asia's only voice is weak)
    named = [s.candidate.record_id for s in select_evidence(pool, 6, named=["ISR"])]
    assert "isr" in named and len(named) == 6                  # in, within the same number of places
    assert set(named) - {"isr"} < set(plain)                   # one of the others made way, nothing new came in


def test_a_named_state_with_nothing_relevant_is_not_forced_in():
    from wcs.rank.evidence import select_evidence

    # the United States' statement is below the relevance floor (a quarter of the best), so it stays out
    chosen = [s.candidate.record_id for s in select_evidence(field_of_ten(), 6, named=["USA"])]
    assert "usa" not in chosen and len(chosen) == 6
    # a statement it made on behalf of a group is not its own
    group = [*field_of_ten()[:10], scored("usa-g", "USA", "americas", 0.9, on_behalf_of="Some group")]
    seeded = select_evidence(group, 3, named=["USA"])
    assert [s.candidate.record_id for s in seeded] == [s.candidate.record_id for s in select_evidence(group, 3)]


def test_named_states_keep_to_the_number_of_sources():
    from wcs.rank.evidence import select_evidence

    pool = field_of_ten() + [scored(f"isr{i}", "ISR", "asia", 0.35 - i * 0.01) for i in range(3)]
    for k in (1, 2, 4, 6):
        chosen = select_evidence(pool, k, named=["ISR", "C09"], named_each=2)
        assert len(chosen) == k
    chosen = [s.candidate.record_id for s in select_evidence(pool, 6, named=["ISR"], named_each=2)]
    assert {"isr", "isr0"} <= set(chosen)                      # two, its best, and no more than any country's cap
    assert sum(1 for s in chosen if s.startswith("isr")) == 2


@pytest.fixture
def narrow(tmp_path, monkeypatch):
    """A search that takes only the three best candidates of each kind, over strong statements by six countries
    and a weaker one by Israel: Israel is not among the three unless it is searched for on its own."""
    from wcs.config import load_settings

    monkeypatch.setenv("WCS_DATA_DIR", str(tmp_path / "data"))
    settings = load_settings(**{"embedding.backend": "hashing", "embedding.dim": 64, "live.gd_bundled": "",
                                "ranking.candidates_bm25": 3, "ranking.candidates_vector": 3})
    text = "Accountability for violations in Gaza, a ceasefire and humanitarian access to Gaza now."
    recs = [speech(f"sc:S/PV.9100:{n:04}", D, code, f"{code}: {text}", symbol="S/PV.9100", n=n)
            for n, code in enumerate(["FRA", "KEN", "BRA", "DEU", "NGA", "MEX"], start=1)]
    recs += [speech("sc:S/PV.9100:0007", D, "ISR", "Israel answers the call for accountability in Gaza.",
                    symbol="S/PV.9100", n=7),
             speech("sc:S/PV.9100:0008", D, "USA", "The United States on fisheries management in the Pacific.",
                    symbol="S/PV.9100", n=8)]
    write_by_year(settings.parquet_dir, "sc", recs)
    idx = Indexer(settings, countries())
    idx.index_all()
    emb = HashingEmbedder(64)
    embed_pending(settings, idx.index, idx.state, emb)
    return Engine(settings, embedder=emb, today=date(2026, 10, 1))


def test_the_engine_searches_for_a_named_state_on_its_own(narrow):
    query = "accountability gaza ceasefire"
    assert "ISR" not in {r["country_code"] for r in narrow.search(query, limit=4).records}
    res = narrow.search(query, limit=4, named=["ISR", "USA"])
    assert len(res.records) == 4
    assert "ISR" in {r["country_code"] for r in res.records}
    assert "USA" not in {r["country_code"] for r in res.records}   # it said nothing on the subject
    assert res.evidence["named"] == [{"code": "ISR", "name": "Israel", "records": 1},
                                     {"code": "USA", "name": "United States of America", "records": 0}]
    # a question that limits who spoke asks what they said: nobody else is brought in
    only = narrow.search(query, Filters(countries=["KEN"]), limit=4, named=["ISR"])
    assert {r["country_code"] for r in only.records} == {"KEN"} and "named" not in only.evidence

