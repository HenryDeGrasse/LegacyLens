# LegacyLens — Implementation Plan

## Phase 0: Pre-Search & Architecture Decisions (Now)
> **Goal:** Lock in all technology choices before writing code.

### Decisions to Make
| Decision | Options | Recommendation | Rationale |
|---|---|---|---|
| **Target Codebase** | GnuCOBOL, LAPACK, BLAS, gfortran | **GnuCOBOL** | COBOL is the quintessential legacy language; paragraph-level chunking is well-defined; strong enterprise relevance |
| **Vector Database** | Pinecone, Qdrant, ChromaDB, pgvector | **Qdrant** (self-hosted via Docker) | Fast, great filtering/metadata support, free locally, easy to deploy, Rust-based performance |
| **Embedding Model** | OpenAI ada-3-small, Voyage Code 2 | **Voyage Code 2** | Purpose-built for code embeddings; better semantic understanding of programming constructs |
| **LLM** | GPT-4, Claude, Llama | **Claude 3.5 Sonnet** | Strong code reasoning, large context window, good at explaining legacy patterns |
| **Framework** | LangChain, LlamaIndex, Custom | **LangChain** | Mature RAG tooling, good vector store integrations, well-documented |
| **Backend** | FastAPI, Express | **Python/FastAPI** | Best LangChain support, async, fast prototyping |
| **Frontend** | React/Next.js, CLI | **Next.js** (with CLI fallback) | SSR, good DX, easy deployment to Vercel |
| **Deployment** | Vercel, Railway, Fly.io | **Vercel** (frontend) + **Railway** (backend + Qdrant) | Simple, scalable, free tiers |

---

## Phase 1: MVP — Basic RAG Pipeline (Day 1, ~24 hours)
> **Goal:** Pass the hard gate. Get a working end-to-end query pipeline.

### Step 1: Project Scaffolding (~1 hour)
- [ ] Set up Python backend with FastAPI + `pyproject.toml` (uv)
- [ ] Set up Next.js frontend
- [ ] Configure environment variables (API keys, DB connection)
- [ ] Docker Compose for local Qdrant

### Step 2: Codebase Ingestion (~3 hours)
- [ ] Clone GnuCOBOL repository
- [ ] Build file discovery — recursively scan `.cob`, `.cbl`, `.CBL` files
- [ ] Preprocessing: handle encoding, normalize whitespace, extract comments
- [ ] **Basic chunking** — start with fixed-size + overlap (get it working first)
- [ ] Extract metadata: file path, line numbers, function/paragraph names

### Step 3: Embedding & Storage (~2 hours)
- [ ] Integrate Voyage Code 2 (or OpenAI text-embedding-3-small as fallback)
- [ ] Batch-embed all chunks
- [ ] Store in Qdrant with full metadata (file, lines, chunk type)
- [ ] Verify: run a test similarity search from Python

### Step 4: Query & Retrieval (~2 hours)
- [ ] Build query endpoint in FastAPI (`POST /query`)
- [ ] Embed incoming query with same model
- [ ] Similarity search: top-k=10 chunks from Qdrant
- [ ] Return results with file paths, line numbers, relevance scores

### Step 5: Answer Generation (~2 hours)
- [ ] Add LLM call (Claude) to synthesize answer from retrieved chunks
- [ ] Prompt template: "Given these code snippets from a COBOL codebase, answer the user's question. Cite file paths and line numbers."
- [ ] Stream response back to client

### Step 6: Minimal Frontend (~3 hours)
- [ ] Search bar for natural language queries
- [ ] Display retrieved code snippets with syntax highlighting
- [ ] Show file paths, line numbers, relevance scores
- [ ] Display LLM-generated explanation

### Step 7: Deploy MVP (~2 hours)
- [ ] Deploy backend to Railway (FastAPI + Qdrant)
- [ ] Deploy frontend to Vercel
- [ ] Verify end-to-end in production
- [ ] Test with the 6 sample queries from the spec

---

## Phase 2: Chunking Refinement (Day 2)
> **Goal:** Replace naive chunking with syntax-aware splitting for COBOL.

- [ ] Build COBOL parser for paragraph-level boundaries (`PARAGRAPH-NAME.` → next paragraph)
- [ ] Function/section-level chunking (PROCEDURE DIVISION sections)
- [ ] Hierarchical chunking: file → division → section → paragraph
- [ ] Re-ingest codebase with improved chunking
- [ ] A/B test retrieval quality: old chunks vs new chunks
- [ ] Document chunking strategy and tradeoffs

---

## Phase 3: Advanced Code Understanding Features (Days 2-3)
> **Goal:** Implement 4+ features from the spec.

### Feature 1: Code Explanation
- [ ] Given a function/paragraph, generate plain English explanation
- [ ] Endpoint: `POST /explain` with file + line range

### Feature 2: Dependency Mapping
- [ ] Parse CALL statements, COPY/INCLUDE, PERFORM references
- [ ] Build a dependency graph (stored as metadata)
- [ ] Query: "What does X depend on?" / "What calls X?"

### Feature 3: Pattern Detection
- [ ] Use embeddings to find similar code patterns across the codebase
- [ ] Endpoint: `POST /find-similar` — given a snippet, find similar ones

### Feature 4: Documentation Generation
- [ ] Generate docstrings/comments for undocumented paragraphs
- [ ] Batch mode: generate docs for entire files

### Feature 5 (Stretch): Business Logic Extraction
- [ ] Identify IF/EVALUATE blocks that encode business rules
- [ ] Extract and summarize in plain English

---

## Phase 4: Evaluation & Performance (Day 3)
> **Goal:** Measure and optimize.

- [ ] Build ground truth test set (20+ query/answer pairs)
- [ ] Measure retrieval precision (target: >70% relevant in top-5)
- [ ] Measure end-to-end latency (target: <3 seconds)
- [ ] Verify 100% file coverage in index
- [ ] Implement caching for repeated queries
- [ ] Add re-ranking step if precision is below target
- [ ] Document failure modes and edge cases

---

## Phase 5: Polish & Documentation (Days 4-5)
> **Goal:** Submission-ready.

- [ ] Write RAG Architecture Document (1-2 pages)
- [ ] Complete AI Cost Analysis (dev spend + production projections)
- [ ] Improve frontend UX: syntax highlighting, drill-down to full file, confidence scores
- [ ] Record 3-5 minute demo video
- [ ] Update README with setup guide and architecture overview
- [ ] Social media post (X/LinkedIn)

---

## Immediate Next Steps (Right Now)

1. **Complete the Pre-Search checklist** in `docs/presearch.md` — lock in all architecture decisions
2. **Scaffold the Python backend** with FastAPI + uv
3. **Clone GnuCOBOL** into a `data/` directory and explore the codebase structure
4. **Get a single chunk embedded and retrieved** — prove the pipeline works end-to-end before scaling

---

## Directory Structure (Proposed)

```
LegacyLens/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI app
│   │   ├── ingestion/
│   │   │   ├── scanner.py       # File discovery
│   │   │   ├── chunker.py       # Syntax-aware chunking
│   │   │   ├── embedder.py      # Embedding generation
│   │   │   └── loader.py        # Vector DB insertion
│   │   ├── retrieval/
│   │   │   ├── search.py        # Similarity search
│   │   │   ├── reranker.py      # Re-ranking logic
│   │   │   └── generator.py     # LLM answer generation
│   │   ├── features/
│   │   │   ├── explain.py       # Code explanation
│   │   │   ├── dependencies.py  # Dependency mapping
│   │   │   ├── patterns.py      # Pattern detection
│   │   │   └── docgen.py        # Documentation generation
│   │   └── config.py            # Settings & env vars
│   ├── pyproject.toml
│   └── Dockerfile
├── frontend/
│   ├── src/
│   ├── package.json
│   └── next.config.js
├── data/                         # Cloned codebases (gitignored)
├── docs/
│   ├── presearch.md
│   ├── IMPLEMENTATION_PLAN.md
│   ├── architecture.md           # RAG architecture doc
│   └── cost-analysis.md          # AI cost analysis
├── docker-compose.yml            # Qdrant + backend
├── .env.example
├── .gitignore
└── README.md
```
