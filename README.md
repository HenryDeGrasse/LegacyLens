# LegacyLens 🔍🛰️

> RAG-powered system for making NASA's SPICE Toolkit Fortran 77 codebase queryable and understandable through natural language.

**Live API:** https://legacylens-production-9578.up.railway.app

## Overview

LegacyLens builds a Retrieval-Augmented Generation (RAG) pipeline over NASA's [NAIF SPICE Toolkit](https://naif.jpl.nasa.gov/naif/toolkit_FORTRAN.html) — a **965,000 LOC** Fortran 77 codebase (1,816 `.f` files) used for spacecraft navigation, planetary science, and mission planning. Ask questions in plain English and get grounded answers with file:line citations, dependency graphs, and code explanations.

### What makes it interesting

- **Custom Fortran 77 parser** — handles fixed-form column rules, C$ header sections, ENTRY points, continuation lines
- **Hybrid retrieval** — Pinecone vector search + BM25 keyword index merged via Reciprocal Rank Fusion (RRF)
- **Intent-aware query router** — regex-first classification (6 intents including OUT_OF_SCOPE) with call-graph-backed routine name validation to eliminate false positives
- **Adversarial guardrails** — prompt injection, off-topic, gibberish, and code-generation detection. Zero API cost for blocked queries.
- **Multi-turn conversation** — 5-turn session history with 30-min TTL for follow-up questions
- **Call graph analysis** — 12,719 call edges across 1,811 routines with 457 ENTRY alias resolutions
- **Swappable LLMs** — OpenRouter integration (Gemini 2.0 Flash default, median E2E 1.9s)
- **Three-tier eval CI** — 25 golden cases: schema tests ($0) → retrieval evals ($0.01) → full pipeline ($0.15)
- **366 unit tests** — router, BM25, conversation, API, context assembly, caching, regressions
- **Interactive TUI + Web UI** — terminal UI with split panels, call graph tree, source viewer, and SSE streaming

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
| Router accuracy | 100% (25/25) |
| Routine recall | 100% |
| Answer faithfulness | 100% (25/25) |
| Median E2E (Gemini 2.0 Flash) | 1.9s |
| Median TTFT | 0.7s |
| Cached latency | ~0.1s |
| Eval cases | 25 (across 10 subcategories) |
| Unit tests | 366 |

Full report: [POSTMORTEM.md](POSTMORTEM.md)

## Architecture

```
User (TUI / CLI / Web UI / API)
    │
    ▼
┌──────────────────┐
│   Query Router   │ ← regex-first intent (DEP/IMPACT/EXPLAIN/PATTERN/SEMANTIC/OUT_OF_SCOPE)
│ + call-graph     │   routine name validation eliminates false positives
│   validation     │   adversarial detection (prompt injection, off-topic, gibberish)
└────────┬─────────┘
         ▼
┌────────────────────────────────────────────────┐
│              Hybrid Retrieval                   │
│  Pinecone (5,386 vecs) ──┐                     │
│    Name / Pattern /      ├─► RRF merge → top-K │
│    Semantic filters      │                     │
│  BM25 keyword index ─────┘                     │
└────────────────────┬───────────────────────────┘
                     ▼
            ┌────────────────┐
            │Context Assembly│ ← doc-first ordering, intent-aware token budget
            └────────┬───────┘
                     ▼
            ┌────────────────┐
            │  LLM (OpenRouter)│ ← swappable (Gemini 2.0 Flash default)
            │  + multi-turn   │   conversation history (5 turns, 30-min TTL)
            └─────────────────┘
```

## Project Structure

```
LegacyLens/
├── app/
│   ├── tui.py                  # Interactive TUI (Textual)
│   ├── cli.py                  # CLI interface
│   ├── config.py               # Pydantic settings
│   ├── main.py                 # FastAPI endpoints + rate limiter
│   ├── services.py             # Shared singletons (OpenAI, Pinecone, cache)
│   ├── ingestion/              # Parse → chunk → embed → upsert pipeline
│   ├── retrieval/
│   │   ├── router.py           # Intent classifier (regex + call-graph validation)
│   │   ├── search.py           # Hybrid retrieval (Pinecone + BM25/RRF)
│   │   ├── bm25_index.py       # BM25 keyword index + Reciprocal Rank Fusion
│   │   ├── context.py          # Token-budgeted context assembly
│   │   └── generator.py        # LLM generation + multi-turn sessions
│   └── features/               # explain, dependencies, impact, patterns, docgen, metrics
├── tests/
│   ├── eval_cases.json         # 25 golden eval cases
│   ├── eval_harness.py         # Tier 3 full-pipeline eval runner
│   ├── eval_schema.py          # Runtime eval case validator
│   ├── eval_assert.py          # Shared assertion helpers
│   ├── fixtures/recorded/      # 25 recorded golden sessions for replay
│   ├── test_router.py          # 118 router unit tests
│   ├── test_bm25.py            # 32 BM25/RRF tests
│   ├── test_conversation.py    # 25 conversation store tests
│   ├── test_api_endpoints.py   # 46 API endpoint tests
│   ├── test_context_assembly.py # 30 context assembly tests
│   ├── test_caching.py         # 20 cache tests
│   ├── test_regressions.py     # 11 regression tests
│   └── ...                     # eval replay, schema, benchmarks, latency
├── data/
│   ├── call_graph.json         # Pre-built call graph (committed)
│   └── spice/                  # SPICE source (gitignored, downloaded)
├── .github/workflows/evals.yml # Three-tier CI (free → retrieval → full pipeline)
├── POSTMORTEM.md               # Audit log, architecture decisions, baselines
├── docs/                       # Architecture deep dive, cost analysis, epics
├── pyproject.toml
├── Dockerfile
└── railway.toml
```

## Documentation

- [Postmortem & Future Directions](POSTMORTEM.md) — audit log, architecture decisions, performance baselines, cost analysis, next steps
- [Architecture Deep Dive](docs/ARCHITECTURE_DEEP_DIVE.md) — full system walkthrough (ingestion, query pipeline, caching, scaling)
- [AI Cost Analysis](docs/AI_COST_ANALYSIS.md) — dev spend, per-query cost, production projections
- [Pre-Research](docs/presearch.md) — codebase analysis and tool selection
- [Deep Dive Summary](docs/DEEP_DIVE_SUMMARY.md) — first audit session (security, bugs, features)
- **Build Epics** (historical):
  - [001: MVP RAG Pipeline](docs/epics/001-mvp-rag-pipeline.md)
  - [002: Chunking Refinement](docs/epics/002-chunking-retrieval-refinement.md)
  - [003: Advanced Features](docs/epics/003-advanced-features.md)
  - [003: Web Frontend](docs/epics/003-web-terminal-frontend.md)
  - [005: TUI & Polish](docs/epics/005-tui-and-polish.md)

## References

- [NAIF SPICE Toolkit (Fortran)](https://naif.jpl.nasa.gov/naif/toolkit_FORTRAN.html)
- [SPICE Documentation](https://naif.jpl.nasa.gov/pub/naif/toolkit_docs/FORTRAN/)

## License

MIT
