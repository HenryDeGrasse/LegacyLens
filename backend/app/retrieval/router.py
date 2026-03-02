"""Query router: classify intent and dispatch to the best retrieval strategy.

Routes:
  DEPENDENCY  → call graph lookup (zero-cost, instant)
  EXPLAIN     → exact routine doc + body lookup in Pinecone
  PATTERN     → pattern-filtered vector search with doc-type preference
  SEMANTIC    → general semantic search (fallback)

The router is intentionally regex-first so it's fast, deterministic,
and testable. No LLM call needed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum, auto


class QueryIntent(Enum):
    DEPENDENCY = auto()   # "what calls X", "what does X call", "callers of X"
    IMPACT = auto()       # "what breaks if X changes", "blast radius of X"
    EXPLAIN = auto()      # "explain X", "what does X do", "how does X work"
    PATTERN = auto()      # "how does error handling work", "show me kernel loading"
    SEMANTIC = auto()     # everything else


@dataclass
class RoutedQuery:
    """Result of routing a user query."""
    intent: QueryIntent
    routine_names: list[str]         # detected routine identifiers
    patterns: list[str]              # detected SPICE pattern categories
    prefer_doc: bool                 # should we prefer routine_doc chunks?
    original_query: str


# ── Routine name extraction ─────────────────────────────────────────

_ROUTINE_NAME_RE = re.compile(r"\b([A-Z][A-Z0-9_]{2,})\b")

_STOP_WORDS = {
    # Common English
    "THE", "AND", "FOR", "THIS", "THAT", "WITH", "FROM", "WHAT", "DOES",
    "HOW", "WHY", "WHERE", "WHEN", "WHICH", "SHOW", "FIND", "ALL", "ARE",
    "NOT", "HAS", "HAVE", "BEEN", "WILL", "CAN", "USE", "USED", "USING",
    "INTO", "ABOUT", "LIKE", "BETWEEN", "EACH", "AFTER", "BEFORE",
    "COULD", "WOULD", "SHOULD", "THERE", "THEIR", "THEY", "THEM",
    # Domain words that look like identifiers
    "SPICE", "FORTRAN", "CODE", "FILE", "FILES", "TYPE",
    "FUNCTION", "SUBROUTINE", "ENTRY", "CALL", "CALLS", "PROGRAM", "MODULE",
    "EXPLAIN", "DESCRIBE", "LIST", "RETURN", "RETURNS", "ERROR", "ERRORS",
    "DATA", "ROUTINE", "ROUTINES", "TOOLKIT", "NASA", "HANDLE", "HANDLES",
    "HANDLING", "PATTERN", "PATTERNS", "ACROSS", "SYSTEM", "SYSTEMS",
    "MATRIX", "VECTOR", "ROTATION", "FRAME", "FRAMES", "KERNEL", "KERNELS",
    "LOAD", "LOADING", "TIME", "EPOCH", "COORDINATE", "COORDINATES",
    "POSITION", "VELOCITY", "STATE", "STATES", "BODY", "BODIES",
    "TARGET", "OBSERVER", "OBJECT", "OBJECTS", "VALUE", "VALUES",
    "INPUT", "OUTPUT", "ARGUMENT", "ARGUMENTS", "PARAMETER", "PARAMETERS",
    "BLOCK", "COMMON", "INCLUDE", "IMPLICIT", "INTEGER", "DOUBLE", "PRECISION",
    "CHARACTER", "LOGICAL", "REAL", "DIMENSION", "EQUIVALENCE",
    "IMPACT", "CHANGE", "CHANGES", "BREAK", "BREAKS", "AFFECTED",
    "RELATIONSHIP", "BETWEEN", "DIFFERENCE", "COMPARE", "SIMILAR",
    "OVERVIEW", "CONCEPT", "APPROACH", "METHOD", "METHODS",
    "MAXIMUM", "MINIMUM", "NUMBER", "COUNT", "SIZE", "LENGTH",
    "ABSTRACT", "BRIEF", "DETAILED", "DESCRIPTION", "PURPOSE",
}


def _extract_routine_names(query: str) -> list[str]:
    """Pull plausible SPICE routine identifiers from the query."""
    candidates = _ROUTINE_NAME_RE.findall(query.upper())
    return [c for c in candidates if c not in _STOP_WORDS and len(c) >= 3]


# ── Pattern detection from query text ───────────────────────────────

_QUERY_PATTERN_MAP: dict[str, str] = {
    # keyword/phrase → SPICE pattern category
    "error handl":    "error_handling",
    "exception":      "error_handling",
    "chkin":          "error_handling",
    "chkout":         "error_handling",
    "sigerr":         "error_handling",
    "kernel":         "kernel_loading",
    "load":           "kernel_loading",
    "furnsh":         "kernel_loading",
    "unload":         "kernel_loading",
    "ephemer":        "spk_operations",
    "spacecraft pos": "spk_operations",
    "state vector":   "spk_operations",
    "spk":            "spk_operations",
    "frame":          "frame_transforms",
    "transform":      "frame_transforms",
    "rotation":       "frame_transforms",
    "coordinate":     "frame_transforms",
    "time conver":    "time_conversion",
    "epoch":          "time_conversion",
    "utc":            "time_conversion",
    "str2et":         "time_conversion",
    "sub-point":      "geometry",
    "sub-observer":   "geometry",
    "intercept":      "geometry",
    "illumin":        "geometry",
    "occult":         "geometry",
    "matrix":         "matrix_vector",
    "vector":         "matrix_vector",
    "cross product":  "matrix_vector",
    "dot product":    "matrix_vector",
    "file i/o":       "file_io",
    "read file":      "file_io",
    "write file":     "file_io",
    "daf":            "file_io",
}


def _detect_patterns(query: str) -> list[str]:
    q = query.lower()
    found: set[str] = set()
    for keyword, pattern in _QUERY_PATTERN_MAP.items():
        if keyword in q:
            found.add(pattern)
    return list(found)


# ── Intent classifiers (regex-first, ordered by specificity) ────────

_DEPENDENCY_RE = re.compile(
    r"\b(what\s+(calls?|does\s+\w+\s+call)|"
    r"callers?\s+of|called\s+by|"
    r"depends?\s+on|dependenc|"
    r"call\s+graph|call\s+tree|"
    r"who\s+uses|what\s+uses|"
    r"forward\s+calls?|reverse\s+calls?)\b",
    re.IGNORECASE,
)

_IMPACT_RE = re.compile(
    r"\b(impact|blast\s+radius|what\s+breaks|"
    r"affected\s+by|ripple|downstream|"
    r"if\s+\w+\s+changes?|change\s+\w+\s+what)\b",
    re.IGNORECASE,
)

_EXPLAIN_RE = re.compile(
    r"\b(explain|describe|walk\s+through|what\s+does\s+\w+\s+do|"
    r"how\s+does\s+\w+\s+work|purpose\s+of|"
    r"what\s+is\s+\w+|tell\s+me\s+about)\b",
    re.IGNORECASE,
)

_CONCEPTUAL_RE = re.compile(
    r"\b(how\s+does\s+spice|how\s+do\s+i|"
    r"what\s+is\s+the\s+(pattern|approach|method)|"
    r"show\s+me\s+(the|all)|"
    r"overview|concept|pattern|approach)\b",
    re.IGNORECASE,
)


def route_query(query: str) -> RoutedQuery:
    """Classify a query and extract structured intent.

    Priority order:
      1. DEPENDENCY / IMPACT — if specific structural question + routine name
      2. EXPLAIN — if asking about a specific routine's behavior
      3. PATTERN — if asking about a conceptual category
      4. SEMANTIC — fallback
    """
    routine_names = _extract_routine_names(query)
    patterns = _detect_patterns(query)

    # 1. Dependency questions (need a routine name to be useful)
    if routine_names and _DEPENDENCY_RE.search(query):
        return RoutedQuery(
            intent=QueryIntent.DEPENDENCY,
            routine_names=routine_names,
            patterns=patterns,
            prefer_doc=False,
            original_query=query,
        )

    # 2. Impact questions
    if routine_names and _IMPACT_RE.search(query):
        return RoutedQuery(
            intent=QueryIntent.IMPACT,
            routine_names=routine_names,
            patterns=patterns,
            prefer_doc=False,
            original_query=query,
        )

    # 3. Explain a specific routine
    if routine_names and _EXPLAIN_RE.search(query):
        return RoutedQuery(
            intent=QueryIntent.EXPLAIN,
            routine_names=routine_names,
            patterns=patterns,
            prefer_doc=True,
            original_query=query,
        )

    # 4. Routine name mentioned but no strong intent signal → explain
    if routine_names:
        return RoutedQuery(
            intent=QueryIntent.EXPLAIN,
            routine_names=routine_names,
            patterns=patterns,
            prefer_doc=True,
            original_query=query,
        )

    # 5. Conceptual / pattern-based query (no specific routine)
    if patterns and _CONCEPTUAL_RE.search(query):
        return RoutedQuery(
            intent=QueryIntent.PATTERN,
            routine_names=[],
            patterns=patterns,
            prefer_doc=True,
            original_query=query,
        )

    # 6. Patterns detected but weaker signal
    if patterns:
        return RoutedQuery(
            intent=QueryIntent.PATTERN,
            routine_names=[],
            patterns=patterns,
            prefer_doc=True,
            original_query=query,
        )

    # 7. Fallback: pure semantic
    return RoutedQuery(
        intent=QueryIntent.SEMANTIC,
        routine_names=[],
        patterns=[],
        prefer_doc=False,
        original_query=query,
    )
