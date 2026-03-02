"""Context assembly from retrieved chunks."""

from __future__ import annotations

from app.config import settings
from app.retrieval.search import RetrievedChunk


def assemble_context(
    chunks: list[RetrievedChunk],
    max_tokens: int | None = None,
) -> str:
    """Assemble retrieved chunks into a context string for LLM.

    Prioritizes routine_doc chunks. If a doc and body from the same
    routine are both present, places them adjacent.

    Args:
        chunks: Retrieved chunks sorted by relevance.
        max_tokens: Maximum estimated tokens for context.

    Returns:
        Formatted context string with file:line citations.
    """
    if max_tokens is None:
        max_tokens = settings.context_max_tokens

    max_chars = max_tokens * 4  # ~4 chars per token

    # Group chunks by routine name
    by_routine: dict[str, list[RetrievedChunk]] = {}
    for chunk in chunks:
        name = chunk.metadata.get("routine_name", "unknown")
        if name not in by_routine:
            by_routine[name] = []
        by_routine[name].append(chunk)

    # Order routines: prioritize those with routine_doc chunks
    def _routine_sort_key(name: str) -> tuple:
        group = by_routine[name]
        has_doc = any(c.metadata.get("chunk_type") == "routine_doc" for c in group)
        best_score = max(c.score for c in group)
        return (not has_doc, -best_score)

    ordered_routines = sorted(by_routine.keys(), key=_routine_sort_key)

    # Build context
    context_parts: list[str] = []
    current_chars = 0

    for routine_name in ordered_routines:
        group = by_routine[routine_name]

        # Sort: routine_doc first, then by score
        def _chunk_sort(c: RetrievedChunk) -> tuple:
            type_order = {"routine_doc": 0, "routine_body": 1, "routine_segment": 2, "include": 3}
            return (type_order.get(c.metadata.get("chunk_type", ""), 9), -c.score)

        group.sort(key=_chunk_sort)

        for chunk in group:
            text = chunk.metadata.get("text", "")
            if not text:
                continue

            file_path = chunk.metadata.get("file_path", "unknown")
            start_line = chunk.metadata.get("start_line", "?")
            end_line = chunk.metadata.get("end_line", "?")
            chunk_type = chunk.metadata.get("chunk_type", "unknown")
            routine = chunk.metadata.get("routine_name", "unknown")

            header = (
                f"--- [{routine}] {chunk_type} | "
                f"File: {file_path} | Lines: {start_line}-{end_line} | "
                f"Score: {chunk.score:.3f} ---"
            )

            block = f"{header}\n{text}\n"
            block_chars = len(block)

            if current_chars + block_chars > max_chars:
                # Truncate this block to fit
                remaining = max_chars - current_chars
                if remaining > 200:  # Only include if meaningful
                    block = f"{header}\n{text[:remaining - len(header) - 20]}...\n"
                    context_parts.append(block)
                break

            context_parts.append(block)
            current_chars += block_chars

    return "\n".join(context_parts)
