"""Build the search index from the Parquet store.

Each speech is split into passages of about 250 words. Each passage carries its filter columns: body, date,
country, kind of speaker, role, Security Council participation, whether it is a group statement, and the groups the
country belonged to on the day. The keyword index is built straight away; embedding (meaning search) is a separate,
resumable step, newest years first.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from wcs.config import Settings
from wcs.embed.base import Embedder
from wcs.index.lance import SearchIndex
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


class State:
    """Which source-years are indexed and embedded (data/index/state.json)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.data: dict[str, dict[str, Any]] = json.loads(self.path.read_text()) if self.path.exists() else {}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1, sort_keys=True))
        tmp.replace(self.path)

    def indexed(self, key: str, passages: int) -> None:
        self.data[key] = {"passages": passages, "embedded": False, "indexed_at": time.time()}
        self.save()

    def embedded(self, key: str) -> None:
        self.data.setdefault(key, {})["embedded"] = True
        self.save()

    def unembedded(self) -> list[str]:
        """Source-years still to embed, newest first."""
        keys = [k for k, v in self.data.items() if not v.get("embedded") and v.get("passages")]
        return sorted(keys, key=lambda k: (-int(k.split(":")[1]), k))


class Indexer:
    def __init__(self, settings: Settings, countries: Countries, index: SearchIndex | None = None):
        self.settings = settings
        self.countries = countries
        self.index = index or SearchIndex(settings.index_dir, int(settings.embedding.get("dim", 384)))
        self.state = State(settings.index_dir / "state.json")

    def index_year(self, source: str, year: int) -> int:
        records = parquet.read_year(self.settings.parquet_dir, source, year)
        rows = passage_rows(records, self.countries, int(self.settings.embedding.get("passage_words", 250)))
        key = f"{source}:{year}"
        self.index.replace_keys([key], rows)
        self.state.indexed(key, len(rows))
        return len(rows)

    def index_all(self, sources: tuple[str, ...] = SOURCES, years: set[int] | None = None) -> int:
        total = 0
        for source in sources:
            for year in parquet.years(self.settings.parquet_dir, source):
                if years and year not in years:
                    continue
                n = self.index_year(source, year)
                log.info("indexed %s %d: %d passages", source, year, n)
                total += n
        self.index.build_indices()
        return total


def embed_pending(settings: Settings, index: SearchIndex, state: State, embedder: Embedder, *,
                  max_seconds: float | None = None, batch: int = 2000) -> int:
    """Embed the passages of indexed-but-unembedded source-years, newest first. Returns passages embedded."""
    started = time.time()
    total = 0
    for key in state.unembedded():
        rows = index.rows_for_keys([key])
        if not rows:
            state.embedded(key)
            continue
        for i in range(0, len(rows), batch):
            part = rows[i:i + batch]
            texts = [f"{r['heading']}: {r['search_text'].split(chr(10), 2)[-1]}" for r in part]
            vecs = embedder.embed_documents(texts)
            # the first batch replaces any older vectors for this source-year; later ones add to them
            index.add_vectors([key] if i == 0 else [], part, vecs)
            total += len(part)
            log.info("embedded %d passages (%s, %.1f/s)", total, key, total / max(time.time() - started, 1e-6))
        state.embedded(key)
        if max_seconds is not None and time.time() - started > max_seconds:
            break
    return total
