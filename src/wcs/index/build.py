"""Build the search index from the Parquet store.

Each speech is split into passages of about 250 words. Each passage carries its filter columns: body, date,
country, kind of speaker, role, Security Council participation, whether it is a group statement, and the groups the
country belonged to on the day. The keyword index is built straight away; embedding (meaning search) is a separate,
resumable step, newest years first.

Re-indexing a year keeps the vectors of passages whose text has not changed, so a new meeting costs the embedding of
its own passages, not the whole year's. After an update, new rows are merged into the existing search indices; they
are built from scratch only the first time, and the meaning-search index is trained again only once the vectors have
grown by a fifth since it was last trained (the shared adapter's rule, which was this site's:
``wts_core.adapters.lancedb.vector_index_due``).
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

import numpy as np
from wts_core.adapters.lancedb import LanceIndex, stored_meta
from wts_core.ports import Embedder

from wcs.adapters import get_embedder
from wcs.config import Settings
from wcs.index.lance import PASSAGES, VECTORS, group_rows, open_index, set_groups
from wcs.reference.countries import Countries
from wcs.store import parquet

log = logging.getLogger(__name__)

_SENTENCE = re.compile(r"(?<=[.!?])\s+")
SOURCES = ("sc", "gd")


def chunk_text(text: str, max_words: int = 250) -> list[str]:
    """Split on paragraph boundaries into passages of at most ~max_words words."""
    chunks: list[str] = []
    current: list[str] = []
    count = 0

    def flush():
        nonlocal current, count
        if current:
            chunks.append("\n\n".join(current))
        current, count = [], 0

    for para in [p for p in re.split(r"\n\s*\n", text) if p.strip()]:
        words = len(para.split())
        if words > max_words:
            flush()
            sentence_buf: list[str] = []
            n = 0
            for sent in _SENTENCE.split(para):
                w = len(sent.split())
                if n + w > max_words and sentence_buf:
                    chunks.append(" ".join(sentence_buf))
                    sentence_buf, n = [], 0
                sentence_buf.append(sent)
                n += w
            if sentence_buf:
                chunks.append(" ".join(sentence_buf))
            continue
        if count + words > max_words:
            flush()
        current.append(para)
        count += words
    flush()
    return chunks or [text]


def heading_of(r: dict[str, Any]) -> str:
    """"Security Council, 7565th meeting — Threats to international peace and security caused by terrorist acts"."""
    parts = [f"{r['body']}, {r['meeting']}" if r.get("meeting") else r["body"], r.get("agenda")]
    return " — ".join(p for p in parts if p)


def speaker_groups(r: dict[str, Any], countries: Countries) -> list[str]:
    groups = countries.groups_on(r.get("country_code"), r["date"])
    if r["source"] == "sc" and r.get("country_code"):
        if r.get("participant") in ("member", "president") and "p5" not in groups:
            groups.append("council_elected")
        elif r.get("participant") == "guest":
            groups.append("council_guest")
    return sorted(set(groups))


def passage_rows(records: list[dict[str, Any]], countries: Countries, max_words: int) -> list[dict[str, Any]]:
    out = []
    for r in records:
        if not (r.get("text") or "").strip():
            continue
        groups = speaker_groups(r, countries)
        heading = heading_of(r)
        name = countries.name_on(r["country_code"], r["date"]) if r.get("country_code") else r.get("country_label")
        who = " ".join(x for x in (name or "", f"on behalf of the {r['on_behalf_of']}" if r.get("on_behalf_of") else "")
                       if x)
        for i, chunk in enumerate(chunk_text(r["text"], max_words)):
            out.append({
                "passage_id": f"{r['record_id']}#{i}",
                "record_id": r["record_id"],
                "source": r["source"],
                "source_key": f"{r['source']}:{r['year']}",
                "body": r["body"],
                "date": r["date"],
                "year": r["year"],
                "country_code": r.get("country_code"),
                "entity_kind": r.get("entity_kind"),
                "speaker_role": r.get("speaker_role") or "",
                "participant": r.get("participant") or "",
                "group_statement": bool(r.get("on_behalf_of")),
                "groups": groups,
                "chunk": i,
                "speaker_name": r.get("speaker_name"),
                "country_name": name,
                "on_behalf_of": r.get("on_behalf_of"),
                "symbol": r.get("symbol"),
                "heading": heading,
                "text": chunk,
                # the agenda item and the country's name are searchable too ("what has Chile said about Haiti"); the
                # body and meeting are not, since they are in every passage and filters already cover them
                "search_text": f"{r.get('agenda') or ''}\n{who}\n{chunk}",
            })
    return out


def embedding_text(r: dict[str, Any]) -> str:
    """What a passage's vector is made from: its heading and its text (``search_text`` without the agenda item and
    speaker lines)."""
    return f"{r['heading']}: {r['search_text'].split(chr(10), 2)[-1]}"


INDICES = "_indices"  # the state's record of the search indices themselves, beside the source-years


class State:
    """Which source-years are indexed and embedded (data/index/state.json). The file's time is also what tells the
    answer cache that the data has changed."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.data: dict[str, dict[str, Any]] = json.loads(self.path.read_text()) if self.path.exists() else {}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1, sort_keys=True))
        tmp.replace(self.path)

    def indexed(self, key: str, passages: int, embedded: bool = False) -> None:
        self.data[key] = {"passages": passages, "embedded": embedded, "indexed_at": time.time()}
        self.save()

    def embedded(self, key: str) -> None:
        self.data.setdefault(key, {})["embedded"] = True
        self.save()

    def unembedded(self) -> list[str]:
        """Source-years still to embed, newest first."""
        keys = [k for k, v in self.data.items() if k != INDICES and not v.get("embedded") and v.get("passages")]
        return sorted(keys, key=lambda k: (-int(k.split(":")[1]), k))

    @property
    def vector_index_trained_on(self) -> int | None:
        """How many vectors the meaning-search index was trained on, as this file recorded it before the shared
        adapter kept the count (in ``vector_index.json``, beside the index). Read once, by ``refresh_indices``."""
        return self.data.get(INDICES, {}).get("vector_index_trained_on")


def open_for_writing(settings: Settings, embedder: Embedder | None = None) -> LanceIndex:
    """The index as the jobs that write to it open it: with the embedder its vectors are made by, so that vectors
    from two models are never mixed (the adapter raises ``wts_core.ports.IndexOutOfDate`` for an index built with
    another). On a computer where the embedding model cannot be loaded, the keyword index can still be built, as
    it always could: an index with no vectors yet is opened as it stands, and one with vectors is opened as what
    it says it was built with (the vectors a re-indexed year keeps are its own)."""
    if embedder is None:
        try:
            embedder = get_embedder(settings)
        except Exception as exc:
            index = open_index(settings.index_dir)
            if not index.stats()["vectors"]:
                log.warning("no embedding model (%s): the keyword index only", type(exc).__name__)
                return index
            built_with = stored_meta(settings.index_dir)
            if built_with is None:
                raise RuntimeError("the index has vectors and the embedding model could not be loaded, so it "
                                   f"cannot be said which model made them ({type(exc).__name__}: {exc})") from exc
            return open_index(settings.index_dir, built_with)
    return open_index(settings.index_dir, embedder)


def refresh_indices(index: LanceIndex, state: State, full: bool = False) -> str:
    """Bring the search indices up to date after passages or vectors were written, and drop superseded versions
    (the shared adapter's ``refresh``, which was this site's rule): new rows are merged into the existing indices;
    the keyword and filter indices are built from scratch only when asked (``full``) or when there are none yet;
    the meaning-search index is trained again once the vectors have grown by a fifth. Returns "built" or "merged".

    The adapter keeps the number of vectors the meaning-search index was trained on beside the index. This site
    used to keep it in state.json: the first time through, that count is handed over, so the index is not trained
    again for nothing."""
    if index.trained_on() is None and state.vector_index_trained_on:
        index.note_trained(int(state.vector_index_trained_on))
    return index.refresh(full)


class Indexer:
    def __init__(self, settings: Settings, countries: Countries, index: LanceIndex | None = None,
                 keep_vectors: bool = True, embedder: Embedder | None = None):
        self.settings = settings
        self.countries = countries
        self.keep_vectors = keep_vectors  # False: every passage is embedded again (a new embedding model)
        self.index = index or open_for_writing(settings, embedder)
        self.state = State(settings.index_dir / "state.json")

    def index_year(self, source: str, year: int) -> int:
        """Replace a source-year's passages. Passages whose text is unchanged keep their vectors (with their filter
        columns brought up to date); only new and changed ones are left for ``embed_pending``. (From Where Councils
        Stand, which keeps a whole council-year's vectors; here it is passage by passage, since a year of the
        Security Council changes with every meeting.)"""
        records = parquet.read_year(self.settings.parquet_dir, source, year)
        rows = passage_rows(records, self.countries, int(self.settings.embedding.get("passage_words", 250)))
        key = f"{source}:{year}"
        kept = self.unchanged_vectors(key, rows)
        self.index.replace([key], rows)
        keep = [r for r in rows if r["passage_id"] in kept]
        if keep:
            self.index.add_vectors([], keep, np.stack([kept[r["passage_id"]] for r in keep]))
        self.state.indexed(key, len(rows), embedded=bool(rows) and len(keep) == len(rows))
        return len(rows)

    def unchanged_vectors(self, key: str, rows: list[dict[str, Any]]) -> dict[str, np.ndarray]:
        """passage_id -> stored vector, for the passages in ``rows`` whose embedded text is as stored."""
        vectors = self.index.vectors_for_keys([key]) if self.keep_vectors else {}
        if not vectors:
            return {}
        stored = {r["passage_id"]: embedding_text(r) for r in self.index.rows_for_keys([key])}
        return {r["passage_id"]: vectors[r["passage_id"]] for r in rows
                if r["passage_id"] in vectors and stored.get(r["passage_id"]) == embedding_text(r)}

    def index_all(self, sources: tuple[str, ...] = SOURCES, years: set[int] | None = None) -> int:
        total = 0
        for source in sources:
            for year in parquet.years(self.settings.parquet_dir, source):
                if years and year not in years:
                    continue
                n = self.index_year(source, year)
                log.info("indexed %s %d: %d passages", source, year, n)
                total += n
        refresh_indices(self.index, self.state, full=True)
        return total


def regroup(settings: Settings, countries: Countries, index: LanceIndex | None = None) -> dict[str, int]:
    """Bring every passage's groups up to date with the reference data (data_static/memberships.csv and the rest),
    in place: nothing is re-read or re-embedded. Returns the passages changed in each table."""
    index = index or open_for_writing(settings)
    out = {}
    for name in (PASSAGES, VECTORS):
        changes = []
        for r in group_rows(index, name):
            new = speaker_groups(r, countries)
            if new != sorted(r["groups"] or []):
                changes.append({"passage_id": r["passage_id"], "groups": new})
        set_groups(index, name, changes)
        out[name] = len(changes)
    if any(out.values()):
        refresh_indices(index, State(settings.index_dir / "state.json"), full=True)
    return out


def embed_pending(settings: Settings, index: LanceIndex, state: State, embedder: Embedder, *,
                  max_seconds: float | None = None, batch: int = 2000) -> int:
    """Embed the passages still without a vector in indexed-but-unembedded source-years, newest first. Stops
    between batches once ``max_seconds`` have passed; the next run carries on where it stopped. Returns passages
    embedded."""
    started = time.time()
    total = 0
    for key in state.unembedded():
        done = index.embedded_ids([key])
        rows = [r for r in index.rows_for_keys([key]) if r["passage_id"] not in done]
        for i in range(0, len(rows), batch):
            if max_seconds is not None and time.time() - started > max_seconds:
                return total
            part = rows[i:i + batch]
            index.add_vectors([], part, embedder.embed_documents([embedding_text(r) for r in part]))
            total += len(part)
            log.info("embedded %d passages (%s, %.1f/s)", total, key, total / max(time.time() - started, 1e-6))
        state.embedded(key)
    return total
