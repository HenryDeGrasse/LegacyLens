"""CLI query interface for LegacyLens."""

from __future__ import annotations

import argparse
import sys

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax

from app.retrieval.search import retrieve
from app.retrieval.context import assemble_context
from app.retrieval.generator import generate_answer


console = Console()


def query(question: str, top_k: int = 10, verbose: bool = False):
    """Execute a query against the SPICE codebase."""

    console.print(f"\n[bold cyan]🔍 Query:[/] {question}\n")

    # Step 1: Retrieve
    with console.status("[yellow]Searching codebase...[/]"):
        chunks = retrieve(question, top_k=top_k)

    if not chunks:
        console.print("[red]No relevant chunks found.[/]")
        return

    if verbose:
        console.print(f"[dim]Retrieved {len(chunks)} chunks[/]")
        for i, c in enumerate(chunks):
            console.print(
                f"  [dim]{i+1}. {c.metadata.get('routine_name', '?')} "
                f"({c.metadata.get('chunk_type', '?')}) "
                f"score={c.score:.3f}[/]"
            )
        console.print()

    # Step 2: Assemble context
    context = assemble_context(chunks)

    # Step 3: Generate answer
    with console.status("[yellow]Generating answer...[/]"):
        response = generate_answer(question, context)

    # Display answer
    console.print(Panel(
        Markdown(response.answer),
        title="[bold green]Answer[/]",
        border_style="green",
    ))

    # Display citations
    if response.citations:
        console.print(f"\n[bold]📎 Citations ({len(response.citations)}):[/]")
        for cite in response.citations:
            console.print(
                f"  • {cite['file_path']}:{cite['start_line']}-{cite['end_line']}"
            )

    # Display retrieved snippets
    console.print(f"\n[bold]📦 Retrieved Chunks ({len(chunks)}):[/]\n")
    for i, chunk in enumerate(chunks[:5]):  # Show top 5
        meta = chunk.metadata
        text = meta.get("text", "")[:500]
        routine = meta.get("routine_name", "unknown")
        chunk_type = meta.get("chunk_type", "unknown")
        file_path = meta.get("file_path", "unknown")
        start_line = meta.get("start_line", "?")
        end_line = meta.get("end_line", "?")

        console.print(Panel(
            Syntax(text, "fortran", line_numbers=False, word_wrap=True),
            title=f"[bold]{routine}[/] ({chunk_type}) — {file_path}:{start_line}-{end_line}",
            subtitle=f"Score: {chunk.score:.3f}",
            border_style="blue",
        ))

    # Usage info
    if response.usage:
        tokens = response.usage.get("total_tokens", 0)
        cost = tokens * 0.15 / 1_000_000  # Rough GPT-4o-mini cost
        console.print(f"\n[dim]Tokens: {tokens} | Est. cost: ${cost:.4f} | Model: {response.model}[/]")


def main():
    parser = argparse.ArgumentParser(description="Query the SPICE Toolkit codebase")
    parser.add_argument("question", help="Natural language question about the codebase")
    parser.add_argument("--top-k", type=int, default=10, help="Number of chunks to retrieve")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show retrieval details")
    args = parser.parse_args()

    query(args.question, top_k=args.top_k, verbose=args.verbose)


if __name__ == "__main__":
    main()
