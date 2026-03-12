# LegacyLens — Presentation Script

> 4 slides + live demo. ~8-10 minutes total.

---

## SLIDE 1: INTRO

### On screen:
```
┌─────────────────────────────────────────────────┐
│                                                 │
│           LegacyLens 🔍🛰️                       │
│                                                 │
│   RAG for NASA's SPICE Toolkit                  │
│   965,000 lines of Fortran 77                   │
│                                                 │
│   Henry DeGrasse                                │
│                                                 │
└─────────────────────────────────────────────────┘
```

### Talk track (~60s):
- Introduce yourself
- One-liner: "I built a RAG system that makes NASA's spacecraft navigation code queryable in plain English"
- Quick hook: "The codebase I chose is 96 times larger than the minimum requirement, in a language from 1977 where which column your code is in determines whether it compiles"

---

## SLIDE 2: THE PROJECT

### On screen:

**Left side — What is SPICE?**
```
NASA NAIF SPICE Toolkit
━━━━━━━━━━━━━━━━━━━━━━
• Fortran 77 (fixed-form, 1977)
• Used by JPL for spacecraft navigation
  → Voyager, Cassini, Mars rovers, Europa Clipper
• 965,146 lines of code
• 1,816 source files + 113 include files
• Column-sensitive syntax:
    Col 1     → Comment (C, c, *, !)
    Col 6     → Continuation line
    Col 7-72  → Code
    Col 73+   → Ignored (punch card era)
```

**Right side — Project constraints**
```
Requirements
━━━━━━━━━━━━
✓ Legacy codebase, 10K+ LOC minimum
✓ Syntax-aware code splitting
✓ Semantic search over code
✓ 4+ code understanding features
✓ CLI or web interface
✓ Deployed & accessible
✓ Eval suite
✓ Cost analysis

What I built                     vs Requirement
─────────────────────────────    ──────────────
965K LOC (96× minimum)           10K LOC
6 features                       4 features
3 interfaces (Web + TUI + CLI)   1 interface
378 tests, 25 golden evals       "eval suite"
$5.61 total dev cost             "cost analysis"
```

### Talk track (~90s):
- "The challenge was: pick a legacy codebase and build a RAG system over it"
- "I chose the hardest possible target — NASA's SPICE Toolkit"
- **Why it's hard:** "No tree-sitter grammar exists for Fortran 77. LangChain's text splitter would produce garbage — it doesn't know that column 6 means continuation, that `C$Procedure` is a structured doc header, or that `ENTRY` creates an alias into another subroutine"
- "SPICE also uses something called ENTRY points — FURNSH is actually an alternative entry into a 4,000-line subroutine called KEEPER. If you don't resolve these aliases, half your call graph is broken"
- "So off-the-shelf tooling wasn't an option. I had to build a custom parser, a custom chunker, and a custom call graph"

---

## SLIDE 3: ARCHITECTURE

### On screen — Reveal 1: Infrastructure

```
┌─────────────────────────────────────────────────────────────────┐
│                      INFRASTRUCTURE                             │
│                                                                 │
│  ┌──────────┐     ┌──────────────┐     ┌──────────────────┐    │
│  │  Railway  │     │   Pinecone   │     │   OpenRouter     │    │
│  │  $5/mo   │     │  Free tier   │     │  Gemini 2.5 Flash│    │
│  │          │     │              │     │                  │    │
│  │ FastAPI  │────▶│ 5,386 vectors│     │  LLM generation  │    │
│  │ Docker   │     │ Serverless   │     │  $0.003/query    │    │
│  │ Non-root │     │ AWS us-east-1│     │  Thinking: OFF   │    │
│  └──────────┘     └──────────────┘     └──────────────────┘    │
│       │                                        │                │
│       │           ┌──────────────┐             │                │
│       └──────────▶│   OpenAI     │◀────────────┘                │
│                   │  Embeddings  │  (text-embedding-3-small)    │
│                   │  $0.16 total │  (1,536 dims)                │
│                   └──────────────┘                               │
│                                                                 │
│  Total dev cost: $5.61  │  Per-query: $0.003  │  Hosting: $5/mo │
└─────────────────────────────────────────────────────────────────┘
```

### Talk track for Reveal 1 (~30s):
- "Four services: Railway hosts the FastAPI app in Docker, Pinecone stores the 5,386 code vectors, OpenAI does embeddings, OpenRouter routes to Gemini 2.5 Flash for generation"
- "Total development cost was $5.61. Per-query is $0.003. The whole thing runs on a $5/month Railway deploy"

---

### On screen — Reveal 2: RAG Pipeline (click to reveal)

```
┌─────────────────────────────────────────────────────────────────┐
│                     QUERY PIPELINE                              │
│                                                                 │
│  User Query                                                     │
│      │                                                          │
│      ▼                                                          │
│  ┌────────────────────┐                                         │
│  │   Intent Router    │  regex-first, 0.03ms, $0                │
│  │   6 intents        │  EXPLAIN│DEPS│IMPACT│PATTERN│           │
│  │   + guardrails     │  SEMANTIC│OUT_OF_SCOPE                  │
│  └────────┬───────────┘                                         │
│           ▼                                                     │
│  ┌────────────────────┐                                         │
│  │  Query Expansion   │  injects domain terms into embedding    │
│  │  "spacecraft" →    │  "SPKEZ SPKEZR SPKPOS velocity"         │
│  └────────┬───────────┘                                         │
│           ▼                                                     │
│  ┌─────────────────────────────────────┐                        │
│  │       Hybrid Retrieval              │                        │
│  │  Pinecone vector ──┐                │                        │
│  │    (filtered by   ├──▶ RRF merge   │                        │
│  │     intent)        │                │                        │
│  │  BM25 keyword ─────┘                │                        │
│  └────────┬────────────────────────────┘                        │
│           ▼                                                     │
│  ┌────────────────────┐    ┌───────────────────┐                │
│  │ Context Assembly   │───▶│  Gemini 2.5 Flash │                │
│  │ 6K tokens, doc-first│   │  streaming SSE    │                │
│  │ tiktoken accurate  │    │  multi-turn (5t)  │                │
│  └────────────────────┘    └───────────────────┘                │
│                                                                 │
│  Key: router DYNAMICALLY picks which combination of             │
│  vector, keyword, and filtered search to run per query          │
│  ─── this is agentic RAG ───                                    │
└─────────────────────────────────────────────────────────────────┘
```

### Talk track for Reveal 2 (~60s):
- "This is the query pipeline. The router classifies intent in 0.03 milliseconds using regex — no LLM call. It detects 6 intents including an out-of-scope blocker for prompt injection, off-topic, and gibberish. All at zero cost."
- "Query expansion is something I added to solve a real problem: if you ask 'how does the spacecraft track its position', the embedding of that natural language question is weak against Fortran code. The expansion layer injects domain terms like SPKEZ, SPKEZR, SPKPOS into the embedding — so the vector search actually finds the right routines."
- "Retrieval is hybrid — Pinecone vector search plus a BM25 keyword index, merged with Reciprocal Rank Fusion. The router decides *which combination* to run per query — name-filtered search for explains, pattern-filtered for conceptual questions, unfiltered for semantic. That's agentic RAG — the system dynamically picks its retrieval strategy."
- "Context assembly is token-budgeted with tiktoken, and puts doc chunks first so the LLM sees routine descriptions before raw code."

---

## SLIDE 4: HARDEST CHALLENGE — The Parser & Latency

### On screen — Left side: Parser

```
┌─────────────────────────────────────────────────────────────────┐
│              FORTRAN 77 PARSER (415 lines, 3-pass)              │
│                                                                 │
│  WHY generic splitters fail on Fortran 77:                      │
│                                                                 │
│  Col: 1     6    7                          72  73+             │
│       │     │    │                          │   │               │
│       ▼     ▼    ▼                          ▼   ▼               │
│       C          This is a comment line             (ignored)   │
│                  SUBROUTINE SPKEZ ( TARG, ET,       (code)      │
│            .          REF, ABCORR, OBS )            (cont'd!)   │
│       C$ Abstract                                   (SPICE hdr) │
│       C     Return the state...                     (doc)       │
│             CALL CHKIN('SPKEZ')                     (body)      │
│             ENTRY FURNSH ( FILE )                   (alias!)    │
│             END                                     (boundary)  │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │ PASS 1: Find boundaries                                 │    │
│  │   Scan for SUBROUTINE/FUNCTION/ENTRY/END               │    │
│  │   → 1,816 routines + 457 ENTRY points found            │    │
│  │                                                         │    │
│  │ PASS 2: Classify lines                                  │    │
│  │   Comment → header_comments (documentation)             │    │
│  │   Declaration → header_comments                         │    │
│  │   Executable → body_code (implementation)               │    │
│  │   Extract: CALL targets, INCLUDE files                  │    │
│  │   Parse: C$ Abstract, C$ Keywords, C$ Brief_I/O         │    │
│  │                                                         │    │
│  │ PASS 3: ENTRY point extraction                          │    │
│  │   FURNSH is ENTRY in KEEPER (4,223 lines)               │    │
│  │   Create separate RoutineInfo with own C$ header        │    │
│  │   Link to parent via alias: FURNSH → KEEPER             │    │
│  │   → 457 aliases resolved in call graph                  │    │
│  └─────────────────────────────────────────────────────────┘    │
│                                                                 │
│  No tree-sitter grammar. No LangChain splitter. Custom built.   │
└─────────────────────────────────────────────────────────────────┘
```

### On screen — Right side: Latency

```
┌─────────────────────────────────────────────────────────────────┐
│                LATENCY: FROM 12s TO 1.5s                        │
│                                                                 │
│  Problem: first version took ~12 seconds per query              │
│                                                                 │
│  ┌──────────────────────────────────────────────────────┐       │
│  │ Optimization                          Savings        │       │
│  │ ─────────────────────────────────     ────────       │       │
│  │ Swap GPT-4o-mini → Gemini 2.5 Flash  -8s (TTFT)     │       │
│  │ Parallel Pinecone queries (ThreadPool) -300ms        │       │
│  │ def → async-safe sync (no event loop   -200ms        │       │
│  │       blocking)                                      │       │
│  │ Disable thinking (reasoning: none)     -400ms        │       │
│  │ Embedding cache (512-entry LRU)        -150ms        │       │
│  │ Answer cache (1hr TTL, SHA-256 key)    → 0.1s total  │       │
│  │ Tighter token budgets (intent-aware)   -200ms        │       │
│  │ Query expansion (better retrieval,     -500ms        │       │
│  │   fewer empty results)                               │       │
│  └──────────────────────────────────────────────────────┘       │
│                                                                 │
│  Result:                                                        │
│    Cold query:  1.5s median                                     │
│    Cached:      0.1s                                            │
│    Router:      0.03ms                                          │
│    Call graph:  <1ms (no API calls)                             │
│                                                                 │
│  3-layer caching:                                               │
│    L1: Embedding cache   (skip OpenAI embed call)               │
│    L2: Answer cache      (skip entire LLM generation)           │
│    L3: Client singletons (skip client init)                     │
└─────────────────────────────────────────────────────────────────┘
```

### Talk track (~2 min):

**Parser (60s):**
- "The hardest technical challenge was the parser. Fortran 77 is fixed-form — column position determines meaning. Column 1 is comments, column 6 is continuation, columns 7-72 are code, and 73 onwards is completely ignored — that's from the punch card era."
- "A generic text splitter like LangChain's would break mid-continuation-line, split comment headers from their routines, and miss ENTRY points entirely. So I built a 415-line, 3-pass parser."
- "The three passes are: find routine boundaries, classify every line as doc vs code, and extract ENTRY points. ENTRY is a Fortran feature where one routine has multiple entry points with different names. FURNSH is actually an entry point inside KEEPER — a 4,223-line subroutine. Without resolving these aliases, half the call graph would be disconnected."
- "This parser is why retrieval works. When you ask 'What does SPKEZ do?', you get the C$ Abstract documentation chunk — not a random slice of code."

**Latency (60s):**
- "The other challenge was latency. First version was 12 seconds per query."
- "The biggest win was swapping from GPT-4o-mini to Gemini 2.5 Flash through OpenRouter — saved 8 seconds just from faster time-to-first-token. I also disabled the model's built-in thinking since our answers are grounded in retrieved context, not reasoning."
- "Then I parallelized Pinecone queries with ThreadPoolExecutor, added embedding caching, and made token budgets intent-aware — dependency queries don't need 6,000 tokens of context."
- "End result: 1.5 second median cold, 100 milliseconds cached, and the router itself runs in 0.03 milliseconds. Three layers of caching: embedding, answer, and client singletons."

---

## SLIDE 5: LIVE DEMO

### On screen:
```
┌─────────────────────────────────────────────────────────────────┐
│                        LIVE DEMO                                │
│                                                                 │
│  legacylens-production-9578.up.railway.app                      │
│                                                                 │
│  Demo queries:                                                  │
│    1. "What does SPKEZ do?"          — core RAG, streaming      │
│    2. /deps FURNSH                   — call graph, $0, instant  │
│    3. /impact CHKIN                  — 1,257 callers             │
│    4. "How does the spacecraft       — query expansion,          │
│        track its position?"            no routine name needed    │
│    5. "What's the weather today?"    — guardrail, blocked        │
│    6. "What about its parameters?"   — multi-turn follow-up      │
│                                                                 │
│  Stats:                                                         │
│    378 tests  │  25 golden evals  │  100% router accuracy       │
│    100% faithfulness  │  96 commits in 3 days  │  $5.61 total   │
└─────────────────────────────────────────────────────────────────┘
```

### Demo order & narration:

1. **"What does SPKEZ do?"** — "Watch the intent badge change to EXPLAIN, the call graph populate, and the answer stream token by token with file:line citations."

2. **`/deps FURNSH`** — "This is instant — pure call graph traversal, zero API calls. Notice FURNSH is an ENTRY point in KEEPER — the alias is resolved automatically."

3. **`/impact CHKIN`** — "CHKIN is the error check-in routine. It has 1,257 callers. This is blast radius analysis — 'if I change this, what breaks?'"

4. **"How does the spacecraft track its position?"** — "No routine name mentioned. Query expansion injects SPKEZ, SPKEZR, SPKPOS into the embedding so the vector search finds the right code."

5. **"What's the weather today?"** — "Blocked before any API call. Prompt injection, off-topic, and gibberish detection — all regex, all free."

6. **"What about its parameters?"** — "This is multi-turn. The system remembers SPKEZ from query 4 and rewrites this as 'What about its parameters regarding SPKEZ?'"

### Closing line:
> "96 commits, 3 days, $5.61. A custom Fortran parser, hybrid retrieval with agentic routing, and a system that makes a million lines of 1977 spacecraft code queryable in plain English. That's LegacyLens."