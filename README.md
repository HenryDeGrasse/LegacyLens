# LegacyLens 🔍🛰️

> RAG-powered system for making NASA's SPICE Toolkit Fortran codebase queryable and understandable through natural language.

## Overview

LegacyLens builds a Retrieval-Augmented Generation (RAG) pipeline over NASA's [NAIF SPICE Toolkit](https://naif.jpl.nasa.gov/naif/toolkit_FORTRAN.html) — a ~304,000 LOC Fortran 77 codebase (~930 `.f` files) used for spacecraft navigation, planetary science, and mission planning. Ask questions in plain English and get relevant code snippets, explanations, and dependency insights with file/line references.

## Features

- **Fortran 77 syntax-aware chunking** — fixed-form parsing, routine boundaries, C$ header extraction
- **Two-path retrieval** — exact routine name matching + semantic vector search + pattern filtering
- **Semantic search** across the entire SPICE Toolkit (5,386 chunks, 965K LOC)
- **Natural language query interface** (CLI + REST API)
- **Grounded answers** with file:line citations from GPT-4o-mini
- **Code explanation** (`/explain`) — structured Purpose, Algorithm, I/O, Modern Equivalent
- **Dependency mapping** (`/dependencies`) — forward + reverse call graph (12,719 edges)
- **Pattern detection** (`/patterns/search`) — 8 SPICE patterns across 4,147 chunks
- **Impact analysis** (`/impact`) — blast radius of changes up to N levels
- **Documentation generation** (`/docgen`) — auto-generate Markdown reference docs per routine

## Tech Stack

| Layer | Choice |
|---|---|
| **Vector Database** | Pinecone (managed, free tier) |
| **Embeddings** | OpenAI `text-embedding-3-small` (1536 dims) |
| **LLM** | GPT-4o-mini |
| **Framework** | LangChain + custom Fortran ingestion |
| **Backend** | Python / FastAPI |
| **Frontend** | CLI client + minimal web UI |
| **Deployment** | Railway |

## Live Demo

**API Endpoint:** https://legacylens-production-9578.up.railway.app

```bash
# Health check
curl https://legacylens-production-9578.up.railway.app/health

# Query the codebase
curl -X POST https://legacylens-production-9578.up.railway.app/query \
  -H "Content-Type: application/json" \
  -d '{"question": "What does SPKEZ do?", "top_k": 5}'
```

## Getting Started

```bash
# Clone the repository
git clone https://github.com/HenryDeGrasse/LegacyLens.git
cd LegacyLens

# Download SPICE Toolkit source
chmod +x scripts/download_spice.sh && ./scripts/download_spice.sh

# Set up backend
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your OPENAI_API_KEY and PINECONE_API_KEY

# Run ingestion (one-time)
python -m app.ingestion.ingest ../data/spice

# Start server
uvicorn app.main:app --host 0.0.0.0 --port 8000

# CLI query
python -m app.cli "What does SPKEZ do?"
```

## Target Codebase

| Property | Value |
|---|---|
| Source | [NASA NAIF SPICE Toolkit (Fortran)](https://naif.jpl.nasa.gov/naif/toolkit_FORTRAN.html) |
| Language | Fortran 77 (fixed-form) |
| Size | ~304,000 LOC |
| Files | ~930 `.f` files + ~60 `.inc` headers |

## Project Status

✅ **MVP Live** — Full RAG pipeline deployed. 5,656 chunks indexed from 965K LOC.

## API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/query` | POST | Natural language RAG query with citations |
| `/explain` | POST | Detailed routine explanation |
| `/dependencies` | POST | Forward/reverse call graph |
| `/impact` | POST | Blast radius analysis |
| `/patterns` | GET | List pattern categories |
| `/patterns/search` | POST | Find routines by pattern |
| `/docgen` | POST | Generate Markdown documentation |
| `/stats` | GET | Pinecone index statistics |
| `/health` | GET | Health check |

## Documentation

- [Pre-Research Document](docs/presearch.md)
- [Implementation Plan](docs/IMPLEMENTATION_PLAN.md)
- [Epic 1: MVP](docs/epics/001-mvp-rag-pipeline.md)
- [Epic 2: Chunking Refinement](docs/epics/002-chunking-retrieval-refinement.md)
- [Epic 3: Advanced Features](docs/epics/003-advanced-features.md)

## References

- [NAIF SPICE Toolkit (Fortran)](https://naif.jpl.nasa.gov/naif/toolkit_FORTRAN.html)
- [SPICE Documentation](https://naif.jpl.nasa.gov/pub/naif/toolkit_docs/FORTRAN/)
- [GitHub build wrapper](https://github.com/maxhlc/spice)

## License

MIT
