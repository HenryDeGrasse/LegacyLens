# LegacyLens Pre-Search Document

## SPICE Toolkit (Fortran 77) — RAG System

Author: [Your Name] Date: [Date]
Codebase: NASA NAIF SPICE Toolkit / SPICELIB (Fortran 77)

## 0 — Project Goal

Build a RAG system over NASA's SPICE Toolkit Fortran source (SPICELIB) that supports semantic search
and developer-style questions, returning relevant code snippets with file + line references and a grounded,
cited answer. MVP must be deployed and publicly accessible within 24 hours.

## Phase 1 — Constraints

1 — Scale & Load Profile
Source origin Official NAIF distribution: https://naif.jpl.nasa.gov/naif/toolkit_FORTRAN.html
Backup reference GitHub wrapper (build reference only): https://github.com/maxhlc/spice
Verified size ~304,000 LOC across ~930 .f files
INCLUDE files ~60 .inc / common-block headers
Minimum threshold Exceeds 10,000 LOC / 50 files by ~30×
Expected query volume Low — single developer + evaluator; prioritize correctness
Update pattern Batch ingestion for MVP; optional rebuild on demand

Acquisition plan:

1. Primary: Download official Fortran toolkit tarball from NAIF.
2. Fallback: Clone maxhlc/spice and extract source.
3. Verification: Run `find` + `wc -l` to confirm LOC before ingestion.

2 — Budget & Cost Ceiling
Embeddings: OpenAI text-embedding-3-small (~$0.02 / 1M tokens). LLM generation: GPT-4o-mini for MVP.
Vector DB: Pinecone free tier. Deployment: Railway hobby tier.

```
Estimated ingestion cost (one-time)~930 routines → ~2,500 chunks; ~1M tokens total; embedding cost ~ $0.02.
Per-query estimate Query embedding: ~50 tokens; GPT-4o-mini generation ~2,000 tokens (~$0.002/query).
```
3 — Time to Ship
MVP: 24 hours — ingest, chunk, embed, store, search, answer, deploy. Final: Day 5–7 for polish.

4 — Data Sensitivity
SPICE is open-source; sending chunks to external APIs is acceptable. No residency constraints.


5 — Team & Skill Constraints
Solo; strong Python & Node.js; prior LangChain experience; no vector DB experience; Fortran 77 reading
proficiency.

## Phase 2 — Architecture Discovery & Decisions

A — Vector Database Selection
Decision: Pinecone (managed). Minimal ops, free tier adequate for MVP. Single index 'spice-fortran', cosine
metric, 1536 dims. Use metadata for filtering.

Metadata schema (per vector)
file_path, start_line, end_line, routine_name, routine_kind, chunk_type, abstract,
keywords, calls, called_by, includes, toolkit_version

B — Embedding Strategy
Decision: OpenAI text-embedding-3-small (1536 dims). Batch size 100, checkpointing, exponential backoff, log
total tokens for cost reporting.

C — Chunking Strategy (Fortran 77 Fixed-Form Aware)
SPICE routines include structured header comment blocks (Abstract, Brief_I/O, Exceptions, Keywords). Chunk
types: routine_doc (header + signature), routine_body (code), routine_segment (oversized bodies), include
(.inc files).

Fixed-Form Parsing Rules

- Comment detection: Column 1 is C, c, *, or! → comment line.
- Continuation lines: Column 6 non-blank → continuation.
- Routine boundaries: Regex in columns 7–72 matching SUBROUTINE/FUNCTION/PROGRAM/ENTRY.
- Header sections: Parse C$ markers to extract Abstract, Keywords, Brief_I/O, Exceptions.
- END detection: 'END' in statement field.

D — Retrieval Pipeline
Two-path retrieval: (1) Exact routine match path: filter by routine_name if detected; return routine_doc + body.
(2) Semantic vector search fallback: embed query, Pinecone top_k=10, present best 5. Prefer routine_doc
chunks. No rerank in MVP.

Context assembly rules

1. Include file path + line range for every chunk. 2. Prefer routine_doc chunks. 3. If doc+body from same
routine, include both. 4. Cap context ~3000 tokens.

E — Answer Generation
Model: GPT-4o-mini for MVP. System prompt enforces: use only provided context, always cite file:line, state
insufficient evidence if needed.


F — Framework Selection
LangChain + custom Fortran ingestion. FastAPI server on Railway. CLI client + optional minimal web UI.

G — Deployment Architecture
Ingestion: local script (parse → embed → upsert). Serving: Railway FastAPI `/query` endpoint that embeds,
searches Pinecone, assembles context, calls OpenAI, and returns JSON. CLI targets the Railway URL.

## Phase 3 — Post-Stack Refinement

Failure Modes & Mitigations
Fixed-form parsing errors Conservative detection; manual spot-check of sample routines.
ENTRY points ambiguity Map ENTRY names to parent routine as alias metadata.
Very small utility routines Merge with routine_doc header or boost by metadata.
Ambiguous queries Return top-5 with confidence; suggest routine names.

Evaluation Strategy
Golden test set (10 queries) covering entry points, modifications, explanations, I/O, dependencies, patterns.
Metrics: Precision@5 > 70%, latency < 3s, source accuracy 100%.

Observability
Log per-query: query_id, detected entities, retrieval path, pinecone latency, top_k scores, chunks returned,
openai tokens, total latency. Use logs for debugging and cost tracking.

## Phase 4 — Required Feature Commitments

Committed Features (4+):

- 1) Code Explanation: retrieve routine_doc + body; generate explanation with citations.
- 2) Dependency Mapping: parse CALLs and build reverse call index.
- 3) Pattern Detection: error handling, kernel loading, argument validation.
- 4) Impact Analysis: reverse-call 'blast radius' up to 2 levels.
- Stretch: Documentation Generation (Markdown per routine).

## MVP Checklist (Explicit Mapping)

```
Ingest SPICE Fortran Planned
Syntax-aware chunking Planned
Generate embeddings Planned
Store in Pinecone Planned
Semantic search (two-path) Planned
```

```
CLI query interface + Railway deploy Planned
File/line refs in responses Planned
GPT-4o-mini grounded answers Planned
```
## References

- NAIF SPICE Toolkit (Fortran): https://naif.jpl.nasa.gov/naif/toolkit_FORTRAN.html
- SPICE Documentation: https://naif.jpl.nasa.gov/pub/naif/toolkit_docs/FORTRAN/
- GitHub build wrapper: https://github.com/maxhlc/spice
- Pinecone docs: https://docs.pinecone.io/
- OpenAI Embeddings: https://platform.openai.com/docs/guides/embeddings
- LangChain: https://python.langchain.com/



