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

## Quick Start

```bash
# Clone
git clone https://github.com/HenryDeGrasse/LegacyLens.git
cd LegacyLens

# Download SPICE Toolkit source (~50MB)
chmod +x scripts/download_spice.sh && ./scripts/download_spice.sh

# Set up backend
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env: set OPENAI_API_KEY and PINECONE_API_KEY

# Run ingestion (one-time, ~10 min, ~$0.16 in OpenAI embeddings)
python -m app.ingestion.ingest ../data/spice

# Start the API server
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## CLI Usage

The CLI has subcommands for every feature. Run from the `backend/` directory with the venv active:

```bash
# Activate the environment
cd backend && source .venv/bin/activate
```

### Natural Language Query

```bash
# Ask anything about the SPICE codebase
python -m app.cli query "What does SPKEZ do?"

# Verbose mode — shows router intent, retrieval scores
python -m app.cli query "How does SPICE handle errors?" -v

# Quiet mode — answer only, no chunk display
python -m app.cli query "What is FURNSH?" -q

# Short alias
python -m app.cli q "What calls FURNSH?" -v
```

### Explain a Routine

```bash
# Structured explanation: Purpose, I/O, Algorithm, Dependencies, Modern Equivalent
python -m app.cli explain SPKEZ
python -m app.cli explain FURNSH
python -m app.cli e STR2ET          # short alias
```

### Dependency Graph

```bash
# Forward calls + reverse callers
python -m app.cli deps SPKEZ
python -m app.cli deps FURNSH --depth 2    # 2-level traversal

# Short alias
python -m app.cli d PXFORM
```

### Impact Analysis

```bash
# Blast radius: what breaks if this routine changes?
python -m app.cli impact SPKEZ
python -m app.cli impact CHKIN --depth 3

# Short alias
python -m app.cli i FURNSH
```

### Pattern Detection

```bash
# List all 8 SPICE patterns
python -m app.cli patterns

# Search for routines matching a pattern
python -m app.cli patterns --search error_handling
python -m app.cli patterns --search kernel_loading --query "How do I load kernels?"
python -m app.cli patterns -s spk_operations --top-k 5
```

### Documentation Generation

```bash
# Generate Markdown docs for a routine
python -m app.cli docgen SPKEZ

# Save to file
python -m app.cli docgen FURNSH -o docs/FURNSH.md
```

### Run Evaluation

```bash
# Full eval: 21 golden queries, measures router/retrieval/answer quality
python -m tests.eval_harness

# Retrieval-only (no OpenAI generation cost)
python -m tests.eval_harness --no-generate
```

## REST API

**Base URL:** `https://legacylens-production-9578.up.railway.app`

| Endpoint | Method | Description | Body |
|---|---|---|---|
| `/health` | GET | Health check | — |
| `/stats` | GET | Pinecone index stats | — |
| `/query` | POST | Natural language RAG query | `{"question": "...", "top_k": 10}` |
| `/explain` | POST | Routine explanation | `{"routine_name": "SPKEZ"}` |
| `/dependencies` | POST | Call graph | `{"routine_name": "SPKEZ", "depth": 1}` |
| `/impact` | POST | Blast radius | `{"routine_name": "SPKEZ", "depth": 2}` |
| `/patterns` | GET | List patterns | — |
| `/patterns/search` | POST | Pattern search | `{"pattern": "error_handling", "query": "", "top_k": 10}` |
| `/docgen` | POST | Generate docs | `{"routine_name": "FURNSH"}` |

### Example API Calls

```bash
# Query
curl -X POST https://legacylens-production-9578.up.railway.app/query \
  -H "Content-Type: application/json" \
  -d '{"question": "What does SPKEZ do?", "top_k": 5}'

# Dependencies
curl -X POST https://legacylens-production-9578.up.railway.app/dependencies \
  -H "Content-Type: application/json" \
  -d '{"routine_name": "SPKEZ", "depth": 1}'

# Impact analysis
curl -X POST https://legacylens-production-9578.up.railway.app/impact \
  -H "Content-Type: application/json" \
  -d '{"routine_name": "FURNSH", "depth": 2}'

# Pattern search
curl -X POST https://legacylens-production-9578.up.railway.app/patterns/search \
  -H "Content-Type: application/json" \
  -d '{"pattern": "spk_operations", "top_k": 5}'
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
| Avg total latency | ~12s (cold) / ~0.1s (cached) |

Full evaluation report: [docs/EVALUATION.md](docs/EVALUATION.md)

## Architecture

```
User Query
    │
    ▼
┌──────────────┐
│ Query Router │ ← classifies intent (DEPENDENCY/IMPACT/EXPLAIN/PATTERN/SEMANTIC)
└──────┬───────┘
       │
       ▼
┌──────────────────────────────────────────┐
│            Retrieval Layer               │
│  ┌─────────────┐  ┌──────────────────┐   │
│  │ Name Filter │  │ Pattern Filter   │   │
│  │ ($eq boost) │  │ ($in on lists)   │   │
│  └─────┬───────┘  └────────┬─────────┘   │
│        └────────┬──────────┘             │
│                 ▼                        │
│     ┌───────────────────┐               │
│     │  Semantic Search  │               │
│     │  (Pinecone)       │               │
│     └───────────────────┘               │
└──────────────────┬───────────────────────┘
                   │
                   ▼
          ┌────────────────┐
          │ Context Assembly│ ← groups by routine, doc-first ordering
          └────────┬───────┘
                   │
                   ▼
          ┌────────────────┐
          │  GPT-4o-mini   │ ← grounded answer with citations
          └────────────────┘
```

## Documentation

- [Pre-Research](docs/presearch.md) — codebase analysis and tool selection
- [Implementation Plan](docs/IMPLEMENTATION_PLAN.md) — 5-phase plan
- [Evaluation Report](docs/EVALUATION.md) — metrics and failure analysis
- **Epics:**
  - [001: MVP RAG Pipeline](docs/epics/001-mvp-rag-pipeline.md)
  - [002: Chunking Refinement](docs/epics/002-chunking-retrieval-refinement.md)
  - [003: Advanced Features](docs/epics/003-advanced-features.md)

## Project Structure

```
LegacyLens/
├── backend/
│   ├── app/
│   │   ├── cli.py              # CLI interface (query/explain/deps/impact/patterns/docgen)
│   │   ├── config.py           # Pydantic settings
│   │   ├── main.py             # FastAPI endpoints
│   │   ├── services.py         # Shared singletons (OpenAI, Pinecone, cache)
│   │   ├── ingestion/
│   │   │   ├── scanner.py      # File discovery
│   │   │   ├── fortran_parser.py # Fortran 77 fixed-form parser
│   │   │   ├── chunker.py      # Chunk creation with pattern detection
│   │   │   ├── call_graph.py   # Forward/reverse call graph builder
│   │   │   ├── embedder.py     # OpenAI embedding with checkpoint
│   │   │   ├── loader.py       # Pinecone upsert
│   │   │   └── ingest.py       # Full pipeline orchestrator
│   │   ├── retrieval/
│   │   │   ├── router.py       # Intent classification (regex-first)
│   │   │   ├── search.py       # Routed multi-path retrieval
│   │   │   ├── context.py      # Context assembly with doc-type awareness
│   │   │   └── generator.py    # LLM answer generation with caching
│   │   └── features/
│   │       ├── explain.py      # Routine explanation
│   │       ├── dependencies.py # Call graph queries
│   │       ├── impact.py       # Blast radius analysis
│   │       ├── patterns.py     # Pattern listing and search
│   │       └── docgen.py       # Markdown documentation generator
│   ├── tests/
│   │   ├── golden_queries.py   # 21 golden test queries
│   │   └── eval_harness.py     # Evaluation framework
│   └── data/
│       └── call_graph.json     # Pre-built call graph (committed)
├── data/
│   └── spice/                  # SPICE source (gitignored, downloaded)
├── docs/
│   ├── IMPLEMENTATION_PLAN.md
│   ├── EVALUATION.md
│   ├── presearch.md
│   └── epics/
└── scripts/
    └── download_spice.sh
```

## References

- [NAIF SPICE Toolkit (Fortran)](https://naif.jpl.nasa.gov/naif/toolkit_FORTRAN.html)
- [SPICE Documentation](https://naif.jpl.nasa.gov/pub/naif/toolkit_docs/FORTRAN/)

## License

MIT
