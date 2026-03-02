# LegacyLens — Implementation Plan

## Target: NASA SPICE Toolkit (Fortran 77)
- ~304,000 LOC across ~930 `.f` files + ~60 `.inc` headers
- Source: https://naif.jpl.nasa.gov/naif/toolkit_FORTRAN.html

---

## Architecture Decisions (Locked In from Pre-Search)

| Decision | Choice | Rationale |
|---|---|---|
| **Vector DB** | Pinecone (managed, free tier) | Zero ops, cosine metric, 1536 dims, metadata filtering |
| **Embeddings** | OpenAI `text-embedding-3-small` | $0.02/1M tokens, 1536 dims, good quality/cost balance |
| **LLM** | GPT-4o-mini | Fast, cheap (~$0.002/query), good for grounded answers |
| **Framework** | LangChain + custom Fortran parser | LangChain for RAG plumbing, custom for Fortran 77 fixed-form |
| **Backend** | Python / FastAPI | Best LangChain ecosystem, async, fast prototyping |
| **Frontend** | CLI client + minimal web UI | CLI first for speed, web for demo/deployment |
| **Deployment** | Railway | Hobby tier, easy FastAPI hosting |

### Pinecone Index Schema
- **Index name:** `spice-fortran`
- **Metric:** cosine
- **Dimensions:** 1536
- **Metadata per vector:** `file_path`, `start_line`, `end_line`, `routine_name`, `routine_kind`, `chunk_type`, `abstract`, `keywords`, `calls`, `called_by`, `includes`, `toolkit_version`

### Chunk Types
| Type | Description |
|---|---|
| `routine_doc` | Header comment block + signature (Abstract, Brief_I/O, Exceptions, Keywords) |
| `routine_body` | Executable code of the routine |
| `routine_segment` | Oversized bodies split into overlapping segments |
| `include` | `.inc` / common-block header files |

---

## Phase 1: MVP — End-to-End RAG Pipeline (Day 1, 24 hours)
> **Hard gate.** All MVP checklist items must pass.

### Step 1: Project Scaffolding (~1 hour)
- [ ] Set up Python backend with FastAPI + `pyproject.toml` (uv)
- [ ] Configure environment variables: `OPENAI_API_KEY`, `PINECONE_API_KEY`, `PINECONE_INDEX`
- [ ] Create `.env.example`
- [ ] Set up Pinecone index (`spice-fortran`, cosine, 1536 dims)

### Step 2: Acquire & Verify SPICE Toolkit (~30 min)
- [ ] Download official Fortran toolkit tarball from NAIF
- [ ] Fallback: clone `maxhlc/spice` and extract source
- [ ] Run `find . -name "*.f" | wc -l` and `find . -name "*.f" -exec cat {} + | wc -l` to verify ~930 files / ~304K LOC
- [ ] Explore file structure, identify `.f` and `.inc` locations

### Step 3: Fortran 77 Fixed-Form Parser (~4 hours)
- [ ] **Comment detection:** Column 1 is `C`, `c`, `*`, or `!` → comment line
- [ ] **Continuation lines:** Column 6 non-blank → continuation of previous statement
- [ ] **Routine boundary detection:** Regex in columns 7–72 matching `SUBROUTINE`, `FUNCTION`, `PROGRAM`, `ENTRY`
- [ ] **END detection:** `END` in statement field
- [ ] **Header section parsing:** Extract `C$` markers for Abstract, Keywords, Brief_I/O, Exceptions
- [ ] **File scanner:** Recursively discover all `.f` and `.inc` files
- [ ] **Chunking pipeline:** Produce `routine_doc`, `routine_body`, `routine_segment`, `include` chunks
- [ ] **Metadata extraction:** routine_name, routine_kind, calls (CALL statements), includes

### Step 4: Embedding & Storage (~2 hours)
- [ ] Batch-embed all chunks with `text-embedding-3-small` (batch size 100)
- [ ] Implement checkpointing (resume if interrupted)
- [ ] Exponential backoff for rate limits
- [ ] Upsert into Pinecone with full metadata
- [ ] Log total tokens for cost reporting
- [ ] Verify: run test similarity search from Python

### Step 5: Two-Path Retrieval (~2 hours)
- [ ] **Path 1 — Exact routine match:** Detect routine names in query → filter by `routine_name` metadata → return `routine_doc` + `routine_body`
- [ ] **Path 2 — Semantic search fallback:** Embed query → Pinecone `top_k=10` → return best 5
- [ ] Context assembly: include file path + line range, prefer `routine_doc`, cap ~3000 tokens
- [ ] If doc + body from same routine found, include both

### Step 6: Answer Generation (~1 hour)
- [ ] GPT-4o-mini with system prompt enforcing:
  - Use only provided context
  - Always cite `file:line`
  - State "insufficient evidence" when needed
- [ ] Return structured JSON: `{ answer, citations, chunks }`

### Step 7: CLI Query Interface (~1 hour)
- [ ] CLI script: `python -m app.cli "What does SPKEZ do?"`
- [ ] Pretty-print: answer, then cited code snippets with file:line

### Step 8: FastAPI Server + Deploy (~2 hours)
- [ ] `POST /query` endpoint — accepts `{ question }`, returns `{ answer, citations, chunks }`
- [ ] Health check endpoint
- [ ] Deploy to Railway
- [ ] Verify end-to-end in production

### Step 9: MVP Validation (~1 hour)
- [ ] Test with golden queries:
  1. "Where is the main entry point of this program?"
  2. "What functions modify ephemeris data?"
  3. "Explain what the SPKEZ subroutine does"
  4. "Find all file I/O operations"
  5. "What are the dependencies of FURNSH?"
  6. "Show me error handling patterns in this codebase"
- [ ] Confirm all MVP checklist items pass

---

## Phase 2: Chunking & Retrieval Refinement (Day 2)
> **Goal:** Improve retrieval quality with better parsing and metadata.

- [ ] Handle `ENTRY` points — map ENTRY names to parent routine as alias metadata
- [ ] Merge very small utility routines with their `routine_doc` header
- [ ] Build `called_by` reverse index from parsed CALL statements
- [ ] Add keyword-based boosting from parsed `C$ Keywords` headers
- [ ] Tune `top_k` and context assembly based on MVP test results
- [ ] Re-ingest with improved chunking, compare retrieval precision

---

## Phase 3: Advanced Code Understanding Features (Days 2–3)
> **Goal:** Implement 4+ committed features.

### Feature 1: Code Explanation
- [ ] Retrieve `routine_doc` + `routine_body` for a given routine
- [ ] Generate plain English explanation with citations
- [ ] Endpoint: `POST /explain` with `{ routine_name }` or `{ file, start_line, end_line }`

### Feature 2: Dependency Mapping
- [ ] Parse all `CALL` statements across codebase
- [ ] Build forward call graph (routine → calls) and reverse call graph (routine → called_by)
- [ ] Store as metadata in Pinecone + local JSON
- [ ] Endpoint: `POST /dependencies` — returns callers and callees

### Feature 3: Pattern Detection
- [ ] Identify common patterns: error handling (`CHKIN`/`CHKOUT`/`SIGERR`), kernel loading (`FURNSH`), argument validation
- [ ] Tag chunks with detected patterns as metadata
- [ ] Endpoint: `POST /patterns` — find routines matching a pattern type

### Feature 4: Impact Analysis
- [ ] Use reverse-call graph for "blast radius" — what breaks if this routine changes?
- [ ] Walk up to 2 levels of callers
- [ ] Endpoint: `POST /impact` with `{ routine_name }`

### Stretch: Documentation Generation
- [ ] Generate Markdown documentation per routine
- [ ] Batch mode for entire modules

---

## Phase 4: Evaluation & Performance (Day 3)
> **Goal:** Measure, optimize, document.

- [ ] Build golden test set (10+ query/answer pairs from pre-search)
- [ ] Measure **Precision@5** (target: >70% relevant chunks)
- [ ] Measure **end-to-end latency** (target: <3 seconds)
- [ ] Verify **100% file coverage** in index
- [ ] Measure **source accuracy** (correct file:line references)
- [ ] Add query caching for repeated queries
- [ ] Log per-query: `query_id`, detected entities, retrieval path, Pinecone latency, top_k scores, OpenAI tokens, total latency
- [ ] Document failure modes and edge cases

---

## Phase 5: Polish & Submission (Days 4–5)
> **Goal:** Submission-ready.

- [ ] **Minimal web UI** — search bar, syntax-highlighted results, file:line links, LLM answer
- [ ] **RAG Architecture Document** (1–2 pages): vector DB selection, embedding strategy, chunking approach, retrieval pipeline, failure modes, performance results
- [ ] **AI Cost Analysis:**
  - Dev spend: embedding tokens, LLM tokens, Pinecone usage
  - Production projections at 100 / 1K / 10K / 100K users
- [ ] Update README with full setup guide and architecture overview
- [ ] Record 3–5 minute demo video
- [ ] Social media post (X/LinkedIn, tag @GauntletAI)

---

## Estimated Costs

| Item | Estimate |
|---|---|
| One-time ingestion (embeddings) | ~$0.02 (1M tokens) |
| Per query (embed + LLM) | ~$0.002 |
| Pinecone | Free tier |
| Railway | Hobby tier (~$5/mo) |
| **Total MVP dev cost** | **< $1** |

---

## Immediate Next Steps (Right Now)

1. **Scaffold the Python backend** — FastAPI + pyproject.toml with uv
2. **Download/acquire the SPICE Toolkit** Fortran source
3. **Build the Fortran 77 fixed-form parser** — this is the hardest custom piece
4. **Get one chunk embedded and retrieved from Pinecone** — prove end-to-end before scaling

---

## Proposed Directory Structure

```
LegacyLens/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI app
│   │   ├── cli.py               # CLI query interface
│   │   ├── config.py            # Settings & env vars
│   │   ├── ingestion/
│   │   │   ├── scanner.py       # File discovery (.f, .inc)
│   │   │   ├── fortran_parser.py # Fortran 77 fixed-form parser
│   │   │   ├── chunker.py       # Chunk assembly (doc, body, segment, include)
│   │   │   ├── embedder.py      # OpenAI embedding + batching
│   │   │   └── loader.py        # Pinecone upsert
│   │   ├── retrieval/
│   │   │   ├── search.py        # Two-path retrieval (exact + semantic)
│   │   │   ├── context.py       # Context assembly (~3000 tokens)
│   │   │   └── generator.py     # GPT-4o-mini answer generation
│   │   └── features/
│   │       ├── explain.py       # Code explanation
│   │       ├── dependencies.py  # Call graph + reverse index
│   │       ├── patterns.py      # Pattern detection
│   │       └── impact.py        # Blast radius analysis
│   ├── pyproject.toml
│   └── Dockerfile
├── frontend/                     # Minimal web UI (Phase 5)
├── data/                         # SPICE source files (gitignored)
├── docs/
│   ├── presearch.md
│   ├── IMPLEMENTATION_PLAN.md
│   ├── architecture.md
│   └── cost-analysis.md
├── .env.example
├── .gitignore
└── README.md
```
