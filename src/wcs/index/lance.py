"""Search index: LanceDB tables for BM25 keyword search and vector search.

Two tables share the same filter columns:

* ``passages``  every searchable passage, with a BM25 full-text index on ``search_text``. Built straight after
  ingest, so keyword search is available immediately.
* ``vectors``   one embedding per passage. Filled by the (slower, resumable) embedding job; hybrid search uses
  whatever is embedded so far.

Rows are keyed by ``source_key`` (one source and year, such as ``sc:2015``), so reloading a year replaces its
rows. ``groups`` holds the groups the speaker's country belonged to on the day (region, the permanent five, EU,
NATO, AU, LDC, LLDC, SIDS, and the speaker's place at a Security Council meeting), so a question about a group is
a filter on the passages themselves.
"""

from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import lancedb
import numpy as np
import pyarrow as pa
from lancedb.index import FTS, Bitmap, BTree

log = logging.getLogger(__name__)

PASSAGES = "passages"
VECTORS = "vectors"

FILTER_FIELDS = [
    ("passage_id", pa.string()),
    ("record_id", pa.string()),
    ("source", pa.string()),             # sc, gd
    ("source_key", pa.string()),         # source and year: "sc:2015"
    ("body", pa.string()),
    ("date", pa.date32()),
    ("year", pa.int32()),
    ("country_code", pa.string()),
    ("entity_kind", pa.string()),        # member_state, observer_state, ..., organisation, un, chair
    ("speaker_role", pa.string()),       # head, minister, representative or ""
    ("participant", pa.string()),        # Security Council: president, member, guest
    ("group_statement", pa.bool_()),     # made on behalf of a group
    ("groups", pa.list_(pa.string())),
]

PASSAGE_SCHEMA = pa.schema([
    *FILTER_FIELDS,
    ("chunk", pa.int32()),
    ("speaker_name", pa.string()),
    ("country_name", pa.string()),
    ("on_behalf_of", pa.string()),
    ("symbol", pa.string()),
    ("heading", pa.string()),
    ("text", pa.string()),
    ("search_text", pa.string()),
])


def vector_schema(dim: int) -> pa.Schema:
    return pa.schema([*FILTER_FIELDS, ("vector", pa.list_(pa.float32(), dim))])


_QUERY_JUNK = re.compile(r"[\"'()\[\]{}:^~*+\-!?\\/<>=|&]")


def sanitise_query(text: str) -> str:
    """Plain words only: user text must never be read as full-text query syntax."""
    return " ".join(_QUERY_JUNK.sub(" ", text or "").split())


def sql_str(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def sql_list(values) -> str:
    return "[" + ", ".join(sql_str(v) for v in values) + "]"


# Every read opens the latest table version, so older versions only take disk space.
KEEP_OLD_VERSIONS = timedelta(hours=1)


class SearchIndex:
    def __init__(self, path: Path, dim: int):
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)
        self.dim = dim
        self.db = lancedb.connect(str(self.path))

    # ---- tables ---------------------------------------------------------------
    def _names(self) -> set[str]:
        res = self.db.list_tables()
        return set(getattr(res, "tables", res))

    def passages(self):
        if PASSAGES not in self._names():
            return self.db.create_table(PASSAGES, schema=PASSAGE_SCHEMA)
        return self.db.open_table(PASSAGES)

    def vectors(self):
        if VECTORS not in self._names():
            return self.db.create_table(VECTORS, schema=vector_schema(self.dim))
        return self.db.open_table(VECTORS)

    def has_data(self) -> bool:
        return PASSAGES in self._names() and self.passages().count_rows() > 0

    # ---- writes ---------------------------------------------------------------
    def replace_keys(self, keys: list[str], rows: list[dict[str, Any]]) -> None:
        """Replace all passages (and their vectors) for the given source keys."""
        tbl = self.passages()
        if keys:
            where = f"source_key IN ({', '.join(sql_str(k) for k in keys)})"
            tbl.delete(where)
            if VECTORS in self._names():
                self.vectors().delete(where)
        if rows:
            tbl.add(pa.Table.from_pylist(rows, schema=PASSAGE_SCHEMA))

    def delete_copies(self, pairs: list[tuple[str, str]], chunk: int = 500) -> int:
        """Delete the passages and vectors of specific (record_id, source_key) copies of records."""
        names = self._names()
        for i in range(0, len(pairs), chunk):
            part = pairs[i:i + chunk]
            # the record_id clause can use the scalar index; the concat clause makes the match exact
            where = (f"record_id IN ({', '.join(sql_str(r) for r in sorted({r for r, _ in part}))}) "
                     f"AND concat(record_id, '|', source_key) IN ({', '.join(sql_str(f'{r}|{k}') for r, k in part)})")
            for name in (PASSAGES, VECTORS):
                if name in names:
                    self.db.open_table(name).delete(where)
        return len(pairs)

    def add_vectors(self, keys: list[str], rows: list[dict[str, Any]], vectors: np.ndarray) -> None:
        tbl = self.vectors()
        if keys:
            tbl.delete(f"source_key IN ({', '.join(sql_str(k) for k in keys)})")
        if not rows:
            return
        cols = {name: [r[name] for r in rows] for name, _ in FILTER_FIELDS}
        cols["vector"] = list(vectors.astype(np.float32))
        tbl.add(pa.Table.from_pydict(cols, schema=vector_schema(self.dim)))

    def build_indices(self) -> None:
        tbl = self.passages()
        if tbl.count_rows() == 0:
            return
        tbl.create_index("search_text", config=FTS(with_position=False, stem=True, remove_stop_words=True),
                         replace=True)
        self.build_scalar_indices(tbl)
        self.ensure_vector_ids()
        if VECTORS in self._names():
            vt = self.vectors()
            n = vt.count_rows()
            if n >= 10_000:
                from lancedb.index import IvfPq

                vt.create_index("vector", config=IvfPq(distance_type="cosine",
                                                       num_sub_vectors=max(1, self.dim // 8)), replace=True)

    def ensure_vector_ids(self) -> bool:
        """An index on the vectors table's passage_id, so updating a few passages does not read every vector into
        memory. Returns True if it had to be built."""
        if VECTORS not in self._names():
            return False
        vt = self.vectors()
        if vt.count_rows() == 0 or any("passage_id" in (ix.columns or []) for ix in vt.list_indices()):
            return False
        vt.create_index("passage_id", config=BTree(), replace=True)
        return True

    def build_scalar_indices(self, tbl) -> None:
        """Indices that let filtered (prefilter) searches skip non-matching rows."""
        specs = [(c, BTree()) for c in ("passage_id", "source_key", "record_id", "country_code", "date")]
        specs += [(c, Bitmap()) for c in ("source", "body", "entity_kind", "speaker_role", "participant",
                                         "group_statement", "year")]
        for col, cfg in specs:
            try:
                tbl.create_index(col, config=cfg, replace=True)
            except Exception as exc:  # scalar indices are an optimisation only
                log.warning("scalar index on %s skipped: %s", col, exc)

    def unmerged(self) -> int:
        """Passages not yet in the keyword (BM25) index: added by an update that stopped before merging them."""
        if PASSAGES not in self._names():
            return 0
        tbl = self.passages()
        for ix in tbl.list_indices():
            if "search_text" in (ix.columns or []):
                st = tbl.index_stats(ix.name)
                return int(st.num_unindexed_rows) if st else 0
        return 0

    def optimize(self) -> None:
        """Merge new rows into the indices, compact small files and drop superseded versions."""
        for name in (PASSAGES, VECTORS):
            if name in self._names():
                self.db.open_table(name).optimize(cleanup_older_than=KEEP_OLD_VERSIONS)

    # ---- reads ----------------------------------------------------------------
    def bm25(self, query: str, where: str | None, limit: int) -> list[dict[str, Any]]:
        q = sanitise_query(query)
        if not q or PASSAGES not in self._names():
            return []
        search = self.passages().search(q, query_type="fts")
        if where:
            search = search.where(where, prefilter=True)
        return search.limit(limit).to_list()

    def vector(self, vec: np.ndarray, where: str | None, limit: int, nprobes: int | None = None,
               refine_factor: int | None = None, columns: list[str] | None = None,
               exact: bool = False) -> list[dict[str, Any]]:
        """Nearest passages by meaning. The index compares compressed vectors, which misses about a third of the
        truly nearest 300; ``refine_factor`` re-checks that many times ``limit`` candidates against the full vectors,
        finding nearly all of them for a few milliseconds more."""
        if VECTORS not in self._names() or self.vectors().count_rows() == 0:
            return []
        search = self.vectors().search(vec.astype(np.float32), vector_column_name="vector").distance_type("cosine")
        if exact:  # every vector compared in full: slow, for checking the index
            search = search.bypass_vector_index()
        else:
            if nprobes:
                search = search.nprobes(int(nprobes))
            if refine_factor:
                search = search.refine_factor(int(refine_factor))
        if where:
            search = search.where(where, prefilter=True)
        # every column but the vector itself: 300 vectors as Python floats cost more than the search
        search = search.select([*(columns or [name for name, _ in FILTER_FIELDS]), "_distance"])
        return search.limit(limit).to_list()

    def rebuild_vector_index(self, rows_per_partition: int = 8192) -> None:
        """Rebuild the meaning-search index with partitions sized for the vectors there are now.

        The index is first built when there are few vectors, and later additions join its existing partitions; with
        millions of vectors in a few dozen partitions, each search reads a third of them. Searches carry on with the
        old index while the new one is built."""
        from lancedb.index import IvfPq

        vt = self.vectors()
        vt.create_index("vector", config=IvfPq(distance_type="cosine", num_sub_vectors=max(1, self.dim // 8),
                                               target_partition_size=int(rows_per_partition)), replace=True)

    def passages_for(self, passage_ids: list[str]) -> dict[str, dict[str, Any]]:
        if not passage_ids:
            return {}
        rows = self.passages().search().where(
            f"passage_id IN ({', '.join(sql_str(p) for p in passage_ids)})").limit(None).to_list()
        return {r["passage_id"]: r for r in rows}

    def rows_for_keys(self, keys: list[str]) -> list[dict[str, Any]]:
        if not keys:
            return []
        # limit(None): an explicit huge limit makes LanceDB size buffers for it (GBs of RSS)
        where = f"source_key IN ({', '.join(sql_str(k) for k in keys)})"
        cols = [name for name, _ in FILTER_FIELDS] + ["heading", "search_text"]
        return self.passages().search().where(where).select(cols).limit(None).to_list()

    def stats(self) -> dict[str, Any]:
        out = {"passages": 0, "vectors": 0}
        if PASSAGES in self._names():
            out["passages"] = self.passages().count_rows()
        if VECTORS in self._names():
            out["vectors"] = self.vectors().count_rows()
        return out


def date_lit(d: date) -> str:
    return f"DATE '{d.isoformat()}'"
