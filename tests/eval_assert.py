"""Shared eval assertion helpers.

Reusable across all eval tiers (retrieval-only, replay, full pipeline).
Modeled after AgentForge's eval-assert.ts — each function checks one
concern and raises AssertionError with a clear message on failure.

Usage:
    from tests.eval_assert import (
        assert_intent, assert_routine_recall, assert_type_hit,
        assert_faithfulness, assert_no_hallucination, assert_eval_invariants,
    )
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tests.eval_schema import EvalCase


# ── Individual assertion helpers ─────────────────────────────────────


def assert_intent(actual_intent: str, expected_intent: str, query: str = "") -> None:
    """Router intent must match expected intent."""
    if actual_intent != expected_intent:
        raise AssertionError(
            f"Intent mismatch for '{query[:60]}': "
            f"got {actual_intent}, expected {expected_intent}"
        )


def assert_routine_recall(
    top_k_routines: list[str],
    expected_routines: list[str],
    min_recall: float = 1.0,
    query: str = "",
) -> float:
    """Fraction of expected routines found in top-K retrieval.

    Returns the recall score. Raises if below min_recall.
    """
    if not expected_routines:
        return 1.0  # no expectation = pass

    found = sum(1 for r in expected_routines if r in top_k_routines)
    recall = found / len(expected_routines)

    if recall < min_recall:
        missing = [r for r in expected_routines if r not in top_k_routines]
        raise AssertionError(
            f"Routine recall {recall:.0%} < {min_recall:.0%} for '{query[:60]}'. "
            f"Missing: {missing}. Got: {top_k_routines}"
        )
    return recall


def assert_type_hit(
    top_k_types: list[str],
    expected_types: list[str],
    query: str = "",
) -> bool:
    """At least one expected chunk type must appear in top-K results.

    Returns True if hit. Raises if no hit and expected_types is non-empty.
    """
    if not expected_types:
        return True  # no expectation = pass

    hit = any(t in top_k_types for t in expected_types)
    if not hit:
        raise AssertionError(
            f"Type miss for '{query[:60]}': "
            f"expected one of {expected_types}, got {top_k_types}"
        )
    return True


def assert_faithfulness(
    answer: str,
    must_contain: list[str],
    min_score: float = 1.0,
    query: str = "",
) -> float:
    """Fraction of must_contain terms found in the answer.

    Returns the score. Raises if below min_score.
    """
    if not must_contain:
        return 1.0

    answer_lower = answer.lower()
    found = sum(1 for t in must_contain if t.lower() in answer_lower)
    score = found / len(must_contain)

    if score < min_score:
        missing = [t for t in must_contain if t.lower() not in answer_lower]
        raise AssertionError(
            f"Faithfulness {score:.0%} < {min_score:.0%} for '{query[:60]}'. "
            f"Missing: {missing}"
        )
    return score


def assert_no_hallucination(
    answer: str,
    must_not_contain: list[str],
    query: str = "",
) -> None:
    """None of the forbidden phrases may appear in the answer."""
    if not must_not_contain:
        return

    answer_lower = answer.lower()
    for phrase in must_not_contain:
        if phrase.lower() in answer_lower:
            snippet = answer[:200] + "..." if len(answer) > 200 else answer
            raise AssertionError(
                f"Hallucination/leak for '{query[:60]}': "
                f"forbidden phrase '{phrase}' found in response: \"{snippet}\""
            )


def assert_must_include_any(
    answer: str,
    must_include_any: list[str],
    query: str = "",
) -> None:
    """At least one of the phrases must appear in the answer (OR logic)."""
    if not must_include_any:
        return

    answer_lower = answer.lower()
    found = any(phrase.lower() in answer_lower for phrase in must_include_any)

    if not found:
        snippet = answer[:200] + "..." if len(answer) > 200 else answer
        raise AssertionError(
            f"mustIncludeAny failed for '{query[:60]}': "
            f"none of {must_include_any} found in: \"{snippet}\""
        )


# ── Composite: run all assertions for an eval case ──────────────────


@dataclass
class EvalResult:
    """Result of running assertions for a single eval case."""
    case_id: str
    query: str
    category: str
    subcategory: str
    intent_correct: bool = False
    actual_intent: str = ""
    routine_recall: float = 0.0
    type_hit: bool = False
    faithfulness: float = 0.0
    passed: bool = False
    error: str = ""
    # Retrieval details
    top5_routines: list[str] = field(default_factory=list)
    top5_types: list[str] = field(default_factory=list)
    top5_scores: list[float] = field(default_factory=list)
    # Answer details (only for full-pipeline tier)
    answer_snippet: str = ""
    latency_ms: float = 0.0
    tokens_used: int = 0


def assert_retrieval_invariants(
    case: "EvalCase",
    actual_intent: str,
    top_k_routines: list[str],
    top_k_types: list[str],
) -> None:
    """Run all retrieval-tier assertions for an eval case.

    Raises AssertionError on the first failure.
    """
    assert_intent(actual_intent, case.expect.intent, case.query)
    assert_routine_recall(
        top_k_routines,
        case.expect.routines,
        min_recall=case.expect.minRoutineRecall,
        query=case.query,
    )
    assert_type_hit(top_k_types, case.expect.chunkTypes, case.query)


def assert_answer_invariants(
    case: "EvalCase",
    answer: str,
) -> None:
    """Run all answer-tier assertions for an eval case.

    Raises AssertionError on the first failure.
    """
    assert_faithfulness(
        answer,
        case.expect.mustIncludeAny,
        min_score=case.expect.minFaithfulness,
        query=case.query,
    )
    assert_no_hallucination(
        answer,
        case.expect.mustNotIncludeAny,
        query=case.query,
    )


def assert_eval_invariants(
    case: "EvalCase",
    actual_intent: str,
    top_k_routines: list[str],
    top_k_types: list[str],
    answer: str = "",
) -> None:
    """Run all assertions (retrieval + answer) for an eval case."""
    assert_retrieval_invariants(case, actual_intent, top_k_routines, top_k_types)
    if answer:
        assert_answer_invariants(case, answer)


# ── Metrics (AgentForge-style) ──────────────────────────────────────


def content_precision(must_contain: list[str], answer: str) -> float:
    """Fraction of must_contain keywords found in response.

    Returns 1.0 when must_contain is empty.
    """
    if not must_contain:
        return 1.0
    lower = answer.lower()
    found = sum(1 for k in must_contain if k.lower() in lower)
    return found / len(must_contain)
