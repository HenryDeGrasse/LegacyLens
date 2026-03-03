"""CLI interface for LegacyLens.

Subcommands:
  query       — Natural language query with RAG pipeline
  explain     — Detailed explanation of a routine
  deps        — Forward/reverse call graph
  impact      — Blast radius analysis
  patterns    — List or search SPICE patterns
  docgen      — Generate Markdown documentation
  eval        — Run the golden test set
"""

from __future__ import annotations

import argparse
import json
import sys

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

console = Console()


# ── query ────────────────────────────────────────────────────────────

def cmd_query(args):
    """Execute a natural language query against the SPICE codebase."""
    from app.retrieval.router import route_query
    from app.retrieval.search import retrieve_routed
    from app.retrieval.context import assemble_context
    from app.retrieval.generator import generate_answer

    console.print(f"\n[bold cyan]Query:[/] {args.question}\n")

    # Route
    routed = route_query(args.question)
    if args.verbose:
        console.print(
            f"[dim]Router → intent={routed.intent.name}  "
            f"routines={routed.routine_names}  "
            f"patterns={routed.patterns}  "
            f"prefer_doc={routed.prefer_doc}[/]\n"
        )

    # Retrieve
    with console.status("[yellow]Searching codebase...[/]"):
        chunks = retrieve_routed(routed, top_k=args.top_k)

    if not chunks:
        console.print("[red]No relevant chunks found.[/]")
        return

    if args.verbose:
        console.print(f"[dim]Retrieved {len(chunks)} chunks[/]")
        for i, c in enumerate(chunks):
            meta = c.metadata
            console.print(
                f"  [dim]{i+1}. {meta.get('routine_name', '?')} "
                f"({meta.get('chunk_type', '?')}) "
                f"score={c.score:.3f}[/]"
            )
        console.print()

    # Assemble + Generate
    context = assemble_context(chunks)

    with console.status("[yellow]Generating answer...[/]"):
        response = generate_answer(args.question, context)

    # Display
    label = "[bold green]Answer[/]"
    if response.cached:
        label += " [dim](cached)[/]"
    console.print(Panel(Markdown(response.answer), title=label, border_style="green"))

    if response.citations:
        console.print(f"\n[bold]Citations ({len(response.citations)}):[/]")
        for cite in response.citations:
            console.print(f"  • {cite['file_path']}:{cite['start_line']}-{cite['end_line']}")

    if not args.quiet:
        console.print(f"\n[bold]Top Chunks ({min(5, len(chunks))}):[/]\n")
        for chunk in chunks[:5]:
            meta = chunk.metadata
            text = (chunk.text or meta.get("text", ""))[:500]
            console.print(Panel(
                Syntax(text, "fortran", line_numbers=False, word_wrap=True),
                title=(
                    f"[bold]{meta.get('routine_name', '?')}[/] "
                    f"({meta.get('chunk_type', '?')}) — "
                    f"{meta.get('file_path', '?')}:{meta.get('start_line', '?')}-{meta.get('end_line', '?')}"
                ),
                subtitle=f"Score: {chunk.score:.3f}",
                border_style="blue",
            ))

    if response.usage:
        tokens = response.usage.get("total_tokens", 0)
        cost = tokens * 0.15 / 1_000_000
        console.print(
            f"\n[dim]Intent: {routed.intent.name} | "
            f"Tokens: {tokens} | Cost: ${cost:.4f} | "
            f"Model: {response.model} | "
            f"Cached: {response.cached}[/]"
        )


# ── explain ──────────────────────────────────────────────────────────

def cmd_explain(args):
    """Explain a SPICE routine in plain English."""
    from app.features.explain import explain_routine

    console.print(f"\n[bold cyan]Explaining:[/] {args.routine}\n")

    with console.status("[yellow]Analysing routine...[/]"):
        result = explain_routine(args.routine)

    console.print(Panel(
        Markdown(result.explanation),
        title=f"[bold green]{result.routine_name}[/] — {result.file_path}:{result.start_line}-{result.end_line}",
        border_style="green",
    ))

    if result.calls:
        console.print(f"\n[bold]Calls:[/] {', '.join(result.calls[:20])}")
    if result.called_by:
        console.print(f"[bold]Called by:[/] {', '.join(result.called_by[:20])}")
    if result.patterns:
        console.print(f"[bold]Patterns:[/] {', '.join(result.patterns)}")
    if result.usage:
        console.print(f"[dim]Tokens: {result.usage.get('total_tokens', 0)}[/]")


# ── deps ─────────────────────────────────────────────────────────────

def cmd_deps(args):
    """Show forward/reverse call graph for a routine."""
    from app.features.dependencies import get_dependencies

    console.print(f"\n[bold cyan]Dependencies:[/] {args.routine} (depth={args.depth})\n")

    result = get_dependencies(args.routine, depth=args.depth)

    table = Table(title=f"{result['routine_name']} Call Graph")
    table.add_column("Direction", style="bold")
    table.add_column("Routines")

    if result.get("is_entry_point"):
        table.add_row("ENTRY in", result.get("parent_routine", "?"))

    table.add_row("Direct calls", ", ".join(result["direct_calls"]) or "(none)")
    table.add_row("All callees", ", ".join(result["all_callees"]) or "(none)")
    table.add_row("All callers", ", ".join(result["all_callers"]) or "(none)")

    console.print(table)
    console.print(f"\n[dim]File: {result['file_path']}[/]")


# ── impact ───────────────────────────────────────────────────────────

def cmd_impact(args):
    """Show blast radius of changing a routine."""
    from app.features.impact import get_impact

    console.print(f"\n[bold cyan]Impact Analysis:[/] {args.routine} (depth={args.depth})\n")

    result = get_impact(args.routine, depth=args.depth)

    console.print(f"[bold]Total affected:[/] {result['total_affected']} routines\n")

    for level_str, routines in result["levels"].items():
        console.print(f"  [bold]Level {level_str}:[/] {', '.join(routines) or '(none)'}")

    console.print(f"\n[dim]File: {result['file_path']}[/]")


# ── patterns ─────────────────────────────────────────────────────────

def cmd_patterns(args):
    """List or search SPICE coding patterns."""
    from app.features.patterns import list_patterns, search_pattern

    if args.search:
        console.print(f"\n[bold cyan]Pattern search:[/] {args.search}\n")

        with console.status("[yellow]Searching...[/]"):
            result = search_pattern(args.search, query=args.query or "", top_k=args.top_k)

        if "error" in result:
            console.print(f"[red]{result['error']}[/]")
            return

        table = Table(title=f"Pattern: {result['pattern']}")
        table.add_column("Routine", style="bold")
        table.add_column("Score")
        table.add_column("Abstract")
        table.add_column("Patterns")

        for r in result["results"]:
            table.add_row(
                r["routine_name"],
                f"{r['score']:.3f}",
                r["abstract"][:60],
                r["all_patterns"][:40],
            )
        console.print(table)
    else:
        console.print("\n[bold cyan]Available SPICE Patterns:[/]\n")
        for p in list_patterns():
            console.print(f"  [bold]{p['name']}[/]")
            console.print(f"    {p['description']}")
            console.print(f"    [dim]Example: {p['example_query']}[/]\n")


# ── docgen ───────────────────────────────────────────────────────────

def cmd_docgen(args):
    """Generate Markdown documentation for a routine."""
    from app.features.docgen import generate_doc

    console.print(f"\n[bold cyan]Generating docs:[/] {args.routine}\n")

    with console.status("[yellow]Generating documentation...[/]"):
        result = generate_doc(args.routine)

    if args.output:
        from pathlib import Path
        Path(args.output).write_text(result["markdown"])
        console.print(f"[green]Documentation saved to {args.output}[/]")
    else:
        console.print(Markdown(result["markdown"]))

    if result.get("usage"):
        console.print(f"\n[dim]Tokens: {result['usage'].get('total_tokens', 0)}[/]")


# ── main ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        prog="legacylens",
        description="LegacyLens — Query NASA's SPICE Toolkit Fortran codebase with natural language",
    )
    sub = parser.add_subparsers(dest="command")

    # query
    p_query = sub.add_parser("query", aliases=["q"], help="Natural language query")
    p_query.add_argument("question", help="Your question about the SPICE codebase")
    p_query.add_argument("--top-k", type=int, default=10)
    p_query.add_argument("-v", "--verbose", action="store_true", help="Show router + retrieval details")
    p_query.add_argument("-q", "--quiet", action="store_true", help="Answer only, no chunks")
    p_query.set_defaults(func=cmd_query)

    # explain
    p_explain = sub.add_parser("explain", aliases=["e"], help="Explain a routine")
    p_explain.add_argument("routine", help="Routine name (e.g. SPKEZ, FURNSH)")
    p_explain.set_defaults(func=cmd_explain)

    # deps
    p_deps = sub.add_parser("deps", aliases=["d"], help="Show call graph")
    p_deps.add_argument("routine", help="Routine name")
    p_deps.add_argument("--depth", type=int, default=1)
    p_deps.set_defaults(func=cmd_deps)

    # impact
    p_impact = sub.add_parser("impact", aliases=["i"], help="Blast radius analysis")
    p_impact.add_argument("routine", help="Routine name")
    p_impact.add_argument("--depth", type=int, default=2)
    p_impact.set_defaults(func=cmd_impact)

    # patterns
    p_patterns = sub.add_parser("patterns", aliases=["p"], help="List or search patterns")
    p_patterns.add_argument("--search", "-s", help="Pattern name to search")
    p_patterns.add_argument("--query", help="Refine pattern search with a question")
    p_patterns.add_argument("--top-k", type=int, default=10)
    p_patterns.set_defaults(func=cmd_patterns)

    # docgen
    p_docgen = sub.add_parser("docgen", help="Generate Markdown docs")
    p_docgen.add_argument("routine", help="Routine name")
    p_docgen.add_argument("-o", "--output", help="Write to file instead of stdout")
    p_docgen.set_defaults(func=cmd_docgen)

    # Backward compat: bare string = query
    args = parser.parse_args()

    if args.command is None:
        # If no subcommand, check for positional args and treat as query
        if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
            # Legacy: python -m app.cli "question"
            args.question = " ".join(sys.argv[1:])
            args.top_k = 10
            args.verbose = False
            args.quiet = False
            cmd_query(args)
        else:
            parser.print_help()
        return

    args.func(args)


if __name__ == "__main__":
    main()
