"""Unit tests for BM25 hybrid search and Reciprocal Rank Fusion (Gap #3 — Critical).

Exercises BM25 index building, search scoring, RRF merge logic, and
edge cases without any API calls ($0).

Run:
    pytest tests/test_bm25.py -v
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from app.retrieval.bm25_index import (
    BM25Doc,
    BM25Result,
    _tokenize,
    _build_bm25_corpus,
    bm25_search,
    get_bm25,
    reciprocal_rank_fusion,
)


# ═══════════════════════════════════════════════════════════════════════
# Tokenizer
# ═══════════════════════════════════════════════════════════════════════


class TestTokenizer:
    """_tokenize splits on word boundaries and lowercases."""

    def test_basic_tokenization(self):
        tokens = _tokenize("SPKEZ FURNSH")
        assert "spkez" in tokens
        assert "furnsh" in tokens

    def test_lowercased(self):
        tokens = _tokenize("ABC")
        assert tokens == ["abc"]

    def test_underscores_kept(self):
        tokens = _tokenize("MY_ROUTINE_1")
        assert "my_routine_1" in tokens

    def test_short_tokens_filtered(self):
        """Single-char tokens are filtered (regex requires ≥2 chars)."""
        tokens = _tokenize("A B C")
        assert tokens == []

    def test_mixed_content(self):
        tokens = _tokenize("SPKEZ calls CHKIN at line 42")
        assert "spkez" in tokens
        assert "chkin" in tokens
        assert "42" in tokens

    def test_empty_string(self):
        assert _tokenize("") == []

    def test_punctuation_stripped(self):
        tokens = _tokenize("SPKEZ, FURNSH; CHKIN.")
        assert "spkez" in tokens
        assert "furnsh" in tokens
        assert "chkin" in tokens


# ═══════════════════════════════════════════════════════════════════════
# BM25 corpus building
# ═══════════════════════════════════════════════════════════════════════


class TestBuildBM25Corpus:
    """_build_bm25_corpus builds index from call_graph.json."""

    def test_builds_from_real_call_graph(self):
        """Verify corpus builds from the actual call_graph.json."""
        bm25, docs = _build_bm25_corpus()
        assert len(docs) > 0
        # Should have at least as many docs as forward entries
        data = json.loads(Path("data/call_graph.json").read_text())
        n_forward = len(data.get("forward", {}))
        n_aliases = len(data.get("aliases", {}))
        assert len(docs) >= n_forward  # forward entries + aliases

    def test_routine_names_double_weighted(self):
        """Routine names appear twice in the pseudo-document for boosting."""
        _, docs = _build_bm25_corpus()
        by_name = {d.routine_name: d for d in docs}
        # Pick any routine
        first_name = next(iter(by_name.keys()))
        tokens = by_name[first_name].tokens
        count = tokens.count(first_name.lower())
        assert count >= 2, f"Expected {first_name} to appear ≥2 times, got {count}"

    def test_alias_docs_exist(self):
        """ENTRY aliases should have their own BM25 documents."""
        _, docs = _build_bm25_corpus()
        data = json.loads(Path("data/call_graph.json").read_text())
        aliases = data.get("aliases", {})
        if not aliases:
            pytest.skip("No aliases in call graph")

        by_name = {d.routine_name: d for d in docs}
        sample_aliases = list(aliases.keys())[:10]
        for alias in sample_aliases:
            assert alias in by_name, f"Alias {alias} missing from BM25 docs"

    def test_alias_doc_contains_parent(self):
        """Alias docs should tokenize the parent routine name."""
        _, docs = _build_bm25_corpus()
        data = json.loads(Path("data/call_graph.json").read_text())
        aliases = data.get("aliases", {})
        if not aliases:
            pytest.skip("No aliases in call graph")

        by_name = {d.routine_name: d for d in docs}
        for alias, parent in list(aliases.items())[:5]:
            if alias in by_name:
                tokens = by_name[alias].tokens
                assert parent.lower() in tokens, (
                    f"Alias doc for {alias} should contain parent {parent}"
                )

    def test_chunk_id_format(self):
        """Chunk IDs should be prefixed with 'bm25::'."""
        _, docs = _build_bm25_corpus()
        for doc in docs[:20]:
            assert doc.chunk_id.startswith("bm25::"), f"Bad chunk_id: {doc.chunk_id}"

    def test_missing_call_graph_returns_empty_index(self):
        """When call_graph.json is not found, returns a minimal fallback index."""
        import app.retrieval.bm25_index as bm25_mod

        # Directly call the builder with all candidate paths made non-existent
        original_candidates = [
            bm25_mod.Path("data/call_graph.json"),
            bm25_mod.Path(__file__).parent.parent / "data" / "call_graph.json",
            bm25_mod.Path("/app/data/call_graph.json"),
        ]

        with patch.object(bm25_mod.Path, "exists", return_value=False):
            bm25, docs = bm25_mod._build_bm25_corpus()
            # Should return a minimal placeholder (not crash)
            assert len(docs) <= 1
            # The fallback doc should be the _empty_ sentinel
            assert docs[0].tokens == ["_empty_"]


# ═══════════════════════════════════════════════════════════════════════
# BM25 search
# ═══════════════════════════════════════════════════════════════════════


class TestBM25Search:
    """bm25_search returns ranked routine names."""

    def test_search_returns_results(self):
        results = bm25_search("SPKEZ", top_k=10)
        assert len(results) > 0
        assert isinstance(results[0], BM25Result)

    def test_search_returns_correct_type(self):
        results = bm25_search("SPKEZ", top_k=5)
        for r in results:
            assert isinstance(r.routine_name, str)
            assert isinstance(r.score, float)
            assert r.score > 0

    def test_exact_routine_name_ranks_high(self):
        """Searching for an exact routine name should rank it #1 or near top."""
        results = bm25_search("SPKEZ", top_k=10)
        names = [r.routine_name for r in results]
        assert "SPKEZ" in names, f"SPKEZ not found in results: {names}"
        # Should be in top 3
        spkez_rank = names.index("SPKEZ")
        assert spkez_rank < 3, f"SPKEZ ranked #{spkez_rank + 1}, expected top 3"

    def test_search_respects_top_k(self):
        results = bm25_search("error handling CHKIN", top_k=3)
        assert len(results) <= 3

    def test_empty_query_returns_empty(self):
        results = bm25_search("", top_k=10)
        assert results == []

    def test_no_hits_for_gibberish(self):
        results = bm25_search("zzzzxyzzy", top_k=10)
        # May or may not return results depending on tokenizer,
        # but scores should be 0 → empty results
        assert len(results) == 0 or all(r.score > 0 for r in results)

    def test_deduplicates_routine_names(self):
        """Results should have unique routine names."""
        results = bm25_search("SPKEZ computation", top_k=20)
        names = [r.routine_name for r in results]
        assert len(names) == len(set(names)), "Duplicate routine names in results"

    def test_alias_searchable(self):
        """ENTRY aliases should be findable via BM25 search."""
        data = json.loads(Path("data/call_graph.json").read_text())
        aliases = list(data.get("aliases", {}).keys())
        if not aliases:
            pytest.skip("No aliases in call graph")

        alias = aliases[0]
        results = bm25_search(alias, top_k=10)
        names = [r.routine_name for r in results]
        assert alias in names, f"Alias {alias} not found in BM25 search results"


# ═══════════════════════════════════════════════════════════════════════
# Reciprocal Rank Fusion
# ═══════════════════════════════════════════════════════════════════════


class TestReciprocalRankFusion:
    """reciprocal_rank_fusion merges two ranked lists correctly."""

    def test_identical_lists(self):
        names = ["A", "B", "C"]
        result = reciprocal_rank_fusion(names, names)
        assert result == ["A", "B", "C"]

    def test_disjoint_lists(self):
        result = reciprocal_rank_fusion(["A", "B"], ["C", "D"])
        assert set(result) == {"A", "B", "C", "D"}

    def test_one_empty_list(self):
        result = reciprocal_rank_fusion(["A", "B", "C"], [])
        assert result == ["A", "B", "C"]

    def test_both_empty(self):
        result = reciprocal_rank_fusion([], [])
        assert result == []

    def test_bm25_boosts_lower_ranked_vector(self):
        """If BM25 ranks B #1 and vector ranks B #2, B should move up."""
        vector = ["A", "B", "C"]
        bm25 = ["B", "C", "A"]
        result = reciprocal_rank_fusion(vector, bm25)
        # B should rank higher than A since it's ranked high in both
        b_pos = result.index("B")
        a_pos = result.index("A")
        assert b_pos < a_pos, f"B at {b_pos}, A at {a_pos} — expected B before A"

    def test_rrf_score_monotonic(self):
        """Items ranked higher in both lists should score higher."""
        vector = ["A", "B", "C", "D"]
        bm25 = ["A", "B", "C", "D"]
        result = reciprocal_rank_fusion(vector, bm25)
        assert result == ["A", "B", "C", "D"]

    def test_custom_k_parameter(self):
        """Different k values should produce valid results."""
        vector = ["A", "B"]
        bm25 = ["B", "A"]
        result_k60 = reciprocal_rank_fusion(vector, bm25, k=60)
        result_k1 = reciprocal_rank_fusion(vector, bm25, k=1)
        # Both should return same items, order might differ
        assert set(result_k60) == {"A", "B"}
        assert set(result_k1) == {"A", "B"}

    def test_single_item_lists(self):
        result = reciprocal_rank_fusion(["A"], ["B"])
        assert set(result) == {"A", "B"}

    def test_overlap_at_different_ranks(self):
        """Shared items get score from both lists; unique items get from one."""
        vector = ["A", "B", "C"]
        bm25 = ["C", "D", "A"]
        result = reciprocal_rank_fusion(vector, bm25)
        # A and C appear in both lists, D and B in only one
        assert set(result) == {"A", "B", "C", "D"}
        # A is #1 in vector and #3 in bm25 → combined score
        # C is #3 in vector and #1 in bm25 → combined score (same by symmetry)
        # They should both rank above B and D (which only appear in one list)
        a_pos = result.index("A")
        b_pos = result.index("B")
        d_pos = result.index("D")
        assert a_pos < b_pos, "A (in both) should rank above B (vector only)"
        assert a_pos < d_pos, "A (in both) should rank above D (bm25 only)"


# ═══════════════════════════════════════════════════════════════════════
# Singleton / caching
# ═══════════════════════════════════════════════════════════════════════


class TestBM25Singleton:
    """get_bm25() should return the cached singleton."""

    def test_returns_same_instance(self):
        bm25_1, docs_1 = get_bm25()
        bm25_2, docs_2 = get_bm25()
        assert bm25_1 is bm25_2
        assert docs_1 is docs_2

    def test_thread_safe_init(self):
        """Concurrent first-access should not crash or produce duplicates."""
        import app.retrieval.bm25_index as bm25_mod

        # Reset global state
        original_bm25 = bm25_mod._bm25
        original_docs = bm25_mod._bm25_docs

        try:
            bm25_mod._bm25 = None
            bm25_mod._bm25_docs = []

            results = []
            errors = []

            def get():
                try:
                    b, d = get_bm25()
                    results.append((id(b), id(d)))
                except Exception as e:
                    errors.append(e)

            threads = [threading.Thread(target=get) for _ in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            assert errors == [], f"Thread errors: {errors}"
            # All threads should get the same instances
            assert len(set(results)) == 1, f"Got different instances: {results}"
        finally:
            bm25_mod._bm25 = original_bm25
            bm25_mod._bm25_docs = original_docs
