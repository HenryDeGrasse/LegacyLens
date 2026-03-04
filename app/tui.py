"""LegacyLens TUI — Interactive terminal UI for exploring NASA SPICE Fortran code.

Launch:  uv run python -m app.tui
"""

from __future__ import annotations

import asyncio
from typing import Any

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.widgets import (
    Footer,
    Header,
    Input,
    Label,
    Markdown,
    Static,
    Tree,
)
from textual.widgets.tree import TreeNode

# ── Version ──────────────────────────────────────────────────────────
__version__ = "0.8.2"


# ── Helpers ──────────────────────────────────────────────────────────

def _get_call_graph() -> dict | None:
    from app.services import get_call_graph
    return get_call_graph()


def _retrieve_chunks(question: str, top_k: int = 10) -> tuple[Any, list, list[dict]]:
    """Route + retrieve. Returns (routed, raw_chunks, chunk_list) — no LLM call."""
    from app.retrieval.router import route_query
    from app.retrieval.search import retrieve_routed

    routed = route_query(question)
    chunks = retrieve_routed(routed, top_k=top_k)

    chunk_list = []
    for c in (chunks or []):
        meta = c.metadata
        chunk_list.append({
            "routine_name": meta.get("routine_name", "unknown"),
            "chunk_type": meta.get("chunk_type", "unknown"),
            "file_path": meta.get("file_path", "unknown"),
            "start_line": meta.get("start_line", 0),
            "end_line": meta.get("end_line", 0),
            "score": c.score,
            "text": (c.text or meta.get("text", ""))[:2000],
        })

    return routed, chunks or [], chunk_list


def _stream_answer(question: str, context: str):
    """Yield (token, final_response) from the streaming generator."""
    from app.retrieval.generator import generate_answer_stream
    yield from generate_answer_stream(question, context)


def _run_explain(routine_name: str) -> dict:
    """Run explain pipeline synchronously."""
    from app.features.explain import explain_routine
    result = explain_routine(routine_name)
    return {
        "routine_name": result.routine_name,
        "explanation": result.explanation,
        "file_path": result.file_path,
        "start_line": result.start_line,
        "end_line": result.end_line,
        "calls": result.calls,
        "called_by": result.called_by,
        "patterns": result.patterns,
        "usage": result.usage,
    }


def _run_deps(routine_name: str, depth: int = 1) -> dict:
    """Run dependency analysis synchronously."""
    from app.features.dependencies import get_dependencies
    return get_dependencies(routine_name, depth=depth)


def _run_impact(routine_name: str, depth: int = 2) -> dict:
    """Run impact analysis synchronously."""
    from app.features.impact import get_impact
    return get_impact(routine_name, depth=depth)


# ── Custom Widgets ───────────────────────────────────────────────────

class StatusBar(Static):
    """Top status bar showing context and readiness."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._intent = ""
        self._cached = False
        self._status = "READY"

    def update_status(self, intent: str = "", cached: bool = False, status: str = "READY"):
        self._intent = intent
        self._cached = cached
        self._status = status
        self._render_bar()

    def _render_bar(self):
        intent_badge = f"[bold cyan][{self._intent}][/]" if self._intent else ""
        cache_badge = " [dim italic](cached)[/]" if self._cached else ""
        status_color = "green" if self._status == "READY" else "yellow"
        self.update(
            f"  {intent_badge}{cache_badge}"
            f"[{status_color} bold]  ⟨{self._status}⟩[/]"
        )

    def on_mount(self):
        self._render_bar()


class AnswerPanel(VerticalScroll):
    """Left panel: shows the LLM answer as Markdown."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._question = ""
        self._answer_so_far = ""

    def compose(self) -> ComposeResult:
        yield Markdown("*Ask a question below to get started...*", id="answer-md")

    def set_answer(self, question: str, answer: str):
        self._question = question
        self._answer_so_far = answer
        self._refresh()

    def start_streaming(self, question: str):
        """Reset panel for a new streaming answer."""
        self._question = question
        self._answer_so_far = ""
        self._refresh()

    def append_token(self, token: str):
        """Append a streaming token and re-render."""
        self._answer_so_far += token
        self._refresh()

    def _refresh(self):
        md = self.query_one("#answer-md", Markdown)
        if self._answer_so_far:
            content = f"**USER>** {self._question}\n\n---\n\n**LEGACYLENS>** {self._answer_so_far}"
        else:
            content = f"**USER>** {self._question}\n\n---\n\n*Generating...*"
        md.update(content)


class CallGraphPanel(VerticalScroll):
    """Right panel: shows the call graph as a tree.
    
    Tree node data stores routine names for drill-down.
    Click a leaf → deps (instant). Enter on leaf → explain (LLM).
    """

    def compose(self) -> ComposeResult:
        tree: Tree[str] = Tree("Call Graph", id="call-tree")
        tree.show_root = True
        tree.root.expand()
        yield tree

    def set_graph(self, routine_name: str, forward: list[str], reverse: list[str]):
        tree = self.query_one("#call-tree", Tree)
        tree.clear()
        tree.root.data = routine_name
        tree.root.set_label(f"[bold]{routine_name}[/]")

        if forward:
            calls_node = tree.root.add("[cyan]Calls →[/]", expand=True)
            for name in forward[:20]:
                leaf = calls_node.add_leaf(f"[green]{name}[/]")
                leaf.data = name  # store routine name for drill-down

        if reverse:
            callers_node = tree.root.add("[magenta]← Called by[/]", expand=True)
            for name in reverse[:20]:
                leaf = callers_node.add_leaf(f"[yellow]{name}[/]")
                leaf.data = name

        tree.root.expand()

    def set_impact(self, routine_name: str, levels: dict):
        tree = self.query_one("#call-tree", Tree)
        tree.clear()
        tree.root.data = routine_name
        tree.root.set_label(f"[bold red]Impact: {routine_name}[/]")

        for level_str, routines in levels.items():
            if routines:
                level_node = tree.root.add(f"[yellow]Level {level_str}[/] ({len(routines)})", expand=True)
                for name in routines[:15]:
                    leaf = level_node.add_leaf(f"{name}")
                    leaf.data = name

        tree.root.expand()


class SourcePanel(VerticalScroll):
    """Bottom panel: shows source code chunks."""

    def compose(self) -> ComposeResult:
        yield Markdown("*Source code will appear here after a query.*", id="source-md")

    def set_chunks(self, chunks: list[dict]):
        if not chunks:
            md = self.query_one("#source-md", Markdown)
            md.update("*No source chunks retrieved.*")
            return

        parts = []
        for i, c in enumerate(chunks[:5]):
            header = (
                f"**{c['routine_name']}** ({c['chunk_type']}) — "
                f"`{c['file_path']}:{c['start_line']}-{c['end_line']}` "
                f"— Score: {c['score']:.3f}"
            )
            # Fortran code block
            code = c["text"][:1200]
            parts.append(f"{header}\n\n```fortran\n{code}\n```")

        md = self.query_one("#source-md", Markdown)
        md.update("\n\n---\n\n".join(parts))


class QueryInput(Input):
    """The main query input with up/down arrow history navigation."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._history: list[str] = []
        self._history_idx: int = -1  # -1 = not browsing history
        self._draft: str = ""  # saves current input when browsing

    def add_to_history(self, text: str):
        """Add a submitted query to history."""
        if text and (not self._history or self._history[-1] != text):
            self._history.append(text)
        self._history_idx = -1
        self._draft = ""

    def _on_key(self, event) -> None:
        if event.key == "up":
            if not self._history:
                return
            if self._history_idx == -1:
                # Starting to browse — save current draft
                self._draft = self.value
                self._history_idx = len(self._history) - 1
            elif self._history_idx > 0:
                self._history_idx -= 1
            self.value = self._history[self._history_idx]
            self.cursor_position = len(self.value)
            event.prevent_default()
            event.stop()
        elif event.key == "down":
            if self._history_idx == -1:
                return
            if self._history_idx < len(self._history) - 1:
                self._history_idx += 1
                self.value = self._history[self._history_idx]
            else:
                # Past the end — restore draft
                self._history_idx = -1
                self.value = self._draft
            self.cursor_position = len(self.value)
            event.prevent_default()
            event.stop()
        else:
            # Any other key resets history browsing
            if self._history_idx != -1:
                self._history_idx = -1


# ── Main App ─────────────────────────────────────────────────────────

MAIN_CSS = """
Screen {
    layout: vertical;
}

#status-bar {
    dock: top;
    height: 1;
    background: $surface;
    color: $text;
    text-style: bold;
}

#main-area {
    height: 1fr;
}

#top-panels {
    height: 2fr;
}

#answer-panel {
    width: 1fr;
    border: round $accent;
    border-title-color: $success;
    border-title-style: bold;
}

#callgraph-panel {
    width: 1fr;
    border: round $accent;
    border-title-color: $warning;
    border-title-style: bold;
}

#source-panel {
    height: 1fr;
    border: round $accent;
    border-title-color: $primary;
    border-title-style: bold;
}

#query-input {
    dock: bottom;
    margin: 0 1;
    height: 3;
}

Footer {
    dock: bottom;
}
"""


class LegacyLensApp(App):
    """LegacyLens — NASA SPICE Legacy Code Assistant."""

    TITLE = "LegacyLens — NASA SPICE Legacy Code Assistant"
    SUB_TITLE = f"v{__version__}"
    CSS = MAIN_CSS

    BINDINGS = [
        Binding("f1", "focus_search", "Search", show=True),
        Binding("f2", "focus_tree", "Tree Nav", show=True),
        Binding("f3", "show_calltree", "Call Tree", show=True),
        Binding("f4", "show_docs", "Docs", show=True),
        Binding("e", "explain_node", "Explain", show=False, priority=True),
        Binding("i", "impact_node", "Impact", show=False, priority=True),
        Binding("ctrl+q", "quit", "Quit", show=True),
        Binding("escape", "focus_search", "Focus Search", show=False),
    ]

    def __init__(self):
        super().__init__()
        self._last_routines: list[str] = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield StatusBar(id="status-bar")
        with Vertical(id="main-area"):
            with Horizontal(id="top-panels"):
                yield AnswerPanel(id="answer-panel")
                yield CallGraphPanel(id="callgraph-panel")
            yield SourcePanel(id="source-panel")
        yield QueryInput(
            placeholder="Ask about SPICE Fortran code... (e.g., 'What does SPKEZ do?')",
            id="query-input",
        )
        yield Footer()

    def on_mount(self) -> None:
        # Set border titles
        self.query_one("#answer-panel").border_title = "Query / Explanation"
        self.query_one("#callgraph-panel").border_title = "Call Graph / Dependencies"
        self.query_one("#source-panel").border_title = "Source Code (Annotated)"

        # Focus the input
        self.query_one("#query-input", QueryInput).focus()

        # Pre-warm clients in background (saves ~0.6s on first query)
        self._prewarm()

    @work(thread=True)
    def _prewarm(self) -> None:
        """Initialize API clients and load call graph in background."""
        try:
            from app.services import get_llm, get_openai, get_index, get_call_graph
            get_openai()  # embeddings client
            get_llm()     # LLM completions client
            get_index()
            get_call_graph()
        except Exception:
            pass  # Non-fatal, will init on first query

    # ── Actions ──────────────────────────────────────────────────

    def action_focus_search(self) -> None:
        self.query_one("#query-input", QueryInput).focus()

    def action_focus_tree(self) -> None:
        self.query_one("#call-tree", Tree).focus()

    def action_show_calltree(self) -> None:
        if self._last_routines:
            self._do_deps(self._last_routines[0])

    def action_show_docs(self) -> None:
        if self._last_routines:
            self._do_explain(self._last_routines[0])

    # ── Tree drill-down ────────────────────────────────────────

    def _get_highlighted_routine(self) -> str | None:
        """Get the routine name from the currently highlighted tree node."""
        try:
            tree = self.query_one("#call-tree", Tree)
            node = tree.cursor_node
            if node and node.data and isinstance(node.data, str):
                return node.data
        except Exception:
            pass
        return None

    @on(Tree.NodeSelected, "#call-tree")
    def handle_tree_select(self, event: Tree.NodeSelected) -> None:
        """Enter/click on a routine → instant deps drill-down."""
        node = event.node
        routine_name = node.data
        if not routine_name or not isinstance(routine_name, str):
            return
        # Only act on leaf nodes (skip category labels like "Calls →")
        if not node.children:
            self._do_deps(routine_name)

    @on(Tree.NodeHighlighted, "#call-tree")
    def handle_tree_highlight(self, event: Tree.NodeHighlighted) -> None:
        """Update border title with action hints for the highlighted node."""
        node = event.node
        routine_name = node.data
        cg_panel = self.query_one("#callgraph-panel")
        if routine_name and isinstance(routine_name, str) and not node.children:
            cg_panel.border_title = f"Call Graph — {routine_name}  ↵ drill  e=explain  i=impact"
        else:
            cg_panel.border_title = "Call Graph / Dependencies"

    def action_explain_node(self) -> None:
        """Press 'e' while tree is focused → explain the highlighted routine."""
        # Only fire when the tree has focus
        try:
            tree = self.query_one("#call-tree", Tree)
            if not tree.has_focus:
                return
        except Exception:
            return
        routine = self._get_highlighted_routine()
        if routine:
            self._do_explain(routine)

    def action_impact_node(self) -> None:
        """Press 'i' while tree is focused → impact analysis on highlighted routine."""
        try:
            tree = self.query_one("#call-tree", Tree)
            if not tree.has_focus:
                return
        except Exception:
            return
        routine = self._get_highlighted_routine()
        if routine:
            self._do_impact(routine)

    # ── Input handling ───────────────────────────────────────────

    @on(Input.Submitted, "#query-input")
    def handle_query(self, event: Input.Submitted) -> None:
        question = event.value.strip()
        if not question:
            return

        # Save to history and clear input
        inp = self.query_one("#query-input", QueryInput)
        inp.add_to_history(question)
        inp.value = ""

        # Check for special commands
        lower = question.lower()
        if lower.startswith("/explain ") or lower.startswith("/e "):
            routine = question.split(maxsplit=1)[1].strip().upper()
            self._do_explain(routine)
            return
        if lower.startswith("/deps ") or lower.startswith("/d "):
            routine = question.split(maxsplit=1)[1].strip().upper()
            self._do_deps(routine)
            return
        if lower.startswith("/impact ") or lower.startswith("/i "):
            routine = question.split(maxsplit=1)[1].strip().upper()
            self._do_impact(routine)
            return
        if lower.startswith("/metrics ") or lower.startswith("/m "):
            routine = question.split(maxsplit=1)[1].strip().upper()
            self._do_metrics(routine)
            return
        if lower == "/help":
            self._show_help()
            return

        # Normal query
        self._do_query(question)

    # ── Workers (async, non-blocking) ────────────────────────────

    @work(thread=True)
    def _do_query(self, question: str) -> None:
        status = self.query_one("#status-bar", StatusBar)
        status.update_status(status="SEARCHING...")

        # Phase 1: Retrieve chunks (fast — ~1.5s)
        try:
            routed, raw_chunks, chunk_list = _retrieve_chunks(question)
        except Exception as e:
            self.call_from_thread(self._show_error, str(e))
            return

        # Show chunks + call graph immediately while LLM generates
        self.call_from_thread(self._display_retrieval, question, routed, chunk_list)

        if not chunk_list:
            self.call_from_thread(self._show_error, "No relevant chunks found.")
            return

        # Phase 2: Stream LLM answer (reuse raw_chunks, no double-fetch)
        try:
            from app.retrieval.context import assemble_context
            from app.retrieval.router import QueryIntent

            # Smaller context for structural queries (deps/impact) — cuts TTFT
            ctx_budget = {
                QueryIntent.DEPENDENCY: 2000,
                QueryIntent.IMPACT: 2500,
            }.get(routed.intent)
            context = assemble_context(raw_chunks, max_tokens=ctx_budget)

            answer_panel = self.query_one("#answer-panel", AnswerPanel)
            self.call_from_thread(lambda: answer_panel.start_streaming(question))

            final_resp = None
            for token, resp in _stream_answer(question, context):
                if resp is not None:
                    final_resp = resp
                elif token is not None:
                    self.call_from_thread(lambda t=token: answer_panel.append_token(t))

            cached = final_resp.cached if final_resp else False
            self.call_from_thread(
                lambda: status.update_status(
                    intent=routed.intent.name, cached=cached, status="READY"
                )
            )
        except Exception as e:
            self.call_from_thread(self._show_error, str(e))

    @work(thread=True)
    def _do_explain(self, routine_name: str) -> None:
        status = self.query_one("#status-bar", StatusBar)
        status.update_status(intent="EXPLAIN", status="ANALYZING...")

        answer_panel = self.query_one("#answer-panel", AnswerPanel)
        self.call_from_thread(lambda: answer_panel.start_streaming(f"/explain {routine_name}"))

        try:
            from app.features.explain import explain_routine_stream

            metadata = None
            for token, meta in explain_routine_stream(routine_name):
                if meta is not None:
                    metadata = meta
                elif token is not None:
                    self.call_from_thread(lambda t=token: answer_panel.append_token(t))

            # Update call graph with the routine's deps
            if metadata:
                self.call_from_thread(
                    self._finish_explain_stream, routine_name, metadata
                )
            else:
                self.call_from_thread(
                    lambda: status.update_status(intent="EXPLAIN", status="READY")
                )
        except Exception as e:
            self.call_from_thread(self._show_error, str(e))

    @work(thread=True)
    def _do_deps(self, routine_name: str) -> None:
        status = self.query_one("#status-bar", StatusBar)
        status.update_status(intent="DEPENDENCY", status="RESOLVING...")

        try:
            result = _run_deps(routine_name, depth=2)
        except Exception as e:
            self.call_from_thread(self._show_error, str(e))
            return

        self.call_from_thread(self._display_deps_result, result)

    @work(thread=True)
    def _do_metrics(self, routine_name: str) -> None:
        status = self.query_one("#status-bar", StatusBar)
        status.update_status(intent="METRICS", status="ANALYZING...")

        try:
            from app.features.metrics import get_metrics
            result = get_metrics(routine_name)
        except Exception as e:
            self.call_from_thread(self._show_error, str(e))
            return

        self.call_from_thread(self._display_metrics_result, result)

    @work(thread=True)
    def _do_impact(self, routine_name: str) -> None:
        status = self.query_one("#status-bar", StatusBar)
        status.update_status(intent="IMPACT", status="CALCULATING...")

        try:
            result = _run_impact(routine_name, depth=2)
        except Exception as e:
            self.call_from_thread(self._show_error, str(e))
            return

        self.call_from_thread(self._display_impact_result, result)

    # ── Display methods (called on main thread) ──────────────────

    def _display_retrieval(self, question: str, routed, chunk_list: list[dict]) -> None:
        """Show chunks + call graph immediately (before LLM answer arrives)."""
        status = self.query_one("#status-bar", StatusBar)
        status.update_status(intent=routed.intent.name, status="GENERATING...")

        # Track routines for F3/F4
        self._last_routines = routed.routine_names
        if not self._last_routines and chunk_list:
            self._last_routines = [chunk_list[0]["routine_name"]]

        # Show source chunks immediately
        source_panel = self.query_one("#source-panel", SourcePanel)
        source_panel.set_chunks(chunk_list)

        # Show call graph immediately
        if self._last_routines:
            self._populate_callgraph(self._last_routines[0])

    def _display_explain_result(self, result: dict) -> None:
        status = self.query_one("#status-bar", StatusBar)
        status.update_status(intent="EXPLAIN", status="READY")

        self._last_routines = [result["routine_name"]]

        answer_panel = self.query_one("#answer-panel", AnswerPanel)
        answer_panel.set_answer(
            f"/explain {result['routine_name']}",
            result["explanation"],
        )

        # Show call graph
        cg_panel = self.query_one("#callgraph-panel", CallGraphPanel)
        cg_panel.set_graph(
            result["routine_name"],
            result.get("calls", []),
            result.get("called_by", []),
        )

    def _finish_explain_stream(self, routine_name: str, metadata: dict) -> None:
        """Called after streaming explain completes — update call graph + status."""
        status = self.query_one("#status-bar", StatusBar)
        status.update_status(intent="EXPLAIN", status="READY")

        self._last_routines = [routine_name]

        cg_panel = self.query_one("#callgraph-panel", CallGraphPanel)
        cg_panel.set_graph(
            routine_name,
            metadata.get("calls", []),
            metadata.get("called_by", []),
        )

    def _display_deps_result(self, result: dict) -> None:
        status = self.query_one("#status-bar", StatusBar)
        status.update_status(intent="DEPENDENCY", status="READY")

        self._last_routines = [result["routine_name"]]

        # Show in call graph panel
        cg_panel = self.query_one("#callgraph-panel", CallGraphPanel)
        cg_panel.set_graph(
            result["routine_name"],
            result.get("all_callees", []),
            result.get("all_callers", []),
        )

        # Show summary in answer panel
        answer_panel = self.query_one("#answer-panel", AnswerPanel)
        summary = (
            f"## Dependencies: {result['routine_name']}\n\n"
            f"**File:** `{result.get('file_path', 'unknown')}`\n\n"
            f"**Direct calls:** {', '.join(result.get('direct_calls', [])) or '(none)'}\n\n"
            f"**All callees (depth 2):** {len(result.get('all_callees', []))} routines\n\n"
            f"**All callers:** {len(result.get('all_callers', []))} routines"
        )
        answer_panel.set_answer(f"/deps {result['routine_name']}", summary)

    def _display_impact_result(self, result: dict) -> None:
        status = self.query_one("#status-bar", StatusBar)
        status.update_status(intent="IMPACT", status="READY")

        self._last_routines = [result["routine_name"]]

        # Show in call graph panel as impact tree
        cg_panel = self.query_one("#callgraph-panel", CallGraphPanel)
        cg_panel.set_impact(result["routine_name"], result.get("levels", {}))

        # Summary in answer
        answer_panel = self.query_one("#answer-panel", AnswerPanel)
        summary = (
            f"## Impact Analysis: {result['routine_name']}\n\n"
            f"**Total affected:** {result.get('total_affected', 0)} routines\n\n"
        )
        for level, routines in result.get("levels", {}).items():
            summary += f"**Level {level}:** {', '.join(routines[:10])}"
            if len(routines) > 10:
                summary += f" (+{len(routines)-10} more)"
            summary += "\n\n"
        answer_panel.set_answer(f"/impact {result['routine_name']}", summary)

    def _display_metrics_result(self, result: dict) -> None:
        status = self.query_one("#status-bar", StatusBar)
        status.update_status(intent="METRICS", status="READY")

        if "error" in result:
            self._show_error(result["error"])
            return

        name = result["routine_name"]
        self._last_routines = [name]

        loc = result.get("loc", {})
        cx = result.get("complexity", {})
        deps = result.get("dependencies", {})

        summary = (
            f"## Metrics: {name}\n\n"
            f"**File:** `{result.get('file_path', 'unknown')}`\n\n"
            f"### Lines of Code\n"
            f"| Metric | Value |\n|---|---|\n"
            f"| Total | {loc.get('total', 0)} |\n"
            f"| Code | {loc.get('code', 0)} |\n"
            f"| Comments | {loc.get('comment', 0)} ({int(loc.get('comment_ratio', 0)*100)}%) |\n"
            f"| Size | **{result.get('size_rating', '?')}** |\n\n"
            f"### Complexity\n"
            f"| Metric | Value |\n|---|---|\n"
            f"| Cyclomatic | {cx.get('cyclomatic', 0)} |\n"
            f"| Max Depth | {cx.get('max_nesting_depth', 0)} |\n"
            f"| Rating | **{cx.get('rating', '?')}** |\n"
            f"| Params | {result.get('parameters', 0)} |\n\n"
            f"### Dependencies\n"
            f"| Calls | Callers |\n|---|---|\n"
            f"| {deps.get('calls', 0)} | {deps.get('callers', 0)} |\n"
        )
        if result.get("patterns"):
            summary += f"\n**Patterns:** {', '.join(result['patterns'])}"

        answer_panel = self.query_one("#answer-panel", AnswerPanel)
        answer_panel.set_answer(f"/metrics {name}", summary)

        # Also populate call graph
        self._populate_callgraph(name)

    def _populate_callgraph(self, routine_name: str) -> None:
        """Populate call graph panel from local call graph data."""
        cg = _get_call_graph()
        if not cg:
            return

        name_upper = routine_name.upper()
        # Check aliases
        aliases = cg.get("aliases", {})
        resolved = aliases.get(name_upper, name_upper)

        forward = cg.get("forward", {}).get(resolved, [])
        reverse = cg.get("reverse", {}).get(resolved, [])

        cg_panel = self.query_one("#callgraph-panel", CallGraphPanel)
        cg_panel.set_graph(resolved, forward, reverse)

    def _show_error(self, message: str) -> None:
        status = self.query_one("#status-bar", StatusBar)
        status.update_status(status="ERROR")

        answer_panel = self.query_one("#answer-panel", AnswerPanel)
        md = answer_panel.query_one("#answer-md", Markdown)
        md.update(f"## ❌ Error\n\n```\n{message}\n```")

    def _show_help(self) -> None:
        answer_panel = self.query_one("#answer-panel", AnswerPanel)
        help_text = """\
## LegacyLens Commands

| Command | Description |
|---|---|
| *(any question)* | Natural language query about SPICE code |
| `/explain ROUTINE` | Detailed explanation of a routine |
| `/deps ROUTINE` | Show call graph dependencies |
| `/impact ROUTINE` | Blast radius analysis |
| `/metrics ROUTINE` | Code complexity metrics (no LLM) |
| `/help` | Show this help |

### Keyboard Shortcuts

| Key | Action |
|---|---|
| **F1** | Focus search |
| **F3** | Show call tree for last routine |
| **F4** | Explain last routine |
| **Ctrl+Q** | Quit |

### Example Queries

- `What does SPKEZ do?`
- `How does light time correction work?`
- `Which routines handle coordinate transformations?`
- `/explain FURNSH`
- `/deps SPKGEO`
- `/impact CHKIN`
"""
        md = answer_panel.query_one("#answer-md", Markdown)
        md.update(help_text)

        status = self.query_one("#status-bar", StatusBar)
        status.update_status(status="READY")


# ── Entry point ──────────────────────────────────────────────────────

def main():
    app = LegacyLensApp()
    app.run()


if __name__ == "__main__":
    main()
