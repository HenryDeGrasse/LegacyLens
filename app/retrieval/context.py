"""Context assembly from retrieved chunks.

Improvements:
  - Uses tiktoken for accurate token counting (not char/4 estimate)
  - Uses chunk.text (populated by search.py) with metadata fallback
  - Handles patterns stored as list or comma-separated string
  - Groups by routine, orders doc-first within each group
  - Hard-stops at token limit to prevent LLM TTFT blowup
"""

from __future__ import annotations

import tiktoken

from app.config import settings
from app.retrieval.search import RetrievedChunk

# Cache the tokenizer — tiktoken encoding init is ~10ms first time
_enc: tiktoken.Encoding | None = None


def _get_enc() -> tiktoken.Encoding:
    global _enc
    if _enc is None:
        _enc = tiktoken.encoding_for_model("gpt-4o-mini")
    return _enc


def _count_tokens(text: str) -> int:
    return len(_get_enc().encode(text))


def _format_patterns(raw) -> str:
    """Normalise patterns from list or CSV string to display string."""
    if isinstance(raw, list):
        return ", ".join(raw)
    return str(raw) if raw else ""


def assemble_context(
    chunks: list[RetrievedChunk],
    max_tokens: int | None = None,
) -> str:
    """Assemble retrieved chunks into a context string for LLM.

    Prioritizes routine_doc chunks. If a doc and body from the same
    routine are both present, places them adjacent.

    Uses tiktoken for accurate token counting — ensures we stay under
    max_tokens to keep LLM TTFT low.

    Args:
        chunks: Retrieved chunks sorted by relevance.
        max_tokens: Maximum tokens for context (default: settings.context_max_tokens).

    Returns:
        Formatted context string with file:line citations.
    """
    if max_tokens is None:
        max_tokens = settings.context_max_tokens

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

    # Build context with accurate token counting
    context_parts: list[str] = []
    current_tokens = 0

    for routine_name in ordered_routines:
        group = by_routine[routine_name]

        # Sort: routine_doc first, then by score
        def _chunk_sort(c: RetrievedChunk) -> tuple:
            type_order = {"routine_doc": 0, "routine_body": 1, "routine_segment": 2, "include": 3}
            return (type_order.get(c.metadata.get("chunk_type", ""), 9), -c.score)

        group.sort(key=_chunk_sort)

        for chunk in group:
            text = chunk.text or chunk.metadata.get("text", "")
            if not text:
                continue

            meta = chunk.metadata
            file_path = meta.get("file_path", "unknown")
            start_line = meta.get("start_line", "?")
            end_line = meta.get("end_line", "?")
            chunk_type = meta.get("chunk_type", "unknown")
            routine = meta.get("routine_name", "unknown")

            called_by = meta.get("called_by", "")
            patterns = _format_patterns(meta.get("patterns", ""))
            entry_aliases = meta.get("entry_aliases", "")

            header_parts = [
                f"--- [{routine}] {chunk_type}",
                f"File: {file_path}",
                f"Lines: {start_line}-{end_line}",
            ]
            if called_by:
                header_parts.append(f"Called by: {called_by[:200]}")
            if entry_aliases:
                header_parts.append(f"Entry points: {entry_aliases}")
            if patterns:
                header_parts.append(f"Patterns: {patterns}")
            header_parts.append(f"Score: {chunk.score:.3f} ---")
            header = " | ".join(header_parts)

            block = f"{header}\n{text}\n"
            block_tokens = _count_tokens(block)

            # Hard stop: don't exceed token budget
            if current_tokens + block_tokens > max_tokens:
                # Try to fit a truncated version
                remaining_tokens = max_tokens - current_tokens
                if remaining_tokens > 50:
                    # Truncate text to fit
                    enc = _get_enc()
                    header_tokens = _count_tokens(header + "\n")
                    text_budget = remaining_tokens - header_tokens - 5  # room for "...\n"
                    if text_budget > 20:
                        text_token_ids = enc.encode(text)[:text_budget]
                        truncated_text = enc.decode(text_token_ids) + "..."
                        block = f"{header}\n{truncated_text}\n"
                        context_parts.append(block)
                        current_tokens = max_tokens  # we're at the limit
                break  # stop adding chunks

            context_parts.append(block)
            current_tokens += block_tokens

    return "\n".join(context_parts)
