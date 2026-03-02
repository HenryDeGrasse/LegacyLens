# Epic: MVP — End-to-End RAG Pipeline for SPICE Toolkit

## Goal

Build a fully working RAG pipeline over NASA's SPICE Toolkit Fortran 77 source code (~304K LOC, ~930 `.f` files) that accepts natural language questions and returns grounded answers with file:line citations. The MVP must be deployed and publicly accessible. This is a **hard gate** — all checklist items must pass within 24 hours.

## Scope (MVP)

- Download and verify the SPICE Toolkit Fortran source
- Python/FastAPI backend scaffolded with uv
- Fortran 77 fixed-form parser that detects routine boundaries, comments, continuations, and header sections
- Syntax-aware chunking into 4 types: `routine_doc`, `routine_body`, `routine_segment`, `include`
- Embed all chunks with OpenAI `text-embedding-3-small` (1536 dims)
- Store in Pinecone (`spice-fortran` index) with full metadata
- Two-path retrieval: exact routine name match + semantic vector search
- GPT-4o-mini answer generation with file:line citations
- CLI query interface
- FastAPI `/query` endpoint
- Deployed to Railway, publicly accessible

## Non-Goals

- Interactive TUI (deferred to Phase 5 polish)
- Re-ranking or advanced retrieval tuning (Phase 2)
- Advanced features: dependency mapping, pattern detection, impact analysis (Phase 3)
- ENTRY point aliasing or `called_by` reverse index (Phase 2)
- Cost analysis or architecture documentation (Phase 5)
- Tests beyond manual golden query validation
- CI/CD pipeline
- Authentication or rate limiting

---

## Implementation Tasks

### Task 1: Project Scaffolding — Python backend with FastAPI + uv

Set up the Python project skeleton with all dependencies and configuration.

- Files: `backend/pyproject.toml`, `backend/app/__init__.py`, `backend/app/main.py`, `backend/app/config.py`, `.env.example`
- Steps:
  1. Create `backend/` directory structure matching the proposed layout
  2. Initialize `pyproject.toml` with uv — deps: `fastapi`, `uvicorn`, `langchain`, `langchain-openai`, `langchain-pinecone`, `pinecone-client`, `openai`, `python-dotenv`, `rich` (for CLI)
  3. Create `app/config.py` with pydantic `Settings` loading from env: `OPENAI_API_KEY`, `PINECONE_API_KEY`, `PINECONE_INDEX` (default `spice-fortran`)
  4. Create `app/main.py` with a bare FastAPI app + `/health` endpoint returning `{"status": "ok"}`
  5. Create `.env.example` with all required keys
  6. Create empty `__init__.py` files for all subpackages: `app/`, `app/ingestion/`, `app/retrieval/`, `app/features/`
- Verify: `cd backend && uv run uvicorn app.main:app --host 0.0.0.0 --port 8000` starts and `curl localhost:8000/health` returns `{"status": "ok"}`
- Depends on: none

---

### Task 2: Acquire & Verify SPICE Toolkit Source

Download the Fortran source and confirm it meets the size threshold.

- Files: `data/` (gitignored), `scripts/download_spice.sh`
- Steps:
  1. Create `scripts/download_spice.sh` that:
     - Downloads the official macOS/Linux Fortran toolkit tarball from `https://naif.jpl.nasa.gov/pub/naif/toolkit/FORTRAN/`
     - Extracts to `data/spice/`
     - Falls back to `git clone https://github.com/maxhlc/spice.git data/spice` if tarball fails
  2. Run the script
  3. Locate the `.f` and `.inc` files within the extracted tree
  4. Verify: `find data/spice -name "*.f" | wc -l` shows ~930 files
  5. Verify: `find data/spice -name "*.f" -exec cat {} + | wc -l` shows ~304K LOC
  6. Note the exact paths to `.f` and `.inc` directories for the scanner
- Verify: `scripts/download_spice.sh` runs clean; file/LOC counts match expectations
- Depends on: none

---

### Task 3: File Scanner — Recursive discovery of `.f` and `.inc` files

Build the module that discovers all source files in the SPICE tree.

- Files: `backend/app/ingestion/scanner.py`
- Steps:
  1. Implement `scan_directory(root: str, extensions: list[str]) -> list[Path]` that recursively finds all files matching given extensions (`.f`, `.inc`, `.for`)
  2. Return sorted list of absolute paths
  3. Add a `get_file_stats(paths: list[Path]) -> dict` helper that returns total file count and total LOC
  4. Add `if __name__ == "__main__"` block that scans `data/spice/` and prints stats
- Verify: `cd backend && uv run python -m app.ingestion.scanner` prints ~930 `.f` files and ~304K LOC
- Depends on: Task 2

---

### Task 4: Fortran 77 Fixed-Form Parser

The core custom piece. Parse Fortran 77 fixed-form source into structured routine representations.

- Files: `backend/app/ingestion/fortran_parser.py`
- Steps:
  1. Implement line-level classification:
     - **Comment line:** column 1 is `C`, `c`, `*`, or `!`
     - **Continuation line:** column 6 is non-blank (not space or `0`)
     - **Statement line:** columns 7–72 contain the statement
  2. Implement routine boundary detection:
     - Regex in columns 7–72 matching `^\s*(SUBROUTINE|FUNCTION|PROGRAM|ENTRY|INTEGER FUNCTION|DOUBLE PRECISION FUNCTION|LOGICAL FUNCTION|CHARACTER\*?\(?\d*\)?\s+FUNCTION)\s+(\w+)`
     - `END` statement detection (standalone `END` or `END SUBROUTINE`, `END FUNCTION`)
  3. Implement SPICE header comment parsing:
     - Detect `C$` section markers: `Abstract`, `Keywords`, `Brief_I/O`, `Detailed_Input`, `Detailed_Output`, `Exceptions`, `Particulars`
     - Extract content between markers as structured metadata
  4. Implement `parse_file(path: Path) -> list[RoutineInfo]` where `RoutineInfo` is a dataclass:
     ```python
     @dataclass
     class RoutineInfo:
         name: str
         kind: str  # SUBROUTINE, FUNCTION, PROGRAM
         file_path: str
         start_line: int
         end_line: int
         header_comments: str  # full header block
         body_code: str  # executable code
         abstract: str
         keywords: list[str]
         calls: list[str]  # parsed CALL statements
         includes: list[str]  # INCLUDE statements
     ```
  5. Parse `CALL` statements: regex for `CALL\s+(\w+)` in statement lines
  6. Parse `INCLUDE` statements: regex for `INCLUDE\s+'([^']+)'`
  7. Handle edge cases: files with no routines (just comments/data), multiple routines per file, ENTRY points (store as additional name on parent routine for now)
- Verify: `cd backend && uv run python -m app.ingestion.fortran_parser data/spice/src/spicelib/spkez.f` prints parsed routine info for SPKEZ (name, kind, abstract, calls, line range)
- Depends on: Task 2

---

### Task 5: Chunking Pipeline — Turn parsed routines into embeddable chunks

Convert `RoutineInfo` objects into chunks with metadata ready for embedding.

- Files: `backend/app/ingestion/chunker.py`
- Steps:
  1. Define `Chunk` dataclass:
     ```python
     @dataclass
     class Chunk:
         id: str              # deterministic: f"{file_path}::{routine_name}::{chunk_type}::{index}"
         text: str            # the content to embed
         metadata: dict       # all Pinecone metadata fields
     ```
  2. Implement `chunk_routine(routine: RoutineInfo) -> list[Chunk]`:
     - **`routine_doc`**: header comments + first line of signature. One chunk per routine.
     - **`routine_body`**: executable code. One chunk if ≤ 1500 tokens (estimate ~4 chars/token). If larger → split into `routine_segment` chunks with ~200 token overlap.
     - Token estimation: use `len(text) // 4` as rough proxy (or `tiktoken` if available)
  3. Implement `chunk_include(path: Path) -> list[Chunk]`:
     - Read `.inc` file, produce single `include` chunk with file path metadata
  4. Implement `chunk_codebase(routines: list[RoutineInfo], include_paths: list[Path]) -> list[Chunk]`:
     - Process all routines + include files
     - Return full list of chunks
  5. Metadata per chunk: `file_path`, `start_line`, `end_line`, `routine_name`, `routine_kind`, `chunk_type`, `abstract`, `keywords` (comma-separated string), `calls` (comma-separated), `includes` (comma-separated)
- Verify: `cd backend && uv run python -m app.ingestion.chunker` processes all SPICE files and prints total chunk count (expect ~2,000–3,000)
- Depends on: Tasks 3, 4

---

### Task 6: Embedding & Pinecone Storage

Batch-embed all chunks and upsert into Pinecone.

- Files: `backend/app/ingestion/embedder.py`, `backend/app/ingestion/loader.py`
- Steps:
  1. **`embedder.py`**: Implement `embed_chunks(chunks: list[Chunk], batch_size=100) -> list[tuple[Chunk, list[float]]]`
     - Use OpenAI `text-embedding-3-small`
     - Batch requests (100 chunks per API call)
     - Exponential backoff on rate limits (retry 3x with 2/4/8s delays)
     - Checkpointing: write processed chunk IDs to `data/embed_checkpoint.json` — skip already-done on restart
     - Log running token count for cost tracking
  2. **`loader.py`**: Implement `upsert_to_pinecone(embedded_chunks: list[tuple[Chunk, list[float]]])`
     - Initialize Pinecone client, create index `spice-fortran` if not exists (dims=1536, metric=cosine)
     - Batch upsert (100 vectors per call) with `id=chunk.id`, `values=embedding`, `metadata=chunk.metadata`
     - Log progress every 500 vectors
  3. Create `backend/app/ingestion/ingest.py` — orchestrator script:
     - Scan → Parse → Chunk → Embed → Upsert (full pipeline)
     - Print summary: files scanned, routines parsed, chunks created, vectors upserted, tokens used, estimated cost
- Verify: `cd backend && uv run python -m app.ingestion.ingest` runs full pipeline; then verify with: `uv run python -c "from pinecone import Pinecone; pc = Pinecone(); idx = pc.Index('spice-fortran'); print(idx.describe_index_stats())"` shows ~2000+ vectors
- Depends on: Task 5

---

### Task 7: Two-Path Retrieval

Implement the search logic: exact routine match + semantic fallback.

- Files: `backend/app/retrieval/search.py`, `backend/app/retrieval/context.py`
- Steps:
  1. **`search.py`**: Implement `retrieve(query: str, top_k: int = 10) -> list[RetrievedChunk]`:
     - **Path 1 — Exact match:** Use regex to detect routine names in query (uppercase words that match known patterns like `SPKEZ`, `FURNSH`, etc.). If found, query Pinecone with `filter={"routine_name": detected_name}`, fetch `routine_doc` + `routine_body` chunks.
     - **Path 2 — Semantic search:** Embed the query with `text-embedding-3-small`, query Pinecone `top_k=10`, return results.
     - If Path 1 finds results, prepend them; fill remaining slots from Path 2 (deduplicate by chunk ID).
     - Return top 5–10 `RetrievedChunk` objects (score, chunk text, metadata).
  2. **`context.py`**: Implement `assemble_context(chunks: list[RetrievedChunk]) -> str`:
     - Prefer `routine_doc` chunks first
     - If doc + body from same routine both present, include both adjacent
     - Format each chunk as: `--- File: {file_path} | Lines: {start_line}-{end_line} | Routine: {routine_name} ---\n{text}\n`
     - Cap total context at ~3000 tokens (estimate by char count)
- Verify: `cd backend && uv run python -c "from app.retrieval.search import retrieve; r = retrieve('What does SPKEZ do?'); print(r[0].metadata)"` returns SPKEZ-related chunks
- Depends on: Task 6

---

### Task 8: Answer Generation with GPT-4o-mini

Generate grounded answers from retrieved context.

- Files: `backend/app/retrieval/generator.py`
- Steps:
  1. Implement `generate_answer(query: str, context: str) -> AnswerResponse`:
     ```python
     @dataclass
     class AnswerResponse:
         answer: str
         citations: list[dict]  # [{file_path, start_line, end_line, routine_name}]
     ```
  2. System prompt:
     ```
     You are a SPICE Toolkit expert. Answer questions about the Fortran 77 codebase using ONLY the provided code context.
     Rules:
     - Always cite sources as [file_path:start_line-end_line]
     - If the context doesn't contain enough information, say so explicitly
     - Explain Fortran 77 constructs in modern terms when helpful
     - Be precise about routine names, arguments, and behavior
     ```
  3. User prompt: `"Question: {query}\n\nCode Context:\n{context}"`
  4. Call GPT-4o-mini, parse response
  5. Extract citations from the answer text (regex for `[filepath:line-line]` patterns)
  6. Return `AnswerResponse`
- Verify: `cd backend && uv run python -c "from app.retrieval.generator import generate_answer; print(generate_answer('What does SPKEZ do?', '<mock context>').answer[:200])"` returns a coherent answer
- Depends on: Task 7

---

### Task 9: CLI Query Interface

User-facing CLI for querying the codebase.

- Files: `backend/app/cli.py`
- Steps:
  1. Accept query as command-line argument: `python -m app.cli "What does SPKEZ do?"`
  2. Call `retrieve()` → `assemble_context()` → `generate_answer()`
  3. Pretty-print with `rich`:
     - Answer text (markdown-rendered)
     - Separator
     - Each cited code snippet with syntax highlighting, file path, and line numbers
     - Relevance scores for each chunk
  4. Support `--top-k` flag (default 10)
  5. Support `--verbose` flag to show retrieval details (scores, paths taken)
- Verify: `cd backend && uv run python -m app.cli "Explain what the SPKEZ subroutine does"` prints a formatted answer with citations
- Depends on: Task 8

---

### Task 10: FastAPI `/query` Endpoint

Wire up the retrieval pipeline to the web server.

- Files: `backend/app/main.py`
- Steps:
  1. Add `POST /query` endpoint:
     - Request body: `{ "question": str, "top_k": int = 10 }`
     - Response: `{ "answer": str, "citations": [...], "chunks": [...] }`
  2. Add CORS middleware (allow all origins for MVP)
  3. Add basic error handling (return 500 with message on failure)
  4. Add `GET /stats` endpoint that returns Pinecone index stats (vector count, etc.)
- Verify: Start server, then `curl -X POST localhost:8000/query -H "Content-Type: application/json" -d '{"question": "What does SPKEZ do?"}'` returns JSON with answer and citations
- Depends on: Task 8

---

### Task 11: Deploy to Railway

Get the backend live and publicly accessible.

- Files: `backend/Dockerfile`, `backend/Procfile`, `railway.toml` (or Railway dashboard config)
- Steps:
  1. Create `backend/Dockerfile`:
     - Base: `python:3.12-slim`
     - Install uv, copy project, `uv sync`
     - CMD: `uv run uvicorn app.main:app --host 0.0.0.0 --port $PORT`
  2. Alternatively create `Procfile`: `web: uv run uvicorn app.main:app --host 0.0.0.0 --port $PORT`
  3. Set environment variables in Railway: `OPENAI_API_KEY`, `PINECONE_API_KEY`, `PINECONE_INDEX`
  4. Deploy via `railway up` or GitHub integration
  5. Note the public URL
- Verify: `curl https://<railway-url>/health` returns `{"status": "ok"}`; `curl -X POST https://<railway-url>/query -H "Content-Type: application/json" -d '{"question": "What does SPKEZ do?"}'` returns a grounded answer
- Depends on: Task 10

---

### Task 12: MVP Validation — Golden Query Testing

Run the golden test set and confirm all MVP checklist items pass.

- Files: `scripts/validate_mvp.py`
- Steps:
  1. Create a validation script that hits the deployed `/query` endpoint with these queries:
     - "Where is the main entry point of this program?"
     - "What functions modify ephemeris data?"
     - "Explain what the SPKEZ subroutine does"
     - "Find all file I/O operations"
     - "What are the dependencies of FURNSH?"
     - "Show me error handling patterns in this codebase"
  2. For each query, check:
     - Response returns within 10 seconds
     - `answer` field is non-empty
     - `citations` contains at least 1 file:line reference
     - `chunks` contains at least 1 chunk with valid metadata
  3. Print pass/fail summary
  4. Manual review: verify file:line references are accurate (spot-check 3 queries)
- Verify: `uv run python scripts/validate_mvp.py https://<railway-url>` prints all green
- Depends on: Task 11

---

## Parallelism Analysis

```
Phase 1 (parallel — no dependencies):
  - Task 1: Project Scaffolding
  - Task 2: Acquire SPICE Toolkit

Phase 2 (depends on Phase 1):
  - Task 3: File Scanner (depends on Task 2)
  - Task 4: Fortran 77 Parser (depends on Task 2)

Phase 3 (depends on Phase 2):
  - Task 5: Chunking Pipeline (depends on Tasks 3 + 4)

Phase 4 (depends on Phase 3):
  - Task 6: Embedding & Pinecone Storage (depends on Task 5)

Phase 5 (depends on Phase 4):
  - Task 7: Two-Path Retrieval (depends on Task 6)

Phase 6 (depends on Phase 5):
  - Task 8: Answer Generation (depends on Task 7)

Phase 7 (parallel — depends on Phase 6):
  - Task 9: CLI Interface (depends on Task 8)
  - Task 10: FastAPI Endpoint (depends on Task 8)

Phase 8 (depends on Phase 7):
  - Task 11: Deploy to Railway (depends on Task 10)

Phase 9 (depends on Phase 8):
  - Task 12: MVP Validation (depends on Task 11)
```

## Acceptance Criteria

- [ ] SPICE Toolkit source acquired and verified: ~930 `.f` files, ~304K LOC
- [ ] Fortran 77 parser correctly identifies routine boundaries, headers, and CALL statements (spot-check 5 routines)
- [ ] All source files chunked — total chunk count in range 2,000–4,000
- [ ] All chunks embedded and stored in Pinecone — `idx.describe_index_stats()` confirms vector count
- [ ] Query "What does SPKEZ do?" returns SPKEZ routine chunks with correct file:line refs
- [ ] Query "What are the dependencies of FURNSH?" returns FURNSH and its CALL targets
- [ ] CLI prints formatted answer with syntax-highlighted code snippets
- [ ] `POST /query` returns JSON with `answer`, `citations`, and `chunks` fields
- [ ] Deployed Railway URL responds to `/health` and `/query`
- [ ] All 6 golden queries return non-empty answers with at least 1 citation each
- [ ] End-to-end query latency < 10 seconds (relaxed for MVP; tighten to <3s in Phase 4)

## Risks / Notes

- **Fortran 77 fixed-form parsing is the riskiest task.** SPICE files may have inconsistencies. Plan to handle edge cases iteratively — get 90% of files parsing correctly first, then fix outliers.
- **NAIF download may be slow or require accepting license.** The `maxhlc/spice` GitHub mirror is the reliable fallback.
- **Pinecone free tier limit:** 100K vectors, 1 index — should be fine for ~2,500 chunks but monitor.
- **Token estimation for chunking:** Using `len(text)//4` is rough. If chunks are too large for embedding, switch to `tiktoken` for precise counting.
- **ENTRY points:** For MVP, ENTRY names are stored as metadata on the parent routine. Full aliasing deferred to Phase 2.
- **GPT-4o-mini may hallucinate file paths.** The system prompt enforces grounding, but validation in Task 12 should catch this.
