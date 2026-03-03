"""Deep performance profiler for LegacyLens RAG pipeline.

Instruments every stage of the hot path and reports:
  - Client initialization times
  - Embedding latency (cold vs cached)
  - Pinecone query latency (per-query, by strategy)
  - Router overhead
  - Context assembly time
  - LLM time-to-first-token (TTFT)
  - LLM total generation time
  - Token throughput (tokens/sec)
  - End-to-end latency

Usage: uv run python scripts/profile_pipeline.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Force fresh state — clear any cached singletons
import app.services as svc
svc._openai_client = None
svc._pinecone_index = None
svc._embed_cache.clear()
svc._answer_cache.clear()
svc._call_graph = None


@dataclass
class TimingResult:
    name: str
    duration_ms: float
    details: dict = field(default_factory=dict)


@contextmanager
def timed(name: str, results: list[TimingResult], **extra):
    t0 = time.perf_counter()
    yield
    dt = (time.perf_counter() - t0) * 1000
    results.append(TimingResult(name=name, duration_ms=dt, details=extra))


def profile_query(query: str, label: str = "") -> dict:
    """Profile a single query through the full pipeline. Returns timing dict."""
    timings: list[TimingResult] = []

    # ── Stage 0: Client init ──────────────────────────────────
    with timed("openai_client_init", timings):
        from app.services import get_openai
        get_openai()

    with timed("pinecone_client_init", timings):
        from app.services import get_index
        get_index()

    with timed("call_graph_load", timings):
        from app.services import get_call_graph
        get_call_graph()

    # ── Stage 1: Routing ──────────────────────────────────────
    with timed("routing", timings):
        from app.retrieval.router import route_query
        routed = route_query(query)

    route_info = {
        "intent": routed.intent.name,
        "routine_names": routed.routine_names,
        "patterns": routed.patterns,
        "prefer_doc": routed.prefer_doc,
    }

    # ── Stage 2: Embedding ────────────────────────────────────
    # Clear embed cache for cold measurement
    svc._embed_cache.clear()

    with timed("embedding_cold", timings):
        from app.services import embed_text
        query_vec = embed_text(routed.original_query)

    embed_dim = len(query_vec)

    # Warm measurement
    with timed("embedding_cached", timings):
        embed_text(routed.original_query)

    # ── Stage 3: Pinecone retrieval (parallel via retrieve_routed) ──
    from app.retrieval.search import retrieve_routed, RetrievedChunk

    with timed("pinecone_retrieve_routed", timings, intent=routed.intent.name):
        all_chunks = retrieve_routed(routed, top_k=10)

    pinecone_queries = len(routed.routine_names) + len(routed.patterns) + 1

    # ── Stage 4: Context assembly ─────────────────────────────
    from app.retrieval.context import assemble_context, _count_tokens
    from app.retrieval.router import QueryIntent

    ctx_budget = {
        QueryIntent.DEPENDENCY: 2000,
        QueryIntent.IMPACT: 2500,
    }.get(routed.intent)

    with timed("context_assembly", timings):
        context = assemble_context(all_chunks, max_tokens=ctx_budget)

    context_chars = len(context)
    context_est_tokens = _count_tokens(context)

    # ── Stage 5: LLM generation (streaming) ───────────────────
    from app.retrieval.generator import generate_answer_stream
    # Clear answer cache to force LLM call
    svc._answer_cache.clear()

    llm_start = time.perf_counter()
    first_token_time = None
    token_count = 0
    full_answer = ""
    final_resp = None

    for token, resp in generate_answer_stream(query, context):
        if resp is not None:
            final_resp = resp
        elif token is not None:
            if first_token_time is None:
                first_token_time = time.perf_counter()
            token_count += 1
            full_answer += token

    llm_end = time.perf_counter()
    llm_total_ms = (llm_end - llm_start) * 1000
    ttft_ms = ((first_token_time - llm_start) * 1000) if first_token_time else llm_total_ms
    generation_ms = ((llm_end - first_token_time) * 1000) if first_token_time else 0
    tokens_per_sec = (token_count / (generation_ms / 1000)) if generation_ms > 0 else 0

    timings.append(TimingResult("llm_ttft", ttft_ms))
    timings.append(TimingResult("llm_generation", generation_ms, details={
        "token_count": token_count, "tokens_per_sec": round(tokens_per_sec, 1),
    }))
    timings.append(TimingResult("llm_total", llm_total_ms))

    # ── Stage 6: Cached answer retrieval ──────────────────────
    with timed("answer_cache_hit", timings):
        for token, resp in generate_answer_stream(query, context):
            pass  # should be instant cache hit

    # ── Summary ───────────────────────────────────────────────
    # End-to-end = routing + embedding(cold) + pinecone + context + llm
    e2e_ms = sum(t.duration_ms for t in timings if t.name in [
        "routing", "embedding_cold", "pinecone_retrieve_routed",
        "context_assembly", "llm_total",
    ])

    # Retrieval latency = everything before LLM
    retrieval_ms = sum(t.duration_ms for t in timings if t.name in [
        "routing", "embedding_cold", "pinecone_retrieve_routed",
        "context_assembly",
    ])

    return {
        "label": label or query[:50],
        "query": query,
        "route": route_info,
        "timings": {t.name: {"ms": round(t.duration_ms, 2), **t.details} for t in timings},
        "summary": {
            "retrieval_ms": round(retrieval_ms, 1),
            "ttft_ms": round(ttft_ms, 1),
            "llm_generation_ms": round(generation_ms, 1),
            "e2e_ms": round(e2e_ms, 1),
            "token_count": token_count,
            "tokens_per_sec": round(tokens_per_sec, 1),
            "context_tokens_est": context_est_tokens,
            "pinecone_queries": pinecone_queries,
            "chunks_retrieved": len(all_chunks),
            "answer_chars": len(full_answer),
        },
    }


def print_report(results: list[dict]):
    """Pretty-print the profiling report."""
    print("\n" + "=" * 80)
    print("  LEGACYLENS PERFORMANCE PROFILE")
    print("=" * 80)

    for r in results:
        print(f"\n{'─' * 80}")
        print(f"  Query: {r['query'][:70]}")
        print(f"  Route: {r['route']['intent']} → routines={r['route']['routine_names']}")
        print(f"{'─' * 80}")
        print(f"  {'Stage':<30} {'Time (ms)':>10}  {'Details'}")
        print(f"  {'─' * 28} {'─' * 10}  {'─' * 30}")

        order = [
            "openai_client_init", "pinecone_client_init", "call_graph_load",
            "routing", "embedding_cold", "embedding_cached",
            "pinecone_retrieve_routed",
            "context_assembly",
            "llm_ttft", "llm_generation", "llm_total",
            "answer_cache_hit",
        ]

        for stage in order:
            if stage in r["timings"]:
                t = r["timings"][stage]
                details = {k: v for k, v in t.items() if k != "ms"}
                detail_str = json.dumps(details) if details else ""
                bar = "█" * max(1, int(t["ms"] / 100))
                print(f"  {stage:<30} {t['ms']:>10.1f}  {bar}  {detail_str}")

        s = r["summary"]
        print(f"\n  ── Summary ──")
        print(f"  Retrieval (route+embed+search+ctx):  {s['retrieval_ms']:>8.1f} ms")
        print(f"  Time to first token (TTFT):          {s['ttft_ms']:>8.1f} ms")
        print(f"  LLM generation:                      {s['llm_generation_ms']:>8.1f} ms  ({s['token_count']} tokens, {s['tokens_per_sec']} tok/s)")
        print(f"  End-to-end:                          {s['e2e_ms']:>8.1f} ms")
        print(f"  Pinecone queries:                    {s['pinecone_queries']:>8d}")
        print(f"  Chunks retrieved:                    {s['chunks_retrieved']:>8d}")
        print(f"  Context size:                        {s['context_tokens_est']:>8d} tokens (est)")

    # ── Cross-query comparison ────────────────────────────────
    if len(results) > 1:
        print(f"\n{'=' * 80}")
        print(f"  COMPARISON TABLE")
        print(f"{'=' * 80}")
        print(f"  {'Query':<35} {'Retrieval':>10} {'TTFT':>10} {'LLM Gen':>10} {'E2E':>10} {'Tok/s':>8}")
        print(f"  {'─' * 35} {'─' * 10} {'─' * 10} {'─' * 10} {'─' * 10} {'─' * 8}")
        for r in results:
            s = r["summary"]
            label = r["label"][:35]
            print(f"  {label:<35} {s['retrieval_ms']:>8.0f}ms {s['ttft_ms']:>8.0f}ms {s['llm_generation_ms']:>8.0f}ms {s['e2e_ms']:>8.0f}ms {s['tokens_per_sec']:>6.0f}")

    # ── Bottleneck analysis ───────────────────────────────────
    print(f"\n{'=' * 80}")
    print(f"  BOTTLENECK ANALYSIS")
    print(f"{'=' * 80}")

    avg_retrieval = sum(r["summary"]["retrieval_ms"] for r in results) / len(results)
    avg_ttft = sum(r["summary"]["ttft_ms"] for r in results) / len(results)
    avg_llm = sum(r["summary"]["llm_generation_ms"] for r in results) / len(results)
    avg_e2e = sum(r["summary"]["e2e_ms"] for r in results) / len(results)
    avg_embed = sum(r["timings"].get("embedding_cold", {}).get("ms", 0) for r in results) / len(results)
    avg_pinecone = sum(
        r["timings"].get("pinecone_retrieve_routed", {}).get("ms", 0)
        for r in results
    ) / len(results)

    pct_embed = (avg_embed / avg_e2e) * 100 if avg_e2e else 0
    pct_pinecone = (avg_pinecone / avg_e2e) * 100 if avg_e2e else 0
    pct_ttft = (avg_ttft / avg_e2e) * 100 if avg_e2e else 0
    pct_llm = (avg_llm / avg_e2e) * 100 if avg_e2e else 0

    print(f"  Avg embedding (cold):     {avg_embed:>8.0f} ms  ({pct_embed:4.1f}% of E2E)")
    print(f"  Avg Pinecone queries:     {avg_pinecone:>8.0f} ms  ({pct_pinecone:4.1f}% of E2E)")
    print(f"  Avg retrieval total:      {avg_retrieval:>8.0f} ms")
    print(f"  Avg TTFT:                 {avg_ttft:>8.0f} ms  ({pct_ttft:4.1f}% of E2E)")
    print(f"  Avg LLM generation:       {avg_llm:>8.0f} ms  ({pct_llm:4.1f}% of E2E)")
    print(f"  Avg end-to-end:           {avg_e2e:>8.0f} ms")

    # Identify top bottleneck
    bottlenecks = [
        ("Embedding API", avg_embed),
        ("Pinecone queries", avg_pinecone),
        ("LLM TTFT", avg_ttft),
        ("LLM generation", avg_llm),
    ]
    bottlenecks.sort(key=lambda x: x[1], reverse=True)

    print(f"\n  Top bottlenecks (ranked by absolute time):")
    for i, (name, ms) in enumerate(bottlenecks):
        pct = (ms / avg_e2e) * 100 if avg_e2e else 0
        print(f"    {i+1}. {name:<25} {ms:>8.0f} ms  ({pct:4.1f}%)")

    print()


# ── Test queries covering different intents ───────────────────────

TEST_QUERIES = [
    ("What does SPKEZ do?",                       "EXPLAIN (named routine)"),
    ("What routines does FURNSH call?",            "DEPENDENCY (call graph)"),
    ("How does SPICE handle errors?",              "PATTERN (error handling)"),
    ("What is the purpose of kernel loading?",     "SEMANTIC (conceptual)"),
    ("What breaks if CHKIN changes?",              "IMPACT (blast radius)"),
]


def main():
    print("Loading environment...")
    from dotenv import load_dotenv
    load_dotenv()

    results = []
    for query, label in TEST_QUERIES:
        print(f"\nProfiling: {label}...")
        result = profile_query(query, label)
        results.append(result)

    print_report(results)

    # Save raw data
    out_path = Path("docs/PERFORMANCE_PROFILE.json")
    out_path.write_text(json.dumps(results, indent=2))
    print(f"Raw data saved to {out_path}")


if __name__ == "__main__":
    main()
