"""Model Quality Evaluation Matrix.

Runs all golden eval cases across multiple LLM models and produces
a comparison matrix showing faithfulness, hallucination rate, latency,
and cost per model.

Usage:
    python tests/model_comparison.py                    # full comparison
    python tests/model_comparison.py --models gpt-4o-mini google/gemini-2.0-flash-001
    python tests/model_comparison.py --cases 5          # first 5 cases only
    python tests/model_comparison.py --output results   # save to data/model_comparison.json

Requires: OPENAI_API_KEY + PINECONE_API_KEY + OPENROUTER_API_KEY in .env
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

# Ensure imports work
sys.path.insert(0, str(Path(__file__).parent.parent))

# Load env
_env_path = Path(__file__).parent.parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

from tests.eval_schema import load_eval_cases, EvalCase
from tests.eval_assert import (
    assert_faithfulness,
    assert_no_hallucination,
    content_precision,
)
from app.retrieval.router import route_query
from app.retrieval.search import retrieve_routed
from app.retrieval.context import assemble_context
from app.retrieval.generator import generate_answer
from app.config import settings


# ── Default models to compare ────────────────────────────────────────

DEFAULT_MODELS = [
    "gpt-4o-mini",
    "google/gemini-2.0-flash-001",
    "google/gemini-2.5-flash",
]


@dataclass
class CaseResult:
    """Result for a single case on a single model."""
    case_id: str
    query: str
    category: str
    model: str
    faithfulness: float = 0.0
    hallucination: bool = False
    answer_len: int = 0
    latency_ms: float = 0.0
    tokens_used: int = 0
    passed: bool = False
    error: str = ""
    answer_snippet: str = ""


@dataclass
class ModelSummary:
    """Aggregate metrics for a model across all cases."""
    model: str
    total_cases: int = 0
    passed: int = 0
    pass_rate: float = 0.0
    avg_faithfulness: float = 0.0
    hallucination_count: int = 0
    hallucination_rate: float = 0.0
    avg_latency_ms: float = 0.0
    median_latency_ms: float = 0.0
    total_tokens: int = 0
    est_cost: float = 0.0
    avg_answer_len: float = 0.0


# ── Cost per 1M tokens (input + output blended estimate) ────────────

MODEL_COSTS = {
    "gpt-4o-mini": 0.30,
    "google/gemini-2.0-flash-001": 0.10,
    "google/gemini-2.5-flash": 0.15,
    "anthropic/claude-3.5-haiku": 0.80,
    "meta-llama/llama-3.3-70b-instruct": 0.30,
}


def _estimate_cost(model: str, tokens: int) -> float:
    """Estimate cost in USD for token usage."""
    rate = MODEL_COSTS.get(model, 0.30)  # default fallback
    return tokens * rate / 1_000_000


def run_case_on_model(case: EvalCase, model: str) -> CaseResult:
    """Run a single eval case with a specific model."""
    result = CaseResult(
        case_id=case.id,
        query=case.query,
        category=case.meta.category,
        model=model,
    )

    try:
        # Override model setting
        settings.llm_model = model

        # Route
        routed = route_query(case.query)

        # Skip retrieval for out-of-scope
        if routed.intent.name == "OUT_OF_SCOPE":
            result.passed = True
            result.faithfulness = 1.0
            result.answer_snippet = "(out of scope — no LLM call)"
            return result

        # Retrieve
        t0 = time.time()
        chunks = retrieve_routed(routed, top_k=5)
        if not chunks:
            result.error = "No chunks retrieved"
            return result

        # Context
        context = assemble_context(chunks)

        # Generate
        answer = generate_answer(case.query, context)
        elapsed_ms = (time.time() - t0) * 1000

        result.latency_ms = elapsed_ms
        result.tokens_used = answer.usage.get("total_tokens", 0)
        result.answer_len = len(answer.answer)
        result.answer_snippet = answer.answer[:300]

        # Faithfulness check
        if case.expect.mustIncludeAny:
            try:
                result.faithfulness = assert_faithfulness(
                    answer.answer,
                    case.expect.mustIncludeAny,
                    min_score=0.0,  # don't raise, just measure
                    query=case.query,
                )
            except AssertionError:
                result.faithfulness = content_precision(
                    case.expect.mustIncludeAny, answer.answer
                )
        else:
            result.faithfulness = 1.0

        # Hallucination check
        try:
            assert_no_hallucination(
                answer.answer,
                case.expect.mustNotIncludeAny,
                query=case.query,
            )
            result.hallucination = False
        except AssertionError as e:
            result.hallucination = True
            result.error = str(e)

        result.passed = (
            result.faithfulness >= case.expect.minFaithfulness
            and not result.hallucination
        )

    except Exception as e:
        result.error = str(e)[:200]

    return result


def summarize_model(model: str, results: list[CaseResult]) -> ModelSummary:
    """Compute aggregate metrics for a model."""
    if not results:
        return ModelSummary(model=model)

    latencies = [r.latency_ms for r in results if r.latency_ms > 0]
    sorted_latencies = sorted(latencies)

    return ModelSummary(
        model=model,
        total_cases=len(results),
        passed=sum(1 for r in results if r.passed),
        pass_rate=sum(1 for r in results if r.passed) / len(results),
        avg_faithfulness=sum(r.faithfulness for r in results) / len(results),
        hallucination_count=sum(1 for r in results if r.hallucination),
        hallucination_rate=sum(1 for r in results if r.hallucination) / len(results),
        avg_latency_ms=sum(latencies) / len(latencies) if latencies else 0,
        median_latency_ms=sorted_latencies[len(sorted_latencies) // 2] if sorted_latencies else 0,
        total_tokens=sum(r.tokens_used for r in results),
        est_cost=_estimate_cost(model, sum(r.tokens_used for r in results)),
        avg_answer_len=sum(r.answer_len for r in results) / len(results),
    )


def run_comparison(
    models: list[str] | None = None,
    max_cases: int | None = None,
    verbose: bool = True,
) -> dict[str, ModelSummary]:
    """Run full model comparison and return summaries."""
    if models is None:
        models = DEFAULT_MODELS

    cases = load_eval_cases(stage="golden")
    # Filter out adversarial/out-of-scope cases (no LLM involved)
    cases = [c for c in cases if c.meta.category != "adversarial"]

    if max_cases:
        cases = cases[:max_cases]

    print(f"\n{'='*80}")
    print(f"MODEL COMPARISON MATRIX — {len(cases)} cases × {len(models)} models")
    print(f"{'='*80}\n")

    all_results: dict[str, list[CaseResult]] = {m: [] for m in models}

    for model in models:
        print(f"▶ {model}")
        for i, case in enumerate(cases):
            if verbose:
                print(f"  [{i+1:2d}/{len(cases)}] {case.query[:60]}", end="", flush=True)

            # Clear answer cache between models to avoid cross-contamination
            from app.services import _answer_cache, _answer_lock
            with _answer_lock:
                _answer_cache.clear()

            result = run_case_on_model(case, model)
            all_results[model].append(result)

            if verbose:
                status = "✅" if result.passed else "❌"
                faith = f"F:{result.faithfulness:.0%}"
                lat = f"{result.latency_ms:.0f}ms" if result.latency_ms else "—"
                print(f" → {status} {faith} {lat}")

        print()

    # ── Summary table ────────────────────────────────────────────

    summaries = {m: summarize_model(m, all_results[m]) for m in models}

    print(f"\n{'='*80}")
    print("COMPARISON MATRIX")
    print(f"{'='*80}\n")

    # Header
    header = f"{'Model':38s} {'Pass':>5s} {'Faith':>6s} {'Halluc':>6s} {'Med ms':>7s} {'Tokens':>7s} {'Cost':>7s} {'Avg Len':>7s}"
    print(header)
    print("─" * len(header))

    for model in models:
        s = summaries[model]
        print(
            f"{s.model:38s} "
            f"{s.pass_rate:>4.0%}  "
            f"{s.avg_faithfulness:>5.0%}  "
            f"{s.hallucination_rate:>5.0%}  "
            f"{s.median_latency_ms:>6.0f}  "
            f"{s.total_tokens:>6d}  "
            f"${s.est_cost:>.4f} "
            f"{s.avg_answer_len:>6.0f}"
        )

    print(f"\n{'='*80}")

    # Per-category breakdown
    categories = sorted(set(c.meta.category for c in cases))
    for cat in categories:
        print(f"\n  {cat}:")
        for model in models:
            cat_results = [r for r in all_results[model] if r.category == cat]
            if cat_results:
                cat_pass = sum(1 for r in cat_results if r.passed)
                cat_faith = sum(r.faithfulness for r in cat_results) / len(cat_results)
                print(f"    {model:38s} {cat_pass}/{len(cat_results)} ({cat_pass/len(cat_results):.0%}) faith={cat_faith:.0%}")

    # ── Failures per model ───────────────────────────────────────

    for model in models:
        failures = [r for r in all_results[model] if not r.passed]
        if failures:
            print(f"\n  ❌ {model} failures ({len(failures)}):")
            for r in failures:
                print(f"    • {r.case_id}: {r.error[:100]}")

    print(f"\n{'='*80}\n")

    # ── Save results ─────────────────────────────────────────────

    out_path = Path("data/model_comparison.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    output = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "models": models,
        "cases_count": len(cases),
        "summaries": {
            m: {
                "pass_rate": s.pass_rate,
                "avg_faithfulness": s.avg_faithfulness,
                "hallucination_rate": s.hallucination_rate,
                "median_latency_ms": s.median_latency_ms,
                "total_tokens": s.total_tokens,
                "est_cost_usd": s.est_cost,
                "avg_answer_len": s.avg_answer_len,
            }
            for m, s in summaries.items()
        },
        "per_case": {
            m: [
                {
                    "case_id": r.case_id,
                    "query": r.query,
                    "category": r.category,
                    "faithfulness": r.faithfulness,
                    "hallucination": r.hallucination,
                    "latency_ms": round(r.latency_ms),
                    "tokens": r.tokens_used,
                    "passed": r.passed,
                    "error": r.error,
                    "answer_snippet": r.answer_snippet,
                }
                for r in all_results[m]
            ]
            for m in models
        },
    }

    out_path.write_text(json.dumps(output, indent=2))
    print(f"  Results saved to {out_path}")

    return summaries


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="LegacyLens Model Comparison")
    parser.add_argument(
        "--models",
        nargs="+",
        default=None,
        help="Models to compare (OpenRouter format)",
    )
    parser.add_argument(
        "--cases",
        type=int,
        default=None,
        help="Max number of eval cases to run",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Minimal output",
    )
    args = parser.parse_args()

    run_comparison(
        models=args.models,
        max_cases=args.cases,
        verbose=not args.quiet,
    )
