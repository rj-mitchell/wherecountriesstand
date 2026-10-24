"""Zstd-compressed Parquet storage: one file per source and year.

Speeches are stored at ``parquet/records/<source>/<year>.parquet`` and replaced whole when a source is reloaded
(the corpora are published as complete releases, not daily corrections). Votes are one file,
``parquet/votes.parquet``.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

# One row per speech. A statement made on behalf of a group keeps the speaker's country in ``country_code`` and
# names the group in ``on_behalf_of``: it is the group's statement, not the country's own (docs/DECISIONS.md, 7).
RECORD_SCHEMA = pa.schema([
    ("record_id", pa.string()),         # "sc:S/PV.7565:0004", "gd:78:GBR"
    ("source", pa.string()),            # "sc" (Security Council debates), "gd" (General Debate)
    ("body", pa.string()),              # "Security Council", "General Assembly"
    ("date", pa.date32()),
    ("date_precision", pa.string()),    # "day"; "session" for the General Debate, whose day is not recorded
    ("year", pa.int32()),
    ("session", pa.int32()),            # General Assembly session; null for the Security Council
    ("meeting", pa.string()),           # "7565th meeting", "78th session"
    ("symbol", pa.string()),            # document symbol of the record: "S/PV.7565"; null for the General Debate
    ("speech_number", pa.int32()),
    ("agenda", pa.string()),            # agenda item or topic as the record gives it
    ("agenda_area", pa.string()),       # broad area: "Africa", "Middle East", "Thematic"
    ("speaker_name", pa.string()),      # as printed: "Mr. Delattre"
    ("speaker_post", pa.string()),      # e.g. "Minister for Foreign Affairs", when the source gives it
    ("speaker_role", pa.string()),      # "head", "minister", "representative" or "" (see reference.roles)
    ("participant", pa.string()),       # Security Council: "president", "member", "guest"
    ("country_code", pa.string()),      # ISO 3166 alpha-3 (or a former state's code); null for organisations
    ("country_label", pa.string()),     # the label in the source: "France", "European Union"
    ("entity_kind", pa.string()),       # "member_state", "observer_state", "other_state", "organisation", "un"
    ("on_behalf_of", pa.string()),      # "Group of 77 and China", "European Union", "Security Council" ...
    ("language", pa.string()),          # language spoken, when not English: "French"
    ("ocr", pa.bool_()),                # text from a scanned document (may contain recognition errors)
    ("text", pa.string()),
    ("url", pa.string()),
])

VOTE_SCHEMA = pa.schema([
    ("undl_id", pa.string()),
    ("country_code", pa.string()),
    ("country_name", pa.string()),      # official name at the time of the vote, as recorded
    ("vote", pa.string()),              # Y, N, A (abstained), X (did not vote)
    ("date", pa.date32()),
    ("session", pa.int32()),
    ("resolution", pa.string()),
    ("draft", pa.string()),
    ("meeting", pa.string()),
    ("title", pa.string()),
    ("agenda_title", pa.string()),
    ("subjects", pa.string()),
    ("vote_note", pa.string()),
    ("total_yes", pa.int32()),
    ("total_no", pa.int32()),
    ("total_abstentions", pa.int32()),
    ("total_non_voting", pa.int32()),
    ("total_ms", pa.int32()),
    ("url", pa.string()),
])


def _atomic_write(table: pa.Table, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    pq.write_table(table, tmp, compression="zstd", row_group_size=20_000)
    os.replace(tmp, path)


def record_path(root: Path, source: str, year: int) -> Path:
    return Path(root) / "records" / source / f"{year}.parquet"


def write_year(root: Path, source: str, year: int, rows: Iterable[dict[str, Any]]) -> Path:
    """Replace one source's records for one year."""
    rows = sorted(rows, key=lambda r: (r["date"], r["record_id"]))
    path = record_path(root, source, year)
    _atomic_write(pa.Table.from_pylist(rows, schema=RECORD_SCHEMA), path)
    return path


def write_by_year(root: Path, source: str, rows: Iterable[dict[str, Any]]) -> dict[int, int]:
    """Group rows by year and replace each year's file; returns rows written per year."""
    by_year: dict[int, list[dict[str, Any]]] = {}
    for r in rows:
        by_year.setdefault(r["year"], []).append(r)
    for year, items in by_year.items():
        write_year(root, source, year, items)
    return {y: len(v) for y, v in sorted(by_year.items())}


def years(root: Path, source: str) -> list[int]:
    d = Path(root) / "records" / source
    return sorted(int(p.stem) for p in d.glob("*.parquet")) if d.exists() else []


def read_year(root: Path, source: str, year: int) -> list[dict[str, Any]]:
    path = record_path(root, source, year)
    return pq.read_table(path).to_pylist() if path.exists() else []


def read_records(root: Path, source: str, year: int, record_ids: set[str]) -> list[dict[str, Any]]:
    """The given records from one source and year."""
    path = record_path(root, source, year)
    if not path.exists() or not record_ids:
        return []
    table = pq.read_table(path, filters=[("record_id", "in", sorted(record_ids))])
    return table.to_pylist()


def votes_path(root: Path) -> Path:
    return Path(root) / "votes.parquet"


def write_votes(root: Path, rows: list[dict[str, Any]]) -> Path:
    path = votes_path(root)
    _atomic_write(pa.Table.from_pylist(rows, schema=VOTE_SCHEMA), path)
    return path


def attach_store(con: duckdb.DuckDBPyConnection, root: Path) -> list[str]:
    """Views over the stored Parquet: ``records`` (all speeches) and ``votes``. Returns the views created."""
    made = []
    files = sorted(str(p) for p in (Path(root) / "records").glob("*/*.parquet"))
    if files:
        con.execute(f"CREATE OR REPLACE VIEW records AS SELECT * FROM read_parquet({files!r}, union_by_name=true)")
        made.append("records")
    if votes_path(root).exists():
        con.execute(f"CREATE OR REPLACE VIEW votes AS SELECT * FROM read_parquet('{votes_path(root)}')")
        made.append("votes")
    return made
