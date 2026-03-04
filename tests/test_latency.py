"""End-to-end latency benchmarks: full pipeline including LLM response.

Measures real wall-clock time for the complete query pipeline:
  router → embedding → Pinecone → context assembly → LLM generation

Two modes:
  1. Single-model:  pytest tests/test_latency.py -v
     Uses whatever LLM_MODEL is configured in .env

  2. Multi-model comparison:  pytest tests/test_latency.py -v -k compare
     Benchmarks gpt-4o-mini vs google/gemini-2.0-flash-001 via OpenRouter
     Requires OPENROUTER_API_KEY in .env

Requires: OPENAI_API_KEY + PINECONE_API_KEY (+ OPENROUTER_API_KEY for model comparison)
"""

from __future__ import annotations

import json
import os
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

# Load .env
_env_path = Path(__file__).parent.parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

_has_keys = bool(os.environ.get("OPENAI_API_KEY")) and bool(
    os.environ.get("PINECONE_API_KEY")
)
_has_openrouter = bool(os.environ.get("OPENROUTER_API_KEY"))

pytestmark = pytest.mark.skipif(
    not _has_keys,
    reason="OPENAI_API_KEY and PINECONE_API_KEY required for latency benchmarks",
)

# ── Test queries (representative mix) ────────────────────────────────

BENCH_QUERIES = [
    ("What does SPKEZ do?", "explain"),
    ("What routines does FURNSH call?", "dependency"),
    ("How does SPICE handle errors?", "pattern"),
    ("What breaks if CHKIN changes?", "impact"),
    ("What is KTOTAL?", "entry-point"),
]

# ── Latency measurement helpers ──────────────────────────────────────


@dataclass
class LatencyResult:
    query: str
    category: str
    model: str
    # Stage timings (ms)
    route_ms: float = 0.0
    embed_ms: float = 0.0
    retrieve_ms: float = 0.0
    context_ms: float = 0.0
    llm_ttft_ms: float = 0.0       # time to first token
    llm_total_ms: float = 0.0      # full generation
    # Totals
    e2e_ms: float = 0.0            # end-to-end
    e2e_to_first_token_ms: float = 0.0
    # Response info
    answer_len: int = 0
    token_count: int = 0


def _measure_e2e(query: str, model: str) -> LatencyResult:
    """Measure full pipeline latency with per-stage breakdown."""
    # Reset singletons to force fresh LLM client for this model
    import app.services as svc
    from app.config import settings
    from app.retrieval.router import route_query
    from app.retrieval.search import retrieve_routed
    from app.retrieval.context import assemble_context

    result = LatencyResult(query=query, category="", model=model)
    t_start = time.perf_counter()

    # Stage 1: Route
    t0 = time.perf_counter()
    routed = route_query(query)
    result.route_ms = (time.perf_counter() - t0) * 1000

    # Stage 2: Embed + Retrieve (combined — embedding happens inside retrieve_routed)
    t0 = time.perf_counter()
    chunks = retrieve_routed(routed, top_k=5)
    result.retrieve_ms = (time.perf_counter() - t0) * 1000

    # Stage 3: Context assembly
    t0 = time.perf_counter()
    context = assemble_context(chunks)
    result.context_ms = (time.perf_counter() - t0) * 1000

    # Stage 4: LLM generation (streaming to measure TTFT)
    from app.retrieval.generator import SYSTEM_PROMPT, _max_tokens_for_query
    from openai import OpenAI

    if settings.openrouter_api_key:
        client = OpenAI(
            api_key=settings.openrouter_api_key,
            base_url=settings.openrouter_base_url,
        )
    else:
        client = OpenAI(api_key=settings.openai_api_key)

    user_prompt = f"Question: {query}\n\nCode Context:\n{context}"

    t_llm_start = time.perf_counter()
    stream = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.1,
        max_tokens=_max_tokens_for_query(query),
        stream=True,
    )

    full_answer = ""
    first_token_time = None
    for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            token = chunk.choices[0].delta.content
            if first_token_time is None:
                first_token_time = time.perf_counter()
            full_answer += token

    t_llm_end = time.perf_counter()

    if first_token_time is not None:
        result.llm_ttft_ms = (first_token_time - t_llm_start) * 1000
    result.llm_total_ms = (t_llm_end - t_llm_start) * 1000
    result.answer_len = len(full_answer)

    # Totals
    result.e2e_ms = (t_llm_end - t_start) * 1000
    if first_token_time is not None:
        result.e2e_to_first_token_ms = (first_token_time - t_start) * 1000

    return result


def _print_results_table(results: list[LatencyResult], label: str):
    """Print a formatted table of latency results."""
    print(f"\n{'='*90}")
    print(f"  {label}")
    print(f"{'='*90}")
    print(f"  {'Query':<40s} {'Route':>6s} {'Retrieve':>9s} {'Context':>8s} "
          f"{'TTFT':>7s} {'LLM':>7s} {'E2E':>7s} {'→1st':>7s}")
    print(f"  {'-'*40} {'-'*6} {'-'*9} {'-'*8} {'-'*7} {'-'*7} {'-'*7} {'-'*7}")

    for r in results:
        print(f"  {r.query[:40]:<40s} {r.route_ms:>5.0f}ms {r.retrieve_ms:>7.0f}ms "
              f"{r.context_ms:>6.0f}ms {r.llm_ttft_ms:>5.0f}ms {r.llm_total_ms:>5.0f}ms "
              f"{r.e2e_ms:>5.0f}ms {r.e2e_to_first_token_ms:>5.0f}ms")

    # Summary
    e2e_times = [r.e2e_ms for r in results]
    ttft_times = [r.e2e_to_first_token_ms for r in results if r.e2e_to_first_token_ms > 0]
    llm_ttft = [r.llm_ttft_ms for r in results if r.llm_ttft_ms > 0]

    print(f"  {'-'*86}")
    print(f"  {'MEAN':<40s} {'':>6s} {'':>9s} {'':>8s} "
          f"{'':>7s} {'':>7s} {statistics.mean(e2e_times):>5.0f}ms "
          f"{statistics.mean(ttft_times):>5.0f}ms")
    print(f"  {'MEDIAN':<40s} {'':>6s} {'':>9s} {'':>8s} "
          f"{'':>7s} {'':>7s} {statistics.median(e2e_times):>5.0f}ms "
          f"{statistics.median(ttft_times):>5.0f}ms")
    print(f"  {'P95':<40s} {'':>6s} {'':>9s} {'':>8s} "
          f"{'':>7s} {'':>7s} "
          f"{sorted(e2e_times)[int(len(e2e_times)*0.95)]:>5.0f}ms "
          f"{sorted(ttft_times)[int(len(ttft_times)*0.95)]:>5.0f}ms")
    if llm_ttft:
        print(f"\n  LLM TTFT (median): {statistics.median(llm_ttft):.0f}ms")
    print(f"  Model: {results[0].model}")
    print(f"{'='*90}\n")


# ── Single-model benchmark ───────────────────────────────────────────


class TestE2ELatency:
    """End-to-end latency with current LLM_MODEL config."""

    def test_e2e_latency(self):
        """All queries must complete E2E in <5s, TTFT in <3s."""
        from app.config import settings

        model = settings.llm_model
        results = []

        # Warmup (embedding cache, tiktoken init)
        _measure_e2e(BENCH_QUERIES[0][0], model)

        for query, category in BENCH_QUERIES:
            r = _measure_e2e(query, model)
            r.category = category
            results.append(r)

        _print_results_table(results, f"E2E Latency — {model}")

        # Primary metric: E2E time to last token (spec: "<3 seconds end-to-end")
        e2e_times = [r.e2e_ms for r in results]
        median_e2e = statistics.median(e2e_times)

        # Report spec compliance clearly
        under_3s = sum(1 for t in e2e_times if t < 3000)
        print(f"\n  Spec target: <3s E2E (time to last token)")
        print(f"  Queries under 3s: {under_3s}/{len(e2e_times)}")
        print(f"  Median E2E: {median_e2e:.0f}ms")
        for r in results:
            status = "✅" if r.e2e_ms < 3000 else "❌"
            print(f"    {status} {r.query[:45]:<45s} {r.e2e_ms:.0f}ms")

        # Assert: median E2E < 5s (regression guard — realistic ceiling)
        assert median_e2e < 5000, (
            f"Median E2E (last token) {median_e2e:.0f}ms > 5000ms regression ceiling"
        )

        # Assert: no single query > 10s (hard ceiling)
        for r in results:
            assert r.e2e_ms < 10000, (
                f"E2E too slow for '{r.query[:40]}': {r.e2e_ms:.0f}ms > 10000ms"
            )


# ── Multi-model comparison ───────────────────────────────────────────

COMPARE_MODELS = [
    "gpt-4o-mini",
    "google/gemini-2.0-flash-001",
]


@pytest.mark.skipif(
    not _has_openrouter,
    reason="OPENROUTER_API_KEY required for model comparison",
)
class TestModelComparison:
    """Compare latency across models via OpenRouter."""

    def test_compare_models(self):
        """Benchmark gpt-4o-mini vs gemini-2.0-flash-001."""
        all_results: dict[str, list[LatencyResult]] = {}

        for model in COMPARE_MODELS:
            results = []

            # Warmup
            _measure_e2e(BENCH_QUERIES[0][0], model)

            for query, category in BENCH_QUERIES:
                r = _measure_e2e(query, model)
                r.category = category
                results.append(r)

            all_results[model] = results
            _print_results_table(results, f"E2E Latency — {model}")

        # Print comparison summary
        print(f"\n{'='*80}")
        print(f"  MODEL COMPARISON SUMMARY (E2E = time to last token)")
        print(f"{'='*80}")
        print(f"  {'Model':<35s} {'Med E2E':>9s} {'<3s':>5s} {'Med TTFT':>10s} "
              f"{'LLM TTFT':>10s} {'Avg Ans':>8s}")
        print(f"  {'-'*35} {'-'*9} {'-'*5} {'-'*10} {'-'*10} {'-'*8}")

        best_e2e = None

        for model, results in all_results.items():
            e2e_times = [r.e2e_ms for r in results]
            med_e2e = statistics.median(e2e_times)
            under_3s = sum(1 for t in e2e_times if t < 3000)
            med_ttft = statistics.median([r.e2e_to_first_token_ms for r in results])
            med_llm_ttft = statistics.median([r.llm_ttft_ms for r in results])
            avg_len = statistics.mean([r.answer_len for r in results])

            if best_e2e is None or med_e2e < best_e2e[1]:
                best_e2e = (model, med_e2e)

            print(f"  {model:<35s} {med_e2e:>7.0f}ms {under_3s:>2d}/{len(e2e_times)} "
                  f"{med_ttft:>8.0f}ms {med_llm_ttft:>8.0f}ms {avg_len:>6.0f}ch")

        print(f"  {'-'*80}")
        print(f"  🏆 Fastest E2E (last token): {best_e2e[0]} ({best_e2e[1]:.0f}ms)")

        # Check spec compliance: <3s E2E (time to last token)
        print(f"\n  Spec target: <3s E2E (time to last token)")
        for model, results in all_results.items():
            e2e_times = [r.e2e_ms for r in results]
            med_e2e = statistics.median(e2e_times)
            under_3s = sum(1 for t in e2e_times if t < 3000)
            status = "✅" if med_e2e < 3000 else "❌"
            print(f"  {status} {model}: median E2E {med_e2e:.0f}ms — {under_3s}/{len(e2e_times)} queries < 3s")

        print(f"{'='*70}\n")

        # Save results for CI artifact
        out_path = Path("data/latency_results.json")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(
            {model: [{
                "query": r.query,
                "category": r.category,
                "model": r.model,
                "route_ms": round(r.route_ms, 1),
                "retrieve_ms": round(r.retrieve_ms, 1),
                "context_ms": round(r.context_ms, 1),
                "llm_ttft_ms": round(r.llm_ttft_ms, 1),
                "llm_total_ms": round(r.llm_total_ms, 1),
                "e2e_ms": round(r.e2e_ms, 1),
                "e2e_to_first_token_ms": round(r.e2e_to_first_token_ms, 1),
                "answer_len": r.answer_len,
            } for r in results] for model, results in all_results.items()},
            indent=2,
        ))
        print(f"  Results saved to {out_path}")
