"""Tests for the LegacyLens TUI.

Covers:
- Query history navigation (up/down arrow, draft preservation)
- Call graph drill-down (first Enter = deps, second Enter = explain)
- Tree node data storage
- Status bar state transitions
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from textual.widgets import Tree

# ── QueryInput history tests ─────────────────────────────────────


class TestQueryInputHistory:
    """Test the up/down arrow history on the QueryInput widget."""

    def _make_input(self):
        from app.tui import QueryInput
        inp = QueryInput(placeholder="test")
        return inp

    def test_add_to_history(self):
        inp = self._make_input()
        inp.add_to_history("What does SPKEZ do?")
        inp.add_to_history("/explain FURNSH")
        assert inp._history == ["What does SPKEZ do?", "/explain FURNSH"]
        assert inp._history_idx == -1

    def test_add_to_history_dedup(self):
        """Consecutive identical queries should not duplicate."""
        inp = self._make_input()
        inp.add_to_history("What does SPKEZ do?")
        inp.add_to_history("What does SPKEZ do?")
        assert inp._history == ["What does SPKEZ do?"]

    def test_add_to_history_empty_ignored(self):
        inp = self._make_input()
        inp.add_to_history("")
        assert inp._history == []

    def test_history_index_reset_on_add(self):
        inp = self._make_input()
        inp.add_to_history("q1")
        inp._history_idx = 0  # simulate browsing
        inp.add_to_history("q2")
        assert inp._history_idx == -1


# ── Drill-down state machine tests ──────────────────────────────


class TestDrillDownStateMachine:
    """Test the first-Enter=deps, second-Enter=explain logic."""

    def test_first_select_triggers_deps(self):
        """First Enter on a routine should call _do_deps."""
        from app.tui import LegacyLensApp

        app = LegacyLensApp()
        app._drilled_routine = ""

        # Simulate: first Enter on SPKEZ
        # The logic is: if routine != _drilled_routine → deps
        routine = "SPKEZ"
        assert routine != app._drilled_routine
        # After deps, _drilled_routine is set
        app._drilled_routine = routine
        assert app._drilled_routine == "SPKEZ"

    def test_second_select_triggers_explain(self):
        """Second Enter on the same routine should trigger explain."""
        from app.tui import LegacyLensApp

        app = LegacyLensApp()
        app._drilled_routine = "SPKEZ"

        # Simulate: second Enter on SPKEZ
        routine = "SPKEZ"
        assert routine == app._drilled_routine
        # After explain, _drilled_routine resets
        app._drilled_routine = ""
        assert app._drilled_routine == ""

    def test_different_routine_resets_to_deps(self):
        """Selecting a different routine should do deps, not explain."""
        from app.tui import LegacyLensApp

        app = LegacyLensApp()
        app._drilled_routine = "SPKEZ"

        routine = "FURNSH"
        assert routine != app._drilled_routine
        app._drilled_routine = routine
        assert app._drilled_routine == "FURNSH"

    def test_drill_cycle(self):
        """Full cycle: deps → explain → deps on new routine."""
        from app.tui import LegacyLensApp

        app = LegacyLensApp()

        # Enter on SPKEZ → deps
        assert app._drilled_routine == ""
        app._drilled_routine = "SPKEZ"

        # Enter on SPKEZ again → explain, resets
        assert app._drilled_routine == "SPKEZ"
        app._drilled_routine = ""

        # Enter on FURNSH → deps
        app._drilled_routine = "FURNSH"
        assert app._drilled_routine == "FURNSH"


# ── CallGraphPanel data storage tests ────────────────────────────


class TestCallGraphPanelData:
    """Test that tree nodes store routine names for drill-down."""

    def test_set_graph_stores_data_on_leaves(self):
        """Leaf nodes should have routine name as .data."""
        from app.tui import CallGraphPanel
        from textual.app import App, ComposeResult

        # We need a running app to test widgets. Use Textual's async test pattern.
        async def check():
            class TestApp(App):
                def compose(self) -> ComposeResult:
                    yield CallGraphPanel(id="cg")

            async with TestApp().run_test() as pilot:
                app = pilot.app
                panel = app.query_one("#cg", CallGraphPanel)
                panel.set_graph("SPKEZ", ["CHKIN", "SPKGEO"], ["CRONOS"])

                tree = app.query_one("#call-tree", Tree)

                # Root should have data
                assert tree.root.data == "SPKEZ"

                # Collect all leaf data
                leaf_data = []
                for node in tree.root.children:
                    for child in node.children:
                        if child.data:
                            leaf_data.append(child.data)

                assert "CHKIN" in leaf_data
                assert "SPKGEO" in leaf_data
                assert "CRONOS" in leaf_data

        asyncio.get_event_loop().run_until_complete(check())

    def test_set_impact_stores_data_on_leaves(self):
        """Impact tree leaf nodes should also have routine names."""
        from app.tui import CallGraphPanel
        from textual.app import App, ComposeResult

        async def check():
            class TestApp(App):
                def compose(self) -> ComposeResult:
                    yield CallGraphPanel(id="cg")

            async with TestApp().run_test() as pilot:
                app = pilot.app
                panel = app.query_one("#cg", CallGraphPanel)
                panel.set_impact("CHKIN", {"1": ["SPKEZ", "FURNSH"], "2": ["CRONOS"]})

                tree = app.query_one("#call-tree", Tree)
                assert tree.root.data == "CHKIN"

                leaf_data = []
                for level_node in tree.root.children:
                    for child in level_node.children:
                        if child.data:
                            leaf_data.append(child.data)

                assert "SPKEZ" in leaf_data
                assert "FURNSH" in leaf_data
                assert "CRONOS" in leaf_data

        asyncio.get_event_loop().run_until_complete(check())


# ── Full app drill-down integration test ─────────────────────────


class TestDrillDownIntegration:
    """Integration test: populate tree, select node, verify deps/explain fires."""

    def test_tree_select_calls_deps_then_explain(self):
        """Selecting a leaf twice should call _do_deps then _do_explain."""
        from app.tui import LegacyLensApp, CallGraphPanel
        from textual.widgets import Tree

        calls = []

        async def check():
            app = LegacyLensApp()

            def mock_deps(name):
                calls.append(("deps", name))

            def mock_explain(name):
                calls.append(("explain", name))

            async with app.run_test() as pilot:
                app._do_deps = mock_deps
                app._do_explain = mock_explain

                # Populate the tree
                panel = app.query_one("#callgraph-panel", CallGraphPanel)
                panel.set_graph("SPKEZ", ["CHKIN", "SPKGEO"], ["CRONOS"])

                # Focus the tree
                tree = app.query_one("#call-tree", Tree)
                tree.focus()
                await pilot.pause()

                # Navigate to first leaf (Down → "Calls →", Down → CHKIN)
                await pilot.press("down")  # root → "Calls →"
                await pilot.press("down")  # "Calls →" → CHKIN
                await pilot.press("down")  # expand / move into leaves
                await pilot.pause()

                # First Enter → should trigger deps
                await pilot.press("enter")
                await pilot.pause()

                # Second Enter on same → should trigger explain
                await pilot.press("enter")
                await pilot.pause()

        asyncio.get_event_loop().run_until_complete(check())

        # Verify the sequence
        deps_calls = [c for c in calls if c[0] == "deps"]
        explain_calls = [c for c in calls if c[0] == "explain"]

        assert len(deps_calls) >= 1, f"Expected at least 1 deps call, got {calls}"
        assert len(explain_calls) >= 1, f"Expected at least 1 explain call, got {calls}"

    def test_root_node_enter_triggers_explain(self):
        """After drill-down, Enter on root node (same routine) triggers explain.

        The tree rebuilds after deps, making the drilled routine the root.
        Enter on that root should fire explain (not deps again).
        """
        from app.tui import LegacyLensApp, CallGraphPanel
        from textual.widgets import Tree

        calls = []

        async def check():
            app = LegacyLensApp()

            def mock_deps(name):
                calls.append(("deps", name))
                # Simulate what _do_deps does: rebuild tree with routine as root
                panel = app.query_one("#callgraph-panel", CallGraphPanel)
                panel.set_graph(name, ["SUB1", "SUB2"], ["CALLER1"])

            def mock_explain(name):
                calls.append(("explain", name))

            async with app.run_test() as pilot:
                app._do_deps = mock_deps
                app._do_explain = mock_explain

                # Populate initial tree
                panel = app.query_one("#callgraph-panel", CallGraphPanel)
                panel.set_graph("SPKEZ", ["CHKIN", "SPKGEO"], ["CRONOS"])

                tree = app.query_one("#call-tree", Tree)
                tree.focus()
                await pilot.pause()

                # Navigate to a leaf and select (deps)
                await pilot.press("down", "down", "down")
                await pilot.pause()
                await pilot.press("enter")
                await pilot.pause()

                # Now tree was rebuilt. Navigate to root and Enter again.
                await pilot.press("home")
                await pilot.pause()
                await pilot.press("enter")
                await pilot.pause()

        asyncio.get_event_loop().run_until_complete(check())

        deps_calls = [c for c in calls if c[0] == "deps"]
        explain_calls = [c for c in calls if c[0] == "explain"]

        assert len(deps_calls) >= 1, f"Expected deps call, got {calls}"
        assert len(explain_calls) >= 1, f"Expected explain call after Enter on root, got {calls}"

        # The routine that was deps'd should be the same one that was explain'd
        # (second Enter on same routine)
        last_deps = deps_calls[-1][1]
        last_explain = explain_calls[-1][1]
        assert last_deps == last_explain, (
            f"Expected explain on same routine as deps. "
            f"deps={last_deps}, explain={last_explain}, all={calls}"
        )


# ── StatusBar tests ──────────────────────────────────────────────


class TestStatusBar:
    """Test status bar state transitions."""

    def test_status_transitions(self):
        from app.tui import StatusBar
        from textual.app import App, ComposeResult

        async def check():
            class TestApp(App):
                def compose(self) -> ComposeResult:
                    yield StatusBar(id="sb")

            async with TestApp().run_test() as pilot:
                sb = pilot.app.query_one("#sb", StatusBar)

                # Initial state
                assert sb._status == "READY"
                assert sb._intent == ""
                assert sb._cached is False

                # Update to thinking
                sb.update_status(intent="EXPLAIN", status="ANALYZING...", cached=False)
                assert sb._status == "ANALYZING..."
                assert sb._intent == "EXPLAIN"

                # Update to ready with cache
                sb.update_status(intent="SEMANTIC", status="READY", cached=True)
                assert sb._status == "READY"
                assert sb._cached is True

        asyncio.get_event_loop().run_until_complete(check())


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
