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

The tables themselves, and the twenty or so methods every site's index shares (open or create, replace by key, add
vectors, build and merge the indexes, search by words and by meaning), are the Where They Stand sites' shared
adapter (wts-core, ``wts_core.adapters.lancedb.LanceIndex``). What is here is this site's own: its columns, which of
them are indexed for filtering, and the two things only this site does with an index (``group_rows`` and
``set_groups``, for a change to the reference data). The adapter also records what the index was built with (the
embedding model, by name and by the SHA-256 of its weights) beside it in ``wts_index.json``, and refuses to open it
for another model: vectors from two models are never mixed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pyarrow as pa
from wts_core.adapters.lancedb import IndexSchema, LanceIndex, ScalarColumns
from wts_core.ports import Embedder, IndexMeta

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

PASSAGE_FIELDS = [
    ("chunk", pa.int32()),
    ("speaker_name", pa.string()),
    ("country_name", pa.string()),
    ("on_behalf_of", pa.string()),
    ("symbol", pa.string()),
    ("heading", pa.string()),
    ("text", pa.string()),
    ("search_text", pa.string()),
]


SCHEMA = IndexSchema(filter_fields=FILTER_FIELDS, passage_fields=PASSAGE_FIELDS)
# Indexes that let filtered searches skip the rows that do not match: a B-tree for columns of many values, a bitmap
# for those of few.
SCALARS = ScalarColumns(btree=("passage_id", "source_key", "record_id", "country_code", "date"),
                        bitmap=("source", "body", "entity_kind", "speaker_role", "participant", "group_statement",
                                "year"))
# The meaning-search index (IVF-PQ) is built once there are this many vectors (below that, every vector is compared,
# which is quick enough), and trained again once there are this many times as many as it was trained on: vectors
# added later join its existing partitions, which grow, and searches slow down.
MIN_VECTOR_INDEX_ROWS = 10_000
VECTOR_INDEX_GROWTH = 1.2


def open_index(path: Path, embedder: Embedder | IndexMeta | None = None) -> LanceIndex:
    """The site's index in ``path``. With the embedder its vectors are made by, it can be written to, and the
    adapter raises ``wts_core.ports.IndexOutOfDate`` if it was built with another. With none it is opened as it
    stands: for search by words, counts and tidying; such an index adds no vectors."""
    return LanceIndex(Path(path), embedder, schema=SCHEMA, scalar_columns=SCALARS, passages=PASSAGES,
                      vectors=VECTORS, min_vector_index_rows=MIN_VECTOR_INDEX_ROWS,
                      vector_index_growth=VECTOR_INDEX_GROWTH)


def group_rows(index: LanceIndex, name: str) -> list[dict[str, Any]]:
    """What decides each passage's groups (``speaker_groups``), with the groups it now carries."""
    if name not in index.names():
        return []
    cols = ["passage_id", "country_code", "date", "source", "participant", "groups"]
    rows: list[dict[str, Any]] = index.db.open_table(name).search().select(cols).limit(None).to_list()
    return rows


def set_groups(index: LanceIndex, name: str, changes: list[dict[str, Any]], chunk: int = 20_000) -> None:
    """Rewrite the groups of the given passages only (``passage_id``, ``groups``), keeping everything else,
    vectors included."""
    schema = pa.schema([("passage_id", pa.string()), ("groups", pa.list_(pa.string()))])
    tbl = index.db.open_table(name)
    for i in range(0, len(changes), chunk):
        part = pa.Table.from_pylist(changes[i:i + chunk], schema=schema)
        tbl.merge_insert("passage_id").when_matched_update_all().execute(part)
