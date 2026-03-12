---
title: LegacyLens
author: Henry DeGrasse
theme:
  name: dark
options:
  end_slide_shorthand: true
---

# LegacyLens
### Week Three — Gauntlet AI

**Henry DeGrasse**

---

# The Challenge

**Retrieval-Augmented Generation for Legacy Code**

Turn a legacy codebase into something queryable through natural language

- Ingest source code into a vector database
- Retrieve relevant code chunks per query
- Ground LLM answers in **actual source code** with citations
- Build code-understanding features: explain, deps, impact, patterns

<!-- pause -->

## The Target: NASA NAIF SPICE Toolkit

The library JPL uses for **spacecraft navigation**

> Voyager · Cassini · Mars rovers · Europa Clipper

Written in **Fortran 77** — fixed-form, column-sensitive, 1977

```
  Col 1     → Comment
  Col 6     → Continuation line
  Col 7-72  → Code
  Col 73+   → Ignored  (punch card era)
```

| | |
|---|---|
| Lines of code | **965,146** |
| Source files | **1,816** `.f` + **113** `.inc` |
| Routines parsed | **1,816** + **457** ENTRY points |
| Call graph edges | **12,719** |

No tree-sitter grammar. No off-the-shelf parser works.

---

# Architecture: System Design

```mermaid +render
%%{init: {'theme': 'dark'}}%%
flowchart TD
    U["User"]
    FE["Frontend — Railway"]
    BE["Backend — FastAPI"]
    R["Intent Router"]
    CG["Call Graph"]
    PC["Pinecone"]
    OR["OpenRouter"]
    OA["OpenAI Embeddings"]

    U --> FE
    FE --> BE
    BE --> R
    BE --> CG
    R --> PC
    R --> OR
    R --> OA

    style U fill:#1e3a5f,stroke:#3b82f6,color:#fff
    style FE fill:#1e3a5f,stroke:#3b82f6,color:#fff
    style BE fill:#1e3a5f,stroke:#3b82f6,color:#fff
    style R fill:#1e40af,stroke:#3b82f6,color:#fff
    style CG fill:#1e40af,stroke:#3b82f6,color:#fff
    style PC fill:#14532d,stroke:#22c55e,color:#fff
    style OR fill:#581c87,stroke:#a855f7,color:#fff
    style OA fill:#581c87,stroke:#a855f7,color:#fff
    linkStyle default stroke:#3b82f6,stroke-width:2px
```

---

# Architecture: RAG Pipeline

```mermaid +render
%%{init: {'theme': 'dark'}}%%
flowchart TD
    Q["User Query"]
    RW["Follow-up Rewriter"]
    RT["Intent Router"]
    EX["Query Expansion"]
    HY["Hybrid Retrieval"]
    RRF["RRF Merge"]
    CA["Context Assembly"]
    LLM["Gemini 2.5 Flash"]
    ANS["Answer + Citations"]
    BLOCK["Blocked"]

    Q --> RW
    RW --> RT
    RT --> EX
    RT --> BLOCK
    EX --> HY
    HY --> RRF
    RRF --> CA
    CA --> LLM
    LLM --> ANS

    style Q fill:#1e3a5f,stroke:#3b82f6,color:#fff
    style RW fill:#1e3a5f,stroke:#3b82f6,color:#fff
    style RT fill:#1e40af,stroke:#3b82f6,color:#fff
    style EX fill:#1e40af,stroke:#3b82f6,color:#fff
    style HY fill:#14532d,stroke:#22c55e,color:#fff
    style RRF fill:#14532d,stroke:#22c55e,color:#fff
    style CA fill:#1e3a5f,stroke:#3b82f6,color:#fff
    style LLM fill:#581c87,stroke:#a855f7,color:#fff
    style ANS fill:#1e3a5f,stroke:#3b82f6,color:#fff
    style BLOCK fill:#7f1d1d,stroke:#dc2626,color:#fff
    linkStyle default stroke:#3b82f6,stroke-width:2px
```

---

# Hardest Challenge: The Fortran 77 Parser

No off-the-shelf parser understands Fortran 77 — the *column a character
is on* determines what it means. You can't split by lines, you can't
split by tokens, and LangChain has never heard of it.

So I wrote a **3-pass parser from scratch** — it reads every source file,
figures out where routines start and end, separates documentation from
code, and then handles the really tricky part: **ENTRY points**, where
one function is secretly hiding inside another 4,000-line file
under a completely different name.

```mermaid +render
%%{init: {'theme': 'dark'}}%%
flowchart TD
    RAW["Raw Fortran 77 Source"]

    P1["Pass 1 — Find Boundaries"]
    P1D["SUBROUTINE · FUNCTION · ENTRY · END"]

    P2["Pass 2 — Classify Lines"]
    P2D["header comments vs executable body"]

    P3["Pass 3 — Resolve ENTRY Aliases"]
    P3D["FURNSH is ENTRY inside KEEPER"]

    DOC["routine_doc chunk"]
    BODY["routine_body chunk"]
    GRAPH["Call graph edges"]
    ALIAS["457 aliases resolved"]

    RAW --> P1 --> P1D
    P1D --> P2 --> P2D
    P2D --> P3 --> P3D
    P3D --> DOC
    P3D --> BODY
    P3D --> GRAPH
    P3D --> ALIAS

    style RAW fill:#1e3a5f,stroke:#3b82f6,color:#fff
    style P1 fill:#1e40af,stroke:#3b82f6,color:#fff
    style P1D fill:#1e3a5f,stroke:#3b82f6,color:#fff
    style P2 fill:#1e40af,stroke:#3b82f6,color:#fff
    style P2D fill:#1e3a5f,stroke:#3b82f6,color:#fff
    style P3 fill:#1e40af,stroke:#3b82f6,color:#fff
    style P3D fill:#1e3a5f,stroke:#3b82f6,color:#fff
    style DOC fill:#14532d,stroke:#22c55e,color:#fff
    style BODY fill:#14532d,stroke:#22c55e,color:#fff
    style GRAPH fill:#14532d,stroke:#22c55e,color:#fff
    style ALIAS fill:#14532d,stroke:#22c55e,color:#fff
    linkStyle default stroke:#3b82f6,stroke-width:2px
```

---

# Hardest Challenge: Latency

## 12s → 1.5s

| Optimization | Savings |
|---|---|
| GPT-4o-mini → Gemini 2.5 Flash | **−8s** |
| Disable model thinking | −400ms |
| Parallel Pinecone queries | −300ms |
| Query expansion | −500ms |
| Embedding cache + answer cache | → **0.1s cached** |

**Cold: 1.5s median · Cached: 0.1s · Router: 0.03ms**

---

# Evals: Why They Matter

RAG systems break **silently** — a model swap, a schema change,
a new chunk type can all degrade retrieval without raising an error

```mermaid +render
%%{init: {'theme': 'dark'}}%%
flowchart TD
    T1["Tier 1 — Every Push"]
    S["Schema · Golden invariants · Session replay · Latency benchmarks"]

    T2["Tier 2 — Pull Requests"]
    RET["Live Pinecone retrieval · Routine recall · Type hit"]

    T3["Tier 3 — Nightly"]
    FP["Full pipeline · LLM generation · Faithfulness scoring"]

    T1 --> S
    S -->|passes| T2
    T2 --> RET
    RET -->|passes| T3
    T3 --> FP

    style T1 fill:#14532d,stroke:#22c55e,color:#fff
    style S fill:#14532d,stroke:#22c55e,color:#fff
    style T2 fill:#1e40af,stroke:#3b82f6,color:#fff
    style RET fill:#1e40af,stroke:#3b82f6,color:#fff
    style T3 fill:#581c87,stroke:#a855f7,color:#fff
    style FP fill:#581c87,stroke:#a855f7,color:#fff
    linkStyle default stroke:#3b82f6,stroke-width:2px
```

---

# Evals: Results

| Metric | Result |
|---|---|
| Router accuracy | **100%** (25/25) |
| Routine recall | **100%** |
| Answer faithfulness | **100%** (25/25) |
| Unit tests | **378** |
| Eval categories | explain · deps · impact · pattern · semantic · entry · adversarial |

---

# Live Demo

**legacylens-production-9578.up.railway.app**

| # | Query | Shows |
|---|---|---|
| 1 | `What does SPKEZ do?` | Core RAG · streaming · citations |
| 2 | `/deps FURNSH` | Call graph · ENTRY alias · $0 |
| 3 | `/impact CHKIN` | 1,257 callers · blast radius |
| 4 | `How does the spacecraft track position?` | Query expansion |
| 5 | `What's the weather today?` | Guardrail · blocked · $0 |
| 6 | `What about its parameters?` | Multi-turn follow-up |

<!-- pause -->

**96 commits · 378 tests · 25 golden evals · $5.61 total**
