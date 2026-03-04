"""Performance regression benchmarks.

These tests assert timing thresholds on the CPU-bound hot paths.
They do NOT make API calls (no OpenAI/Pinecone).
Run with: pytest tests/test_benchmarks.py -v
"""

from __future__ import annotations

import json
import statistics
import time
from pathlib import Path

import pytest
import tiktoken

# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def call_graph():
    from app.ingestion.call_graph import CallGraph

    data = json.load(open("data/call_graph.json"))
    return CallGraph(
        forward=data["forward"],
        reverse=data["reverse"],
        aliases=data.get("aliases", {}),
        routine_files=data.get("routine_files", {}),
    )


@pytest.fixture(scope="module")
def all_names():
    data = json.load(open("data/call_graph.json"))
    return sorted(set(list(data["forward"].keys()) + list(data["aliases"].keys())))


@pytest.fixture(scope="module")
def mock_chunks():
    from app.retrieval.search import RetrievedChunk

    text = (
        "C     Comment\n"
        "      SUBROUTINE EXAMPLE(A, B, C)\n"
        "      IMPLICIT NONE\n"
        "      DOUBLE PRECISION A, B, C\n"
        "      CALL CHKIN('EXAMPLE')\n"
    ) * 30  # ~1500 chars per chunk

    chunks = []
    for i in range(10):
        chunks.append(
            RetrievedChunk(
                id=f"chunk_{i}",
                text=text,
                score=0.9 - i * 0.05,
                metadata={
                    "routine_name": f"ROUTINE_{i}",
                    "chunk_type": "routine_doc" if i < 3 else "routine_body",
                    "file_path": f"src/routine_{i}.f",
                    "start_line": 1,
                    "end_line": 200,
                    "patterns": ["error_handling"] if i < 2 else [],
                    "called_by": "PARENT1, PARENT2",
                    "entry_aliases": "",
                },
            )
        )
    return chunks


def _bench(fn, n=100):
    """Run fn n times, return p50 in ms."""
    times = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        times.append((time.perf_counter() - t0) * 1000)
    return statistics.median(times)


# ── Router benchmarks ────────────────────────────────────────────────


class TestRouterPerf:
    """Router must classify queries in <0.1ms p50."""

    @pytest.mark.parametrize(
        "query",
        [
            "What routines does SPKEZ call?",
            "What breaks if CHKIN changes?",
            "How does SPICE handle errors?",
        ],
    )
    def test_router_latency(self, query):
        from app.retrieval.router import route_query

        p50 = _bench(lambda: route_query(query), n=200)
        assert p50 < 0.1, f"Router p50={p50:.3f}ms exceeds 0.1ms threshold"


# ── Call graph benchmarks ────────────────────────────────────────────


class TestCallGraphPerf:
    """Call graph traversals must complete in <1ms for depth≤5."""

    def test_callers_of_chkin_depth2(self, call_graph):
        """Worst case: CHKIN has 1257 callers at depth 1."""
        p50 = _bench(lambda: call_graph.callers_of("CHKIN", depth=2), n=200)
        assert p50 < 1.0, f"callers_of CHKIN d=2 p50={p50:.3f}ms exceeds 1ms"

    def test_callees_of_spkez_depth5(self, call_graph):
        p50 = _bench(lambda: call_graph.callees_of("SPKEZ", depth=5), n=200)
        assert p50 < 0.5, f"callees_of SPKEZ d=5 p50={p50:.3f}ms exceeds 0.5ms"


# ── Context assembly benchmarks ──────────────────────────────────────


class TestContextAssemblyPerf:
    """assemble_context must complete in <20ms for 10 realistic chunks."""

    def test_assemble_context_10_chunks(self, mock_chunks):
        from app.retrieval.context import assemble_context

        # Warmup tiktoken
        assemble_context(mock_chunks)

        p50 = _bench(lambda: assemble_context(mock_chunks), n=50)
        assert p50 < 20.0, f"assemble_context p50={p50:.3f}ms exceeds 20ms"


# ── Autocomplete benchmarks ─────────────────────────────────────────


class TestAutocompletePerf:
    """Autocomplete search must complete in <2ms p50."""

    def test_autocomplete_prefix_search(self, all_names):
        def search():
            q = "SPK"
            prefix = [n for n in all_names if n.startswith(q)]
            prefix_set = set(prefix)
            substring = [n for n in all_names if q in n and n not in prefix_set]
            return (prefix + substring)[:50]

        p50 = _bench(search, n=200)
        assert p50 < 2.0, f"Autocomplete p50={p50:.3f}ms exceeds 2ms"
