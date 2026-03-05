"""Routed retrieval with type-aware scoring and query expansion.

Key design:
  - Query expansion enriches the user's natural language before embedding,
    so vector search finds relevant Fortran chunks even for vague phrasing.
  - Query router dispatches to specialised retrieval strategies
  - Pattern filter uses $in on list metadata
  - Doc chunks get a conditional boost when query intent prefers docs
  - Client singletons + embedding cache via app.services
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


# ── Query expansion ─────────────────────────────────────────────────
#
# Natural language queries like "How does the spacecraft track its position?"
# produce weak embeddings against Fortran code. We enrich the query with
# domain-specific terms derived from the router's output (intent, patterns,
# routine names) so the vector space alignment improves — without an LLM call.

_PATTERN_EXPANSION: dict[str, str] = {
    "error_handling": "SPICE error handling: CHKIN CHKOUT SIGERR SETMSG error checking",
    "kernel_loading": "SPICE kernel management: FURNSH UNLOAD KCLEAR LDPOOL kernel loading",
    "spk_operations": "SPICE ephemeris: SPKEZ SPKEZR SPKPOS spacecraft position velocity state vector",
    "frame_transforms": "SPICE reference frames: PXFORM SXFORM FRMCHG coordinate transformation rotation",
    "time_conversion": "SPICE time: STR2ET ET2UTC TIMOUT epoch UTC time conversion",
    "geometry": "SPICE geometry: SUBPNT SINCPT ILLUMF surface intercept sub-observer point",
    "matrix_vector": "SPICE math: MXV VCRSS VNORM VDOT matrix vector rotation quaternion",
    "file_io": "SPICE file I/O: DAFOPR DAFCLS TXTOPN DAF read write",
}

_INTENT_EXPANSION: dict[QueryIntent, str] = {
    QueryIntent.DEPENDENCY: "call graph dependencies callers callees subroutine",
    QueryIntent.IMPACT: "impact analysis affected routines blast radius callers",
    QueryIntent.EXPLAIN: "explanation purpose parameters algorithm usage",
}

_BASE_EXPANSION = "NASA SPICE Toolkit Fortran subroutine"


def _expand_query(routed: RoutedQuery) -> str:
    """Enrich the user query with domain context for better embedding.

    Appends relevant terms based on detected intent, patterns, and
    routine names. No LLM call — purely deterministic from router output.

    Examples:
      "How does the spacecraft track its position?"
      → "How does the spacecraft track its position? — NASA SPICE Toolkit
         Fortran subroutine. SPICE ephemeris: SPKEZ SPKEZR SPKPOS
         spacecraft position velocity state vector"

      "SPKEZ"
      → "SPKEZ — NASA SPICE Toolkit Fortran subroutine. Explain the
         SPICE routine SPKEZ. explanation purpose parameters algorithm usage"
    """
    parts = [routed.original_query, "—", _BASE_EXPANSION]

    # Add routine name context for bare/short queries
    if routed.routine_names:
        names = " ".join(routed.routine_names[:3])
        if routed.intent == QueryIntent.EXPLAIN:
            parts.append(f"Explain the SPICE routine {names}")
        elif routed.intent == QueryIntent.DEPENDENCY:
            parts.append(f"Dependencies and call graph for {names}")
        elif routed.intent == QueryIntent.IMPACT:
            parts.append(f"Impact and callers of {names}")

    # Add pattern domain terms
    for pattern in routed.patterns:
        expansion = _PATTERN_EXPANSION.get(pattern)
        if expansion:
            parts.append(expansion)

    # Add intent-specific terms
    intent_exp = _INTENT_EXPANSION.get(routed.intent)
    if intent_exp:
        parts.append(intent_exp)

    return " ".join(parts)


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

    Hybrid search: runs Pinecone vector + BM25 keyword in parallel,
    merges via Reciprocal Rank Fusion (RRF) for better recall.
    """
    by_id: dict[str, RetrievedChunk] = {}

    def _merge(new_chunks: list[RetrievedChunk]):
        for c in new_chunks:
            existing = by_id.get(c.id)
            if existing is None or c.score > existing.score:
                by_id[c.id] = c

    # ── Short-circuit for out-of-scope queries ─────────────────

    if routed.intent == QueryIntent.OUT_OF_SCOPE:
        return []  # no retrieval needed

    # Expand the query with domain context, then embed.
    # This is the key to robust retrieval: natural language queries get
    # enriched with SPICE-specific terms so the embedding aligns with
    # Fortran code chunks in vector space.
    expanded = _expand_query(routed)
    query_vec = embed_text(expanded)

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

    # ── Execute vector + BM25 in parallel ────────────────────────

    from app.retrieval.bm25_index import bm25_search, reciprocal_rank_fusion

    if len(tasks) == 1:
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

    # Use the best-scoring chunk per ID after parallel retrieval merges.
    results: list[RetrievedChunk] = list(by_id.values())

    # ── BM25 re-ranking via RRF ──────────────────────────────────
    #
    # Run BM25 keyword search and merge with vector results using
    # Reciprocal Rank Fusion. This improves recall for exact keyword
    # queries (e.g. bare routine names) where vector search is weaker.

    fused_rank: dict[str, int] | None = None
    try:
        bm25_hits = bm25_search(routed.original_query, top_k=20)
        if bm25_hits:
            # Vector ranking: routine names ordered by best score
            vector_names = []
            for r in sorted(results, key=lambda x: -x.score):
                name = r.metadata.get("routine_name", "")
                if name and name not in vector_names:
                    vector_names.append(name)

            bm25_names = [h.routine_name for h in bm25_hits]

            # RRF merge
            fused_order = reciprocal_rank_fusion(vector_names, bm25_names)
            fused_rank = {name: i for i, name in enumerate(fused_order)}
    except Exception as e:
        logger.warning(f"BM25 re-ranking failed (falling back to vector-only): {e}")

    # Apply doc-type preference boost
    results = _apply_doc_preference(results, routed.prefer_doc)

    # Final ranking:
    # - If BM25 fused ranks exist, keep fused order primary and score secondary.
    # - Otherwise fall back to pure score sort.
    if fused_rank:
        results.sort(key=lambda r: (
            fused_rank.get(r.metadata.get("routine_name", ""), 9999),
            -r.score,
        ))
    else:
        results.sort(key=lambda r: r.score, reverse=True)

    return results[:top_k]
