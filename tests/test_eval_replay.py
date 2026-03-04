"""Tier 1.5 — Replay eval tests ($0, no API calls).

Replays previously recorded LLM sessions to validate answer-level
assertions without calling OpenAI. Catches assertion tightening
(e.g., adding a new mustNotIncludeAny) against real recorded answers.

Run:
    pytest tests/test_eval_replay.py -v

Recording sessions:
    EVAL_RECORD=1 python tests/eval_harness.py

What this catches:
  - Assertion tightening: new mustNotIncludeAny violates recorded answer
  - Faithfulness regressions: must_contain expectations vs real LLM output
  - Prompt injection leaks: mustNotIncludeAny catches system prompt leaks

What this does NOT catch (use Tier 3 for these):
  - LLM behaviour drift (model weight updates)
  - System prompt changes (different answers)
  - New eval cases (no recorded session exists yet)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from tests.eval_schema import load_eval_cases, EvalCase
from tests.eval_assert import assert_answer_invariants, assert_retrieval_invariants

# ── Discover recorded sessions ───────────────────────────────────────

RECORDED_DIR = Path(__file__).parent / "fixtures" / "recorded"


def _get_recorded_ids() -> set[str]:
    """Discover which eval cases have a recorded session on disk."""
    if not RECORDED_DIR.exists():
        return set()
    return {
        p.stem for p in RECORDED_DIR.glob("*.json")
    }


def _load_session(case_id: str) -> dict:
    """Load a recorded session."""
    return json.loads((RECORDED_DIR / f"{case_id}.json").read_text())


ALL_CASES = load_eval_cases(stage="golden")
RECORDED_IDS = _get_recorded_ids()
REPLAY_CASES = [c for c in ALL_CASES if c.id in RECORDED_IDS]
SKIPPED_CASES = [c for c in ALL_CASES if c.id not in RECORDED_IDS]


# ── Tests ────────────────────────────────────────────────────────────


def _replay_ids() -> list[str]:
    return [c.id for c in REPLAY_CASES]


def _case_by_id(case_id: str) -> EvalCase:
    return next(c for c in ALL_CASES if c.id == case_id)


@pytest.mark.skipif(
    len(REPLAY_CASES) == 0,
    reason="No recorded sessions found in tests/fixtures/recorded/",
)
class TestReplay:
    """Replay recorded LLM sessions against current assertions."""

    @pytest.mark.parametrize("case_id", _replay_ids())
    def test_replay(self, case_id: str) -> None:
        case = _case_by_id(case_id)
        session = _load_session(case_id)

        # Validate retrieval-tier assertions against recorded data
        assert_retrieval_invariants(
            case,
            actual_intent=session["actual_intent"],
            top_k_routines=session["top5_routines"],
            top_k_types=session["top5_types"],
        )

        # Validate answer-tier assertions against recorded LLM answer
        if session.get("answer"):
            assert_answer_invariants(case, session["answer"])


class TestReplayCoverage:
    """Report on replay coverage."""

    def test_replay_coverage_report(self):
        """At least report how many cases have recordings."""
        total = len(ALL_CASES)
        recorded = len(REPLAY_CASES)
        skipped = len(SKIPPED_CASES)

        print(f"\n[Replay] {recorded}/{total} cases have recordings, {skipped} skipped")

        if skipped > 0 and SKIPPED_CASES:
            print(f"  Missing recordings for: {[c.id for c in SKIPPED_CASES[:10]]}")

        # Don't fail — just informational
        assert True
