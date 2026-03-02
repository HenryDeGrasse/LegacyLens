"""Impact analysis: blast radius for routine changes."""

from __future__ import annotations

from app.ingestion.call_graph import load_call_graph, CallGraph


_graph: CallGraph | None = None


def _get_graph() -> CallGraph:
    global _graph
    if _graph is None:
        _graph = load_call_graph()
    return _graph


def get_impact(routine_name: str, depth: int = 2) -> dict:
    """Analyze the blast radius of changing a routine.

    Walks up the reverse call graph to find all routines that would
    be affected by a change, up to `depth` levels.

    Returns:
        Dict with affected routines at each level.
    """
    graph = _get_graph()
    name = routine_name.upper()
    actual_name = graph.aliases.get(name, name)

    levels: dict[int, list[str]] = {}
    seen: set[str] = {actual_name, name}
    frontier = {actual_name, name}

    for level in range(1, depth + 1):
        next_frontier: set[str] = set()
        for n in frontier:
            for caller in graph.reverse.get(n, []):
                if caller not in seen:
                    seen.add(caller)
                    next_frontier.add(caller)
            # Also check alias
            resolved = graph.aliases.get(n, n)
            for caller in graph.reverse.get(resolved, []):
                if caller not in seen:
                    seen.add(caller)
                    next_frontier.add(caller)

        levels[level] = sorted(next_frontier)
        frontier = next_frontier

    total_affected = sum(len(v) for v in levels.values())

    return {
        "routine_name": name,
        "resolved_name": actual_name if actual_name != name else None,
        "depth": depth,
        "total_affected": total_affected,
        "levels": {str(k): v for k, v in levels.items()},
        "file_path": graph.routine_files.get(actual_name, graph.routine_files.get(name, "unknown")),
    }
