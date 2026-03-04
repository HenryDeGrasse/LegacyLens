"""Isomorphism tests: verify optimizations don't change outputs.

Loads golden_outputs.json and validates that router, call graph traversal,
and autocomplete produce identical results after any code changes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

GOLDEN_PATH = Path(__file__).parent / "golden_outputs.json"


@pytest.fixture(scope="module")
def golden():
    return json.loads(GOLDEN_PATH.read_text())


@pytest.fixture(scope="module")
def call_graph():
    from app.ingestion.call_graph import CallGraph

    data = json.load(open("data/call_graph.json"))
    return CallGraph(
        forward=data["forward"],
        reverse=data["reverse"],
        aliases=data.get("aliases", {}),
        routine_files=data.get("routine_files", {}),
    )


# ── Router invariants ────────────────────────────────────────────────


class TestRouterInvariants:
    """Query router must produce identical intent/routines/patterns."""

    def test_all_router_golden(self, golden):
        from app.retrieval.router import route_query

        for query, expected in golden["router"].items():
            routed = route_query(query)
            assert routed.intent.name == expected["intent"], (
                f"Intent mismatch for '{query}': "
                f"{routed.intent.name} != {expected['intent']}"
            )
            assert routed.routine_names == expected["routine_names"], (
                f"Routine names mismatch for '{query}'"
            )
            assert routed.patterns == expected["patterns"], (
                f"Patterns mismatch for '{query}'"
            )
            assert routed.prefer_doc == expected["prefer_doc"], (
                f"prefer_doc mismatch for '{query}'"
            )


# ── Call graph traversal invariants ──────────────────────────────────


class TestCallGraphInvariants:
    """callers_of and callees_of must return identical sets."""

    def test_all_graph_golden(self, golden, call_graph):
        for key, expected in golden["graph"].items():
            parts = key.rsplit("_", 1)
            name_and_type = parts[0]
            depth = int(parts[1][1:])

            if "_callers_" in key:
                name = name_and_type.split("_callers")[0]
                actual = sorted(call_graph.callers_of(name, depth=depth))
            else:
                name = name_and_type.split("_callees")[0]
                actual = sorted(call_graph.callees_of(name, depth=depth))

            assert actual == expected, (
                f"Mismatch for {key}: "
                f"got {len(actual)} results, expected {len(expected)}"
            )


# ── Autocomplete invariants ──────────────────────────────────────────


class TestAutocompleteInvariants:
    """Autocomplete search must return identical match lists."""

    def test_all_autocomplete_golden(self, golden):
        data = json.load(open("data/call_graph.json"))
        forward = data["forward"]
        aliases = data["aliases"]
        all_names = sorted(set(list(forward.keys()) + list(aliases.keys())))

        assert len(all_names) == golden["all_names_count"]

        for q, expected in golden["autocomplete"].items():
            query_upper = q.strip().upper()[:100]
            if query_upper:
                prefix = [n for n in all_names if n.startswith(query_upper)]
                substring = [
                    n for n in all_names if query_upper in n and n not in prefix
                ]
                matches = (prefix + substring)[:50]
            else:
                matches = all_names[:50]

            assert matches == expected, (
                f"Autocomplete mismatch for q='{q}': "
                f"got {len(matches)}, expected {len(expected)}"
            )
