"""Regression tests for deep-audit fixes.

These tests focus on previously observed gaps:
- out-of-scope queries should not trigger embedding/retrieval API work
- BM25 alias docs should index alias tokens correctly
- RRF ranking should affect final retrieval order
- mixed-signal adversarial prompts should route OUT_OF_SCOPE
- decimal citations should be parsed into integer line ranges
- /query and /api/stream should behave consistently for OUT_OF_SCOPE
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient


def test_out_of_scope_retrieve_skips_embedding(monkeypatch):
    from app.retrieval.router import route_query, QueryIntent
    from app.retrieval import search as search_mod

    routed = route_query("What's the weather today?")
    assert routed.intent == QueryIntent.OUT_OF_SCOPE

    monkeypatch.setattr(
        search_mod,
        "embed_text",
        lambda _q: (_ for _ in ()).throw(AssertionError("embed_text should not be called")),
    )

    results = search_mod.retrieve_routed(routed, top_k=5)
    assert results == []


def test_bm25_alias_docs_include_alias_token():
    from app.retrieval.bm25_index import _build_bm25_corpus

    data = json.loads(Path("data/call_graph.json").read_text())
    aliases = list(data.get("aliases", {}).keys())
    assert aliases, "expected aliases in call_graph.json"

    _, docs = _build_bm25_corpus()
    by_name = {d.routine_name: d for d in docs}

    # Check a sample for speed; enough to catch stale-variable tokenization bugs.
    sample = aliases[:25]
    for alias in sample:
        assert alias in by_name, f"alias doc missing for {alias}"
        tokens = by_name[alias].tokens
        assert alias.lower() in tokens, (
            f"alias token missing in BM25 doc for {alias}: {tokens[:12]}"
        )


def test_rrf_ranking_is_preserved_in_final_order(monkeypatch):
    from app.retrieval.search import RetrievedChunk, retrieve_routed
    from app.retrieval.router import QueryIntent, RoutedQuery
    from app.retrieval import search as search_mod
    from app.retrieval import bm25_index as bm25_mod

    monkeypatch.setattr(search_mod, "embed_text", lambda _q: [0.0])

    def fake_semantic(_vec, _top_k):
        return [
            RetrievedChunk(
                id="a",
                text="A",
                score=0.99,
                metadata={"routine_name": "A", "chunk_type": "routine_doc"},
            ),
            RetrievedChunk(
                id="b",
                text="B",
                score=0.5,
                metadata={"routine_name": "B", "chunk_type": "routine_doc"},
            ),
        ]

    monkeypatch.setattr(search_mod, "_retrieve_semantic", fake_semantic)
    monkeypatch.setattr(bm25_mod, "bm25_search", lambda _q, top_k=20: [bm25_mod.BM25Result("B", 9.0, "routine_doc")])
    monkeypatch.setattr(bm25_mod, "reciprocal_rank_fusion", lambda v, b, k=60: ["B", "A"])

    routed = RoutedQuery(
        intent=QueryIntent.SEMANTIC,
        routine_names=[],
        patterns=[],
        prefer_doc=False,
        original_query="SPKEZ",
    )

    out = retrieve_routed(routed, top_k=2)
    names = [c.metadata.get("routine_name") for c in out]
    assert names == ["B", "A"], f"expected fused order ['B', 'A'], got {names}"


def test_router_blocks_mixed_signal_adversarial_queries():
    from app.retrieval.router import route_query, QueryIntent

    assert route_query("Tell me a joke about kernels").intent == QueryIntent.OUT_OF_SCOPE
    assert route_query("How's the weather in orbit?").intent == QueryIntent.OUT_OF_SCOPE


def test_decimal_citations_are_parsed(monkeypatch):
    from app.retrieval import generator

    fake_response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="Answer [spicelib/spkez.f:3.0-10.0]"))],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=2, total_tokens=3),
        model="fake-model",
    )
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **_kwargs: fake_response,
            )
        )
    )

    monkeypatch.setattr(generator, "get_llm", lambda: fake_client)
    monkeypatch.setattr(generator, "get_cached_answer", lambda *args, **kwargs: None)
    monkeypatch.setattr(generator, "set_cached_answer", lambda *args, **kwargs: None)

    resp = generator.generate_answer("q", "ctx")
    assert resp.citations == [
        {"file_path": "spicelib/spkez.f", "start_line": 3, "end_line": 10}
    ]


def test_streaming_decimal_citations_are_parsed(monkeypatch):
    """Same decimal citation bug affects generate_answer_stream — verify both paths."""
    from app.retrieval import generator
    from types import SimpleNamespace

    # Simulate a streaming response that yields chunks with decimal citations
    class FakeStream:
        def __init__(self):
            self.chunks = [
                SimpleNamespace(
                    choices=[SimpleNamespace(delta=SimpleNamespace(content="Answer [spicelib/spkez.f:3.0-10.0]"))],
                    model="fake-model",
                ),
                SimpleNamespace(
                    choices=[SimpleNamespace(delta=SimpleNamespace(content=None))],
                    model="fake-model",
                ),
            ]
        def __iter__(self):
            return iter(self.chunks)

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **_kwargs: FakeStream(),
            )
        )
    )

    monkeypatch.setattr(generator, "get_llm", lambda: fake_client)
    monkeypatch.setattr(generator, "get_cached_answer", lambda *args, **kwargs: None)
    monkeypatch.setattr(generator, "set_cached_answer", lambda *args, **kwargs: None)

    # Consume the stream
    final_resp = None
    for token, resp in generator.generate_answer_stream("q", "ctx"):
        if resp is not None:
            final_resp = resp

    assert final_resp is not None
    assert final_resp.citations == [
        {"file_path": "spicelib/spkez.f", "start_line": 3, "end_line": 10}
    ]


def test_extract_routine_names_deduplicates():
    """Repeated routine names in query should be deduplicated."""
    from app.retrieval.router import _extract_routine_names
    names = _extract_routine_names("Compare SPKEZ with SPKEZ and also SPKEZ")
    assert names == ["SPKEZ"], f"Expected deduped ['SPKEZ'], got {names}"


def test_extract_routine_names_preserves_order_across_dedup():
    """Dedup preserves first-seen order."""
    from app.retrieval.router import _extract_routine_names
    names = _extract_routine_names("FURNSH then SPKEZ then FURNSH again")
    assert names == ["FURNSH", "SPKEZ"], f"Expected ['FURNSH', 'SPKEZ'], got {names}"


def test_router_fallback_without_call_graph(monkeypatch):
    """When call graph is unavailable, router should still work
    (but may extract false-positive routine names from English words)."""
    import app.retrieval.router as router_mod

    # Force call-graph-unavailable path
    monkeypatch.setattr(router_mod, "_get_known_routines", lambda: None)

    names = router_mod._extract_routine_names("What does SPKEZ do?")
    assert "SPKEZ" in names, "Real routine should still be extracted"

    # Without call graph, false positives pass through (known limitation)
    names_fp = router_mod._extract_routine_names("How does SPACESHIP work?")
    assert "SPACESHIP" in names_fp, (
        "Without call graph, SPACESHIP should pass stop-word filter "
        "(it's not in the stop list)"
    )


def test_router_with_call_graph_filters_false_positives():
    """With call graph, English words that aren't routines are filtered."""
    from app.retrieval.router import _extract_routine_names
    names = _extract_routine_names("How does SPACESHIP use SPKEZ to TRACK location?")
    assert names == ["SPKEZ"], f"Expected only ['SPKEZ'], got {names}"


def test_out_of_scope_api_and_stream_parity():
    from app.main import app
    from app.retrieval.router import _OUT_OF_SCOPE_RESPONSE

    client = TestClient(app)

    q = "What's the weather like today?"

    r1 = client.post("/query", json={"question": q, "top_k": 5})
    assert r1.status_code == 200
    body = r1.json()
    assert body["answer"] == _OUT_OF_SCOPE_RESPONSE
    assert body["chunks"] == []
    assert body["routing"]["intent"] == "OUT_OF_SCOPE"

    r2 = client.post("/api/stream", json={"question": q, "top_k": 5})
    assert r2.status_code == 200
    text = r2.text
    assert "event: routing" in text
    assert '"intent": "OUT_OF_SCOPE"' in text
    assert _OUT_OF_SCOPE_RESPONSE in text
