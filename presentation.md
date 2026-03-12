# LegacyLens 🔍🛰️

## RAG for NASA's SPICE Toolkit

### 965,000 lines of Fortran 77

**Henry DeGrasse**

<!-- end_slide -->

# The Challenge

## Retrieval-Augmented Generation for Legacy Code

Turn a legacy codebase into something **queryable through natural language**

- Ingest source code into a vector database
- Retrieve relevant code chunks per query
- Ground LLM answers in **actual source code** with citations
- Build code understanding features: explain, dependencies, impact

The hard part: legacy code has **no documentation**, **no modern tooling**,
and **no one alive who fully understands it**

<!-- pause -->

## NASA NAIF SPICE Toolkit

The library JPL uses for **spacecraft navigation**
  - Voyager, Cassini, Mars rovers, Europa Clipper

Written in **Fortran 77** — a fixed-form language from 1977

```
  Col 1     → Comment (C, c, *, !)
  Col 6     → Continuation line
  Col 7-72  → Code
  Col 73+   → Ignored (punch card era)
```

**965,146** lines of code · **1,816** source files · **113** include files

No tree-sitter grammar exists. No off-the-shelf parser works.

<!-- end_slide -->

# Architecture: High Level

```
  ┌──────────┐
  │   User   │
  └────┬─────┘
       │
       ▼
  ┌──────────────────────────────────────┐
  │   Frontend  (Railway — $5/mo)        │
  │   CRT terminal UI · SSE streaming    │
  │   Slash commands · Debug panel       │
  └────────────────┬─────────────────────┘
                   │
                   ▼
  ┌──────────────────────────────────────┐
  │   Backend — FastAPI  (Railway)       │
  │                                      │
  │   ┌──────────┐  ┌────────────────┐   │
  │   │  Intent   │  │  Call Graph    │   │
  │   │  Router   │  │  (in-memory)  │   │
  │   │  0.03ms   │  │  12,719 edges │   │
  │   └─────┬─────┘  └───────────────┘   │
  │         │                             │
  │         ▼                             │
  │   ┌───────────┐    ┌──────────────┐   │
  │   │ Pinecone  │    │  OpenRouter   │   │
  │   │ 5,386 vecs│    │ Gemini 2.5   │   │
  │   │ free tier │    │ Flash $0.003 │   │
  │   └───────────┘    └──────────────┘   │
  │                                       │
  │   ┌──────────────┐                    │
  │   │   OpenAI     │                    │
  │   │  Embeddings  │                    │
  │   │  $0.16 total │                    │
  │   └──────────────┘                    │
  └───────────────────────────────────────┘

  Total dev cost: $5.61  ·  Per-query: $0.003
```

<!-- end_slide -->

# Architecture: RAG Pipeline

```
  User Query
      │
      ▼
  ┌────────────────────┐
  │   Intent Router    │  regex, 0.03ms, $0
  │   6 intents +      │  EXPLAIN │ DEPS │ IMPACT
  │   guardrails       │  PATTERN │ SEMANTIC │ OUT_OF_SCOPE
  └────────┬───────────┘
           ▼
  ┌────────────────────┐
  │  Query Expansion   │  "spacecraft position" →
  │  (no LLM call)     │  "SPKEZ SPKEZR SPKPOS velocity"
  └────────┬───────────┘
           ▼
  ┌────────────────────────────────────┐
  │       Hybrid Retrieval             │
  │  Pinecone vector ──┐               │
  │    (filtered by    ├─▶ RRF merge   │
  │     intent)        │               │
  │  BM25 keyword ─────┘               │
  └────────┬───────────────────────────┘
           ▼
  ┌────────────────────┐  ┌───────────────────┐
  │ Context Assembly   │─▶│ Gemini 2.5 Flash  │
  │ 6K tokens          │  │ streaming SSE     │
  │ doc-first ordering │  │ multi-turn (5t)   │
  └────────────────────┘  └───────────────────┘
```

Router **dynamically picks** which combination of vector,
keyword, and filtered search to run — **agentic RAG**

<!-- end_slide -->

# Hardest Challenge: The Fortran 77 Parser

## Why generic splitters fail

```
  Col: 1     6    7                          72   73+
       │     │    │                           │    │
       ▼     ▼    ▼                           ▼    ▼
       C          This is a comment line           (ignored)
                  SUBROUTINE SPKEZ ( TARG,         (code)
            .          REF, ABCORR, OBS )          (cont'd!)
       C$ Abstract                                 (SPICE hdr)
       C     Return the state...                   (doc)
             CALL CHKIN('SPKEZ')                   (body)
             ENTRY FURNSH ( FILE )                 (alias!)
             END                                   (boundary)
```

<!-- pause -->

## 3-Pass Parser (415 lines, custom built)

**Pass 1** — Find boundaries
  → Scan for SUBROUTINE / FUNCTION / ENTRY / END
  → 1,816 routines + 457 ENTRY points

**Pass 2** — Classify every line
  → Comment → header (documentation)
  → Executable → body (implementation)
  → Extract: CALL targets, C$ Abstract, C$ Keywords

**Pass 3** — ENTRY point extraction
  → FURNSH is ENTRY in KEEPER (4,223 lines!)
  → Create separate chunk with own C$ header
  → 457 aliases resolved in call graph

<!-- pause -->

## Latency: 12s → 1.5s

| Optimization                          | Savings  |
|---------------------------------------|----------|
| GPT-4o-mini → Gemini 2.5 Flash       | **-8s**  |
| Disable thinking (reasoning: none)    | -400ms   |
| Parallel Pinecone queries             | -300ms   |
| Query expansion (better retrieval)    | -500ms   |
| Embedding cache (512-entry LRU)       | -150ms   |
| Intent-aware token budgets            | -200ms   |
| Answer cache (1hr TTL)                | → 0.1s   |

**Cold: 1.5s** · **Cached: 0.1s** · **Router: 0.03ms**

<!-- end_slide -->

# Live Demo

**legacylens-production-9578.up.railway.app**

| # | Query                                     | Shows                          |
|---|-------------------------------------------|--------------------------------|
| 1 | `What does SPKEZ do?`                     | Core RAG, streaming, citations |
| 2 | `/deps FURNSH`                            | Call graph, ENTRY alias, $0    |
| 3 | `/impact CHKIN`                           | 1,257 callers, blast radius    |
| 4 | `How does the spacecraft track position?` | Query expansion, no routine    |
| 5 | `What's the weather today?`               | Guardrail, blocked, $0         |
| 6 | `What about its parameters?`              | Multi-turn follow-up           |

<!-- pause -->

**378 tests · 25 golden evals · 100% router accuracy
100% faithfulness · 96 commits in 3 days · $5.61 total**
