"""Shared service singletons: client reuse, embedding cache, index access.

All retrieval code should go through these instead of constructing
OpenAI / Pinecone clients per-request.
"""

from __future__ import annotations

import hashlib
import json
import time
from functools import lru_cache
from pathlib import Path
from threading import Lock

from openai import OpenAI
from pinecone import Pinecone

from app.config import settings

# ── Client singletons ──────────────────────────────────────────────

_openai_client: OpenAI | None = None
_openai_lock = Lock()

_pinecone_index = None
_pinecone_lock = Lock()


def get_openai() -> OpenAI:
    """Return a reusable OpenAI client (connection-pooled by httpx)."""
    global _openai_client
    if _openai_client is None:
        with _openai_lock:
            if _openai_client is None:
                _openai_client = OpenAI(api_key=settings.openai_api_key)
    return _openai_client


def get_index():
    """Return a reusable Pinecone index handle."""
    global _pinecone_index
    if _pinecone_index is None:
        with _pinecone_lock:
            if _pinecone_index is None:
                pc = Pinecone(api_key=settings.pinecone_api_key)
                _pinecone_index = pc.Index(settings.pinecone_index)
    return _pinecone_index


# ── Embedding cache ─────────────────────────────────────────────────

_embed_cache: dict[str, list[float]] = {}
_EMBED_CACHE_MAX = 512


def embed_text(text: str) -> list[float]:
    """Embed text with LRU cache. Identical strings skip the API."""
    key = text.strip()
    if key in _embed_cache:
        return _embed_cache[key]

    client = get_openai()
    resp = client.embeddings.create(
        input=key,
        model=settings.embedding_model,
        dimensions=settings.embedding_dimensions,
    )
    vec = resp.data[0].embedding

    # Evict oldest if full (simple FIFO; good enough for demo)
    if len(_embed_cache) >= _EMBED_CACHE_MAX:
        oldest = next(iter(_embed_cache))
        del _embed_cache[oldest]
    _embed_cache[key] = vec
    return vec


# ── Answer cache ────────────────────────────────────────────────────

_answer_cache: dict[str, tuple[float, dict]] = {}   # key → (timestamp, response)
_ANSWER_TTL = 3600  # 1 hour
_ANSWER_CACHE_MAX = 256


def _answer_cache_key(query: str, context_hash: str, model: str) -> str:
    raw = f"{query}||{context_hash}||{model}"
    return hashlib.md5(raw.encode()).hexdigest()


def get_cached_answer(query: str, context_hash: str, model: str) -> dict | None:
    key = _answer_cache_key(query, context_hash, model)
    entry = _answer_cache.get(key)
    if entry is None:
        return None
    ts, resp = entry
    if time.time() - ts > _ANSWER_TTL:
        del _answer_cache[key]
        return None
    return resp


def set_cached_answer(query: str, context_hash: str, model: str, response: dict):
    key = _answer_cache_key(query, context_hash, model)
    if len(_answer_cache) >= _ANSWER_CACHE_MAX:
        oldest_key = next(iter(_answer_cache))
        del _answer_cache[oldest_key]
    _answer_cache[key] = (time.time(), response)


# ── Call graph singleton ────────────────────────────────────────────

_call_graph: dict | None = None
_cg_lock = Lock()


def get_call_graph() -> dict | None:
    """Lazily load the call graph JSON once."""
    global _call_graph
    if _call_graph is not None:
        return _call_graph
    with _cg_lock:
        if _call_graph is not None:
            return _call_graph
        candidates = [
            Path("data/call_graph.json"),
            Path(__file__).parent.parent / "data" / "call_graph.json",
            Path("/app/data/call_graph.json"),
        ]
        for p in candidates:
            if p.exists():
                _call_graph = json.loads(p.read_text())
                return _call_graph
    return None
