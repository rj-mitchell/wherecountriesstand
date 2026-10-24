"""Text embedders for semantic search.

* :class:`FastEmbedEmbedder` runs a small open-source model locally through
  ONNX Runtime (fastembed). Weights are downloaded once from Hugging Face into
  ``data/models`` and their SHA-256 is recorded, so the model in use is pinned
  and auditable. No UN data comes from Hugging Face.
* :class:`HashingEmbedder` is deterministic, dependency-free and offline. It
  captures word overlap only; it exists for tests and the offline sample demo.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Protocol

import numpy as np

# Retrieval instruction prepended to queries (not documents) for models trained with one.
QUERY_PREFIXES = {
    "BAAI/bge-small-en-v1.5": "Represent this sentence for searching relevant passages: ",
    "BAAI/bge-base-en-v1.5": "Represent this sentence for searching relevant passages: ",
    "snowflake/snowflake-arctic-embed-xs": "Represent this sentence for searching relevant passages: ",
    "snowflake/snowflake-arctic-embed-s": "Represent this sentence for searching relevant passages: ",
    "nomic-ai/nomic-embed-text-v1.5": "search_query: ",
}
DOC_PREFIXES = {"nomic-ai/nomic-embed-text-v1.5": "search_document: "}


class Embedder(Protocol):
    name: str
    dim: int
    # cosine of unrelated text (floor) and of a strong match (ceiling) for this model
    similarity_range: tuple[float, float]

    def embed_documents(self, texts: list[str]) -> np.ndarray: ...

    def embed_query(self, text: str) -> np.ndarray: ...


def _normalise(m: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (m / norms).astype(np.float32)


class HashingEmbedder:
    """Feature-hashed unigrams + bigrams. Offline stand-in for tests and demos."""

    _TOKEN = re.compile(r"[a-z0-9]+")

    def __init__(self, dim: int = 256):
        self.dim = dim
        self.name = f"hashing-{dim}"
        self.similarity_range = (0.0, 0.6)

    def _vec(self, text: str) -> np.ndarray:
        v = np.zeros(self.dim, dtype=np.float32)
        toks = self._TOKEN.findall(text.lower())
        for gram in toks + [f"{a} {b}" for a, b in zip(toks, toks[1:], strict=False)]:
            h = int.from_bytes(hashlib.blake2b(gram.encode(), digest_size=8).digest(), "little")
            v[h % self.dim] += 1.0 if (h >> 63) & 1 else -1.0
        return v

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        return _normalise(np.stack([self._vec(t) for t in texts]))

    def embed_query(self, text: str) -> np.ndarray:
        return self.embed_documents([text])[0]


class FastEmbedEmbedder:
    def __init__(self, model: str, cache_dir: Path, threads: int | None = None, batch_size: int = 64):
        import os

        # Once the model is on disk, never ask Hugging Face about it again: a slow or blocked network would
        # otherwise hold up the first search after every restart.
        if any(Path(cache_dir).rglob("*.onnx")):
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
        from fastembed import TextEmbedding  # optional dependency ("embed" extra)

        self.name = model
        self.batch_size = batch_size
        # retrieval-tuned sentence embedders score unrelated passages around 0.5
        self.similarity_range = (0.5, 0.85)
        self._model = TextEmbedding(model, cache_dir=str(cache_dir), threads=threads)
        self.dim = len(next(iter(self._model.embed(["dimension probe"]))))
        self._qp = QUERY_PREFIXES.get(model, "")
        self._dp = DOC_PREFIXES.get(model, "")

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        vecs = list(self._model.embed([self._dp + t for t in texts], batch_size=self.batch_size))
        return _normalise(np.stack(vecs))

    def embed_query(self, text: str) -> np.ndarray:
        return _normalise(np.stack(list(self._model.embed([self._qp + text]))))[0]


def model_fingerprint(cache_dir: Path) -> str:
    """SHA-256 over the downloaded ONNX weights, recorded to pin the model in use."""
    h = hashlib.sha256()
    for p in sorted(Path(cache_dir).rglob("*.onnx")):
        h.update(p.name.encode())
        with open(p, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    return h.hexdigest()


def get_embedder(settings) -> Embedder:
    cfg = settings.embedding
    backend = cfg.get("backend", "fastembed")
    if backend == "hashing":
        return HashingEmbedder(int(cfg.get("dim", 256)))
    if backend == "fastembed":
        return FastEmbedEmbedder(cfg["model"], settings.data_dir / "models",
                                 threads=cfg.get("threads"), batch_size=int(cfg.get("batch_size", 64)))
    raise ValueError(f"unknown embedding backend {backend!r}")
