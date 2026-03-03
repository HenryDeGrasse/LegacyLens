"""Tests for the LegacyLens TUI.

Covers:
- Query history navigation (up/down arrow, draft preservation)
- Call graph tree: Enter = deps drill-down, 'e' = explain, 'i' = impact
- Tree node data storage
- Status bar state transitions
"""

from __future__ import annotations

import asyncio

import pytest
from textual.widgets import Tree


# ── QueryInput history tests ─────────────────────────────────────


class TestQueryInputHistory:
    """Test the up/down arrow history on the QueryInput widget."""

    def _make_input(self):
        from app.tui import QueryInput
        return QueryInput(placeholder="test")

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


# ── CallGraphPanel data storage tests ────────────────────────────


class TestCallGraphPanelData:
    """Test that tree nodes store routine names for drill-down."""

    def test_set_graph_stores_data_on_leaves(self):
        """Leaf nodes should have routine name as .data."""
        from app.tui import CallGraphPanel
        from textual.app import App, ComposeResult

        async def check():
            class TestApp(App):
                def compose(self) -> ComposeResult:
                    yield CallGraphPanel(id="cg")

            async with TestApp().run_test() as pilot:
                app = pilot.app
                panel = app.query_one("#cg", CallGraphPanel)
                panel.set_graph("SPKEZ", ["CHKIN", "SPKGEO"], ["CRONOS"])

                tree = app.query_one("#call-tree", Tree)
                assert tree.root.data == "SPKEZ"

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


# ── Tree action tests (Enter=deps, e=explain, i=impact) ─────────


class TestTreeActions:
    """Test that tree keybindings dispatch to the correct handlers."""

    def test_enter_on_leaf_calls_deps(self):
        """Enter on a leaf node should call _do_deps."""
        from app.tui import LegacyLensApp, CallGraphPanel

        calls = []

        async def check():
            app = LegacyLensApp()

            def mock_deps(name):
                calls.append(("deps", name))

            async with app.run_test() as pilot:
                app._do_deps = mock_deps
                app._do_explain = lambda n: calls.append(("explain", n))

                panel = app.query_one("#callgraph-panel", CallGraphPanel)
                panel.set_graph("SPKEZ", ["CHKIN", "SPKGEO"], ["CRONOS"])

                tree = app.query_one("#call-tree", Tree)
                tree.focus()
                await pilot.pause()

                # Navigate to a leaf
                await pilot.press("down", "down", "down")
                await pilot.pause()

                await pilot.press("enter")
                await pilot.pause()

        asyncio.get_event_loop().run_until_complete(check())

        deps_calls = [c for c in calls if c[0] == "deps"]
        assert len(deps_calls) >= 1, f"Enter should trigger deps, got {calls}"

    def test_enter_never_calls_explain(self):
        """Enter should always trigger deps, never explain."""
        from app.tui import LegacyLensApp, CallGraphPanel

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

                panel = app.query_one("#callgraph-panel", CallGraphPanel)
                panel.set_graph("SPKEZ", ["CHKIN", "SPKGEO"], ["CRONOS"])

                tree = app.query_one("#call-tree", Tree)
                tree.focus()
                await pilot.pause()

                # Navigate to leaf and press Enter multiple times
                await pilot.press("down", "down", "down")
                await pilot.pause()
                await pilot.press("enter")
                await pilot.pause()
                await pilot.press("enter")
                await pilot.pause()
                await pilot.press("enter")
                await pilot.pause()

        asyncio.get_event_loop().run_until_complete(check())

        explain_calls = [c for c in calls if c[0] == "explain"]
        assert len(explain_calls) == 0, f"Enter should never trigger explain, got {calls}"

    def test_e_key_calls_explain_when_tree_focused(self):
        """Pressing 'e' with tree focused should call _do_explain."""
        from app.tui import LegacyLensApp, CallGraphPanel

        calls = []

        async def check():
            app = LegacyLensApp()

            def mock_explain(name):
                calls.append(("explain", name))

            async with app.run_test() as pilot:
                app._do_deps = lambda n: calls.append(("deps", n))
                app._do_explain = mock_explain

                panel = app.query_one("#callgraph-panel", CallGraphPanel)
                panel.set_graph("SPKEZ", ["CHKIN", "SPKGEO"], ["CRONOS"])

                tree = app.query_one("#call-tree", Tree)
                tree.focus()
                await pilot.pause()

                # Navigate to a leaf
                await pilot.press("down", "down", "down")
                await pilot.pause()

                # Press 'e' → should trigger explain
                await pilot.press("e")
                await pilot.pause()

        asyncio.get_event_loop().run_until_complete(check())

        explain_calls = [c for c in calls if c[0] == "explain"]
        assert len(explain_calls) >= 1, f"'e' should trigger explain, got {calls}"

    def test_i_key_calls_impact_when_tree_focused(self):
        """Pressing 'i' with tree focused should call _do_impact."""
        from app.tui import LegacyLensApp, CallGraphPanel

        calls = []

        async def check():
            app = LegacyLensApp()

            def mock_impact(name):
                calls.append(("impact", name))

            async with app.run_test() as pilot:
                app._do_deps = lambda n: calls.append(("deps", n))
                app._do_impact = mock_impact

                panel = app.query_one("#callgraph-panel", CallGraphPanel)
                panel.set_graph("SPKEZ", ["CHKIN", "SPKGEO"], ["CRONOS"])

                tree = app.query_one("#call-tree", Tree)
                tree.focus()
                await pilot.pause()

                # Navigate to a leaf
                await pilot.press("down", "down", "down")
                await pilot.pause()

                # Press 'i' → should trigger impact
                await pilot.press("i")
                await pilot.pause()

        asyncio.get_event_loop().run_until_complete(check())

        impact_calls = [c for c in calls if c[0] == "impact"]
        assert len(impact_calls) >= 1, f"'i' should trigger impact, got {calls}"

    def test_e_key_noop_when_search_focused(self):
        """Pressing 'e' when search input is focused should NOT trigger explain."""
        from app.tui import LegacyLensApp, CallGraphPanel, QueryInput

        calls = []

        async def check():
            app = LegacyLensApp()

            def mock_explain(name):
                calls.append(("explain", name))

            async with app.run_test() as pilot:
                app._do_explain = mock_explain

                # Populate tree so there's a highlighted routine
                panel = app.query_one("#callgraph-panel", CallGraphPanel)
                panel.set_graph("SPKEZ", ["CHKIN"], [])

                # Keep focus on search input (default)
                inp = app.query_one("#query-input", QueryInput)
                inp.focus()
                await pilot.pause()

                # Press 'e' → should type 'e' in input, NOT trigger explain
                await pilot.press("e")
                await pilot.pause()

        asyncio.get_event_loop().run_until_complete(check())

        explain_calls = [c for c in calls if c[0] == "explain"]
        assert len(explain_calls) == 0, f"'e' with input focused should not explain, got {calls}"


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

                assert sb._status == "READY"
                assert sb._intent == ""
                assert sb._cached is False

                sb.update_status(intent="EXPLAIN", status="ANALYZING...", cached=False)
                assert sb._status == "ANALYZING..."
                assert sb._intent == "EXPLAIN"

                sb.update_status(intent="SEMANTIC", status="READY", cached=True)
                assert sb._status == "READY"
                assert sb._cached is True

        asyncio.get_event_loop().run_until_complete(check())


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
