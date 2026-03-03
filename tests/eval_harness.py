"""Evaluation harness: run golden queries, measure retrieval quality + latency.

Metrics:
  - Router accuracy: does intent classification match expected?
  - Precision@5: fraction of top-5 chunks that are "relevant" (expected routine or type)
  - Routine recall: do expected routines appear in top-5?
  - Doc-type hit rate: does at least 1 expected chunk type appear?
  - Answer faithfulness: do must_contain substrings appear in the answer?
  - Latency: end-to-end per query (cold + warm)
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
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

from tests.golden_queries import GOLDEN_QUERIES, GoldenQuery
from app.retrieval.router import route_query
from app.retrieval.search import retrieve_routed
from app.retrieval.context import assemble_context
from app.retrieval.generator import generate_answer


@dataclass
class QueryResult:
    query: str
    category: str
    # Router
    expected_intent: str
    actual_intent: str
    intent_correct: bool
    # Retrieval
    top5_routines: list[str]
    top5_types: list[str]
    top5_scores: list[float]
    routine_recall: float        # fraction of expected_routines found in top-5
    type_hit: bool               # at least 1 expected type in top-5
    precision_at_5: float        # fraction of top-5 matching expected routines/types
    # Answer
    answer_snippet: str
    faithfulness: float          # fraction of must_contain found in answer
    missing_terms: list[str]
    # Performance
    retrieval_latency_ms: float
    total_latency_ms: float
    cached: bool
    tokens_used: int


def evaluate_query(gq: GoldenQuery, generate: bool = True) -> QueryResult:
    """Run a single golden query through the full pipeline."""

    # Route
    routed = route_query(gq.query)
    intent_correct = routed.intent.name == gq.expected_intent

    # Retrieve
    t0 = time.time()
    chunks = retrieve_routed(routed, top_k=5)
    t_retrieval = time.time()
    retrieval_ms = (t_retrieval - t0) * 1000

    top5_routines = [c.metadata.get("routine_name", "?") for c in chunks]
    top5_types = [c.metadata.get("chunk_type", "?") for c in chunks]
    top5_scores = [round(c.score, 3) for c in chunks]

    # Routine recall
    if gq.expected_routines:
        found = sum(1 for r in gq.expected_routines if r in top5_routines)
        routine_recall = found / len(gq.expected_routines)
    else:
        routine_recall = 1.0  # no expectation = pass

    # Type hit
    type_hit = any(t in top5_types for t in gq.expected_types) if gq.expected_types else True

    # Precision@5: count chunks that match either expected routines or expected types
    relevant = 0
    for i, c in enumerate(chunks):
        rname = c.metadata.get("routine_name", "")
        ctype = c.metadata.get("chunk_type", "")
        if rname in gq.expected_routines or ctype in gq.expected_types:
            relevant += 1
    precision = relevant / max(len(chunks), 1)

    # Generate answer
    answer_text = ""
    cached = False
    tokens = 0
    total_ms = retrieval_ms

    if generate and chunks:
        context = assemble_context(chunks)
        answer = generate_answer(gq.query, context)
        t_done = time.time()
        total_ms = (t_done - t0) * 1000
        answer_text = answer.answer
        cached = answer.cached
        tokens = answer.usage.get("total_tokens", 0)

    # Faithfulness
    if gq.must_contain:
        answer_lower = answer_text.lower()
        found_terms = sum(1 for t in gq.must_contain if t.lower() in answer_lower)
        faithfulness = found_terms / len(gq.must_contain)
        missing = [t for t in gq.must_contain if t.lower() not in answer_lower]
    else:
        faithfulness = 1.0
        missing = []

    return QueryResult(
        query=gq.query,
        category=gq.category,
        expected_intent=gq.expected_intent,
        actual_intent=routed.intent.name,
        intent_correct=intent_correct,
        top5_routines=top5_routines,
        top5_types=top5_types,
        top5_scores=top5_scores,
        routine_recall=routine_recall,
        type_hit=type_hit,
        precision_at_5=precision,
        answer_snippet=answer_text[:300],
        faithfulness=faithfulness,
        missing_terms=missing,
        retrieval_latency_ms=retrieval_ms,
        total_latency_ms=total_ms,
        cached=cached,
        tokens_used=tokens,
    )


def run_eval(generate: bool = True, verbose: bool = True) -> list[QueryResult]:
    """Run all golden queries and print results."""
    results = []

    print(f"\n{'='*80}")
    print(f"LEGACYLENS EVALUATION — {len(GOLDEN_QUERIES)} golden queries")
    print(f"{'='*80}\n")

    for i, gq in enumerate(GOLDEN_QUERIES):
        if verbose:
            print(f"[{i+1:2d}/{len(GOLDEN_QUERIES)}] {gq.query[:70]}")
        try:
            result = evaluate_query(gq, generate=generate)
            results.append(result)
            if verbose:
                intent_mark = "✅" if result.intent_correct else "❌"
                recall_mark = "✅" if result.routine_recall >= 1.0 else ("⚠️" if result.routine_recall > 0 else "❌")
                type_mark = "✅" if result.type_hit else "❌"
                faith_mark = "✅" if result.faithfulness >= 1.0 else ("⚠️" if result.faithfulness > 0 else "❌")
                print(f"       Intent: {intent_mark} {result.actual_intent:12s} | "
                      f"Recall: {recall_mark} {result.routine_recall:.0%} | "
                      f"Type: {type_mark} | "
                      f"Faith: {faith_mark} {result.faithfulness:.0%} | "
                      f"P@5: {result.precision_at_5:.0%} | "
                      f"Latency: {result.total_latency_ms:.0f}ms")
                if result.missing_terms:
                    print(f"       Missing: {result.missing_terms}")
        except Exception as e:
            print(f"       ❌ ERROR: {e}")

    # ── Summary ──────────────────────────────────────────────────
    print(f"\n{'='*80}")
    print("SUMMARY")
    print(f"{'='*80}\n")

    n = len(results)
    if n == 0:
        print("No results.")
        return results

    intent_acc = sum(r.intent_correct for r in results) / n
    avg_recall = sum(r.routine_recall for r in results) / n
    type_hit_rate = sum(r.type_hit for r in results) / n
    avg_precision = sum(r.precision_at_5 for r in results) / n
    avg_faith = sum(r.faithfulness for r in results) / n
    avg_latency = sum(r.total_latency_ms for r in results) / n
    avg_retrieval = sum(r.retrieval_latency_ms for r in results) / n
    total_tokens = sum(r.tokens_used for r in results)
    cached_count = sum(r.cached for r in results)

    print(f"  Router accuracy:     {intent_acc:.0%} ({sum(r.intent_correct for r in results)}/{n})")
    print(f"  Routine recall:      {avg_recall:.0%}")
    print(f"  Doc-type hit rate:   {type_hit_rate:.0%}")
    print(f"  Precision@5:         {avg_precision:.0%}")
    print(f"  Answer faithfulness: {avg_faith:.0%}")
    print(f"  Avg retrieval:       {avg_retrieval:.0f}ms")
    print(f"  Avg total latency:   {avg_latency:.0f}ms")
    print(f"  Cached answers:      {cached_count}/{n}")
    print(f"  Total tokens:        {total_tokens:,}")
    print(f"  Est. cost:           ${total_tokens * 0.15 / 1_000_000:.4f}")

    # Per-category breakdown
    categories = sorted(set(r.category for r in results))
    print(f"\n  Per-category:")
    for cat in categories:
        cat_results = [r for r in results if r.category == cat]
        cat_n = len(cat_results)
        cat_intent = sum(r.intent_correct for r in cat_results) / cat_n
        cat_recall = sum(r.routine_recall for r in cat_results) / cat_n
        cat_faith = sum(r.faithfulness for r in cat_results) / cat_n
        print(f"    {cat:15s}: intent={cat_intent:.0%} recall={cat_recall:.0%} faith={cat_faith:.0%} (n={cat_n})")

    # Failure analysis
    failures = [r for r in results if not r.intent_correct or r.routine_recall < 1.0 or r.faithfulness < 1.0]
    if failures:
        print(f"\n  Failure cases ({len(failures)}):")
        for r in failures:
            issues = []
            if not r.intent_correct:
                issues.append(f"intent={r.actual_intent} (expected {r.expected_intent})")
            if r.routine_recall < 1.0:
                issues.append(f"recall={r.routine_recall:.0%}")
            if r.faithfulness < 1.0:
                issues.append(f"faith={r.faithfulness:.0%} missing={r.missing_terms}")
            print(f"    Q: {r.query[:60]}")
            print(f"       {', '.join(issues)}")

    # Save results to JSON
    out_path = Path("data/eval_results.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(
        [{
            "query": r.query,
            "category": r.category,
            "intent_expected": r.expected_intent,
            "intent_actual": r.actual_intent,
            "intent_correct": r.intent_correct,
            "top5_routines": r.top5_routines,
            "top5_types": r.top5_types,
            "top5_scores": r.top5_scores,
            "routine_recall": r.routine_recall,
            "type_hit": r.type_hit,
            "precision_at_5": r.precision_at_5,
            "faithfulness": r.faithfulness,
            "missing_terms": r.missing_terms,
            "retrieval_ms": round(r.retrieval_latency_ms),
            "total_ms": round(r.total_latency_ms),
            "cached": r.cached,
            "tokens": r.tokens_used,
        } for r in results],
        indent=2,
    ))
    print(f"\n  Results saved to {out_path}")

    return results


if __name__ == "__main__":
    gen = "--no-generate" not in sys.argv
    verbose = "--quiet" not in sys.argv
    run_eval(generate=gen, verbose=verbose)
