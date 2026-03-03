"""Dependency mapping: forward and reverse call graph queries.

Uses shared call graph singleton via app.services.
"""

from __future__ import annotations

from app.ingestion.call_graph import load_call_graph, CallGraph


_graph: CallGraph | None = None


def _get_graph() -> CallGraph:
    global _graph
    if _graph is None:
        _graph = load_call_graph()
    return _graph


def get_dependencies(routine_name: str, depth: int = 1) -> dict:
    """Get forward and reverse dependencies for a routine."""
    graph = _get_graph()
    name = routine_name.upper()

    actual_name = graph.aliases.get(name, name)
    calls = list(graph.callees_of(actual_name, depth=depth))
    callers = list(graph.callers_of(name, depth=depth))
    direct_calls = graph.forward.get(actual_name, [])

    return {
        "routine_name": name,
        "resolved_name": actual_name if actual_name != name else None,
        "is_entry_point": name in graph.aliases,
        "parent_routine": graph.aliases.get(name),
        "file_path": graph.routine_files.get(
            actual_name, graph.routine_files.get(name, "unknown")
        ),
        "direct_calls": direct_calls,
        "all_callees": sorted(calls),
        "all_callers": sorted(callers),
        "depth": depth,
    }
