#!/usr/bin/env python3
"""Eval coverage matrix reporter.

Reads eval_cases.json and prints a markdown table showing coverage
by subcategory, category, and difficulty — plus gaps.

Modeled after AgentForge's coverage-matrix.ts.

Usage:
    python tests/eval_coverage.py                  # markdown to stdout
    python tests/eval_coverage.py --json            # json to stdout
    python tests/eval_coverage.py > EVAL_COVERAGE.md  # save to file
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tests.eval_schema import load_eval_cases, VALID_SUBCATEGORIES

# ── Load cases ───────────────────────────────────────────────────────

cases = load_eval_cases()

# ── Build coverage data ──────────────────────────────────────────────

CATEGORY_EMOJI = {
    "single-routine": "🔧",
    "multi-routine": "🔗",
    "conceptual": "💡",
    "adversarial": "🛡️ ",
    "edge-case": "⚠️ ",
}

DIFFICULTY_BADGE = {
    "basic": "🟢 basic",
    "intermediate": "🟡 intermediate",
    "advanced": "🔴 advanced",
}


def build_coverage():
    by_sub: dict[str, dict] = {}

    for c in cases:
        sub = c.meta.subcategory
        if sub not in by_sub:
            by_sub[sub] = {
                "subcategory": sub,
                "category": c.meta.category,
                "difficulty": c.meta.difficulty,
                "golden": 0,
                "labeled": 0,
                "live_eligible": 0,
                "total": 0,
            }

        entry = by_sub[sub]
        entry["total"] += 1
        if c.stage == "golden":
            entry["golden"] += 1
        else:
            entry["labeled"] += 1
        if c.liveEligible:
            entry["live_eligible"] += 1

        # Track highest difficulty
        rank = {"basic": 1, "intermediate": 2, "advanced": 3}
        if rank.get(c.meta.difficulty, 0) > rank.get(entry["difficulty"], 0):
            entry["difficulty"] = c.meta.difficulty

    return sorted(by_sub.values(), key=lambda e: e["subcategory"])


def to_markdown(entries):
    lines = []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines.append("# LegacyLens Eval Coverage Matrix\n")
    lines.append(f"> Generated: {now}\n")

    # Summary
    golden = [c for c in cases if c.stage == "golden"]
    labeled = [c for c in cases if c.stage == "labeled"]
    live = [c for c in cases if c.liveEligible]

    lines.append("## Summary\n")
    lines.append("| Tier | Cases | Live-eligible |")
    lines.append("|------|------:|--------------:|")
    lines.append(f"| Golden (every commit) | {len(golden)} | {sum(1 for c in golden if c.liveEligible)} |")
    lines.append(f"| Labeled (nightly) | {len(labeled)} | {sum(1 for c in labeled if c.liveEligible)} |")
    lines.append(f"| **Total** | **{len(cases)}** | **{len(live)}** |")
    lines.append("")

    # Coverage by subcategory
    lines.append("## Coverage by Subcategory\n")
    lines.append("| Subcategory | Category | Golden | Labeled | Live | Total | Difficulty |")
    lines.append("|-------------|----------|-------:|--------:|-----:|------:|------------|")

    for e in entries:
        cat_emoji = CATEGORY_EMOJI.get(e["category"], "")
        diff_badge = DIFFICULTY_BADGE.get(e["difficulty"], e["difficulty"])
        lines.append(
            f"| {e['subcategory']} "
            f"| {cat_emoji} {e['category']} "
            f"| {e['golden']} "
            f"| {e['labeled']} "
            f"| {e['live_eligible']} "
            f"| {e['total']} "
            f"| {diff_badge} |"
        )
    lines.append("")

    # By category
    by_cat: dict[str, dict] = {}
    for e in entries:
        cat = e["category"]
        if cat not in by_cat:
            by_cat[cat] = {"total": 0, "live": 0}
        by_cat[cat]["total"] += e["total"]
        by_cat[cat]["live"] += e["live_eligible"]

    lines.append("## By Category\n")
    lines.append("| Category | Total | Live |")
    lines.append("|----------|------:|-----:|")
    for cat in sorted(by_cat):
        emoji = CATEGORY_EMOJI.get(cat, "")
        lines.append(f"| {emoji} {cat} | {by_cat[cat]['total']} | {by_cat[cat]['live']} |")
    lines.append("")

    # Gaps
    covered = {e["subcategory"] for e in entries}
    missing = sorted(VALID_SUBCATEGORIES - covered)

    lines.append("## Coverage Gaps\n")
    if missing:
        for sub in missing:
            lines.append(f"- ❌ **{sub}** — no eval cases yet")
    else:
        lines.append("✅ All subcategories have at least 1 eval case.")
    lines.append("")

    # Replay coverage
    recorded_dir = Path(__file__).parent / "fixtures" / "recorded"
    if recorded_dir.exists():
        recorded_ids = {p.stem for p in recorded_dir.glob("*.json")}
        all_ids = {c.id for c in cases}
        recorded_count = len(recorded_ids & all_ids)
        lines.append("## Replay Coverage\n")
        lines.append(f"- Recorded sessions: {recorded_count}/{len(all_ids)}")
        missing_recordings = sorted(all_ids - recorded_ids)
        if missing_recordings:
            lines.append(f"- Missing: {', '.join(missing_recordings[:10])}")
            if len(missing_recordings) > 10:
                lines.append(f"  ... and {len(missing_recordings) - 10} more")
        lines.append("")

    return "\n".join(lines)


def to_json(entries):
    return json.dumps(
        {
            "generated": datetime.now(timezone.utc).isoformat(),
            "summary": {
                "total": len(cases),
                "golden": sum(1 for c in cases if c.stage == "golden"),
                "labeled": sum(1 for c in cases if c.stage == "labeled"),
                "liveEligible": sum(1 for c in cases if c.liveEligible),
            },
            "entries": entries,
        },
        indent=2,
    )


# ── Main ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    entries = build_coverage()
    if "--json" in sys.argv:
        print(to_json(entries))
    else:
        print(to_markdown(entries))
