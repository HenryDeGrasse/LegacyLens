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
    U["👤 User"]
    FE["Frontend\nCRT Web UI · TUI · CLI\nRailway — $5/mo"]
    BE["Backend\nFastAPI\nRailway"]
    R["Intent Router\n0.03ms · $0"]
    CG["Call Graph\n12,719 edges\nin-memory"]
    PC["Pinecone\n5,386 vectors\nfree tier"]
    OR["OpenRouter\nGemini 2.5 Flash\n$0.003/query"]
    OA["OpenAI\nEmbeddings\n$0.16 total"]

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
    RW["Follow-up Rewriter\nconversation history · 5 turns"]
    RT["Intent Router\n6 intents · guardrails"]
    EX["Query Expansion\nnaturally injects domain terms"]
    HY["Hybrid Retrieval\nPinecone vector + BM25 keyword"]
    RRF["RRF Merge\nReciprocal Rank Fusion"]
    CA["Context Assembly\n6K tokens · doc-first ordering"]
    LLM["Gemini 2.5 Flash\nstreaming SSE · thinking OFF"]
    ANS["Answer + Citations\nfile:line references"]

    Q --> RW
    RW --> RT
    RT -->|EXPLAIN / DEPS\nIMPACT / PATTERN\nSEMANTIC| EX
    RT -->|OUT_OF_SCOPE| BLOCK["⛔ Blocked\n$0 · no API call"]
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
    subgraph INPUT["Raw .f Source File"]
        direction TB
        L1["C  This is a comment"]
        L2["   SUBROUTINE SPKEZ(TARG,"]
        L3[" .       ET, REF)"]
        L4["C$ Abstract"]
        L5["   CALL CHKIN('SPKEZ')"]
        L6["   ENTRY FURNSH(FILE)"]
        L7["   END"]
    end

    subgraph PASSES["3-Pass Parser"]
        direction TB
        P1["Pass 1\nFind boundaries\nSUBROUTINE · ENTRY · END"]
        P2["Pass 2\nClassify lines\nheader vs body\nCALL extraction"]
        P3["Pass 3\nENTRY aliases\nFURNSH → KEEPER\n457 resolved"]
        P1 --> P2 --> P3
    end

    subgraph OUTPUT["Structured Output"]
        direction TB
        D["routine_doc chunk\nC$ Abstract · signature"]
        B["routine_body chunk\nexecutable code"]
        CG2["Call graph edge\nSPKEZ → CHKIN"]
        AL["Alias\nFURNSH → KEEPER"]
    end

    INPUT --> PASSES --> OUTPUT

    style INPUT fill:#1e3a5f,stroke:#3b82f6,color:#fff
    style PASSES fill:#1e40af,stroke:#3b82f6,color:#fff
    style OUTPUT fill:#14532d,stroke:#22c55e,color:#fff
    linkStyle default stroke:#3b82f6,stroke-width:2px
```

<!-- pause -->

## Latency: 12s → 1.5s

| Optimization | Savings |
|---|---|
| GPT-4o-mini → Gemini 2.5 Flash | **−8s** |
| Disable model thinking | −400ms |
| Parallel Pinecone queries | −300ms |
| Query expansion (better retrieval) | −500ms |
| Embedding cache + answer cache | → **0.1s cached** |

**Cold: 1.5s median · Cached: 0.1s · Router: 0.03ms**

---

# Evals: Why They Matter

```mermaid +render
%%{init: {'theme': 'dark'}}%%
flowchart LR
    subgraph T1["Tier 1 — Every Push\n$0"]
        direction TB
        S["Schema validation"]
        GI["Golden invariants\nrouter · call graph"]
        RP["Session replay\n25 recorded sessions"]
        BM["Benchmarks\nlatency thresholds"]
    end

    subgraph T2["Tier 2 — PRs Only\n~$0.01"]
        direction TB
        RET["Retrieval evals\nPinecone live queries\nroutine recall · type hit"]
    end

    subgraph T3["Tier 3 — Nightly\n~$0.15"]
        direction TB
        FP["Full pipeline\nLLM generation\nfaithfulness scoring"]
        RC["Results uploaded\nas CI artifacts"]
    end

    T1 -->|passes| T2
    T2 -->|passes| T3

    style T1 fill:#14532d,stroke:#22c55e,color:#fff
    style T2 fill:#1e40af,stroke:#3b82f6,color:#fff
    style T3 fill:#581c87,stroke:#a855f7,color:#fff
    linkStyle default stroke:#3b82f6,stroke-width:2px
```

<!-- pause -->

RAG systems break silently — a model upgrade, a schema change,
a new chunk type can all degrade retrieval **without raising an error**

The three tiers let us catch regressions **at the right cost**:
fast free checks on every push, expensive checks only when needed

<!-- pause -->

| Metric | Result |
|---|---|
| Router accuracy | **100%** (25/25) |
| Routine recall | **100%** |
| Answer faithfulness | **100%** (25/25) |
| Total unit tests | **378** |
| Eval subcategories | 8 (explain · deps · impact · pattern · semantic · entry · edge · adversarial) |

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
