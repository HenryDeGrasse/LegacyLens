# LegacyLens — Postmortem & Future Directions

> Last updated: March 2026 | Branch: `spike/deep-audit`

## Project Summary

LegacyLens is a RAG system over NASA's SPICE Toolkit — 965K LOC of Fortran 77 across 1,816 files. It makes the codebase queryable through natural language with grounded, cited answers.

**Stack**: FastAPI + Pinecone + OpenAI embeddings + OpenRouter (swappable LLMs) + Textual TUI

**Key numbers**:
- 5,386 vectors in Pinecone (`text-embedding-3-small` @ 1536 dims)
- 1,811 routines, 12,719 call edges, 457 ENTRY aliases
- 25 eval cases across 10 subcategories
- Median E2E response: **1.9s** (Gemini 2.0 Flash) / **2.7s** (GPT-4o-mini)

---

## What Went Well

### 1. Regex-first query router
The router classifies intent in <0.05ms with zero API calls. It handles 80%+ of queries correctly using simple pattern matching. This was the right call — no need for an LLM to decide "is this a dependency question?"

### 2. Three-tier eval system
Adopted from AgentForge's approach: free schema tests on every commit, Pinecone-only retrieval tests on PRs ($0.01), full LLM pipeline nightly ($0.15). Pass-rate thresholds per category mean CI actually gates on quality.

### 3. Call graph as a first-class feature
Pre-computing the 618KB call graph during ingestion means dependency/impact queries are instant (no API calls). The TUI's tree view makes this explorable interactively.

### 4. OpenRouter integration
Swappable LLM backends without re-indexing. Embeddings stay on OpenAI (matching the Pinecone index), completions route through OpenRouter. This let us benchmark 3 models in minutes.

### 5. Streaming SSE
Time-to-first-token matters more than time-to-last-token for perceived responsiveness. SSE streaming means the user sees content in ~700ms even when full generation takes 3s+.

---

## What Didn't Go Well

### 1. Adversarial query routing
The regex router treats any 3+ char uppercase token not in its stop list as a routine name. "What's the weather?" → routes to EXPLAIN with `routine_names=["WEATHER", "TODAY"]`. The router has no concept of "out-of-scope." Adversarial protection relies entirely on the LLM's grounding to the code context.

### 2. Chunk type expectations
We assumed pattern/semantic queries would return `routine_doc` chunks, but Pinecone actually returns `routine_body` for broad queries. This caused 3 eval failures in CI. The Pinecone index doesn't favor doc chunks for non-routine-specific queries — score is purely semantic similarity.

### 3. Context assembly token budget
We tried to optimize context assembly (pre-encode texts to avoid double-counting), but it either broke isomorphism (different context lengths) or was net-negative (2N encodes vs N). tiktoken dominates at ~1ms/encode but there's no free optimization.

### 4. E2E latency vs spec
The spec says "<3 seconds end-to-end." With the original prompt and token budgets, only 40% of queries met this for full answer generation. After tightening the prompt and halving max_tokens, Gemini 2.0 Flash hits 5/5 under 3s. But this came at the cost of answer depth.

---

## Inefficiencies Found & Fixed (This Session)

| Issue | Fix | Impact |
|---|---|---|
| **Blocking I/O in async endpoints** | Changed 8 `async def` endpoints to `def` so FastAPI auto-threadpools them | Prevents event loop blocking under concurrent load |
| **N+1 Pinecone queries in routine_lookup** | Parallel `ThreadPoolExecutor` for alias resolution (2 names → 2 concurrent queries) | ~100-200ms saved per explain/docgen/metrics call |
| **N+1 Pinecone queries in search.py** | Parallel queries for `_retrieve_by_routine_name` | ~100-300ms saved when resolving ENTRY aliases |
| **Context budget inconsistency** | Aligned `/query` POST with SSE streaming (both now use intent-aware budgets: 2000 for deps, 2500 for impact) | Fewer tokens → faster LLM response for dep/impact |
| **Verbose system prompt** | Rewritten from 9 rules to 7 tight ones, 4-sentence cap | ~40% shorter answers, fits in 3s budget |
| **Token budgets too generous** | Halved: 400→200 (deps), 500→250 (impact), 600→300 (explain) | Direct E2E speedup — fewer tokens to generate |

---

## 10 Ideas for Next Iteration

### ✅ Implemented

#### 1. Hybrid Search (BM25 + Vector) — DONE
BM25 keyword index built from call graph metadata, merged with Pinecone vector results via Reciprocal Rank Fusion (RRF, k=60). Improves recall for exact keyword queries like bare routine names. BM25 index is lazy-built on first query and cached.

#### 4. Adversarial Router Hardening — DONE
Added `OUT_OF_SCOPE` intent with three-layer detection: prompt injection regex, known off-topic patterns (weather, jokes, stocks), and code generation requests. Vague codebase questions like "how does the spaceship track its location?" still route through to SEMANTIC thanks to a positive relevance regex. Zero API cost for blocked queries.

#### 6. Multi-Turn Conversation — DONE
`ConversationStore` keeps last 5 Q&A turns per session (30-min TTL, 500 max sessions). Both `/query` and `/api/stream` accept `session_id`. Frontend auto-creates a session on page load. Follow-up questions like "what about its parameters?" now have context from prior turns.

#### 7. Recorded Eval Baseline — DONE
25 golden sessions recorded with Gemini 2.0 Flash (100% pass rate). Every commit now runs replay tests ($0) against these fixtures, catching regressions in router intent, retrieval recall, and answer faithfulness.

#### 8. Model Quality Eval Matrix — DONE
`tests/model_comparison.py` runs all non-adversarial cases across N models and produces a comparison matrix: pass rate, avg faithfulness, hallucination rate, median latency, total tokens, estimated cost, avg answer length. Results saved to `data/model_comparison.json`.

### 🔴 Still High Priority

#### 2. Query Rewriting / Expansion
**Problem**: Short queries like "SPKEZ" produce weak embeddings. The embedding of a bare routine name doesn't capture intent well.
**Plan**: Before embedding, expand the query with detected context: `"SPKEZ"` → `"Explain the SPICE routine SPKEZ — purpose, parameters, and usage"`. Use the router's detected intent to template the expansion. No LLM call needed.
**Effort**: ~2 hours. **Impact**: Better retrieval for terse queries (the `edge-bare-routine-name` eval case).

#### 3. Re-ranking with Cross-Encoder
**Problem**: Pinecone's bi-encoder similarity misses nuance. A chunk about "loading kernels" scores high for "what kernels can be loaded" even though the chunk doesn't answer the question.
**Plan**: After Pinecone returns top-20, re-rank with a cross-encoder (`cross-encoder/ms-marco-MiniLM-L-6-v2` via sentence-transformers, runs locally in ~50ms). Take top-5 from re-ranked results.
**Effort**: ~3 hours. **Impact**: +15-20% answer faithfulness on conceptual queries.

### 🟢 Lower Priority

#### 9. Codebase Diff Visualization
**Problem**: Impact analysis returns a list of routine names but no way to see what the actual code looks like.
**Plan**: When the user queries impact for a routine, fetch the source chunks for the top-5 affected routines and display them in the TUI's source panel. Highlight the call sites where the changed routine is invoked.
**Effort**: ~4 hours. **Impact**: Impact analysis becomes actionable instead of just informational.

#### 10. Export to Static Site
**Problem**: The docgen feature generates Markdown one routine at a time via LLM. No way to produce a complete documentation site.
**Plan**: Batch-generate docs for all 1,811 routines (or top-200 by caller count) and output a static MkDocs site. Use the call graph to auto-generate a sidebar navigation. Cache LLM responses so re-generation is cheap.
**Effort**: ~6 hours. **Impact**: Searchable, deployable documentation site for the entire SPICE codebase.

---

## Architecture Decision Log

| Decision | Rationale | Trade-off |
|---|---|---|
| Pinecone over ChromaDB | Managed, serverless, free tier (5M vectors). No infra to maintain. | Vendor lock-in, ~100-300ms per query vs <10ms local |
| `text-embedding-3-small` over Voyage Code 2 | OpenAI ecosystem, 1536 dims is enough for code chunks | Voyage may have better code understanding |
| Regex router over LLM classifier | <0.05ms, deterministic, testable, $0 | Can't handle adversarial/ambiguous queries |
| OpenRouter for completions | Single API, swap models via config | Extra hop adds ~50ms latency |
| Fortran parser over tree-sitter | Full control over SPICE-specific constructs (ENTRY, C$ headers) | More code to maintain, no AST |
| Separate embedding + LLM clients | Embeddings must match Pinecone index (OpenAI). Completions are swappable. | Two API keys to manage |
| `def` over `async def` for blocking endpoints | FastAPI auto-threadpools sync functions. Prevents event loop blocking. | Slightly more threads under load |

---

## Cost Analysis

### Development Spend
| Category | Cost |
|---|---|
| Embedding ingestion (5,386 vectors) | ~$0.16 |
| Eval runs (retrieval + LLM, ~20 runs) | ~$3.00 |
| Model benchmarking (3 models × 5 queries × 3 runs) | ~$0.45 |
| Ad-hoc testing during development | ~$2.00 |
| **Total development** | **~$5.61** |

### Production Projections (per month)
| Users | Queries/day | Embedding | LLM | Pinecone | Total |
|---|---|---|---|---|---|
| 100 | 300 | $0.45 | $2.70 | $0 (free tier) | **$3.15** |
| 1,000 | 3,000 | $4.50 | $27.00 | $0 (free tier) | **$31.50** |
| 10,000 | 30,000 | $45.00 | $270.00 | $70 (starter) | **$385.00** |
| 100,000 | 300,000 | $450.00 | $2,700.00 | $230 (standard) | **$3,380.00** |

*Assumptions: 1 embedding call + 1 LLM call per query. Gemini 2.0 Flash pricing. 50% cache hit rate at higher tiers would halve LLM costs.*

---

## Performance Baselines (as of this commit)

### CPU-bound (no API calls)
| Operation | p50 | Threshold |
|---|---|---|
| Router classification | 0.03ms | <0.1ms |
| `callers_of("CHKIN", depth=2)` | 0.4ms | <1.0ms |
| `callees_of("SPKEZ", depth=5)` | 0.15ms | <0.5ms |
| Context assembly (10 chunks) | 12ms | <20ms |
| Autocomplete prefix search | 0.5ms | <2.0ms |

### E2E (with API calls, Gemini 2.0 Flash)
| Query type | Median E2E | Median TTFT |
|---|---|---|
| Explain | 1.9s | 0.7s |
| Dependency | 0.9s | 0.8s |
| Pattern | 1.9s | 0.7s |
| Impact | 2.2s | 0.7s |
| Entry point | 1.4s | 0.7s |
| **Overall** | **1.9s** | **0.7s** |

---

## Files Changed in This Audit

```
app/main.py                    — async→sync endpoints, context budget alignment
app/retrieval/search.py        — parallel Pinecone queries for routine names
app/retrieval/generator.py     — tighter prompt + halved token budgets
app/features/routine_lookup.py — parallel Pinecone queries for alias resolution
.env                           — LLM_MODEL → google/gemini-2.0-flash-001
tests/test_latency.py          — E2E latency + model comparison benchmarks
tests/eval_cases.json          — 25 cases (21 original + 4 adversarial)
tests/eval_schema.py           — runtime schema validator
tests/eval_assert.py           — shared assertion library
tests/eval_harness.py          — rewritten with pass-rate thresholds + recording
tests/eval_coverage.py         — coverage matrix reporter
tests/test_eval_schema.py      — Tier 1 schema validation tests
tests/test_eval_retrieval.py   — Tier 2 retrieval-only tests
tests/test_eval_replay.py      — Tier 1.5 recorded session replay
.github/workflows/evals.yml    — Three-tier CI workflow
POSTMORTEM.md                  — This file
```
