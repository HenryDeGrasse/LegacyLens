# LegacyLens — AI Cost Analysis

## Development Spend (Actuals)

| Item | Tokens | Cost |
|---|---|---|
| **Embedding ingestion** (5,386 chunks) | ~8M input tokens | $0.16 |
| **Re-ingestion** (pattern metadata fix) | ~8M input tokens | $0.16 |
| **LLM queries during dev/testing** (~80 queries) | ~400K input + ~120K output | $0.24 |
| **Evaluation harness** (21 golden queries) | ~100K input + ~30K output | $0.06 |
| **Pinecone** | Free tier (5,386 of 100K vectors) | $0.00 |
| **Railway** | Hobby tier | $5.00/mo |
| | | |
| **Total dev cost** | | **~$5.62** |

### Token pricing used

| Model | Input | Output |
|---|---|---|
| `text-embedding-3-small` | $0.020 / 1M tokens | — |
| `gpt-4o-mini` | $0.150 / 1M tokens | $0.600 / 1M tokens |

---

## Per-Query Cost

A single query involves:

| Step | Tokens | Cost |
|---|---|---|
| Embed query | ~20 tokens | $0.0000004 |
| LLM generation (4,500 context + ~500 answer) | ~5,000 input + ~500 output | $0.001050 |
| Pinecone search | — | $0.00 (free tier) |
| **Total per query** | | **~$0.001** |

With caching (embedding LRU + answer TTL), repeated queries cost **$0.00**.

---

## Production Projections

### Assumptions
- Average 1.2 queries per user session
- 30% cache hit rate (repeat/similar queries)
- Pinecone: free tier up to 100K vectors, then Starter at $70/mo
- Railway: Hobby $5/mo, Pro $20/mo with auto-scaling

### Scaling table

| Users/month | Queries/month | LLM cost | Pinecone | Railway | **Total/month** |
|---|---|---|---|---|---|
| **100** | 120 | $0.13 | $0 (free) | $5 | **$5.13** |
| **1,000** | 1,200 | $1.26 | $0 (free) | $5 | **$6.26** |
| **10,000** | 12,000 | $12.60 | $0 (free) | $20 | **$32.60** |
| **100,000** | 120,000 | $126.00 | $70 (Starter) | $20 | **$216.00** |

### Cost per user

| Scale | Cost/user/month |
|---|---|
| 100 users | $0.051 |
| 1,000 users | $0.006 |
| 10,000 users | $0.003 |
| 100,000 users | $0.002 |

---

## Cost Optimization Levers

| Lever | Savings | Tradeoff |
|---|---|---|
| **Answer caching** (already implemented) | ~30% LLM cost | Stale answers for 1 hour |
| **Embedding cache** (already implemented) | ~20% embedding cost | Memory usage (512 entry LRU) |
| **Switch to GPT-4o-mini → GPT-4.1-nano** | ~50% if pricing drops | May change answer quality |
| **Batch embedding on ingest** | One-time, already batched | None |
| **Reduce context window** (4,500 → 3,000) | ~33% input tokens | Lower answer quality on complex queries |
| **Local embedding model** (e.g., `all-MiniLM-L6-v2`) | 100% embedding cost | Lower retrieval quality, needs GPU |
| **Self-host LLM** (e.g., Llama 3) | 100% LLM cost | Infra cost, lower quality |

---

## Break-even Analysis

At $0.001/query and the current free tier:
- **Free tier capacity:** ~100K queries/month before needing Pinecone Starter
- **Revenue needed to cover 100K users:** $216/month
- **Price per user to break even:** $0.003/month (effectively free with ads or freemium)

---

## Key Insight

The dominant cost is **LLM generation**, not embeddings or vector search. At scale, the most impactful optimization is aggressive answer caching — a 50% cache hit rate would halve the LLM spend. The current 1-hour TTL cache already handles this for repeated queries. For a production system, a semantic cache (similar queries → cached answer) could push hit rates to 60-70%.
