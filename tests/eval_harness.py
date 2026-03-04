"""Tier 3 — Full pipeline evaluation harness.

Runs all golden eval cases through router → Pinecone → LLM.
Costs ~$0.15/run. Use for nightly CI or manual quality checks.

Features:
  - Uses shared eval_schema.py + eval_assert.py (no duplicated logic)
  - Pass-rate thresholds per category (AgentForge-style)
  - Session recording (EVAL_RECORD=1) for the replay tier
  - JSON results output to data/eval_results.json

Usage:
    python tests/eval_harness.py                    # full run
    python tests/eval_harness.py --no-generate      # retrieval-only
    python tests/eval_harness.py --quiet             # minimal output
    EVAL_RECORD=1 python tests/eval_harness.py      # record sessions for replay
"""

from __future__ import annotations

import json
import os
import sys
import time
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
    assert_intent,
    assert_routine_recall,
    assert_type_hit,
    assert_faithfulness,
    assert_no_hallucination,
    assert_must_include_any,
    EvalResult,
)
from app.retrieval.router import route_query
from app.retrieval.search import retrieve_routed
from app.retrieval.context import assemble_context
from app.retrieval.generator import generate_answer


# ── Pass-rate thresholds ─────────────────────────────────────────────

CATEGORY_THRESHOLDS = {
    "single-routine": 1.0,   # deterministic retrieval
    "multi-routine": 1.0,
    "conceptual": 0.7,       # broader queries, LLM variance
    "adversarial": 1.0,      # must always classify correctly
    "edge-case": 0.5,        # bare names, weird inputs
}
OVERALL_THRESHOLD = 0.80

# ── Recording ────────────────────────────────────────────────────────

EVAL_RECORD = os.environ.get("EVAL_RECORD") == "1"
RECORDED_DIR = Path(__file__).parent / "fixtures" / "recorded"


def _save_session(case: EvalCase, result: EvalResult, answer: str) -> None:
    """Persist a session to disk for the replay tier."""
    RECORDED_DIR.mkdir(parents=True, exist_ok=True)
    session = {
        "caseId": case.id,
        "query": case.query,
        "actual_intent": result.actual_intent,
        "top5_routines": result.top5_routines,
        "top5_types": result.top5_types,
        "top5_scores": result.top5_scores,
        "answer": answer,
        "faithfulness": result.faithfulness,
        "routine_recall": result.routine_recall,
        "type_hit": result.type_hit,
        "latency_ms": round(result.latency_ms),
        "tokens_used": result.tokens_used,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    out_path = RECORDED_DIR / f"{case.id}.json"
    out_path.write_text(json.dumps(session, indent=2))


# ── Single case evaluation ───────────────────────────────────────────


def evaluate_case(case: EvalCase, generate: bool = True) -> EvalResult:
    """Run a single eval case through the full pipeline."""
    result = EvalResult(
        case_id=case.id,
        query=case.query,
        category=case.meta.category,
        subcategory=case.meta.subcategory,
    )

    try:
        # Route
        routed = route_query(case.query)
        result.actual_intent = routed.intent.name
        result.intent_correct = routed.intent.name == case.expect.intent

        # Retrieve
        t0 = time.time()
        chunks = retrieve_routed(routed, top_k=5)
        retrieval_ms = (time.time() - t0) * 1000

        result.top5_routines = [c.metadata.get("routine_name", "?") for c in chunks]
        result.top5_types = [c.metadata.get("chunk_type", "?") for c in chunks]
        result.top5_scores = [round(c.score, 3) for c in chunks]

        # Retrieval assertions
        assert_intent(routed.intent.name, case.expect.intent, case.query)

        result.routine_recall = assert_routine_recall(
            result.top5_routines,
            case.expect.routines,
            min_recall=case.expect.minRoutineRecall,
            query=case.query,
        )

        result.type_hit = assert_type_hit(
            result.top5_types,
            case.expect.chunkTypes,
            query=case.query,
        )

        # Generate answer
        answer_text = ""
        if generate and chunks:
            context = assemble_context(chunks)
            answer = generate_answer(case.query, context)
            answer_text = answer.answer
            result.tokens_used = answer.usage.get("total_tokens", 0)
            result.answer_snippet = answer_text[:300]
            result.latency_ms = (time.time() - t0) * 1000

            # Answer assertions
            result.faithfulness = assert_faithfulness(
                answer_text,
                case.expect.mustIncludeAny,
                min_score=case.expect.minFaithfulness,
                query=case.query,
            )
            assert_no_hallucination(
                answer_text,
                case.expect.mustNotIncludeAny,
                query=case.query,
            )
        else:
            result.latency_ms = retrieval_ms
            result.faithfulness = 1.0  # no answer to check

        result.passed = True

        # Record session if requested
        if EVAL_RECORD and result.passed:
            _save_session(case, result, answer_text)

    except (AssertionError, Exception) as e:
        result.error = str(e)
        result.passed = False

    return result


# ── Suite runner ─────────────────────────────────────────────────────


def run_eval(generate: bool = True, verbose: bool = True) -> list[EvalResult]:
    """Run all golden eval cases and enforce pass-rate thresholds."""
    cases = load_eval_cases(stage="golden")
    results: list[EvalResult] = []

    print(f"\n{'='*80}")
    print(f"LEGACYLENS EVALUATION — {len(cases)} eval cases (Tier 3 — full pipeline)")
    if EVAL_RECORD:
        print(f"  📼 RECORDING sessions to {RECORDED_DIR}")
    print(f"{'='*80}\n")

    for i, case in enumerate(cases):
        if verbose:
            print(f"[{i+1:2d}/{len(cases)}] {case.query[:70]}")

        result = evaluate_case(case, generate=generate)
        results.append(result)

        if verbose:
            intent_mark = "✅" if result.intent_correct else "❌"
            recall_mark = "✅" if result.routine_recall >= 1.0 else ("⚠️" if result.routine_recall > 0 else "❌")
            type_mark = "✅" if result.type_hit else "❌"
            faith_mark = "✅" if result.faithfulness >= 1.0 else ("⚠️" if result.faithfulness > 0 else "❌")
            status = "PASS" if result.passed else "FAIL"
            print(
                f"       [{status}] Intent: {intent_mark} {result.actual_intent:12s} | "
                f"Recall: {recall_mark} {result.routine_recall:.0%} | "
                f"Type: {type_mark} | "
                f"Faith: {faith_mark} {result.faithfulness:.0%} | "
                f"Latency: {result.latency_ms:.0f}ms"
            )
            if result.error:
                print(f"       ❌ {result.error[:120]}")

    # ── Summary ──────────────────────────────────────────────────
    n = len(results)
    if n == 0:
        print("No results.")
        return results

    passed = sum(1 for r in results if r.passed)

    print(f"\n{'='*80}")
    print("PASS-RATE REPORT")
    print(f"{'='*80}\n")

    # Per-category breakdown
    by_cat: dict[str, list[EvalResult]] = {}
    for r in results:
        by_cat.setdefault(r.category, []).append(r)

    violations: list[str] = []

    for cat in sorted(by_cat):
        cat_results = by_cat[cat]
        cat_passed = sum(1 for r in cat_results if r.passed)
        cat_total = len(cat_results)
        rate = cat_passed / cat_total
        threshold = CATEGORY_THRESHOLDS.get(cat)
        threshold_str = f" (gate: {threshold:.0%})" if threshold else ""
        bar = "█" * round(rate * 20) + "░" * (20 - round(rate * 20))
        print(f"  {cat:18s} {cat_passed:2d}/{cat_total}  ({rate:.0%})  {bar}{threshold_str}")

        if threshold and rate < threshold:
            violations.append(f"{cat}: {rate:.0%} < {threshold:.0%} ({cat_passed}/{cat_total})")

    overall_rate = passed / n
    print(f"{'─'*80}")
    print(f"  Overall:  {passed}/{n} ({overall_rate:.0%}, gate: {OVERALL_THRESHOLD:.0%})")

    # Metrics
    avg_latency = sum(r.latency_ms for r in results) / n
    total_tokens = sum(r.tokens_used for r in results)
    print(f"  Avg latency:   {avg_latency:.0f}ms")
    print(f"  Total tokens:  {total_tokens:,}")
    print(f"  Est. cost:     ${total_tokens * 0.15 / 1_000_000:.4f}")

    if overall_rate < OVERALL_THRESHOLD:
        violations.append(f"overall: {overall_rate:.0%} < {OVERALL_THRESHOLD:.0%} ({passed}/{n})")

    # Failure details
    failures = [r for r in results if not r.passed]
    if failures:
        print(f"\n  Failures ({len(failures)}):")
        for r in failures:
            print(f"    ✗ [{r.category}] {r.case_id}: {r.error[:120]}")

    # Save results to JSON
    out_path = Path("data/eval_results.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(
        [{
            "case_id": r.case_id,
            "query": r.query,
            "category": r.category,
            "subcategory": r.subcategory,
            "intent_expected": next(c.expect.intent for c in cases if c.id == r.case_id),
            "intent_actual": r.actual_intent,
            "intent_correct": r.intent_correct,
            "top5_routines": r.top5_routines,
            "top5_types": r.top5_types,
            "top5_scores": r.top5_scores,
            "routine_recall": r.routine_recall,
            "type_hit": r.type_hit,
            "faithfulness": r.faithfulness,
            "passed": r.passed,
            "error": r.error,
            "latency_ms": round(r.latency_ms),
            "tokens": r.tokens_used,
        } for r in results],
        indent=2,
    ))
    print(f"\n  Results saved to {out_path}")

    if EVAL_RECORD:
        recorded = sum(1 for r in results if r.passed)
        print(f"  📼 Recorded {recorded} sessions to {RECORDED_DIR}")

    print(f"{'='*80}\n")

    # Exit with error if thresholds violated
    if violations:
        print("❌ THRESHOLD VIOLATIONS:")
        for v in violations:
            print(f"  • {v}")
        return results  # caller can check sys.exit

    print("✅ All pass-rate thresholds met.")
    return results


if __name__ == "__main__":
    gen = "--no-generate" not in sys.argv
    verbose = "--quiet" not in sys.argv
    results = run_eval(generate=gen, verbose=verbose)

    # CI gate: exit non-zero if thresholds violated
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    if total > 0:
        overall_rate = passed / total
        if overall_rate < OVERALL_THRESHOLD:
            sys.exit(1)

        by_cat: dict[str, list[EvalResult]] = {}
        for r in results:
            by_cat.setdefault(r.category, []).append(r)

        for cat, cat_results in by_cat.items():
            threshold = CATEGORY_THRESHOLDS.get(cat)
            if threshold:
                rate = sum(1 for r in cat_results if r.passed) / len(cat_results)
                if rate < threshold:
                    sys.exit(1)
