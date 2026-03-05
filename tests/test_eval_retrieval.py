"""Tier 2 — Retrieval-only eval tests.

Validates router intent + Pinecone retrieval quality for all eval cases.
Costs ~$0.01/run (embedding calls only, no LLM completions).

Run:
    pytest tests/test_eval_retrieval.py -v

Requires: OPENAI_API_KEY + PINECONE_API_KEY in .env (for embeddings + index).
Skip condition: missing API keys → entire module skipped.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Ensure imports work
sys.path.insert(0, str(Path(__file__).parent.parent))

# Load .env
_env_path = Path(__file__).parent.parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

# Skip entire module if no API keys
_has_keys = bool(os.environ.get("OPENAI_API_KEY")) and bool(
    os.environ.get("PINECONE_API_KEY")
)
pytestmark = pytest.mark.skipif(
    not _has_keys,
    reason="OPENAI_API_KEY and PINECONE_API_KEY required for retrieval evals",
)

from tests.eval_schema import load_eval_cases, EvalCase
from tests.eval_assert import (
    assert_intent,
    assert_routine_recall,
    assert_type_hit,
    EvalResult,
)
from app.retrieval.router import route_query
from app.retrieval.search import retrieve_routed


# ── Load cases ───────────────────────────────────────────────────────

ALL_CASES = load_eval_cases(stage="golden")

# ── Pass-rate thresholds (AgentForge-style) ──────────────────────────

CATEGORY_THRESHOLDS = {
    "single-routine": 1.0,   # deterministic retrieval
    "multi-routine": 1.0,
    "conceptual": 0.8,       # broader queries, may miss niche chunk types
    "adversarial": 1.0,      # intent must be correct even for junk
    "edge-case": 0.8,
}
OVERALL_THRESHOLD = 0.90

# ── Collect results for threshold enforcement ────────────────────────

_results: list[EvalResult] = []


# ── Per-case parametric tests ────────────────────────────────────────


def _case_ids() -> list[str]:
    return [c.id for c in ALL_CASES]


def _case_by_id(case_id: str) -> EvalCase:
    return next(c for c in ALL_CASES if c.id == case_id)


@pytest.fixture(scope="module", autouse=True)
def _enforce_thresholds():
    """After all tests, enforce pass-rate thresholds."""
    yield

    if not _results:
        return

    passed = sum(1 for r in _results if r.passed)
    total = len(_results)

    # Per-category
    by_cat: dict[str, list[EvalResult]] = {}
    for r in _results:
        by_cat.setdefault(r.category, []).append(r)

    violations: list[str] = []

    print(f"\n{'='*64}")
    print("RETRIEVAL EVAL RESULTS (Tier 2 — Pinecone, no LLM)")
    print(f"{'='*64}")

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
            violations.append(
                f"{cat}: {rate:.0%} < {threshold:.0%} ({cat_passed}/{cat_total})"
            )

    overall_rate = passed / total
    print(f"{'─'*64}")
    print(f"  Overall:  {passed}/{total} ({overall_rate:.0%}, gate: {OVERALL_THRESHOLD:.0%})")

    if overall_rate < OVERALL_THRESHOLD:
        violations.append(
            f"overall: {overall_rate:.0%} < {OVERALL_THRESHOLD:.0%} ({passed}/{total})"
        )

    failures = [r for r in _results if not r.passed]
    if failures:
        print(f"\n  Failures ({len(failures)}):")
        for r in failures:
            print(f"    ✗ {r.case_id}: {r.error[:120]}")

    print(f"{'='*64}\n")

    if violations:
        pytest.fail(
            f"Retrieval eval pass-rate below threshold:\n  "
            + "\n  ".join(violations)
        )


@pytest.mark.parametrize("case_id", _case_ids())
def test_retrieval(case_id: str) -> None:
    """Validate router intent + retrieval quality for a single eval case."""
    case = _case_by_id(case_id)

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

        # Retrieve
        chunks = retrieve_routed(routed, top_k=5)
        result.top5_routines = [c.metadata.get("routine_name", "?") for c in chunks]
        result.top5_types = [c.metadata.get("chunk_type", "?") for c in chunks]
        result.top5_scores = [round(c.score, 3) for c in chunks]

        # Assert intent
        result.intent_correct = routed.intent.name == case.expect.intent
        assert_intent(routed.intent.name, case.expect.intent, case.query)

        # Guardrail eval: blocked queries should produce no retrieval output.
        if case.expect.mustNotCallLLMTools:
            assert result.top5_routines == [], (
                f"mustNotCallLLMTools violated for '{case.id}': "
                f"got retrieved routines {result.top5_routines}"
            )
            assert result.top5_types == [], (
                f"mustNotCallLLMTools violated for '{case.id}': "
                f"got retrieved chunk types {result.top5_types}"
            )

        # Assert routine recall
        result.routine_recall = assert_routine_recall(
            result.top5_routines,
            case.expect.routines,
            min_recall=case.expect.minRoutineRecall,
            query=case.query,
        )

        # Assert type hit
        result.type_hit = assert_type_hit(
            result.top5_types,
            case.expect.chunkTypes,
            query=case.query,
        )

        result.passed = True

    except (AssertionError, Exception) as e:
        result.error = str(e)
        result.passed = False
        # Don't raise — we collect results and enforce thresholds in afterAll
    finally:
        _results.append(result)

    # Individual test still reports pass/fail for visibility
    if not result.passed:
        pytest.fail(f"[{case.meta.category}] {case.id}: {result.error}")
