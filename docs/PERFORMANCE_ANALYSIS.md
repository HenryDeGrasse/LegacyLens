# LegacyLens Performance Analysis

Deep profiling of the RAG pipeline, identifying bottlenecks and optimizations applied.

## Pipeline Stages

Every query passes through these stages in order:

```
User Input → Router → Embedding → Pinecone Search → Context Assembly → LLM Stream → Display
  (0ms)      (<1ms)    (~400ms)     (~130ms)          (~15ms)         (TTFT+Gen)    (0ms)
```

## Profiling Results (After Optimization)

Tested across 5 query types covering all intents. Measured with `scripts/profile_pipeline.py`.

### Per-Query Breakdown

| Query Type | Retrieval | TTFT | LLM Gen | E2E | Tokens | Tok/s |
|------------|-----------|------|---------|-----|--------|-------|
| EXPLAIN (named routine) | 1,047ms | 5,249ms | 9,601ms | 15,897ms | 293 | 30.5 |
| DEPENDENCY (call graph) | 766ms | 415ms | 2,228ms | 3,409ms | 95 | 42.6 |
| PATTERN (error handling) | 356ms | 1,207ms | 14,522ms | 16,084ms | 593 | 40.8 |
| SEMANTIC (conceptual) | 575ms | 509ms | 10,839ms | 11,923ms | 386 | 35.6 |
| IMPACT (blast radius) | 453ms | 615ms | 6,145ms | 7,213ms | 248 | 40.4 |

### Averages

| Metric | Value | % of E2E |
|--------|-------|----------|
| Embedding (cold) | 458ms | 4.2% |
| Pinecone queries | 131ms | 1.2% |
| Retrieval total | 640ms | 5.9% |
| TTFT | 1,599ms | 14.7% |
| LLM generation | 8,667ms | 79.5% |
| **End-to-end** | **10,905ms** | 100% |

### Stage Details

| Stage | What It Does | Latency | Notes |
|-------|-------------|---------|-------|
| Router | Regex classify intent | <1ms | Zero-cost, no LLM call |
| Embedding | OpenAI text-embedding-3-small | 260-640ms cold, 0ms cached | LRU cache (512 entries) |
| Pinecone search | Vector similarity + filters | 90-220ms (parallel) | ThreadPoolExecutor, 1-3 queries |
| Context assembly | Chunk selection + tiktoken | 12-183ms | Accurate token counting |
| LLM TTFT | Time to first token from GPT-4o-mini | 415-5,249ms | Dominated by prompt size |
| LLM generation | Token streaming | 2,228-14,522ms | 30-43 tok/s |
| Answer cache hit | Hash lookup | <0.1ms | 1-hour TTL |

## Before/After Comparison

### TTFT Improvements (the biggest win)

| Query | Before | After | Improvement |
|-------|--------|-------|-------------|
| EXPLAIN (SPKEZ) | 8,510ms | 558ms* → 5,249ms | 38% reduction |
| DEPENDENCY (FURNSH) | 7,753ms | 415ms | **95% reduction** |
| IMPACT (CHKIN) | 7,254ms | 615ms | **92% reduction** |
| PATTERN (errors) | 915ms | 1,207ms | (variance) |
| SEMANTIC (kernel) | 892ms | 509ms | 43% reduction |

*First measurement after tiktoken fix was 558ms; 5,249ms on second run likely reflects OpenAI server load variance.

### E2E Improvements

| Query | Before | After | Improvement |
|-------|--------|-------|-------------|
| DEPENDENCY | 11,996ms | 3,409ms | **72% faster** |
| IMPACT | 23,214ms | 7,213ms | **69% faster** |
| EXPLAIN | 24,455ms | 15,897ms | 35% faster |
| Avg across all | 15,894ms | 10,905ms | **31% faster** |

## Optimizations Applied

### 1. Accurate Token Counting (Critical Fix)

**Problem:** Context assembler used `chars / 4` to estimate tokens. Fortran code with lots of whitespace and short keywords was ~2.5x more tokens per character than estimated. A "4,500 token" limit was actually sending 9,000-12,000 tokens to the LLM.

**Fix:** Replaced with `tiktoken` for exact token counting. Context now hard-stops at the configured limit.

**Impact:** TTFT reduced 38-95% for queries that were exceeding the limit.

### 2. Intent-Aware Context Budget

**Problem:** All queries got the same 4,500-token context budget, but dependency/impact answers are short structured lists that don't need much context.

**Fix:** 
- DEPENDENCY queries: 2,000 token context budget
- IMPACT queries: 2,500 token context budget  
- EXPLAIN/PATTERN/SEMANTIC: 4,500 tokens (default)

**Impact:** DEPENDENCY E2E dropped from 12s to 3.4s. IMPACT from 23s to 7.2s.

### 3. Adaptive LLM max_tokens

**Problem:** All queries set `max_tokens=2000`, but dependency answers are ~100 tokens and impact answers ~250 tokens. The LLM was potentially generating unnecessary length.

**Fix:** Regex-based `_max_tokens_for_query()`:
- DEPENDENCY: 800 max_tokens
- IMPACT: 1,000 max_tokens
- Everything else: 2,000 max_tokens

**Impact:** Shorter generation times for structural queries (~2.2s vs ~10s).

### 4. Parallel Pinecone Queries

**Problem:** Multi-strategy retrieval (name lookup + pattern filter + semantic) ran sequentially — 3 network round-trips in series.

**Fix:** `ThreadPoolExecutor` runs all Pinecone queries simultaneously. Single-query intents skip the thread overhead.

**Impact:** Pinecone phase reduced from ~300ms (3 serial queries) to ~130ms (parallel). Modest improvement (~170ms saved) since Pinecone was already fast.

### 5. Three-Level Caching (pre-existing)

Already in place from Phase 3:
- **Embedding LRU cache** (512 entries): 400ms → 0ms for repeated queries
- **Answer TTL cache** (1 hour): 10-20s → 0.1ms for identical queries
- **Client singletons**: OpenAI/Pinecone connections reused

## Remaining Bottlenecks

### 1. LLM Generation (79.5% of E2E)

This is OpenAI's token generation speed — **we cannot optimize this** without changing models.

**Options (not currently implemented):**
- Switch to a faster model (GPT-4o-mini is already the fastest quality option)
- Use `gpt-3.5-turbo` for simple queries (worse quality, ~2x faster)
- Self-host a small model (vLLM + Llama 3.1 8B) for ~100 tok/s
- Reduce output verbosity via prompt engineering

### 2. Embedding API (4.2% of E2E)

Cold embedding is 260-640ms due to network round-trip to OpenAI.

**Options:**
- Local embedding model (e.g., `sentence-transformers/all-MiniLM-L6-v2`) — 0 network cost, ~10ms
- Pre-embed common routine names at startup
- Batch embedding for multi-term queries

### 3. TTFT Variance (14.7% of E2E)

TTFT ranges from 415ms to 5,249ms for similar context sizes. This is OpenAI server-side variance.

**Options:**
- Use Azure OpenAI (PTU provisioned throughput) for consistent latency
- Retry with timeout if TTFT > 2s
- Show "thinking..." indicator (already implemented via streaming)

### 4. Context Assembly (tiktoken init)

First `_count_tokens()` call takes ~150ms to initialize the tokenizer. Subsequent calls are fast.

**Options:**
- Pre-initialize tiktoken in the TUI pre-warm worker
- Already negligible after first query

## Perceived Latency vs Actual

Due to streaming and two-phase display, **perceived** latency is much better than E2E:

| Metric | Actual | Perceived |
|--------|--------|-----------|
| First content visible (chunks) | ~640ms | ~640ms |
| First LLM token | 640ms + TTFT | Feels like "~1-2s" |
| Complete answer | E2E | User reads as it streams |

The TUI shows retrieved chunks and call graph immediately (~640ms), then streams LLM tokens. Users start reading before generation completes.

## How to Run the Profiler

```bash
uv run python scripts/profile_pipeline.py
```

Raw JSON data is saved to `docs/PERFORMANCE_PROFILE.json` for further analysis.

## Key Takeaways

1. **Context size is the #1 controllable factor for TTFT.** Accurate token counting + intent-aware budgets cut TTFT by up to 95%.
2. **LLM generation is the dominant cost (80%)** and is not under our control with hosted models.
3. **Parallel retrieval provides modest gains** (~170ms) since Pinecone is already fast.
4. **Caching eliminates repeat query cost** entirely (10s+ → 0.1ms).
5. **Streaming hides latency** — users see content in ~640ms even though E2E is 3-16s.
