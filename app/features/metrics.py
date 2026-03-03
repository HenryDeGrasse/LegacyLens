"""Code Complexity Metrics for SPICE routines.

Computes:
  - LOC (total, comment, code, blank)
  - Cyclomatic complexity (estimated from branch points)
  - Max nesting depth
  - Parameter count
  - Call count (unique callees)
  - Pattern detection summary

Uses the Pinecone-stored code chunks + call graph data.
No LLM calls needed — pure static analysis.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.services import get_index, get_call_graph_obj, embed_text


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
    name = routine_name.upper()

    # Get call graph info
    graph = get_call_graph_obj()
    calls = []
    callers = []
    file_path = "unknown"
    if graph:
        actual_name = graph.aliases.get(name, name)
        calls = graph.forward.get(actual_name, [])
        callers = list(graph.callers_of(name, depth=1))
        file_path = graph.routine_files.get(
            actual_name, graph.routine_files.get(name, "unknown")
        )
    else:
        actual_name = name

    # Retrieve chunks from Pinecone
    index = get_index()
    query_vec = embed_text(f"SPICE routine {name}")

    search_names = [name]
    if graph and actual_name != name:
        search_names.append(actual_name)

    all_text_parts: list[str] = []
    header_text = ""
    start_line = 0
    end_line = 0
    patterns: list[str] = []

    for search_name in search_names:
        results = index.query(
            vector=query_vec,
            top_k=5,
            filter={"routine_name": {"$eq": search_name}},
            include_metadata=True,
        )
        for chunk in results.matches:
            meta = chunk.metadata or {}
            text = meta.get("text", "")
            chunk_type = meta.get("chunk_type", "")

            if not start_line:
                start_line = meta.get("start_line", 0)
                end_line = meta.get("end_line", 0)
                file_path = meta.get("file_path", file_path)

            raw_patterns = meta.get("patterns", [])
            if isinstance(raw_patterns, list):
                patterns.extend(raw_patterns)

            if chunk_type == "routine_doc":
                header_text = text
            all_text_parts.append(text)

    if not all_text_parts:
        return {
            "error": f"No code found for routine '{name}'",
            "routine_name": name,
        }

    full_text = "\n".join(all_text_parts)
    analysis = _analyze_code(full_text)
    param_count = _count_params(header_text or full_text)

    metrics = RoutineMetrics(
        routine_name=name,
        file_path=file_path,
        start_line=start_line,
        end_line=end_line,
        total_lines=analysis["total_lines"],
        code_lines=analysis["code_lines"],
        comment_lines=analysis["comment_lines"],
        blank_lines=analysis["blank_lines"],
        cyclomatic_complexity=analysis["cyclomatic_complexity"],
        max_nesting_depth=analysis["max_nesting_depth"],
        parameter_count=param_count,
        call_count=len(calls),
        caller_count=len(callers),
        patterns=sorted(set(patterns)),
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
