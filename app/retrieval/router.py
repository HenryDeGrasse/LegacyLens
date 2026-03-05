"""Query router: classify intent and dispatch to the best retrieval strategy.

Routes:
  DEPENDENCY  → call graph lookup (zero-cost, instant)
  EXPLAIN     → exact routine doc + body lookup in Pinecone
  PATTERN     → pattern-filtered vector search with doc-type preference
  SEMANTIC    → general semantic search (fallback)

The router is intentionally regex-first so it's fast, deterministic,
and testable. No LLM call needed.

Routine name validation:
  Candidates are validated against the call graph (forward keys + ENTRY
  aliases). This eliminates false-positive extractions of English words
  like SPACESHIP, TRACK, LIBRARY that pass the uppercase regex but are
  not real SPICE routines. The known-names set is built once on first
  use and cached for the process lifetime.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum, auto
from threading import Lock


class QueryIntent(Enum):
    DEPENDENCY = auto()   # "what calls X", "what does X call", "callers of X"
    IMPACT = auto()       # "what breaks if X changes", "blast radius of X"
    EXPLAIN = auto()      # "explain X", "what does X do", "how does X work"
    PATTERN = auto()      # "how does error handling work", "show me kernel loading"
    SEMANTIC = auto()     # everything else — broad codebase questions
    OUT_OF_SCOPE = auto() # completely off-topic queries


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
    # More English words that pass the [A-Z]{3+} filter
    "CONVERSION", "OPERATIONS", "OPERATION", "SUPPORT", "SUPPORTS",
    "CONSTANTS", "CONSTANT", "DEFINED", "DEFINE", "LOADED", "LOADING",
    "ABERRATION", "CORRECTIONS", "CORRECTION", "BLAST", "RADIUS",
    "CHANGING", "SHOW", "TELL", "GIVE", "WORK", "WORKS", "WORKING",
    "AVAILABLE", "POSSIBLE", "DIFFERENT", "SPECIFIC", "VARIOUS",
    "MAX", "MIN", "SUM", "SET", "GET", "PUT", "RUN", "END",
}


# ── Known routine names (call graph validation) ─────────────────────
#
# Lazy-loaded set of all routine names from the call graph. Candidates
# that pass the regex + stop-word filter are validated against this set
# to eliminate false positives like SPACESHIP, TRACK, LIBRARY, etc.
# Falls back to stop-word-only filtering if the call graph is unavailable.

_known_routines: set[str] | None = None
_known_routines_lock = Lock()


def _get_known_routines() -> set[str] | None:
    """Return the cached set of known routine names, or None if unavailable."""
    global _known_routines
    if _known_routines is not None:
        return _known_routines
    with _known_routines_lock:
        if _known_routines is not None:
            return _known_routines
        try:
            from app.services import get_call_graph
            cg = get_call_graph()
            if cg:
                names = set(cg.get("forward", {}).keys())
                names |= set(cg.get("aliases", {}).keys())
                _known_routines = names
                return _known_routines
        except Exception:
            pass
    return None


def _extract_routine_names(query: str) -> list[str]:
    """Pull plausible SPICE routine identifiers from the query.

    Candidates must:
      1. Match the uppercase regex ([A-Z][A-Z0-9_]{2,})
      2. Not be in the stop-word list
      3. Exist in the call graph (if available)

    Step 3 eliminates false positives from common English words that
    slip past the stop-word list (e.g. SPACESHIP, TRACK, LIBRARY).
    """
    candidates = _ROUTINE_NAME_RE.findall(query.upper())
    filtered = [c for c in candidates if c not in _STOP_WORDS and len(c) >= 3]

    known = _get_known_routines()
    if known is not None:
        filtered = [c for c in filtered if c in known]

    # Deduplicate while preserving order (avoids wasting Pinecone queries)
    seen: set[str] = set()
    deduped: list[str] = []
    for name in filtered:
        if name not in seen:
            seen.add(name)
            deduped.append(name)
    return deduped


# ── Pattern detection from query text ───────────────────────────────

_QUERY_PATTERN_MAP: dict[str, str] = {
    # keyword/phrase → SPICE pattern category
    #
    # These are substring-matched against the lowercased query.
    # Only include terms that unambiguously signal a SPICE domain category.
    # Broad natural-language terms (e.g. "position", "track") are handled
    # by query expansion in search.py rather than pattern routing, so the
    # router stays precise and the embedding gets enriched regardless.

    # Error handling
    "error handl":    "error_handling",
    "error check":    "error_handling",
    "handle error":   "error_handling",
    "handle err":     "error_handling",
    "exception":      "error_handling",
    "chkin":          "error_handling",
    "chkout":         "error_handling",
    "sigerr":         "error_handling",

    # Kernel loading
    "kernel":         "kernel_loading",
    "load":           "kernel_loading",
    "furnsh":         "kernel_loading",
    "unload":         "kernel_loading",

    # SPK / ephemeris
    "ephemer":        "spk_operations",
    "spacecraft pos": "spk_operations",
    "state vector":   "spk_operations",
    "spk":            "spk_operations",

    # Frame transforms
    "frame":          "frame_transforms",
    "transform":      "frame_transforms",
    "rotation":       "frame_transforms",
    "coordinate":     "frame_transforms",

    # Time conversion
    "time conver":    "time_conversion",
    "time format":    "time_conversion",
    "epoch":          "time_conversion",
    "utc":            "time_conversion",
    "str2et":         "time_conversion",

    # Geometry
    "sub-point":      "geometry",
    "sub-observer":   "geometry",
    "intercept":      "geometry",
    "illumin":        "geometry",
    "occult":         "geometry",

    # Matrix/vector
    "matrix":         "matrix_vector",
    "vector":         "matrix_vector",
    "cross product":  "matrix_vector",
    "dot product":    "matrix_vector",

    # File I/O
    "file i/o":       "file_io",
    "file io":        "file_io",
    "read file":      "file_io",
    "write file":     "file_io",
    "i/o opera":      "file_io",
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
    r"\b(what\s+(?:routines?\s+)?(?:does\s+\w+\s+)?calls?|"
    r"what\s+does\s+\w+\s+call|"
    r"callers?\s+of|called\s+by|"
    r"depends?\s+on|dependenc|"
    r"call\s+graph|call\s+tree|"
    r"who\s+uses|what\s+uses|what\s+calls|"
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
    r"\b(how\s+does\s+spice|how\s+do\s+i|how\s+does\s+\w+\s+handle|"
    r"what\s+is\s+the\s+(pattern|approach|method)|"
    r"show\s+me\s+(the|all)|"
    r"what\s+\w+\s+operations|what\s+\w+\s+routines|"
    r"overview|concept|pattern|approach)\b",
    re.IGNORECASE,
)


# ── Out-of-scope detection ──────────────────────────────────────────
#
# Layered check:
#   1. Hard blocks: prompt injection, explicit code generation, and
#      non-technical entertainment requests (jokes/poems/etc.).
#   2. Off-topic patterns (weather, stocks, etc.) are blocked unless
#      there's a strong SPICE/codebase relevance signal.
#   3. Ambiguous queries fall through to SEMANTIC for best-effort answers.

_CODEBASE_RELEVANCE_RE = re.compile(
    r"\b(spice|naif|fortran|spacecraft|spaceship|satellite|orbit|planet|"
    r"ephemer|kernel|trajectory|celestial|solar\s+system|"
    r"navigation|mission|position|velocity|state\s+vector|"
    r"coordinate|reference\s+frame|rotation|transform|"
    r"time\s+conver|epoch|utc|aberration|occultation|"
    r"sub.?point|sub.?observer|intercept|illumin|"
    r"subroutine|call\s+graph|function|routine|"
    r"code|library|toolkit|api|module|"
    r"track|location|compute|calculat|determini|"
    r"body|target|observer|light\s+time|"
    r"matrix|vector|quaternion|euler|"
    r"daf|spk|ck|pck|ek|dsk|"
    r"comment|documentation|parameter)\b",
    re.IGNORECASE,
)

# Stronger signal than broad relevance terms like "orbit" or "track".
# Used to avoid false negatives in out-of-scope detection.
_STRONG_CODEBASE_RELEVANCE_RE = re.compile(
    r"\b(spice|naif|fortran|subroutine|routine|call\s+graph|"
    r"toolkit|codebase|function|api|module|"
    r"furnsh|spkez|spkezr|str2et|pxform|chkin|chkout|sigerr|"
    r"kernel\s+file|ephemeris|reference\s+frame|aberration)\b",
    re.IGNORECASE,
)

_OFF_TOPIC_RE = re.compile(
    r"\b(weather|recipes?|cook\w*|stock\s+market|invest\w*|"
    r"sports?\s+scores?|movies?|music|lyrics|"
    r"jokes?|poems?|stor(?:y|ies)|essays?|"
    r"medical|diagnos\w*|symptoms?|"
    r"politic\w*|elections?|presidents?|"
    r"social\s+media|instagram|tiktok|facebook|twitter|"
    r"dating|relationships?|love|"
    r"games?|gaming|minecraft|fortnite)\b",
    re.IGNORECASE,
)

_PROMPT_INJECTION_RE = re.compile(
    r"(ignore\s+(your|all|previous)\s+(instructions?|rules?|prompt)|"
    r"(output|reveal|show|print|display)\s+(the\s+)?(system\s+prompt|instructions?|rules?)|"
    r"you\s+are\s+now\s+(?:a\s+)?(?:new|different)|"
    r"act\s+as\s+(?:a\s+)?(?:different|new)|"
    r"jailbreak|DAN\b|bypass\s+filter)",
    re.IGNORECASE,
)

_CODE_GENERATION_RE = re.compile(
    r"\b(write\s+(?:me\s+)?(?:a\s+)?(?:python|java|c\+\+|javascript|rust|go)\b|"
    r"generate\s+(?:a\s+)?(?:code|script|program)|"
    r"create\s+(?:a\s+)?(?:function|class|app)|"
    r"implement\s+(?:an?\s+)?(?:\w+\s+){0,3}(?:algorithm|solution))",
    re.IGNORECASE,
)

_NON_TECH_REQUEST_RE = re.compile(
    r"\b(tell\s+me\s+a\s+joke|write\s+(?:me\s+)?(?:a\s+)?(?:poem|story|essay)|"
    r"lyrics|funny\s+story)\b",
    re.IGNORECASE,
)

_GIBBERISH_HINT_RE = re.compile(
    r"\b(?:asdf|qwer|zxcv|hjkl|loremipsum)\w*\b",
    re.IGNORECASE,
)


def _is_out_of_scope(query: str) -> bool:
    """Return True if the query is definitely off-topic.

    Returns False for ambiguous or codebase-relevant queries
    (those go to SEMANTIC for best-effort answering).
    """
    # Prompt injection is always out-of-scope
    if _PROMPT_INJECTION_RE.search(query):
        return True

    # Explicit non-technical asks (jokes/poems/etc.) are out-of-scope,
    # even if they contain accidental domain words (e.g. "kernels").
    if _NON_TECH_REQUEST_RE.search(query):
        return True

    # Code generation requests are out-of-scope for this assistant.
    if _CODE_GENERATION_RE.search(query):
        return True

    # Known off-topic intent: allow only when there's a strong codebase signal.
    if _OFF_TOPIC_RE.search(query) and not _STRONG_CODEBASE_RELEVANCE_RE.search(query):
        return True

    # Pure gibberish check: if no alphabetic words >= 3 chars, out-of-scope
    alpha_words = re.findall(r"\b[a-zA-Z]{3,}\b", query)
    if not alpha_words:
        return True

    # Heuristic for keyboard-mash style input
    if _GIBBERISH_HINT_RE.search(query) and not _CODEBASE_RELEVANCE_RE.search(query):
        return True

    # If query has broad codebase relevance signals, it's in-scope
    if _CODEBASE_RELEVANCE_RE.search(query):
        return False

    # Ambiguous → let it through to SEMANTIC (benefit of the doubt)
    return False


_OUT_OF_SCOPE_RESPONSE = (
    "I can only answer questions about NASA's SPICE Toolkit Fortran codebase. "
    "Try asking about specific routines (e.g., 'What does SPKEZ do?'), "
    "patterns (e.g., 'How does SPICE handle errors?'), or concepts "
    "(e.g., 'How does the spacecraft track its position?')."
)


def route_query(query: str) -> RoutedQuery:
    """Classify a query and extract structured intent.

    Priority order:
      0. OUT_OF_SCOPE — prompt injection, off-topic, gibberish
      1. DEPENDENCY / IMPACT — if specific structural question + routine name
      2. EXPLAIN — if asking about a specific routine's behavior
      3. PATTERN — if asking about a conceptual category
      4. SEMANTIC — fallback (includes vague but codebase-relevant questions)
    """
    routine_names = _extract_routine_names(query)
    patterns = _detect_patterns(query)

    # 0. Out-of-scope check — catches prompt injection, off-topic, gibberish.
    # Applied even when false-positive routine names/patterns are extracted.
    if _is_out_of_scope(query):
        return RoutedQuery(
            intent=QueryIntent.OUT_OF_SCOPE,
            routine_names=[],
            patterns=[],
            prefer_doc=False,
            original_query=query,
        )

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

    # 7. Fallback: pure semantic (includes vague codebase questions like
    #    "how does the spaceship track its location?")
    #    Always prefer doc chunks — conceptual queries need routine
    #    descriptions, not raw Fortran code.
    return RoutedQuery(
        intent=QueryIntent.SEMANTIC,
        routine_names=[],
        patterns=[],
        prefer_doc=True,
        original_query=query,
    )
