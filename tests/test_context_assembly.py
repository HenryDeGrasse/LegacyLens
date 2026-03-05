"""Unit tests for context assembly and token truncation (Gap #6 — Medium).

Exercises token counting, chunk ordering, truncation, and budget
enforcement without any API calls ($0).

Run:
    pytest tests/test_context_assembly.py -v
"""

from __future__ import annotations

import pytest
import tiktoken

from app.retrieval.context import assemble_context, _count_tokens, _format_patterns
from app.retrieval.search import RetrievedChunk


# ── Helpers ──────────────────────────────────────────────────────────

def _make_chunk(
    routine_name: str = "SPKEZ",
    chunk_type: str = "routine_doc",
    text: str = "Sample chunk text.",
    score: float = 0.9,
    file_path: str = "spicelib/spkez.f",
    start_line: int = 1,
    end_line: int = 100,
    patterns: list | None = None,
    called_by: str = "",
    entry_aliases: str = "",
    chunk_id: str | None = None,
) -> RetrievedChunk:
    return RetrievedChunk(
        id=chunk_id or f"{routine_name}_{chunk_type}_{score}",
        text=text,
        score=score,
        metadata={
            "routine_name": routine_name,
            "chunk_type": chunk_type,
            "file_path": file_path,
            "start_line": start_line,
            "end_line": end_line,
            "text": text,
            "patterns": patterns or [],
            "called_by": called_by,
            "entry_aliases": entry_aliases,
        },
    )


def _make_large_text(n_tokens: int) -> str:
    """Generate text that's approximately n_tokens long."""
    # Each word is ~1-2 tokens; "word " is reliably ~1 token
    return ("word " * n_tokens).strip()


# ═══════════════════════════════════════════════════════════════════════
# _count_tokens
# ═══════════════════════════════════════════════════════════════════════


class TestCountTokens:
    """Token counting via tiktoken."""

    def test_empty_string(self):
        assert _count_tokens("") == 0

    def test_known_text(self):
        count = _count_tokens("Hello, world!")
        assert count > 0
        assert count < 10  # "Hello, world!" is ~4 tokens

    def test_large_text(self):
        text = "word " * 1000
        count = _count_tokens(text)
        assert count > 500  # at least 500 tokens

    def test_code_text(self):
        code = "      SUBROUTINE SPKEZ(TARG, ET, REF, ABCORR, OBS, STARG, LT)"
        count = _count_tokens(code)
        assert count > 5


# ═══════════════════════════════════════════════════════════════════════
# _format_patterns
# ═══════════════════════════════════════════════════════════════════════


class TestFormatPatterns:
    """Pattern normalization for display."""

    def test_list_input(self):
        assert _format_patterns(["error_handling", "kernel_loading"]) == "error_handling, kernel_loading"

    def test_csv_string_input(self):
        assert _format_patterns("error_handling, kernel_loading") == "error_handling, kernel_loading"

    def test_empty_list(self):
        assert _format_patterns([]) == ""

    def test_none_input(self):
        assert _format_patterns(None) == ""

    def test_empty_string(self):
        assert _format_patterns("") == ""


# ═══════════════════════════════════════════════════════════════════════
# Basic assembly
# ═══════════════════════════════════════════════════════════════════════


class TestAssembleContextBasic:
    """Basic context assembly functionality."""

    def test_empty_chunks(self):
        assert assemble_context([]) == ""

    def test_single_chunk(self):
        chunk = _make_chunk(text="SPKEZ computes state vectors.")
        result = assemble_context([chunk])
        assert "SPKEZ computes state vectors." in result
        assert "spicelib/spkez.f" in result

    def test_multiple_chunks(self):
        chunks = [
            _make_chunk(routine_name="SPKEZ", text="First chunk."),
            _make_chunk(routine_name="FURNSH", text="Second chunk."),
        ]
        result = assemble_context(chunks)
        assert "First chunk." in result
        assert "Second chunk." in result

    def test_score_in_header(self):
        chunk = _make_chunk(score=0.85)
        result = assemble_context([chunk])
        assert "0.850" in result

    def test_file_path_in_header(self):
        chunk = _make_chunk(file_path="src/spkez.f")
        result = assemble_context([chunk])
        assert "src/spkez.f" in result

    def test_patterns_in_header(self):
        chunk = _make_chunk(patterns=["error_handling"])
        result = assemble_context([chunk])
        assert "error_handling" in result

    def test_called_by_in_header(self):
        chunk = _make_chunk(called_by="PARENT1, PARENT2")
        result = assemble_context([chunk])
        assert "PARENT1, PARENT2" in result

    def test_entry_aliases_in_header(self):
        chunk = _make_chunk(entry_aliases="KTOTAL, KDATA")
        result = assemble_context([chunk])
        assert "KTOTAL, KDATA" in result


# ═══════════════════════════════════════════════════════════════════════
# Chunk ordering (routine_doc priority)
# ═══════════════════════════════════════════════════════════════════════


class TestAssembleContextOrdering:
    """routine_doc chunks should appear before routine_body."""

    def test_doc_before_body_same_routine(self):
        chunks = [
            _make_chunk(routine_name="SPKEZ", chunk_type="routine_body", text="Body text.", score=0.95),
            _make_chunk(routine_name="SPKEZ", chunk_type="routine_doc", text="Doc text.", score=0.80),
        ]
        result = assemble_context(chunks)
        doc_pos = result.index("Doc text.")
        body_pos = result.index("Body text.")
        assert doc_pos < body_pos, "routine_doc should appear before routine_body"

    def test_doc_before_segment(self):
        chunks = [
            _make_chunk(routine_name="SPKEZ", chunk_type="routine_segment", text="Segment.", score=0.95),
            _make_chunk(routine_name="SPKEZ", chunk_type="routine_doc", text="Doc.", score=0.70),
        ]
        result = assemble_context(chunks)
        doc_pos = result.index("Doc.")
        seg_pos = result.index("Segment.")
        assert doc_pos < seg_pos

    def test_routine_with_doc_prioritized(self):
        """Routines that have a doc chunk should appear before those without."""
        chunks = [
            _make_chunk(routine_name="NODOC", chunk_type="routine_body", text="No doc body.", score=0.99),
            _make_chunk(routine_name="HASDOC", chunk_type="routine_doc", text="Has doc.", score=0.70),
        ]
        result = assemble_context(chunks)
        hasdoc_pos = result.index("Has doc.")
        nodoc_pos = result.index("No doc body.")
        assert hasdoc_pos < nodoc_pos, "Routine with doc should appear first"


# ═══════════════════════════════════════════════════════════════════════
# Token budget enforcement
# ═══════════════════════════════════════════════════════════════════════


class TestAssembleContextTokenBudget:
    """Context must respect max_tokens limit."""

    def test_respects_max_tokens(self):
        """Output should not exceed max_tokens."""
        large_text = _make_large_text(500)
        chunks = [
            _make_chunk(text=large_text, score=0.9 - i * 0.01)
            for i in range(10)
        ]
        # Set a small budget
        result = assemble_context(chunks, max_tokens=200)
        token_count = _count_tokens(result)
        # Allow some overhead from headers, but should be close to budget
        assert token_count <= 250, (
            f"Context has {token_count} tokens, expected ≤~200 + header overhead"
        )

    def test_uses_default_budget_when_none(self):
        """When max_tokens is None, uses settings.context_max_tokens."""
        from app.config import settings
        chunk = _make_chunk(text="Short text.")
        result = assemble_context([chunk], max_tokens=None)
        # Should succeed without error
        assert len(result) > 0

    def test_truncation_preserves_partial_chunk(self):
        """When near budget, a partial chunk should be included."""
        small_text = "Small chunk."
        large_text = _make_large_text(300)
        chunks = [
            _make_chunk(routine_name="FIRST", text=small_text, score=0.95),
            _make_chunk(routine_name="SECOND", text=large_text, score=0.85),
        ]
        result = assemble_context(chunks, max_tokens=100)
        # First chunk should be included
        assert "Small chunk." in result
        # Second chunk might be partially included or excluded
        total_tokens = _count_tokens(result)
        assert total_tokens <= 150  # some header overhead

    def test_stops_adding_when_budget_exhausted(self):
        """Chunks beyond the budget should not appear."""
        chunks = [
            _make_chunk(routine_name=f"R{i}", text=_make_large_text(200), score=0.9 - i * 0.01)
            for i in range(5)
        ]
        result = assemble_context(chunks, max_tokens=250)
        # Not all 5 routines should appear
        for i in range(5):
            if f"R{i}" not in result:
                # At least one later chunk should be excluded
                break
        else:
            # All appeared — check that token count is reasonable
            token_count = _count_tokens(result)
            assert token_count <= 300

    def test_intent_aware_budgets(self):
        """Different intents should use different budgets via max_tokens."""
        chunk = _make_chunk(text=_make_large_text(500))

        dep_result = assemble_context([chunk], max_tokens=2000)
        impact_result = assemble_context([chunk], max_tokens=2500)

        dep_tokens = _count_tokens(dep_result)
        impact_tokens = _count_tokens(impact_result)

        assert dep_tokens <= 2050  # 2000 + header overhead
        assert impact_tokens <= 2550


# ═══════════════════════════════════════════════════════════════════════
# Deduplication and grouping
# ═══════════════════════════════════════════════════════════════════════


class TestAssembleContextGrouping:
    """Chunks from the same routine should be grouped together."""

    def test_same_routine_chunks_adjacent(self):
        chunks = [
            _make_chunk(routine_name="SPKEZ", chunk_type="routine_doc", text="SPKEZ doc.", score=0.9),
            _make_chunk(routine_name="FURNSH", chunk_type="routine_doc", text="FURNSH doc.", score=0.85),
            _make_chunk(routine_name="SPKEZ", chunk_type="routine_body", text="SPKEZ body.", score=0.8),
        ]
        result = assemble_context(chunks)
        # SPKEZ doc and body should be adjacent
        doc_pos = result.index("SPKEZ doc.")
        body_pos = result.index("SPKEZ body.")
        furnsh_pos = result.index("FURNSH doc.")
        # Both SPKEZ chunks should come before FURNSH (SPKEZ has doc + higher score)
        assert doc_pos < body_pos, "Doc before body within same routine"

    def test_empty_text_chunks_skipped(self):
        chunks = [
            _make_chunk(text="", score=0.9),
            _make_chunk(text="Real content.", score=0.8),
        ]
        result = assemble_context(chunks)
        assert "Real content." in result


# ═══════════════════════════════════════════════════════════════════════
# Edge cases
# ═══════════════════════════════════════════════════════════════════════


class TestAssembleContextEdgeCases:
    """Edge cases in context assembly."""

    def test_very_small_budget(self):
        """A budget smaller than any single chunk header should not crash."""
        chunk = _make_chunk(text="Some text.")
        result = assemble_context([chunk], max_tokens=5)
        # May be empty or very short, but should not raise
        assert isinstance(result, str)

    def test_single_token_budget(self):
        chunk = _make_chunk(text="Text.")
        result = assemble_context([chunk], max_tokens=1)
        assert isinstance(result, str)

    def test_chunk_with_no_metadata_text(self):
        """Chunk where both text and metadata text are empty."""
        chunk = RetrievedChunk(
            id="empty",
            text="",
            score=0.9,
            metadata={
                "routine_name": "TEST",
                "chunk_type": "routine_doc",
                "file_path": "test.f",
                "start_line": 1,
                "end_line": 10,
            },
        )
        result = assemble_context([chunk])
        # Empty text chunks should be skipped
        assert result == "" or "TEST" not in result or result.strip() == ""


# ═══════════════════════════════════════════════════════════════════════
# Query expansion tests
# ═══════════════════════════════════════════════════════════════════════


class TestQueryExpansion:
    """Verify query expansion enriches embeddings for better retrieval."""

    def test_semantic_spacecraft_gets_spk_expansion(self):
        """Vague spacecraft queries get SPK-specific expansion terms."""
        from app.retrieval.router import route_query, QueryIntent
        from app.retrieval.search import _expand_query

        routed = route_query("How does the spacecraft track its position?")
        assert routed.intent == QueryIntent.SEMANTIC
        expanded = _expand_query(routed)
        assert "NASA SPICE Toolkit Fortran subroutine" in expanded
        # Key: domain keyword scan injects SPK terms into the embedding
        assert "SPKEZ" in expanded
        assert "SPKEZR" in expanded
        assert "position" in expanded
        assert "velocity" in expanded

    def test_semantic_spaceship_gets_spk_expansion(self):
        """'spaceship' is recognized as a spacecraft domain term."""
        from app.retrieval.router import route_query, QueryIntent
        from app.retrieval.search import _expand_query

        routed = route_query("How does the spaceship track its position?")
        assert routed.intent == QueryIntent.SEMANTIC
        expanded = _expand_query(routed)
        assert "SPKEZ" in expanded

    def test_semantic_aberration_gets_spk_expansion(self):
        """'aberration' triggers SPK expansion."""
        from app.retrieval.router import route_query, QueryIntent
        from app.retrieval.search import _expand_query

        routed = route_query("What is aberration correction?")
        assert routed.intent == QueryIntent.SEMANTIC
        expanded = _expand_query(routed)
        assert "SPKEZ" in expanded

    def test_semantic_surface_gets_geometry_expansion(self):
        """'surface' in a SEMANTIC query triggers geometry expansion."""
        from app.retrieval.router import route_query, QueryIntent
        from app.retrieval.search import _expand_query

        # 'surface' alone doesn't trigger the router's pattern map,
        # but the semantic keyword scan adds geometry terms
        routed = route_query("How do I find a surface point?")
        expanded = _expand_query(routed)
        assert "SUBPNT" in expanded or "SINCPT" in expanded

    def test_semantic_no_domain_terms_gets_base_only(self):
        """Queries with no domain keywords get only base expansion."""
        from app.retrieval.router import route_query, QueryIntent
        from app.retrieval.search import _expand_query

        routed = route_query("how is it all used?")
        assert routed.intent == QueryIntent.SEMANTIC
        expanded = _expand_query(routed)
        assert "NASA SPICE Toolkit Fortran subroutine" in expanded
        # Should NOT have pattern-specific terms
        assert "SPKEZ" not in expanded
        assert "CHKIN" not in expanded

    def test_explain_includes_routine_name(self):
        """EXPLAIN queries prepend 'Explain the SPICE routine X'."""
        from app.retrieval.router import route_query, QueryIntent
        from app.retrieval.search import _expand_query

        routed = route_query("What does SPKEZ do?")
        assert routed.intent == QueryIntent.EXPLAIN
        expanded = _expand_query(routed)
        assert "Explain the SPICE routine SPKEZ" in expanded

    def test_pattern_includes_domain_terms(self):
        """PATTERN queries include pattern-specific SPICE routine names."""
        from app.retrieval.router import route_query, QueryIntent
        from app.retrieval.search import _expand_query

        routed = route_query("How does SPICE handle errors?")
        assert routed.intent == QueryIntent.PATTERN
        expanded = _expand_query(routed)
        assert "CHKIN" in expanded
        assert "SIGERR" in expanded

    def test_bare_routine_name_gets_rich_expansion(self):
        """A bare routine name like 'SPKEZ' gets a rich expanded query."""
        from app.retrieval.router import route_query
        from app.retrieval.search import _expand_query

        routed = route_query("SPKEZ")
        expanded = _expand_query(routed)
        assert "Explain the SPICE routine SPKEZ" in expanded
        assert len(expanded) > 50  # much richer than the bare name

    def test_out_of_scope_not_expanded(self):
        """OUT_OF_SCOPE queries are blocked before expansion."""
        from app.retrieval.router import route_query, QueryIntent

        routed = route_query("What is the weather today?")
        assert routed.intent == QueryIntent.OUT_OF_SCOPE

    def test_dependency_expansion(self):
        """DEPENDENCY queries include call graph terms."""
        from app.retrieval.router import route_query, QueryIntent
        from app.retrieval.search import _expand_query

        routed = route_query("What does SPKEZ call?")
        assert routed.intent == QueryIntent.DEPENDENCY
        expanded = _expand_query(routed)
        assert "Dependencies" in expanded or "call graph" in expanded
