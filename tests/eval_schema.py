"""Eval case schema: typed definitions + runtime validation.

Mirrors AgentForge's eval-case.schema.ts approach — every eval case
is validated at load time so typos in intents/categories/subcategories
fail immediately, not during a $0.15 eval run.

Usage:
    from tests.eval_schema import load_eval_cases
    cases = load_eval_cases()  # validates + returns typed list
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

# ── Allowed values ───────────────────────────────────────────────────

VALID_INTENTS = {"DEPENDENCY", "IMPACT", "EXPLAIN", "PATTERN", "SEMANTIC"}

VALID_CATEGORIES = {
    "single-routine",
    "multi-routine",
    "conceptual",
    "adversarial",
    "edge-case",
}

VALID_SUBCATEGORIES = {
    "dependency",
    "impact",
    "explain",
    "pattern",
    "semantic",
    "entry-point",
    "edge-case",
    "out-of-scope",
    "prompt-injection",
    "malformed-query",
}

VALID_DIFFICULTIES = {"basic", "intermediate", "advanced"}
VALID_STAGES = {"golden", "labeled"}

# ── Typed structures ─────────────────────────────────────────────────


@dataclass
class EvalExpect:
    intent: str
    routines: list[str]
    chunkTypes: list[str]
    mustIncludeAny: list[str]
    mustNotIncludeAny: list[str]
    minRoutineRecall: float = 1.0
    minFaithfulness: float = 1.0
    mustNotCallLLMTools: bool = False


@dataclass
class EvalMeta:
    category: str
    subcategory: str
    difficulty: str
    description: str


@dataclass
class EvalCase:
    id: str
    query: str
    profile: str
    liveEligible: bool
    stage: str
    expect: EvalExpect
    meta: EvalMeta


# ── Validator ────────────────────────────────────────────────────────


class EvalValidationError(Exception):
    """Raised when an eval case fails schema validation."""


def _validate_case(raw: dict, index: int) -> EvalCase:
    """Validate a single eval case dict and return a typed EvalCase."""
    prefix = f"EvalCase[{index}]"

    # Required top-level fields
    for field_name in ("id", "query", "profile", "liveEligible", "stage", "expect", "meta"):
        if field_name not in raw:
            raise EvalValidationError(f"{prefix}: missing required field '{field_name}'")

    case_id = raw["id"]
    if not isinstance(case_id, str) or not case_id:
        raise EvalValidationError(f"{prefix}.id must be a non-empty string")

    # id must be kebab-case
    import re
    if not re.match(r"^[a-z0-9-]+$", case_id):
        raise EvalValidationError(
            f"{prefix}.id must be kebab-case, got '{case_id}'"
        )

    # stage
    if raw["stage"] not in VALID_STAGES:
        raise EvalValidationError(
            f"{prefix}.stage must be one of {VALID_STAGES}, got '{raw['stage']}'"
        )

    # expect
    exp = raw["expect"]
    if exp["intent"] not in VALID_INTENTS:
        raise EvalValidationError(
            f"{prefix}.expect.intent must be one of {VALID_INTENTS}, got '{exp['intent']}'"
        )

    for list_field in ("routines", "chunkTypes", "mustIncludeAny", "mustNotIncludeAny"):
        if not isinstance(exp.get(list_field, []), list):
            raise EvalValidationError(
                f"{prefix}.expect.{list_field} must be a list"
            )

    # meta
    meta = raw["meta"]
    if meta["category"] not in VALID_CATEGORIES:
        raise EvalValidationError(
            f"{prefix}.meta.category must be one of {VALID_CATEGORIES}, got '{meta['category']}'"
        )
    if meta["subcategory"] not in VALID_SUBCATEGORIES:
        raise EvalValidationError(
            f"{prefix}.meta.subcategory must be one of {VALID_SUBCATEGORIES}, "
            f"got '{meta['subcategory']}'"
        )
    if meta["difficulty"] not in VALID_DIFFICULTIES:
        raise EvalValidationError(
            f"{prefix}.meta.difficulty must be one of {VALID_DIFFICULTIES}, "
            f"got '{meta['difficulty']}'"
        )

    return EvalCase(
        id=raw["id"],
        query=raw["query"],
        profile=raw["profile"],
        liveEligible=raw["liveEligible"],
        stage=raw["stage"],
        expect=EvalExpect(
            intent=exp["intent"],
            routines=exp.get("routines", []),
            chunkTypes=exp.get("chunkTypes", []),
            mustIncludeAny=exp.get("mustIncludeAny", []),
            mustNotIncludeAny=exp.get("mustNotIncludeAny", []),
            minRoutineRecall=exp.get("minRoutineRecall", 1.0),
            minFaithfulness=exp.get("minFaithfulness", 1.0),
            mustNotCallLLMTools=exp.get("mustNotCallLLMTools", False),
        ),
        meta=EvalMeta(
            category=meta["category"],
            subcategory=meta["subcategory"],
            difficulty=meta["difficulty"],
            description=meta["description"],
        ),
    )


def load_eval_cases(
    path: Path | None = None,
    stage: str | None = None,
) -> list[EvalCase]:
    """Load and validate all eval cases from JSON.

    Args:
        path: Path to eval_cases.json (defaults to tests/eval_cases.json).
        stage: Filter to 'golden' or 'labeled' (None = all).

    Returns:
        Validated list of EvalCase objects.

    Raises:
        EvalValidationError: If any case fails schema validation.
    """
    if path is None:
        path = Path(__file__).parent / "eval_cases.json"

    raw_cases = json.loads(path.read_text())

    if not isinstance(raw_cases, list) or len(raw_cases) == 0:
        raise EvalValidationError("eval_cases.json must be a non-empty array")

    # Check for duplicate IDs
    seen_ids: set[str] = set()
    cases: list[EvalCase] = []

    for i, raw in enumerate(raw_cases):
        case = _validate_case(raw, i)

        if case.id in seen_ids:
            raise EvalValidationError(f"Duplicate eval case id: '{case.id}' at index {i}")
        seen_ids.add(case.id)

        if stage is None or case.stage == stage:
            cases.append(case)

    return cases
