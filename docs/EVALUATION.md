# LegacyLens Evaluation Report

## Test Set

21 golden queries across 7 categories: dependency (3), impact (2), explain (5), pattern (4), semantic (4), entry_point (2), edge_case (1).

Each query specifies expected router intent, expected routines in top-5, preferred chunk types, and required answer terms.

## Results Summary

| Metric | Score | Target |
|---|---|---|
| **Router accuracy** | 95% (20/21) | — |
| **Routine recall** | 100% | >90% |
| **Doc-type hit rate** | 86% | >70% |
| **Precision@5** | 53% | >50% |
| **Answer faithfulness** | 100% (21/21) | >80% |
| **Avg retrieval latency** | 440ms | — |
| **Avg total latency** | 11.6s | <15s |
| **Cached query latency** | ~100ms | — |

## Per-Category Breakdown

| Category | Intent Acc | Recall | Faithfulness | n |
|---|---|---|---|---|
| dependency | 100% | 100% | 100% | 3 |
| impact | 100% | 100% | 100% | 2 |
| explain | 100% | 100% | 100% | 5 |
| pattern | 100% | 100% | 100% | 4 |
| semantic | 75% | 100% | 100% | 4 |
| entry_point | 100% | 100% | 100% | 2 |
| edge_case | 100% | 100% | 100% | 1 |

## Known Limitations

### Router
- 1 misclassification: "What is the maximum number of kernels?" → PATTERN (expected SEMANTIC). The query contains "kernel" which triggers kernel_loading pattern detection. Both classifications produce good results.

### Retrieval
- **Precision@5 at 53%** — many queries return a mix of relevant and tangentially-relevant chunks. Segment chunks from the same routine inflate the count. A reranker or deduplication by routine would improve this.
- **Doc-type hit rate 86%** — 3 pattern queries retrieve segment/body chunks instead of doc chunks. This happens when the pattern filter finds code-heavy chunks that match the pattern but aren't documentation.

### Latency
- **Cold queries: 8-25 seconds** — dominated by OpenAI API calls (embedding + completion). Pinecone retrieval itself is ~400ms.
- **Cached queries: ~100ms** — embedding cache + answer cache eliminate all API calls.
- **Railway free tier** adds 1-3 seconds of cold start + CDN overhead.

### Answer Generation
- The LLM occasionally uses different terminology than expected (e.g., "transformation matrix" instead of "rotation matrix" for PXFORM). This is valid but can miss keyword-based faithfulness checks.
- For dependency questions, the LLM sometimes lists a subset of calls rather than all of them.

## Failure Modes

| Failure Mode | Impact | Mitigation |
|---|---|---|
| Common English words match routine name regex | Low (fixed with comprehensive stop words) | Expanded stop word list to 150+ entries |
| Pattern filter misses chunks with 0 patterns | Low | 23% of chunks have no patterns; they're still found by semantic search |
| Large routines have many segments that crowd out other routines | Medium | Segment demotion (-0.03) for doc-preferring queries |
| ENTRY point queries may surface parent routine content | Low | Alias resolution already adds parent to search; parent doc is usually relevant |

## Cost Analysis

| Resource | Per-Query (cold) | Per-Query (cached) | Eval Run (21 queries) |
|---|---|---|---|
| Embedding tokens | ~10 | 0 | ~210 |
| Completion tokens | ~5,000-6,000 | 0 | ~124,000 |
| Pinecone queries | 2-5 | 2-5 | ~70 |
| Estimated cost | ~$0.001 | ~$0 | $0.019 |

## Reproducibility

```bash
cd backend
source .venv/bin/activate
python -m tests.eval_harness          # Full eval with LLM answers
python -m tests.eval_harness --no-generate  # Retrieval-only (no API cost)
```

Results saved to `data/eval_results.json`.
