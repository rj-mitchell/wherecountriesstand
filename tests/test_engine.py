from datetime import date

import pytest
from conftest import speech

from wcs.embed.base import HashingEmbedder
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
    from wcs.index.lance import PASSAGES, VECTORS

    index = engine.index
    for name in (PASSAGES, VECTORS):   # as if indexed before the Group of 77 was added
        index.set_groups(name, [{"passage_id": "sc:S/PV.9000:0005#0", "groups": ["region:africa"]}])
    vectors_before = index.vectors().count_rows()
    assert regroup(settings, countries(), index) == {PASSAGES: 1, VECTORS: 1}
    rows = {r["passage_id"]: r["groups"] for r in index.group_rows(VECTORS)}
    assert {"g77", "nam", "au", "council_elected"} <= set(rows["sc:S/PV.9000:0005#0"])
    assert index.vectors().count_rows() == vectors_before
    assert regroup(settings, countries(), index) == {PASSAGES: 0, VECTORS: 0}
    assert ids(engine.search("climate finance", Filters(groups=["g77"])))
