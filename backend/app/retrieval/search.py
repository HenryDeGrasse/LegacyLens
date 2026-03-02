"""Two-path retrieval with metadata filtering (Phase 2 — refined).

Improvements over Phase 1:
  - Reuses single embedding for both retrieval paths
  - Resolves ENTRY aliases to parent routines
  - Supports pattern-based filtering
  - Keyword-based boosting from C$ Keywords headers
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from openai import OpenAI
from pinecone import Pinecone

from app.config import settings


@dataclass
class RetrievedChunk:
    """A chunk retrieved from Pinecone with relevance score."""

    id: str
    text: str
    score: float
    metadata: dict


# Pattern to detect potential routine names in queries (uppercase identifiers)
_ROUTINE_NAME_RE = re.compile(r"\b([A-Z][A-Z0-9_]{2,})\b")

# Common English words that look like routine names but aren't
_STOP_WORDS = {
    "THE", "AND", "FOR", "THIS", "THAT", "WITH", "FROM", "WHAT", "DOES",
    "HOW", "WHY", "WHERE", "WHEN", "WHICH", "SHOW", "FIND", "ALL", "ARE",
    "NOT", "HAS", "HAVE", "BEEN", "WILL", "CAN", "USE", "USED", "USING",
    "INTO", "ABOUT", "LIKE", "BETWEEN", "EACH", "AFTER", "BEFORE",
    "COULD", "WOULD", "SHOULD", "SPICE", "FORTRAN", "CODE", "FILE",
    "FUNCTION", "SUBROUTINE", "ENTRY", "CALL", "PROGRAM", "MODULE",
    "EXPLAIN", "DESCRIBE", "LIST", "RETURN", "ERROR", "DATA",
}

# Pattern keywords mapping user queries to SPICE pattern metadata
_QUERY_PATTERN_MAP = {
    "error": "error_handling",
    "exception": "error_handling",
    "handling": "error_handling",
    "kernel": "kernel_loading",
    "load": "kernel_loading",
    "furnsh": "kernel_loading",
    "ephemer": "spk_operations",
    "position": "spk_operations",
    "velocity": "spk_operations",
    "state": "spk_operations",
    "frame": "frame_transforms",
    "transform": "frame_transforms",
    "rotation": "frame_transforms",
    "time": "time_conversion",
    "epoch": "time_conversion",
    "utc": "time_conversion",
    "geometry": "geometry",
    "intercept": "geometry",
    "sub-point": "geometry",
    "illumin": "geometry",
    "matrix": "matrix_vector",
    "vector": "matrix_vector",
    "cross product": "matrix_vector",
    "file": "file_io",
    "read": "file_io",
    "write": "file_io",
    "i/o": "file_io",
}

# Lazily loaded call graph for alias resolution
_call_graph_cache: dict | None = None


def _get_call_graph() -> dict | None:
    """Load the call graph for alias resolution."""
    global _call_graph_cache
    if _call_graph_cache is not None:
        return _call_graph_cache
    candidates = [
        Path("data/call_graph.json"),
        Path(__file__).parent.parent.parent / "data" / "call_graph.json",
        Path("/app/data/call_graph.json"),
    ]
    for cg_path in candidates:
        if cg_path.exists():
            _call_graph_cache = json.loads(cg_path.read_text())
            return _call_graph_cache
    return None


def _detect_routine_names(query: str) -> list[str]:
    """Extract potential routine names from a query, resolving ENTRY aliases."""
    candidates = _ROUTINE_NAME_RE.findall(query.upper())
    names = [c for c in candidates if c not in _STOP_WORDS and len(c) >= 3]

    # Resolve ENTRY aliases to parent routine names
    cg = _get_call_graph()
    if cg:
        aliases = cg.get("aliases", {})
        resolved = []
        for name in names:
            resolved.append(name)
            # If this is an ENTRY alias, also search for the parent
            if name in aliases:
                parent = aliases[name]
                if parent not in resolved:
                    resolved.append(parent)
        names = resolved

    return names


def _detect_query_patterns(query: str) -> list[str]:
    """Detect which SPICE patterns a query is asking about."""
    query_lower = query.lower()
    patterns = set()
    for keyword, pattern in _QUERY_PATTERN_MAP.items():
        if keyword in query_lower:
            patterns.add(pattern)
    return list(patterns)


def _get_index():
    """Get the Pinecone index."""
    pc = Pinecone(api_key=settings.pinecone_api_key)
    return pc.Index(settings.pinecone_index)


def _embed_query(query: str) -> list[float]:
    """Embed a query string."""
    client = OpenAI(api_key=settings.openai_api_key)
    response = client.embeddings.create(
        input=query,
        model=settings.embedding_model,
        dimensions=settings.embedding_dimensions,
    )
    return response.data[0].embedding


def _pinecone_results_to_chunks(results) -> list[RetrievedChunk]:
    """Convert Pinecone query results to RetrievedChunk objects."""
    chunks = []
    for match in results.matches:
        meta = match.metadata or {}
        chunks.append(RetrievedChunk(
            id=match.id,
            text=meta.get("text", ""),  # We'll need to handle this
            score=match.score,
            metadata=meta,
        ))
    return chunks


def retrieve(query: str, top_k: int = 10) -> list[RetrievedChunk]:
    """Two-path retrieval: exact routine name match + semantic search.

    Path 1: If routine names are detected in the query, filter by
             routine_name metadata and return doc + body chunks.
    Path 2: Embed the query and do semantic similarity search.

    Results are merged and deduplicated.
    """
    index = _get_index()
    seen_ids: set[str] = set()
    results: list[RetrievedChunk] = []

    # Embed query once — reuse for both paths
    query_vec = _embed_query(query)

    # Path 1: Exact routine name match (boosted score)
    routine_names = _detect_routine_names(query)
    for name in routine_names[:2]:
        try:
            path1_results = index.query(
                vector=query_vec,
                top_k=3,
                filter={"routine_name": {"$eq": name}},
                include_metadata=True,
            )
            for match in path1_results.matches:
                if match.id not in seen_ids:
                    seen_ids.add(match.id)
                    meta = match.metadata or {}
                    boosted_score = min(match.score + 0.5, 1.0)
                    results.append(RetrievedChunk(
                        id=match.id,
                        text="",
                        score=boosted_score,
                        metadata=meta,
                    ))
        except Exception as e:
            print(f"  Path 1 lookup for '{name}' failed: {e}")

    # Path 1b: Pattern-filtered search (when query matches known patterns)
    detected_patterns = _detect_query_patterns(query)
    if detected_patterns and not routine_names:
        # Only do pattern search if no specific routine was mentioned
        for pattern in detected_patterns[:1]:  # Limit to 1 pattern filter
            try:
                pattern_results = index.query(
                    vector=query_vec,
                    top_k=5,
                    filter={"patterns": {"$eq": pattern}},
                    include_metadata=True,
                )
                for match in pattern_results.matches:
                    if match.id not in seen_ids:
                        seen_ids.add(match.id)
                        meta = match.metadata or {}
                        # Slight boost for pattern-matched results
                        boosted_score = min(match.score + 0.1, 1.0)
                        results.append(RetrievedChunk(
                            id=match.id,
                            text="",
                            score=boosted_score,
                            metadata=meta,
                        ))
            except Exception as e:
                print(f"  Pattern filter for '{pattern}' failed: {e}")

    # Path 2: Semantic search (unfiltered fallback)
    path2_results = index.query(
        vector=query_vec,
        top_k=top_k,
        include_metadata=True,
    )
    for match in path2_results.matches:
        if match.id not in seen_ids:
            seen_ids.add(match.id)
            meta = match.metadata or {}
            results.append(RetrievedChunk(
                id=match.id,
                text="",
                score=match.score,
                metadata=meta,
            ))

    # Sort by score descending, take top results
    results.sort(key=lambda r: r.score, reverse=True)
    return results[:top_k]
