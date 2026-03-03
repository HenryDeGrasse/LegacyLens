# LegacyLens 🔍🛰️

> RAG-powered system for making NASA's SPICE Toolkit Fortran 77 codebase queryable and understandable through natural language.

**Live API:** https://legacylens-production-9578.up.railway.app

## Overview

LegacyLens builds a Retrieval-Augmented Generation (RAG) pipeline over NASA's [NAIF SPICE Toolkit](https://naif.jpl.nasa.gov/naif/toolkit_FORTRAN.html) — a **965,000 LOC** Fortran 77 codebase (1,816 `.f` files) used for spacecraft navigation, planetary science, and mission planning. Ask questions in plain English and get grounded answers with file:line citations, dependency graphs, and code explanations.

### What makes it interesting

- **Custom Fortran 77 parser** — handles fixed-form column rules, C$ header sections, ENTRY points, continuation lines
- **Intent-aware query router** — classifies queries (dependency / impact / explain / pattern / semantic) and dispatches to specialised retrieval strategies
- **Call graph analysis** — 12,719 call edges across 1,811 routines with ENTRY alias resolution
- **Pattern detection** — 8 SPICE coding patterns across 4,147 chunks, filtered with Pinecone `$in` on list metadata
- **Three-level caching** — embedding LRU + answer TTL + client singletons. Repeated queries: 13s → 0.1s
- **Interactive TUI** — full terminal UI with split panels, call graph tree, source viewer, and streaming answers

## Quick Start

Requires [uv](https://docs.astral.sh/uv/) (recommended). No manual venv needed.

```bash
# Clone
git clone https://github.com/HenryDeGrasse/LegacyLens.git
cd LegacyLens

# Download SPICE Toolkit source (~50MB)
chmod +x scripts/download_spice.sh && ./scripts/download_spice.sh

# Configure environment
cp .env.example .env
# Edit .env: set OPENAI_API_KEY and PINECONE_API_KEY

# Run ingestion (one-time, ~10 min, ~$0.16 in OpenAI embeddings)
uv run python -m app.ingestion.ingest data/spice

# Launch the TUI
uv run legacylens-tui
```

## Web UI

Launch a local web server and open the browser interface:

```bash
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
# Then open http://127.0.0.1:8000 in your browser
```

## Interactive TUI

The primary interface. Launch with:

```bash
uv run legacylens-tui
# or: uv run python -m app.tui
```

```
┌─────────────────────────────────────────────────────────────────┐
│  LegacyLens 🔍🛰️                           [EXPLAIN] ⟨READY⟩  │
├─────────────────────────────────────────────────────────────────┤
│ ┌─ Query / Explanation ────────┐ ┌─ Call Graph ──────────────┐  │
│ │ USER> What does SPKEZ do?    │ │ SPKEZ                     │  │
│ │                              │ │ ├── Calls →               │  │
│ │ LEGACYLENS> SPKEZ returns    │ │ │   ├── CHKIN             │  │
│ │ the state (position and      │ │ │   ├── SPKGEO            │  │
│ │ velocity) of a target body   │ │ │   ├── SPKACS            │  │
│ │ relative to an observing     │ │ │   └── ZZVALCOR          │  │
│ │ body...                      │ │ └── ← Called by           │  │
│ │                              │ │     ├── CRONOS            │  │
│ │ [spkez.f:3-1345]            │ │     └── ET2LST            │  │
│ └──────────────────────────────┘ └───────────────────────────┘  │
│ ┌─ Source Code (Annotated) ─────────────────────────────────┐   │
│ │ SUBROUTINE SPKEZ ( TARG, ET, REF, ABCORR, OBS, ...)      │   │
│ │ C$ Abstract                                               │   │
│ │ C  Return the state (position and velocity) of ...        │   │
│ └───────────────────────────────────────────────────────────┘   │
├─────────────────────────────────────────────────────────────────┤
│ > What does SPKEZ do?                                           │
└─ F1 Search  F3 Call Tree  F4 Docs  Ctrl+Q Quit ────────────────┘
```

### TUI Commands

Type these directly in the search box:

| Command | Description |
|---|---|
| *(any question)* | Natural language query with RAG |
| `/explain ROUTINE` | Detailed explanation of a routine |
| `/deps ROUTINE` | Show call graph dependencies |
| `/impact ROUTINE` | Blast radius analysis |
| `/metrics ROUTINE` | Code complexity metrics (no LLM call) |
| `/help` | Show all commands |

### Keyboard Shortcuts

| Key | Action |
|---|---|
| **F1** / **Escape** | Focus search box |
| **F3** | Show call tree for last routine |
| **F4** | Explain last routine |
| **Ctrl+Q** | Quit |

## CLI Usage

For scripting and one-off queries. All commands run from the project root.

```bash
# Natural language query
uv run python -m app.cli query "What does SPKEZ do?"
uv run python -m app.cli q "How does SPICE handle errors?" -v  # verbose
uv run python -m app.cli q "What is FURNSH?" -q                # quiet

# Explain a routine
uv run python -m app.cli explain SPKEZ

# Dependency graph
uv run python -m app.cli deps SPKEZ --depth 2

# Impact analysis
uv run python -m app.cli impact CHKIN --depth 3

# Code complexity metrics (no LLM call)
uv run python -m app.cli metrics SPKEZ

# Pattern detection
uv run python -m app.cli patterns                          # list all
uv run python -m app.cli patterns -s error_handling        # search

# Documentation generation
uv run python -m app.cli docgen FURNSH -o FURNSH.md
```

## REST API

**Base URL:** `https://legacylens-production-9578.up.railway.app`

| Endpoint | Method | Description |
|---|---|---|
| `/health` | GET | Health check |
| `/stats` | GET | Pinecone index stats |
| `/query` | POST | Natural language RAG query |
| `/explain` | POST | Routine explanation |
| `/dependencies` | POST | Call graph |
| `/impact` | POST | Blast radius |
| `/metrics` | POST | Code complexity metrics |
| `/api/routines` | GET | Routine name autocomplete |
| `/patterns` | GET | List patterns |
| `/patterns/search` | POST | Pattern search |
| `/docgen` | POST | Generate docs |

```bash
curl -X POST https://legacylens-production-9578.up.railway.app/query \
  -H "Content-Type: application/json" \
  -d '{"question": "What does SPKEZ do?", "top_k": 5}'
```

## Target Codebase

| Property | Value |
|---|---|
| Source | [NASA NAIF SPICE Toolkit (Fortran)](https://naif.jpl.nasa.gov/naif/toolkit_FORTRAN.html) |
| Language | Fortran 77 (fixed-form) |
| Total LOC | 965,146 |
| Source files | 1,816 `.f` + 113 `.inc` |
| Routines parsed | 1,816 + 457 ENTRY points |
| Call graph edges | 12,719 |
| Indexed chunks | 5,386 |
| Embedding cost | ~$0.16 (one-time) |

## Evaluation Results

| Metric | Score |
|---|---|
| Router accuracy | 95% (20/21) |
| Routine recall | 100% |
| Doc-type hit rate | 86% |
| Precision@5 | 53% |
| Answer faithfulness | 100% (21/21) |
| Avg retrieval latency | 440ms |
| Cold latency | ~12s |
| Cached latency | ~0.1s |

Full report: [docs/EVALUATION.md](docs/EVALUATION.md)

## Architecture

```
User (TUI / CLI / API)
    │
    ▼
┌──────────────┐
│ Query Router │ ← classifies intent (DEPENDENCY/IMPACT/EXPLAIN/PATTERN/SEMANTIC)
└──────┬───────┘
       ▼
┌──────────────────────────────────────────┐
│            Retrieval Layer               │
│  Name Filter ──► Pattern Filter ──►      │
│  Semantic Search (Pinecone, 5386 vecs)   │
└──────────────────┬───────────────────────┘
                   ▼
          ┌────────────────┐
          │ Context Assembly│ ← doc-first ordering, token budget
          └────────┬───────┘
                   ▼
          ┌────────────────┐
          │  GPT-4o-mini   │ ← grounded answer with citations
          └────────────────┘
```

## Project Structure

```
LegacyLens/
├── app/
│   ├── tui.py              # Interactive TUI (Textual)
│   ├── cli.py              # CLI interface
│   ├── config.py           # Pydantic settings
│   ├── main.py             # FastAPI endpoints
│   ├── services.py         # Shared singletons (OpenAI, Pinecone, cache)
│   ├── ingestion/          # Parse → chunk → embed → upsert pipeline
│   ├── retrieval/          # Router → search → context → generate
│   └── features/           # explain, dependencies, impact, patterns, docgen
├── tests/
│   ├── golden_queries.py   # 21 golden test queries
│   └── eval_harness.py     # Evaluation framework
├── data/
│   ├── call_graph.json     # Pre-built call graph (committed)
│   └── spice/              # SPICE source (gitignored, downloaded)
├── docs/                   # Plans, evaluation, epics
├── scripts/
│   └── download_spice.sh
├── pyproject.toml          # uv/hatch config
├── Dockerfile              # Railway deployment
└── railway.toml
```

## Documentation

- [RAG Architecture](docs/RAG_ARCHITECTURE.md) — vector DB, chunking, retrieval pipeline, failure modes
- [AI Cost Analysis](docs/AI_COST_ANALYSIS.md) — dev spend, per-query cost, production projections
- [Pre-Research](docs/presearch.md) — codebase analysis and tool selection
- [Implementation Plan](docs/IMPLEMENTATION_PLAN.md) — 5-phase plan
- [Evaluation Report](docs/EVALUATION.md) — metrics and failure analysis
- **Epics:**
  - [001: MVP RAG Pipeline](docs/epics/001-mvp-rag-pipeline.md)
  - [002: Chunking Refinement](docs/epics/002-chunking-retrieval-refinement.md)
  - [003: Advanced Features](docs/epics/003-advanced-features.md)
  - [005: TUI & Polish](docs/epics/005-tui-and-polish.md)

## References

- [NAIF SPICE Toolkit (Fortran)](https://naif.jpl.nasa.gov/naif/toolkit_FORTRAN.html)
- [SPICE Documentation](https://naif.jpl.nasa.gov/pub/naif/toolkit_docs/FORTRAN/)

## License

MIT
