"""Code Complexity Metrics for SPICE routines.

Computes:
  - LOC (total, comment, code, blank)
  - Cyclomatic complexity (estimated from branch points)
  - Max nesting depth
  - Parameter count
  - Call count (unique callees)
  - Pattern detection summary

Uses the Pinecone-stored code chunks + call graph data.
Uses routine_lookup for call graph resolution and chunk fetching.
No LLM calls needed — pure static analysis.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.features.routine_lookup import resolve_routine, fetch_routine_chunks


@dataclass
class RoutineMetrics:
    """Computed metrics for a single routine."""
    routine_name: str
    file_path: str
    start_line: int
    end_line: int

    # LOC breakdown
    total_lines: int
    code_lines: int
    comment_lines: int
    blank_lines: int

    # Complexity
    cyclomatic_complexity: int
    max_nesting_depth: int
    parameter_count: int

    # Dependencies
    call_count: int       # unique routines called
    caller_count: int     # unique callers
    patterns: list[str]

    # Ratings (human-readable)
    complexity_rating: str    # LOW / MEDIUM / HIGH / VERY HIGH
    size_rating: str          # SMALL / MEDIUM / LARGE / VERY LARGE


# ── Branch/nesting patterns ──────────────────────────────────────

_BRANCH_RE = re.compile(
    r"^\s*(?:IF\s*\(|ELSE\s*IF|ELSE|DO\s|DO\s+\w+\s*=|"
    r"GO\s*TO|GOTO|CALL\s+SIGERR|RETURN)",
    re.IGNORECASE,
)

_NESTING_OPEN_RE = re.compile(
    r"^\s*(?:IF\s*\(.*\)\s*THEN|DO\s|DO\s+\w+\s*=)",
    re.IGNORECASE,
)

_NESTING_CLOSE_RE = re.compile(
    r"^\s*(?:END\s*IF|END\s*DO|CONTINUE)\s*$",
    re.IGNORECASE,
)

_PARAM_RE = re.compile(
    r"(?:SUBROUTINE|FUNCTION|ENTRY)\s+\w+\s*\(([^)]*)\)",
    re.IGNORECASE,
)

_COMMENT_RE = re.compile(r"^[Cc*!]")


def _analyze_code(text: str) -> dict:
    """Analyze a block of Fortran code for complexity metrics."""
    lines = text.split("\n")

    total = len(lines)
    code_lines = 0
    comment_lines = 0
    blank_lines = 0
    branch_count = 0
    max_depth = 0
    current_depth = 0

    for line in lines:
        stripped = line.strip()
        if not stripped:
            blank_lines += 1
            continue

        if _COMMENT_RE.match(stripped):
            comment_lines += 1
            continue

        code_lines += 1

        # Extract statement (skip column 1-6 for fixed-form)
        stmt = line[6:72].strip() if len(line) > 6 else stripped

        # Count branches for cyclomatic complexity
        if _BRANCH_RE.match(stmt):
            branch_count += 1

        # Track nesting depth
        if _NESTING_OPEN_RE.match(stmt):
            current_depth += 1
            max_depth = max(max_depth, current_depth)
        if _NESTING_CLOSE_RE.match(stmt):
            current_depth = max(0, current_depth - 1)

    # Cyclomatic complexity = branches + 1
    cyclomatic = branch_count + 1

    return {
        "total_lines": total,
        "code_lines": code_lines,
        "comment_lines": comment_lines,
        "blank_lines": blank_lines,
        "cyclomatic_complexity": cyclomatic,
        "max_nesting_depth": max_depth,
    }


def _count_params(header_text: str) -> int:
    """Count parameters from the routine signature."""
    match = _PARAM_RE.search(header_text)
    if not match:
        return 0
    params = match.group(1).strip()
    if not params:
        return 0
    return len([p.strip() for p in params.split(",") if p.strip()])


def _rate_complexity(cc: int) -> str:
    if cc <= 5:
        return "LOW"
    elif cc <= 10:
        return "MEDIUM"
    elif cc <= 20:
        return "HIGH"
    return "VERY HIGH"


def _rate_size(loc: int) -> str:
    if loc <= 50:
        return "SMALL"
    elif loc <= 200:
        return "MEDIUM"
    elif loc <= 500:
        return "LARGE"
    return "VERY LARGE"


def get_metrics(routine_name: str) -> dict:
    """Compute complexity metrics for a SPICE routine.

    Retrieves the routine's code from Pinecone and analyzes it statically.
    No LLM calls needed.
    """
    info = resolve_routine(routine_name)
    chunks = fetch_routine_chunks(info, embed_query=f"SPICE routine {info.name}")

    if not chunks:
        return {
            "error": f"No code found for routine '{info.name}'",
            "routine_name": info.name,
        }

    # Extract text parts and find the header (routine_doc) chunk
    all_text_parts: list[str] = []
    header_text = ""
    for chunk in chunks.chunks:
        if chunk.chunk_type == "routine_doc":
            header_text = chunk.text
        all_text_parts.append(chunk.text)

    full_text = "\n".join(all_text_parts)
    analysis = _analyze_code(full_text)
    param_count = _count_params(header_text or full_text)

    metrics = RoutineMetrics(
        routine_name=info.name,
        file_path=chunks.file_path,
        start_line=chunks.start_line,
        end_line=chunks.end_line,
        total_lines=analysis["total_lines"],
        code_lines=analysis["code_lines"],
        comment_lines=analysis["comment_lines"],
        blank_lines=analysis["blank_lines"],
        cyclomatic_complexity=analysis["cyclomatic_complexity"],
        max_nesting_depth=analysis["max_nesting_depth"],
        parameter_count=param_count,
        call_count=len(info.calls),
        caller_count=len(info.callers),
        patterns=chunks.patterns,
        complexity_rating=_rate_complexity(analysis["cyclomatic_complexity"]),
        size_rating=_rate_size(analysis["code_lines"]),
    )

    return {
        "routine_name": metrics.routine_name,
        "file_path": metrics.file_path,
        "start_line": metrics.start_line,
        "end_line": metrics.end_line,
        "loc": {
            "total": metrics.total_lines,
            "code": metrics.code_lines,
            "comment": metrics.comment_lines,
            "blank": metrics.blank_lines,
            "comment_ratio": round(
                metrics.comment_lines / max(metrics.total_lines, 1), 2
            ),
        },
        "complexity": {
            "cyclomatic": metrics.cyclomatic_complexity,
            "max_nesting_depth": metrics.max_nesting_depth,
            "rating": metrics.complexity_rating,
        },
        "size_rating": metrics.size_rating,
        "parameters": metrics.parameter_count,
        "dependencies": {
            "calls": metrics.call_count,
            "callers": metrics.caller_count,
        },
        "patterns": metrics.patterns,
    }
