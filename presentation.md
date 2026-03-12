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

```mermaid +render
%%{init: {'theme': 'dark'}}%%
flowchart LR
    subgraph INPUT["Raw .f File"]
        L1["C  comment"]
        L2["   SUBROUTINE SPKEZ(...)"]
        L3["   CALL CHKIN(...)"]
        L4["   ENTRY FURNSH(FILE)"]
        L5["   END"]
    end

    subgraph PASSES["3-Pass Parser"]
        P1["Pass 1: Boundaries"]
        P2["Pass 2: Classify Lines"]
        P3["Pass 3: ENTRY Aliases"]
        P1 --> P2 --> P3
    end

    subgraph OUTPUT["Output"]
        D["routine_doc chunk"]
        B["routine_body chunk"]
        CG2["Call graph edge"]
        AL["FURNSH → KEEPER"]
    end

    INPUT --> PASSES --> OUTPUT

    style INPUT fill:#1e3a5f,stroke:#3b82f6,color:#fff
    style PASSES fill:#1e40af,stroke:#3b82f6,color:#fff
    style OUTPUT fill:#14532d,stroke:#22c55e,color:#fff
    style L1 fill:#1e3a5f,stroke:#3b82f6,color:#fff
    style L2 fill:#1e3a5f,stroke:#3b82f6,color:#fff
    style L3 fill:#1e3a5f,stroke:#3b82f6,color:#fff
    style L4 fill:#1e3a5f,stroke:#3b82f6,color:#fff
    style L5 fill:#1e3a5f,stroke:#3b82f6,color:#fff
    style P1 fill:#1e40af,stroke:#3b82f6,color:#fff
    style P2 fill:#1e40af,stroke:#3b82f6,color:#fff
    style P3 fill:#1e40af,stroke:#3b82f6,color:#fff
    style D fill:#14532d,stroke:#22c55e,color:#fff
    style B fill:#14532d,stroke:#22c55e,color:#fff
    style CG2 fill:#14532d,stroke:#22c55e,color:#fff
    style AL fill:#14532d,stroke:#22c55e,color:#fff
    linkStyle default stroke:#3b82f6,stroke-width:2px
```

<!-- pause -->

## Latency: 12s → 1.5s

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
flowchart LR
    subgraph T1["Tier 1 — Every Push — $0"]
        S["Schema validation"]
        GI["Golden invariants"]
        RP["Session replay"]
        BM["Latency benchmarks"]
    end

    subgraph T2["Tier 2 — PRs — $0.01"]
        RET["Live Pinecone retrieval"]
        RC["Routine recall"]
    end

    subgraph T3["Tier 3 — Nightly — $0.15"]
        FP["Full pipeline eval"]
        FA["Faithfulness scoring"]
    end

    T1 -->|passes| T2
    T2 -->|passes| T3

    style T1 fill:#14532d,stroke:#22c55e,color:#fff
    style T2 fill:#1e40af,stroke:#3b82f6,color:#fff
    style T3 fill:#581c87,stroke:#a855f7,color:#fff
    style S fill:#14532d,stroke:#22c55e,color:#fff
    style GI fill:#14532d,stroke:#22c55e,color:#fff
    style RP fill:#14532d,stroke:#22c55e,color:#fff
    style BM fill:#14532d,stroke:#22c55e,color:#fff
    style RET fill:#1e40af,stroke:#3b82f6,color:#fff
    style RC fill:#1e40af,stroke:#3b82f6,color:#fff
    style FP fill:#581c87,stroke:#a855f7,color:#fff
    style FA fill:#581c87,stroke:#a855f7,color:#fff
    linkStyle default stroke:#3b82f6,stroke-width:2px
```

<!-- pause -->

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
