"""Golden test set for LegacyLens evaluation.

Each query specifies:
  - expected_intent:   what the router should classify it as
  - expected_routines: routine names that MUST appear in top-5 retrieval
  - expected_types:    preferred chunk types (at least 1 should appear)
  - must_contain:      substrings that MUST appear in the LLM answer
  - category:          grouping for reporting
"""

from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class GoldenQuery:
    query: str
    expected_intent: str                          # DEPENDENCY, IMPACT, EXPLAIN, PATTERN, SEMANTIC
    expected_routines: list[str] = field(default_factory=list)  # must appear in top-5
    expected_types: list[str] = field(default_factory=list)     # at least 1 should appear
    must_contain: list[str] = field(default_factory=list)       # substrings in answer
    category: str = "general"


GOLDEN_QUERIES: list[GoldenQuery] = [
    # ── Dependency queries ───────────────────────────────────────
    GoldenQuery(
        query="What routines does SPKEZ call?",
        expected_intent="DEPENDENCY",
        expected_routines=["SPKEZ"],
        expected_types=["routine_doc"],
        must_contain=["CHKIN"],
        category="dependency",
    ),
    GoldenQuery(
        query="What calls FURNSH?",
        expected_intent="DEPENDENCY",
        expected_routines=["FURNSH"],
        expected_types=["routine_doc"],
        must_contain=["LDKLST"],
        category="dependency",
    ),
    GoldenQuery(
        query="Show me the callers of STR2ET",
        expected_intent="DEPENDENCY",
        expected_routines=["STR2ET"],
        expected_types=["routine_doc"],
        must_contain=[],
        category="dependency",
    ),

    # ── Impact queries ───────────────────────────────────────────
    GoldenQuery(
        query="What breaks if CHKIN changes?",
        expected_intent="IMPACT",
        expected_routines=["CHKIN"],
        expected_types=["routine_doc"],
        must_contain=[],
        category="impact",
    ),
    GoldenQuery(
        query="What is the blast radius of changing SPKEZ?",
        expected_intent="IMPACT",
        expected_routines=["SPKEZ"],
        expected_types=["routine_doc"],
        must_contain=[],
        category="impact",
    ),

    # ── Explain queries ──────────────────────────────────────────
    GoldenQuery(
        query="What does SPKEZ do?",
        expected_intent="EXPLAIN",
        expected_routines=["SPKEZ"],
        expected_types=["routine_doc"],
        must_contain=["position", "velocity"],
        category="explain",
    ),
    GoldenQuery(
        query="Explain FURNSH",
        expected_intent="EXPLAIN",
        expected_routines=["FURNSH"],
        expected_types=["routine_doc"],
        must_contain=["kernel", "load"],
        category="explain",
    ),
    GoldenQuery(
        query="How does STR2ET work?",
        expected_intent="EXPLAIN",
        expected_routines=["STR2ET"],
        expected_types=["routine_doc"],
        must_contain=["time", "string"],
        category="explain",
    ),
    GoldenQuery(
        query="What is the relationship between SPKEZ and SPKEZR?",
        expected_intent="EXPLAIN",
        expected_routines=["SPKEZ", "SPKEZR"],
        expected_types=["routine_doc"],
        must_contain=["SPKEZ", "SPKEZR"],
        category="explain",
    ),
    GoldenQuery(
        query="Tell me about PXFORM",
        expected_intent="EXPLAIN",
        expected_routines=["PXFORM"],
        expected_types=["routine_doc"],
        must_contain=["frame", "transform"],
        category="explain",
    ),

    # ── Pattern queries ──────────────────────────────────────────
    GoldenQuery(
        query="How does SPICE handle errors?",
        expected_intent="PATTERN",
        expected_routines=[],  # any error-handling routine is fine
        expected_types=["routine_doc"],
        must_contain=["CHKIN", "SIGERR"],
        category="pattern",
    ),
    GoldenQuery(
        query="How do I load SPICE kernel files?",
        expected_intent="PATTERN",
        expected_routines=[],
        expected_types=["routine_doc"],
        must_contain=["FURNSH"],
        category="pattern",
    ),
    GoldenQuery(
        query="Show me time conversion routines in SPICE",
        expected_intent="PATTERN",
        expected_routines=[],
        expected_types=["routine_doc"],
        must_contain=["time"],
        category="pattern",
    ),
    GoldenQuery(
        query="What file I/O operations does SPICE support?",
        expected_intent="PATTERN",
        expected_routines=[],
        expected_types=["routine_doc"],
        must_contain=["file"],
        category="pattern",
    ),

    # ── Semantic / open-ended queries ────────────────────────────
    GoldenQuery(
        query="How does SPICE handle coordinate systems?",
        expected_intent="PATTERN",
        expected_routines=[],
        expected_types=["routine_doc"],
        must_contain=["coordinate"],
        category="semantic",
    ),
    GoldenQuery(
        query="What constants are defined in the include files?",
        expected_intent="SEMANTIC",
        expected_routines=[],
        expected_types=["include"],
        must_contain=[],
        category="semantic",
    ),
    GoldenQuery(
        query="What is the maximum number of kernels that can be loaded?",
        expected_intent="SEMANTIC",
        expected_routines=["KEEPER"],
        expected_types=["routine_doc", "routine_segment"],
        must_contain=[],
        category="semantic",
    ),

    # ── ENTRY point queries ──────────────────────────────────────
    GoldenQuery(
        query="What is KTOTAL?",
        expected_intent="EXPLAIN",
        expected_routines=["KTOTAL"],
        expected_types=["routine_doc"],
        must_contain=["kernel"],
        category="entry_point",
    ),
    GoldenQuery(
        query="Explain UNLOAD",
        expected_intent="EXPLAIN",
        expected_routines=["UNLOAD"],
        expected_types=["routine_doc"],
        must_contain=["kernel"],
        category="entry_point",
    ),

    # ── Edge cases ───────────────────────────────────────────────
    GoldenQuery(
        query="SPKEZ",
        expected_intent="EXPLAIN",
        expected_routines=["SPKEZ"],
        expected_types=["routine_doc"],
        must_contain=[],
        category="edge_case",
    ),
    GoldenQuery(
        query="What aberration corrections does SPICE support?",
        expected_intent="SEMANTIC",
        expected_routines=[],
        expected_types=["routine_doc"],
        must_contain=["aberration"],
        category="semantic",
    ),
]
