"""Two-path retrieval: exact routine name match + semantic vector search."""

from __future__ import annotations

import re
from dataclasses import dataclass

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


def _detect_routine_names(query: str) -> list[str]:
    """Extract potential routine names from a query."""
    candidates = _ROUTINE_NAME_RE.findall(query.upper())
    return [c for c in candidates if c not in _STOP_WORDS and len(c) >= 3]


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

    # Path 1: Exact routine name match
    routine_names = _detect_routine_names(query)
    for name in routine_names[:3]:  # Limit to 3 routine name lookups
        try:
            # Query with metadata filter for this routine name
            # We need a dummy vector for filtered queries, so embed a simple string
            query_vec = _embed_query(name)
            path1_results = index.query(
                vector=query_vec,
                top_k=5,
                filter={"routine_name": {"$eq": name}},
                include_metadata=True,
            )
            for match in path1_results.matches:
                if match.id not in seen_ids:
                    seen_ids.add(match.id)
                    meta = match.metadata or {}
                    results.append(RetrievedChunk(
                        id=match.id,
                        text="",  # Text stored in metadata
                        score=match.score,
                        metadata=meta,
                    ))
        except Exception as e:
            print(f"  Path 1 lookup for '{name}' failed: {e}")

    # Path 2: Semantic search
    query_vec = _embed_query(query)
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
