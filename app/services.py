"""Shared service singletons: client reuse, embedding cache, index access.

All retrieval code should go through these instead of constructing
OpenAI / Pinecone clients per-request.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from functools import lru_cache
from pathlib import Path
from threading import Lock

from openai import OpenAI
from pinecone import Pinecone

from app.config import settings

logger = logging.getLogger(__name__)

# ── Client singletons ──────────────────────────────────────────────
#
# Two separate OpenAI-compatible clients:
#   get_openai()  → OpenAI API (for embeddings — must match Pinecone index)
#   get_llm()     → OpenRouter or OpenAI (for chat completions — swappable)

_openai_client: OpenAI | None = None
_openai_lock = Lock()

_llm_client: OpenAI | None = None
_llm_lock = Lock()

_pinecone_index = None
_pinecone_lock = Lock()


def get_openai() -> OpenAI:
    """Return a reusable OpenAI client for embeddings (always OpenAI API)."""
    global _openai_client
    if _openai_client is None:
        with _openai_lock:
            if _openai_client is None:
                _openai_client = OpenAI(api_key=settings.openai_api_key)
    return _openai_client


def get_llm() -> OpenAI:
    """Return a reusable client for LLM completions.

    Routes through OpenRouter when OPENROUTER_API_KEY is set,
    otherwise falls back to the standard OpenAI client.
    """
    global _llm_client
    if _llm_client is None:
        with _llm_lock:
            if _llm_client is None:
                if settings.openrouter_api_key:
                    _llm_client = OpenAI(
                        api_key=settings.openrouter_api_key,
                        base_url=settings.openrouter_base_url,
                    )
                    logger.info(
                        f"LLM client: OpenRouter ({settings.openrouter_base_url}), "
                        f"model={settings.llm_model}"
                    )
                else:
                    _llm_client = get_openai()
                    logger.info("LLM client: OpenAI (direct)")
    return _llm_client


def get_index():
    """Return a reusable Pinecone index handle."""
    global _pinecone_index
    if _pinecone_index is None:
        with _pinecone_lock:
            if _pinecone_index is None:
                pc = Pinecone(api_key=settings.pinecone_api_key)
                _pinecone_index = pc.Index(settings.pinecone_index)
    return _pinecone_index


# ── Embedding cache (thread-safe) ──────────────────────────────────

_embed_cache: dict[str, list[float]] = {}
_embed_lock = Lock()
_EMBED_CACHE_MAX = 512


def embed_text(text: str) -> list[float]:
    """Embed text with thread-safe LRU cache. Identical strings skip the API."""
    key = text.strip()
    with _embed_lock:
        if key in _embed_cache:
            return _embed_cache[key]

    client = get_openai()
    resp = client.embeddings.create(
        input=key,
        model=settings.embedding_model,
        dimensions=settings.embedding_dimensions,
    )
    vec = resp.data[0].embedding

    with _embed_lock:
        # Evict oldest if full (simple FIFO; good enough for demo)
        if len(_embed_cache) >= _EMBED_CACHE_MAX:
            oldest = next(iter(_embed_cache))
            del _embed_cache[oldest]
        _embed_cache[key] = vec
    return vec


# ── Answer cache (thread-safe) ─────────────────────────────────────

_answer_cache: dict[str, tuple[float, dict]] = {}   # key → (timestamp, response)
_answer_lock = Lock()
_ANSWER_TTL = 3600  # 1 hour
_ANSWER_CACHE_MAX = 256


def _answer_cache_key(query: str, context_hash: str, model: str) -> str:
    raw = f"{query}||{context_hash}||{model}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def get_cached_answer(query: str, context_hash: str, model: str) -> dict | None:
    key = _answer_cache_key(query, context_hash, model)
    with _answer_lock:
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
    with _answer_lock:
        if len(_answer_cache) >= _ANSWER_CACHE_MAX:
            oldest_key = next(iter(_answer_cache))
            del _answer_cache[oldest_key]
        _answer_cache[key] = (time.time(), response)


# ── Call graph singletons (shared single parse) ─────────────────────
#
# Previously call_graph.json was loaded and parsed TWICE: once as a raw
# dict (get_call_graph) and once as a CallGraph dataclass (get_call_graph_obj).
# Now we parse once and derive both from the same read.

_call_graph: dict | None = None
_call_graph_obj = None
_cg_lock = Lock()
_CG_CANDIDATES = [
    Path("data/call_graph.json"),
    Path(__file__).parent.parent / "data" / "call_graph.json",
    Path("/app/data/call_graph.json"),
]


def _load_call_graph_once() -> None:
    """Parse call_graph.json once, populate both raw dict and CallGraph obj."""
    global _call_graph, _call_graph_obj
    for p in _CG_CANDIDATES:
        if p.exists():
            data = json.loads(p.read_text())
            _call_graph = data
            from app.ingestion.call_graph import CallGraph
            _call_graph_obj = CallGraph(
                forward=data["forward"],
                reverse=data["reverse"],
                aliases=data.get("aliases", {}),
                routine_files=data.get("routine_files", {}),
            )
            return


def get_call_graph() -> dict | None:
    """Lazily load the call graph JSON dict once."""
    if _call_graph is not None:
        return _call_graph
    with _cg_lock:
        if _call_graph is not None:
            return _call_graph
        _load_call_graph_once()
    return _call_graph


def get_call_graph_obj():
    """Return a cached CallGraph dataclass instance (used by features)."""
    if _call_graph_obj is not None:
        return _call_graph_obj
    with _cg_lock:
        if _call_graph_obj is not None:
            return _call_graph_obj
        if _call_graph is None:
            _load_call_graph_once()
    return _call_graph_obj
