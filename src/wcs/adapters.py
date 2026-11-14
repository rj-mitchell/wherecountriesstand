"""The outside tools this site is built on, each chosen here from its settings and reached through the Where They
Stand sites' shared package (wts-core, ``wts_core.adapters``): the embedding model that search by meaning uses, and
the AI model that reads questions and writes briefings. Everything else in the site takes what these give and
names no outside library, so changing one is a change to this file or to config/settings.toml.
"""

from __future__ import annotations

from collections.abc import Mapping

from wts_core import adapters
from wts_core.ports import Embedder, ModelClient

from wcs.config import SITE, Settings


def get_embedder(settings: Settings) -> Embedder:
    """The embedder ``[embedding]`` names: a small open-source model run on this computer (its weights downloaded
    once into data/models and pinned by their SHA-256; none of the record goes to, or comes from, Hugging Face), or
    the offline hashing embedder that tests use."""
    cfg = settings.embedding
    backend = cfg.get("backend", "fastembed")
    if backend == "hashing":
        from wts_core.adapters.hashing import HashingEmbedder

        return HashingEmbedder(int(cfg.get("dim", 256)))
    if backend == "fastembed":
        from wts_core.adapters.fastembed import FastEmbedEmbedder

        return FastEmbedEmbedder(cfg["model"], settings.models_dir, threads=cfg.get("threads"),
                                 batch_size=int(cfg.get("batch_size", 64)))
    raise ValueError(f"unknown embedding backend {backend!r}")


def has_model(settings: Settings, env: Mapping[str, str] | None = None) -> bool:
    """Whether there is a model to ask (an API key is set): without one the site shows the evidence only."""
    return adapters.has_model(settings, SITE, env)


def model_client(settings: Settings, env: Mapping[str, str] | None = None) -> ModelClient:
    """The model that reads questions and writes briefings. Made once and kept for the life of the process: it
    remembers what the provider refused."""
    return adapters.model_client(settings, SITE, env)
