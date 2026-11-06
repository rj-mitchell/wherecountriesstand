"""Keeping the search index current without waste: what an update writes, re-embeds and rebuilds."""

import json
from datetime import date

import numpy as np
import pytest
from conftest import speech

from wcs.embed.base import HashingEmbedder
from wcs.index import lance
from wcs.index.build import Indexer, embed_pending, refresh_indices
from wcs.index.lance import vector_index_due
from wcs.reference.countries import countries
from wcs.store.parquet import write_year
from wcs.update import Report, run, update_index

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
    real = lance.SearchIndex.optimize
    monkeypatch.setattr(lance.SearchIndex, "optimize", lambda self: (calls.append(1), real(self)))
    monkeypatch.setattr(lance.SearchIndex, "build_indices",
                        lambda self, vectors=True: pytest.fail("a routine update rebuilt the indices"))
    write_year(loaded.parquet_dir, "sc", 2026, [meeting(n) for n in range(1, 5)])
    report = Report(started="test")
    update_index(loaded, countries(), {"sc": {2026}, "gd": set()}, report, embed_seconds=60)
    assert report.indexed == 4 and report.embedded == 1 and report.index == "merged" and calls == [1]
    idx = Indexer(loaded, countries())
    assert idx.index.unmerged() == 0
    assert [r["record_id"] for r in idx.index.bm25("Kenya speech", None, 10)].count("sc:S/PV.9900:0004") == 1


def test_the_meaning_index_is_trained_again_only_when_vectors_grow_by_a_fifth(settings, monkeypatch):
    monkeypatch.setattr(lance, "MIN_VECTOR_INDEX_ROWS", 256)
    write_year(settings.parquet_dir, "sc", 2026, [meeting(n) for n in range(1, 301)])
    idx = Indexer(settings, countries())
    idx.index_all()   # no vectors yet: no meaning-search index
    assert idx.state.vector_index_trained_on is None
    embed_pending(settings, idx.index, idx.state, HashingEmbedder(64))
    assert refresh_indices(idx.index, idx.state) == "built" and idx.state.vector_index_trained_on == 300
    for upto, outcome, trained in ((330, "merged", 300), (370, "built", 370)):
        write_year(settings.parquet_dir, "sc", 2026, [meeting(n) for n in range(1, upto + 1)])
        idx.index_year("sc", 2026)
        embed_pending(settings, idx.index, idx.state, HashingEmbedder(64))
        assert refresh_indices(idx.index, idx.state) == outcome
        assert idx.state.vector_index_trained_on == trained
    state = json.loads((settings.index_dir / "state.json").read_text())
    assert state["_indices"]["vector_index_trained_on"] == 370 and idx.state.unembedded() == []


def test_vector_index_due():
    n = lance.MIN_VECTOR_INDEX_ROWS
    assert not vector_index_due(n - 1, None, False)          # too few to be worth an index
    assert vector_index_due(n, None, False)                  # first build
    assert vector_index_due(n, None, True)                   # built before its size was recorded
    assert not vector_index_due(int(n * 1.2), n, True)       # merged in
    assert vector_index_due(int(n * 1.2) + 1, n, True)       # grown by more than a fifth
    assert vector_index_due(n, n, False)                     # the index has gone

