"""Routed retrieval with type-aware scoring (Phase 3 refined).

Changes from Phase 2:
  - Query router dispatches to specialised retrieval strategies
  - Pattern filter uses $in on list metadata (was broken with $eq on CSV)
  - Doc chunks get a conditional boost when query intent prefers docs
  - Client singletons + embedding cache via app.services
  - RetrievedChunk.text is actually populated
  - Pinecone queries run in parallel via ThreadPoolExecutor
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from app.config import settings
from app.services import embed_text, get_index, get_call_graph
from app.retrieval.router import route_query, QueryIntent, RoutedQuery

logger = logging.getLogger(__name__)


@dataclass
class RetrievedChunk:
    """A chunk retrieved from Pinecone with relevance score."""
    id: str
    text: str
    score: float
    metadata: dict


# ── Score adjustments ───────────────────────────────────────────────

# When the query intent prefers documentation, boost doc chunks
_DOC_BOOST = 0.08
# Exact routine-name match boost
_NAME_BOOST = 0.5
# Pattern-filter match boost (mild — the filter itself is the main signal)
_PATTERN_BOOST = 0.05


def _match_to_chunk(match, boost: float = 0.0) -> RetrievedChunk:
    """Convert a Pinecone match to a RetrievedChunk with actual text."""
    meta = match.metadata or {}
    return RetrievedChunk(
        id=match.id,
        text=meta.get("text", ""),
        score=min(match.score + boost, 1.0),
        metadata=meta,
    )


def _apply_doc_preference(
    chunks: list[RetrievedChunk],
    prefer_doc: bool,
) -> list[RetrievedChunk]:
    """If the query prefers docs, boost routine_doc chunks and demote segments."""
    if not prefer_doc:
        return chunks
    for c in chunks:
        ct = c.metadata.get("chunk_type", "")
        if ct == "routine_doc":
            c.score = min(c.score + _DOC_BOOST, 1.0)
        elif ct == "routine_segment":
            # Mild penalty — segments are noisy for conceptual questions
            c.score = max(c.score - 0.03, 0.0)
    return chunks


# ── Retrieval strategies ────────────────────────────────────────────

def _retrieve_by_routine_name(
    query_vec: list[float],
    names: list[str],
    top_k: int,
) -> list[RetrievedChunk]:
    """Path 1: filtered by routine_name, boosted. Queries run in parallel."""
    index = get_index()
    cg = get_call_graph()
    seen: set[str] = set()
    results: list[RetrievedChunk] = []

    # Resolve ENTRY aliases → also search parent
    search_names = list(names)
    if cg:
        aliases = cg.get("aliases", {})
        for n in names:
            parent = aliases.get(n)
            if parent and parent not in search_names:
                search_names.append(parent)

    search_names = search_names[:3]  # cap to avoid excessive Pinecone calls

    def _query_one(name: str):
        return index.query(
            vector=query_vec,
            top_k=4,
            filter={"routine_name": {"$eq": name}},
            include_metadata=True,
        )

    # Parallel Pinecone queries (saves ~100-300ms when 2-3 names)
    if len(search_names) == 1:
        try:
            res = _query_one(search_names[0])
            for m in res.matches:
                if m.id not in seen:
                    seen.add(m.id)
                    results.append(_match_to_chunk(m, boost=_NAME_BOOST))
        except Exception as e:
            logger.warning(f"Name lookup for '{search_names[0]}' failed: {e}")
    else:
        with ThreadPoolExecutor(max_workers=len(search_names)) as pool:
            futures = {pool.submit(_query_one, name): name for name in search_names}
            for future in as_completed(futures):
                name = futures[future]
                try:
                    res = future.result()
                    for m in res.matches:
                        if m.id not in seen:
                            seen.add(m.id)
                            results.append(_match_to_chunk(m, boost=_NAME_BOOST))
                except Exception as e:
                    logger.warning(f"Name lookup for '{name}' failed: {e}")

    return results


def _retrieve_by_pattern(
    query_vec: list[float],
    patterns: list[str],
    top_k: int,
) -> list[RetrievedChunk]:
    """Path 1b: filtered by pattern (uses $in on list metadata)."""
    index = get_index()
    seen: set[str] = set()
    results: list[RetrievedChunk] = []

    for pattern in patterns[:2]:
        try:
            res = index.query(
                vector=query_vec,
                top_k=top_k,
                filter={"patterns": {"$in": [pattern]}},
                include_metadata=True,
            )
            for m in res.matches:
                if m.id not in seen:
                    seen.add(m.id)
                    results.append(_match_to_chunk(m, boost=_PATTERN_BOOST))
        except Exception as e:
            logger.warning(f"Pattern filter for '{pattern}' failed: {e}")

    return results


def _retrieve_semantic(
    query_vec: list[float],
    top_k: int,
) -> list[RetrievedChunk]:
    """Path 2: unfiltered semantic search."""
    index = get_index()
    res = index.query(
        vector=query_vec,
        top_k=top_k,
        include_metadata=True,
    )
    return [_match_to_chunk(m) for m in res.matches]


# ── Main entry point ────────────────────────────────────────────────

def retrieve(query: str, top_k: int = 10) -> list[RetrievedChunk]:
    """Route a query and execute the appropriate retrieval strategy.

    Returns deduplicated, score-sorted chunks.
    """
    routed = route_query(query)
    return retrieve_routed(routed, top_k=top_k)


def retrieve_routed(routed: RoutedQuery, top_k: int = 10) -> list[RetrievedChunk]:
    """Execute retrieval for an already-routed query.

    Pinecone queries run in parallel via ThreadPoolExecutor.
    """
    query_vec = embed_text(routed.original_query)
    seen: set[str] = set()
    results: list[RetrievedChunk] = []

    def _merge(new_chunks: list[RetrievedChunk]):
        for c in new_chunks:
            if c.id not in seen:
                seen.add(c.id)
                results.append(c)

    # ── Build task list per intent ───────────────────────────────

    tasks: list[tuple[str, tuple]] = []  # (label, (func, *args))

    if routed.intent == QueryIntent.DEPENDENCY:
        tasks.append(("name", (_retrieve_by_routine_name, query_vec, routed.routine_names, top_k)))
        tasks.append(("semantic", (_retrieve_semantic, query_vec, 5)))

    elif routed.intent == QueryIntent.IMPACT:
        tasks.append(("name", (_retrieve_by_routine_name, query_vec, routed.routine_names, top_k)))
        tasks.append(("semantic", (_retrieve_semantic, query_vec, 5)))

    elif routed.intent == QueryIntent.EXPLAIN:
        tasks.append(("name", (_retrieve_by_routine_name, query_vec, routed.routine_names, top_k)))
        if routed.patterns:
            tasks.append(("pattern", (_retrieve_by_pattern, query_vec, routed.patterns, 5)))
        tasks.append(("semantic", (_retrieve_semantic, query_vec, 5)))

    elif routed.intent == QueryIntent.PATTERN:
        if routed.patterns:
            tasks.append(("pattern", (_retrieve_by_pattern, query_vec, routed.patterns, top_k)))
        tasks.append(("semantic", (_retrieve_semantic, query_vec, top_k)))

    else:  # SEMANTIC
        tasks.append(("semantic", (_retrieve_semantic, query_vec, top_k)))

    # ── Execute all Pinecone queries in parallel ─────────────────

    if len(tasks) == 1:
        # Single query — no thread overhead
        _, (func, *args) = tasks[0]
        _merge(func(*args))
    else:
        with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
            futures = {}
            for label, (func, *args) in tasks:
                futures[pool.submit(func, *args)] = label
            for future in as_completed(futures):
                try:
                    _merge(future.result())
                except Exception as e:
                    logger.warning(f"Retrieval task '{futures[future]}' failed: {e}")

    # Apply doc-type preference boost
    results = _apply_doc_preference(results, routed.prefer_doc)

    # Sort by score descending, take top_k
    results.sort(key=lambda r: r.score, reverse=True)
    return results[:top_k]
