# LegacyLens 🔍🛰️

> RAG-powered system for making NASA's SPICE Toolkit Fortran codebase queryable and understandable through natural language.

## Overview

LegacyLens builds a Retrieval-Augmented Generation (RAG) pipeline over NASA's [NAIF SPICE Toolkit](https://naif.jpl.nasa.gov/naif/toolkit_FORTRAN.html) — a ~304,000 LOC Fortran 77 codebase (~930 `.f` files) used for spacecraft navigation, planetary science, and mission planning. Ask questions in plain English and get relevant code snippets, explanations, and dependency insights with file/line references.

## Features

- **Fortran 77 syntax-aware chunking** — fixed-form parsing, routine boundaries, header extraction
- **Two-path retrieval** — exact routine name matching + semantic vector search
- **Semantic search** across the entire SPICE Toolkit
- **Natural language query interface** (CLI + web)
- **Grounded answers** with file:line citations from GPT-4o-mini
- **Code explanation** — plain English descriptions of routines
- **Dependency mapping** — CALL graph and reverse-call index
- **Pattern detection** — error handling, kernel loading, argument validation
- **Impact analysis** — blast radius of changes up to 2 levels

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

## Getting Started

```bash
# Clone the repository
git clone https://github.com/HenryDeGrasse/LegacyLens.git
cd LegacyLens

# Setup instructions coming soon
```

## Target Codebase

| Property | Value |
|---|---|
| Source | [NASA NAIF SPICE Toolkit (Fortran)](https://naif.jpl.nasa.gov/naif/toolkit_FORTRAN.html) |
| Language | Fortran 77 (fixed-form) |
| Size | ~304,000 LOC |
| Files | ~930 `.f` files + ~60 `.inc` headers |

## Project Status

🚧 **In Development** — Pre-research complete, implementation starting.

## Documentation

- [Pre-Research Document](docs/presearch.md)
- [Implementation Plan](docs/IMPLEMENTATION_PLAN.md)

## References

- [NAIF SPICE Toolkit (Fortran)](https://naif.jpl.nasa.gov/naif/toolkit_FORTRAN.html)
- [SPICE Documentation](https://naif.jpl.nasa.gov/pub/naif/toolkit_docs/FORTRAN/)
- [GitHub build wrapper](https://github.com/maxhlc/spice)

## License

MIT
