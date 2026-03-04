"""Shared routine lookup: call graph resolution + Pinecone chunk fetching.

Centralizes the pattern duplicated across explain.py, docgen.py, and metrics.py:
  1. Resolve routine name via call graph (alias, calls, callers, file_path)
  2. Fetch matching chunks from Pinecone by routine name
  3. Extract common metadata (start_line, end_line, patterns, text)
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.services import get_index, get_call_graph_obj, embed_text


@dataclass
class RoutineInfo:
    """Call graph resolution results for a routine."""
    name: str               # uppercased input name
    actual_name: str         # resolved name (parent if ENTRY alias)
    calls: list[str]         # forward dependencies
    callers: list[str]       # reverse dependencies (depth=1)
    file_path: str
    is_entry: bool
    has_graph: bool          # whether the call graph was available


@dataclass
class ChunkData:
    """A single chunk's extracted metadata and text."""
    text: str
    chunk_type: str
    file_path: str
    start_line: int | str
    end_line: int | str
    patterns: list[str]
    raw_metadata: dict


@dataclass
class RoutineChunks:
    """Fetched Pinecone chunks with extracted metadata for a routine."""
    chunks: list[ChunkData]
    context_text: str        # chunks joined as "--- {type} ---\n{text}" blocks
    file_path: str           # from first chunk (or call graph fallback)
    start_line: int | str
    end_line: int | str
    patterns: list[str]      # deduplicated, sorted


def resolve_routine(routine_name: str) -> RoutineInfo:
    """Resolve a routine name against the call graph.

    Handles ENTRY aliases, forward/reverse lookups, and file path resolution.
    Safe to call when the call graph is unavailable (returns defaults).
    """
    name = routine_name.upper()
    graph = get_call_graph_obj()

    if graph:
        actual_name = graph.aliases.get(name, name)
        calls = graph.forward.get(actual_name, [])
        callers = list(graph.callers_of(name, depth=1))
        file_path = graph.routine_files.get(
            actual_name, graph.routine_files.get(name, "unknown")
        )
        is_entry = name in graph.aliases
        return RoutineInfo(
            name=name,
            actual_name=actual_name,
            calls=calls,
            callers=callers,
            file_path=file_path,
            is_entry=is_entry,
            has_graph=True,
        )

    return RoutineInfo(
        name=name,
        actual_name=name,
        calls=[],
        callers=[],
        file_path="unknown",
        is_entry=False,
        has_graph=False,
    )


def _parse_patterns(raw_patterns) -> list[str]:
    """Normalize patterns from list or CSV string."""
    if isinstance(raw_patterns, list):
        return list(raw_patterns)
    if raw_patterns:
        return [p.strip() for p in str(raw_patterns).split(",") if p.strip()]
    return []


def fetch_routine_chunks(
    routine_info: RoutineInfo,
    embed_query: str,
    top_k: int = 5,
) -> RoutineChunks | None:
    """Fetch and assemble chunks from Pinecone for a routine.

    Args:
        routine_info: Resolved routine info from resolve_routine().
        embed_query: Text to embed for semantic search (e.g. "Explain routine X").
        top_k: Max chunks per search name.

    Returns:
        RoutineChunks with assembled data, or None if no chunks found.
    """
    index = get_index()
    query_vec = embed_text(embed_query)

    # Build search names: always include the input name, plus parent if aliased
    search_names = [routine_info.name]
    if routine_info.actual_name != routine_info.name:
        search_names.append(routine_info.actual_name)

    all_matches = []
    for search_name in search_names:
        results = index.query(
            vector=query_vec,
            top_k=top_k,
            filter={"routine_name": {"$eq": search_name}},
            include_metadata=True,
        )
        all_matches.extend(results.matches)

    if not all_matches:
        return None

    # Extract metadata from chunks
    chunks: list[ChunkData] = []
    context_parts: list[str] = []
    file_path = routine_info.file_path
    start_line: int | str = 0
    end_line: int | str = 0
    all_patterns: set[str] = set()

    for match in all_matches:
        meta = match.metadata or {}
        text = meta.get("text", "")
        chunk_type = meta.get("chunk_type", "")

        # Use first chunk's location as the primary reference
        if not start_line:
            start_line = meta.get("start_line", 0)
            end_line = meta.get("end_line", 0)
            file_path = meta.get("file_path", file_path)

        chunk_patterns = _parse_patterns(meta.get("patterns", []))
        all_patterns.update(chunk_patterns)

        chunks.append(ChunkData(
            text=text,
            chunk_type=chunk_type,
            file_path=meta.get("file_path", "unknown"),
            start_line=meta.get("start_line", 0),
            end_line=meta.get("end_line", 0),
            patterns=chunk_patterns,
            raw_metadata=meta,
        ))

        context_parts.append(f"--- {chunk_type} ---\n{text}")

    return RoutineChunks(
        chunks=chunks,
        context_text="\n\n".join(context_parts),
        file_path=file_path,
        start_line=start_line,
        end_line=end_line,
        patterns=sorted(all_patterns),
    )
