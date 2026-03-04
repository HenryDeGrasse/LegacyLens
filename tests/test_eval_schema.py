"""Tier 1 — Eval case schema validation (runs every commit, $0).

Validates that eval_cases.json is well-formed and all fields pass
schema checks. Catches typos in intents/categories before they reach
a live eval run.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.eval_schema import load_eval_cases, VALID_INTENTS, VALID_CATEGORIES, VALID_SUBCATEGORIES


EVAL_JSON = Path(__file__).parent / "eval_cases.json"


class TestEvalCaseSchema:
    """All eval cases must pass schema validation."""

    def test_load_succeeds(self):
        """eval_cases.json loads without validation errors."""
        cases = load_eval_cases()
        assert len(cases) > 0, "eval_cases.json must not be empty"

    def test_no_duplicate_ids(self):
        """Every case must have a unique ID."""
        raw = json.loads(EVAL_JSON.read_text())
        ids = [c["id"] for c in raw]
        assert len(ids) == len(set(ids)), f"Duplicate IDs: {[x for x in ids if ids.count(x) > 1]}"

    def test_all_ids_kebab_case(self):
        """Case IDs must be kebab-case (lowercase alphanumeric + hyphens)."""
        import re
        cases = load_eval_cases()
        for c in cases:
            assert re.match(r"^[a-z0-9-]+$", c.id), f"ID '{c.id}' is not kebab-case"

    def test_all_intents_valid(self):
        """Every case must reference a valid intent."""
        cases = load_eval_cases()
        for c in cases:
            assert c.expect.intent in VALID_INTENTS, (
                f"Case '{c.id}': invalid intent '{c.expect.intent}'"
            )

    def test_all_categories_valid(self):
        """Every case must reference a valid category."""
        cases = load_eval_cases()
        for c in cases:
            assert c.meta.category in VALID_CATEGORIES, (
                f"Case '{c.id}': invalid category '{c.meta.category}'"
            )

    def test_all_subcategories_valid(self):
        """Every case must reference a valid subcategory."""
        cases = load_eval_cases()
        for c in cases:
            assert c.meta.subcategory in VALID_SUBCATEGORIES, (
                f"Case '{c.id}': invalid subcategory '{c.meta.subcategory}'"
            )

    def test_golden_cases_have_queries(self):
        """Golden cases must have non-empty queries."""
        cases = load_eval_cases(stage="golden")
        for c in cases:
            assert c.query.strip(), f"Case '{c.id}' has empty query"

    def test_minimum_coverage(self):
        """Must have at least 20 golden eval cases."""
        cases = load_eval_cases(stage="golden")
        assert len(cases) >= 20, f"Only {len(cases)} golden cases — need ≥20"

    def test_has_adversarial_cases(self):
        """Must have at least 1 adversarial case."""
        cases = load_eval_cases()
        adversarial = [c for c in cases if c.meta.category == "adversarial"]
        assert len(adversarial) >= 1, "No adversarial eval cases found"

    def test_has_dependency_cases(self):
        """Must have at least 1 dependency case."""
        cases = load_eval_cases()
        dep = [c for c in cases if c.meta.subcategory == "dependency"]
        assert len(dep) >= 1, "No dependency eval cases found"

    def test_has_explain_cases(self):
        """Must have at least 1 explain case."""
        cases = load_eval_cases()
        explain = [c for c in cases if c.meta.subcategory == "explain"]
        assert len(explain) >= 1, "No explain eval cases found"
