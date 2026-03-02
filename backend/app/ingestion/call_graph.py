"""Build forward and reverse call graphs from parsed routines.

Produces:
  - forward:   routine_name -> set of routines it CALLs
  - reverse:   routine_name -> set of routines that CALL it
  - aliases:   entry_name -> parent_routine_name
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from app.ingestion.fortran_parser import RoutineInfo


@dataclass
class CallGraph:
    """Forward and reverse call graph for the codebase."""

    forward: dict[str, list[str]] = field(default_factory=dict)   # name -> calls
    reverse: dict[str, list[str]] = field(default_factory=dict)   # name -> called_by
    aliases: dict[str, str] = field(default_factory=dict)         # entry -> parent
    routine_files: dict[str, str] = field(default_factory=dict)   # name -> file_path

    def callers_of(self, name: str, depth: int = 1) -> set[str]:
        """Get all callers of a routine up to N levels deep."""
        result: set[str] = set()
        frontier = {name.upper()}
        for _ in range(depth):
            next_frontier: set[str] = set()
            for n in frontier:
                # Also check if this is an alias
                actual = self.aliases.get(n, n)
                for caller in self.reverse.get(actual, []):
                    if caller not in result and caller != name.upper():
                        result.add(caller)
                        next_frontier.add(caller)
                # Check the name itself too
                for caller in self.reverse.get(n, []):
                    if caller not in result and caller != name.upper():
                        result.add(caller)
                        next_frontier.add(caller)
            frontier = next_frontier
        return result

    def callees_of(self, name: str, depth: int = 1) -> set[str]:
        """Get all routines called by a routine up to N levels deep."""
        result: set[str] = set()
        frontier = {name.upper()}
        for _ in range(depth):
            next_frontier: set[str] = set()
            for n in frontier:
                for callee in self.forward.get(n, []):
                    if callee not in result and callee != name.upper():
                        result.add(callee)
                        next_frontier.add(callee)
            frontier = next_frontier
        return result


def build_call_graph(routines: list[RoutineInfo]) -> CallGraph:
    """Build forward and reverse call graphs from parsed routines."""
    graph = CallGraph()

    # Build forward graph and aliases
    for r in routines:
        if r.kind == "ENTRY":
            graph.aliases[r.name] = r.parent_routine
            graph.routine_files[r.name] = r.file_path
            continue

        graph.forward[r.name] = r.calls[:]
        graph.routine_files[r.name] = r.file_path

        # Register entry points as aliases
        for entry in r.entry_points:
            graph.aliases[entry] = r.name

    # Build reverse graph
    for caller, callees in graph.forward.items():
        for callee in callees:
            if callee not in graph.reverse:
                graph.reverse[callee] = []
            graph.reverse[callee].append(caller)

    # Sort for determinism
    for k in graph.reverse:
        graph.reverse[k] = sorted(set(graph.reverse[k]))

    return graph


def save_call_graph(graph: CallGraph, path: str = "data/call_graph.json"):
    """Persist the call graph to JSON."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "forward": graph.forward,
        "reverse": graph.reverse,
        "aliases": graph.aliases,
        "routine_files": graph.routine_files,
        "stats": {
            "total_routines": len(graph.forward),
            "total_entry_aliases": len(graph.aliases),
            "total_call_edges": sum(len(v) for v in graph.forward.values()),
            "total_reverse_edges": sum(len(v) for v in graph.reverse.values()),
        },
    }, indent=2))
    print(f"Call graph saved to {path}")


def load_call_graph(path: str = "data/call_graph.json") -> CallGraph:
    """Load a previously saved call graph."""
    data = json.loads(Path(path).read_text())
    return CallGraph(
        forward=data["forward"],
        reverse=data["reverse"],
        aliases=data.get("aliases", {}),
        routine_files=data.get("routine_files", {}),
    )


if __name__ == "__main__":
    import sys
    sys.path.insert(0, ".")

    from app.ingestion.scanner import scan_directory
    from app.ingestion.fortran_parser import parse_file

    source_dir = sys.argv[1] if len(sys.argv) > 1 else "../data/spice"
    files = scan_directory(source_dir, [".f"])

    all_routines: list[RoutineInfo] = []
    for f in files:
        all_routines.extend(parse_file(f))

    graph = build_call_graph(all_routines)
    save_call_graph(graph)

    print(f"\nCall Graph Stats:")
    print(f"  Routines:       {len(graph.forward)}")
    print(f"  Entry aliases:  {len(graph.aliases)}")
    print(f"  Forward edges:  {sum(len(v) for v in graph.forward.values())}")
    print(f"  Reverse edges:  {sum(len(v) for v in graph.reverse.values())}")

    # Demo: SPKEZ dependencies
    print(f"\n  SPKEZ calls:     {graph.forward.get('SPKEZ', [])}")
    print(f"  SPKEZ called by: {graph.reverse.get('SPKEZ', [])}")
    print(f"  FURNSH alias:    {graph.aliases.get('FURNSH', 'N/A')}")
    print(f"  FURNSH callers (2 levels): {graph.callers_of('FURNSH', 2)}")
