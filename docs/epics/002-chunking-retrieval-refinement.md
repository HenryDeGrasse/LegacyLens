# Epic: Phase 2 — Chunking & Retrieval Refinement

## Goal

Improve retrieval quality by building a call graph, enriching metadata, merging small chunks, and adding `called_by` reverse index. This directly supports the Phase 3 advanced features (dependency mapping, impact analysis).

## Analysis (from MVP data)

- 457 ENTRY points have no body text (share parent's body)
- 136 small routines have <50 tokens of body code
- 405 routines have doc headers >3000 tokens
- 58 routines have no abstract
- Top called: CHKIN (1257), SIGERR (950), SETMSG (912)
- 662 routines are never called by other routines in the codebase

## Scope

- Build and persist a full call graph (forward + reverse)
- Enrich Pinecone metadata with `called_by` and `entry_aliases`
- Merge small routine body chunks into their doc chunks
- Add keyword-based metadata filtering to retrieval
- Re-ingest with improved chunking
- Measure retrieval precision improvement

## Non-Goals

- Interactive TUI (Phase 5)
- Advanced features endpoints (Phase 3)
- Re-ranking with a separate model
- Changing the embedding model
