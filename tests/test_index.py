"""Keeping the search index current without waste: what an update writes, re-embeds and rebuilds."""

import json
from datetime import date

import numpy as np
import pytest
from conftest import speech
from wts_core.adapters.hashing import HashingEmbedder
from wts_core.adapters.lancedb import LanceIndex, vector_index_due
from wts_core.ports import IndexOutOfDate

from wcs.index import lance
from wcs.index.build import Indexer, embed_pending, refresh_indices
from wcs.reference.countries import countries
from wcs.store.parquet import write_year
from wcs.update import REPORTS_KEPT, Report, run, update_index, write_report

D = date(2026, 3, 2)


class Counting(HashingEmbedder):
    def __init__(self):
        super().__init__(64)
        self.texts: list[str] = []

    def embed_documents(self, texts):
        self.texts += texts
        return super().embed_documents(texts)


def meeting(n: int, words: str = "climate finance") -> dict:
    return speech(f"sc:S/PV.9900:{n:04d}", D, "KEN", f"Kenya speech {n} on {words}.", n=n)


@pytest.fixture
def loaded(settings):
    write_year(settings.parquet_dir, "sc", 2026, [meeting(n) for n in range(1, 4)])
    idx = Indexer(settings, countries())
    idx.index_all()
    embed_pending(settings, idx.index, idx.state, HashingEmbedder(64))
    refresh_indices(idx.index, idx.state)
    return settings


def test_a_new_meeting_embeds_only_its_own_passages(loaded):
    """One new meeting, or one corrected speech, must not re-embed the whole year (as it did)."""
    idx = Indexer(loaded, countries())
    before = idx.index.vectors_for_keys(["sc:2026"])
    changed = meeting(2, "sea-level rise")
    write_year(loaded.parquet_dir, "sc", 2026, [meeting(1), changed, meeting(3), meeting(4)])
    idx.index_year("sc", 2026)
    assert idx.state.unembedded() == ["sc:2026"]
    emb = Counting()
    assert embed_pending(loaded, idx.index, idx.state, emb) == 2
    assert sorted(t.split(": ", 1)[1] for t in emb.texts) == ["Kenya speech 2 on sea-level rise.",
                                                               "Kenya speech 4 on climate finance."]
    after = idx.index.vectors_for_keys(["sc:2026"])
    assert len(after) == 4 and idx.state.unembedded() == []
    for pid in ("sc:S/PV.9900:0001#0", "sc:S/PV.9900:0003#0"):
        assert np.array_equal(before[pid], after[pid])
    # unchanged throughout: nothing at all is left to embed
    idx.index_year("sc", 2026)
    assert idx.state.unembedded() == [] and len(idx.index.vectors_for_keys(["sc:2026"])) == 4
    # a new embedding model: every passage is embedded again
    Indexer(loaded, countries(), keep_vectors=False).index_year("sc", 2026)
    assert idx.index.vectors_for_keys(["sc:2026"]) == {}


def test_an_update_with_no_changes_writes_no_new_version(loaded):
    """Each write leaves a new version of the index on disk; the twice-daily update used to rebuild every index
    whether or not anything had changed."""
    idx = Indexer(loaded, countries())
    versions = idx.index.versions()
    report = run(loaded, do_votes=False, do_sc=False, do_gd=False, embed_seconds=60)
    assert report.index is None and report.indexed == report.embedded == 0
    assert idx.index.versions() == versions


def test_an_update_merges_new_rows_and_cleans_up(loaded, monkeypatch):
    calls = []
    real = LanceIndex.tidy
    monkeypatch.setattr(LanceIndex, "tidy", lambda self: (calls.append(1), real(self)))
    monkeypatch.setattr(LanceIndex, "build_indexes",
                        lambda self, vectors=True: pytest.fail("a routine update rebuilt the indices"))
    write_year(loaded.parquet_dir, "sc", 2026, [meeting(n) for n in range(1, 5)])
    report = Report(started="test")
    update_index(loaded, countries(), {"sc": {2026}, "gd": set()}, report, embed_seconds=60)
    assert report.indexed == 4 and report.embedded == 1 and report.index == "merged" and calls == [1]
    idx = Indexer(loaded, countries())
    assert idx.index.unmerged() == 0
    assert [r["record_id"] for r in idx.index.search_words("Kenya speech", None, 10)].count("sc:S/PV.9900:0004") == 1


def test_the_meaning_index_is_trained_again_only_when_vectors_grow_by_a_fifth(settings, monkeypatch):
    monkeypatch.setattr(lance, "MIN_VECTOR_INDEX_ROWS", 256)
    write_year(settings.parquet_dir, "sc", 2026, [meeting(n) for n in range(1, 301)])
    idx = Indexer(settings, countries())
    idx.index_all()   # no vectors yet: no meaning-search index
    assert idx.index.trained_on() is None
    embed_pending(settings, idx.index, idx.state, HashingEmbedder(64))
    assert refresh_indices(idx.index, idx.state) == "built" and idx.index.trained_on() == 300
    for upto, outcome, trained in ((330, "merged", 300), (370, "built", 370)):
        write_year(settings.parquet_dir, "sc", 2026, [meeting(n) for n in range(1, upto + 1)])
        idx.index_year("sc", 2026)
        embed_pending(settings, idx.index, idx.state, HashingEmbedder(64))
        assert refresh_indices(idx.index, idx.state) == outcome
        assert idx.index.trained_on() == trained
    # the count is kept beside the index by the shared adapter, no longer in this site's state.json
    assert json.loads((settings.index_dir / "vector_index.json").read_text()) == {"vectors": 370}
    assert "_indices" not in json.loads((settings.index_dir / "state.json").read_text())
    assert idx.state.unembedded() == []


def test_vector_index_due():
    n = lance.MIN_VECTOR_INDEX_ROWS
    assert not vector_index_due(n - 1, None, False)          # too few to be worth an index
    assert vector_index_due(n, None, False)                  # first build
    assert vector_index_due(n, None, True)                   # built before its size was recorded
    assert not vector_index_due(int(n * 1.2), n, True)       # merged in
    assert vector_index_due(int(n * 1.2) + 1, n, True)       # grown by more than a fifth
    assert vector_index_due(n, n, False)                     # the index has gone


def test_update_reports_are_pruned(settings):
    logs = settings.data_dir / "logs"
    logs.mkdir(parents=True)
    for day in range(1, 71):
        (logs / f"update-202609{day:02d}T0541.json").write_text("{}")  # 70 older ones
    write_report(settings, Report(started="2026-11-06T05:41:00+00:00"))
    kept = sorted(p.name for p in logs.glob("update-*.json"))
    assert len(kept) == REPORTS_KEPT and kept[-1] == "update-20261106T054100.json"
    assert kept[0] == f"update-202609{71 - REPORTS_KEPT + 1:02d}T0541.json"


# ---- an index built by the site's own code, before the shared adapter --------------------------------------------


@pytest.fixture
def old_index(settings, monkeypatch):
    """The index tests/golden/record.py built with the code as it was (300 passages, each with a vector, and a
    meaning-search index trained on them), unpacked into the data folder."""
    import tarfile

    import shared

    monkeypatch.setattr(lance, "MIN_VECTOR_INDEX_ROWS", 256)  # as it was built
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(shared.GOLDEN / "old-index.tar.gz") as tar:
        tar.extractall(settings.data_dir, filter="data")
    return settings


def test_an_index_made_before_the_move_opens_and_searches_the_same(old_index):
    import shared

    from wcs.engine import Engine, Filters
    from wcs.index.lance import group_rows, open_index

    before = shared.read("index.json")
    embedder = HashingEmbedder(64)
    assert not (old_index.index_dir / "wts_index.json").exists()
    index = open_index(old_index.index_dir, embedder)
    assert index.stats() == before["stats"]
    assert index.has_word_index() is before["has_word_index"]
    assert index.has_index("vectors", "vector") is before["has_vector_index"]
    for found in before["words"]:
        hits = shared.close_hits(index.search_words(found["query"], found["where"], 16), "_score")
        assert hits == found["hits"], found
    for found in before["meaning"]:
        hits = shared.close_hits(index.search_meaning(embedder.embed_query(found["query"]), found["where"], 16,
                                                      exact=True), "_distance")
        assert hits == found["hits"], found
    assert sorted(r["passage_id"] for r in index.rows_for_keys(["sc:2020"])) == before["rows_for_keys"]
    assert sorted(index.embedded_ids(["sc:2020"])) == before["embedded_ids"]
    assert sorted([r["passage_id"], sorted(r["groups"] or [])]
                  for r in group_rows(index, "passages"))[:20] == before["group_rows"]
    # the same records, in the same order, with the same scores: retrieval and ranking over it are unchanged
    engine = Engine(old_index, embedder=embedder, today=shared.TODAY)
    for found in before["engine"]:
        res = engine.search(found["query"], Filters(countries=found["countries"]), limit=6)
        assert res.candidates == found["candidates"] and res.semantic_available is found["semantic"]
        assert [[r["record_id"], round(r["ranking"]["score"], 4)] for r in res.records] == found["records"], found


def test_the_trained_count_is_handed_over_so_the_old_index_is_not_trained_again(old_index, monkeypatch):
    """This site kept the number of vectors its meaning-search index was trained on in state.json; the adapter
    keeps it beside the index. The first refresh hands the count over: without it the adapter would not know, and
    would train the index again once (wts-core docs/AS-BUILT.md, "adapters")."""
    import shared

    from wcs.index.build import State
    from wcs.index.lance import open_index

    index = open_index(old_index.index_dir, HashingEmbedder(64))
    state = State(old_index.index_dir / "state.json")
    assert state.vector_index_trained_on == shared.read("index.json")["trained_on"] == 300
    assert index.trained_on() is None
    monkeypatch.setattr(LanceIndex, "build_vector_index", lambda self: pytest.fail("the index was trained again"))
    assert refresh_indices(index, state) == "merged"
    assert index.trained_on() == 300


def test_the_index_records_its_embedder_and_refuses_another(old_index):
    """New with the shared adapter: what the index was built with is written beside it the first time it is opened
    with an embedder, and an index is not opened for writing, or searched by meaning, with another model. Search by
    words carries on."""
    from wcs.engine import Engine
    from wcs.index.lance import open_index

    open_index(old_index.index_dir, HashingEmbedder(64))
    assert json.loads((old_index.index_dir / "wts_index.json").read_text())["embedder_id"] == "hashing-64"

    class Another(HashingEmbedder):
        def __init__(self):
            super().__init__(64)
            self.id = self.name = "another-model"

    with pytest.raises(IndexOutOfDate):
        open_index(old_index.index_dir, Another())
    assert open_index(old_index.index_dir).stats()["passages"] == 300  # as it stands: still opened, for words
    engine = Engine(old_index, embedder=Another(), today=date(2026, 10, 24))
    res = engine.search("climate finance for small islands")
    assert res.semantic_available is False and res.records
    # and the update says so, where the owner will see it
    report = Report(started="test")
    old_index.raw["embedding"] = {**old_index.raw["embedding"], "dim": 32}  # the settings now name another model
    update_index(old_index, countries(), {"sc": set(), "gd": set()}, report, embed_seconds=60)
    assert len(report.warnings) == 1 and "meaning search is off" in report.warnings[0]
    assert "wcs index --re-embed" in report.warnings[0] and report.embedded == 0
