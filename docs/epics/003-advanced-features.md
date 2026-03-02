# Epic: Phase 3 — Advanced Code Understanding Features

## Goal

Implement 4+ code understanding features on top of the RAG pipeline to help developers deeply understand the SPICE Toolkit codebase.

## Features Delivered (5 total)

### 1. Code Explanation (`POST /explain`)
- Retrieves routine_doc + body chunks from Pinecone
- Loads call graph context (calls, callers)
- Generates structured explanation with GPT-4o-mini:
  - Purpose, Inputs & Outputs, Algorithm, Dependencies, Usage Context, Modern Equivalent
- Tested: SPKEZ, FURNSH working with full citations

### 2. Dependency Mapping (`POST /dependencies`) — Phase 2
- Forward + reverse call graph (12,719 edges)
- ENTRY alias resolution (457 aliases)
- Configurable depth traversal

### 3. Pattern Detection (`GET /patterns` + `POST /patterns/search`)
- 8 SPICE pattern categories detected across 4,147 chunks
- Pattern-filtered vector search with optional query refinement
- Returns deduplicated routine list with abstracts and scores

### 4. Impact Analysis (`POST /impact`) — Phase 2
- Blast radius up to N levels of reverse call graph
- SPKEZ: 15 routines affected at depth=2

### 5. Documentation Generation (`POST /docgen`) — Stretch
- Generates Markdown reference docs per routine
- Synopsis, Description, Parameters, Errors, Examples, See Also
- Includes call graph context and file:line source references

## API Summary

| Endpoint | Method | Description |
|---|---|---|
| `/query` | POST | Natural language RAG query |
| `/explain` | POST | Detailed routine explanation |
| `/dependencies` | POST | Forward/reverse call graph |
| `/impact` | POST | Blast radius analysis |
| `/patterns` | GET | List pattern categories |
| `/patterns/search` | POST | Search by pattern |
| `/docgen` | POST | Generate Markdown docs |
| `/stats` | GET | Pinecone index stats |
| `/health` | GET | Health check |
