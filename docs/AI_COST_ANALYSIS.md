# LegacyLens — AI Cost Analysis

## Development Spend (Actuals)

| Category | Cost |
|---|---|
| Embedding ingestion (5,386 vectors × `text-embedding-3-small`) | ~$0.16 |
| Eval runs (retrieval + LLM, ~20 runs) | ~$3.00 |
| Model benchmarking (3 models × 5 queries × 3 runs) | ~$0.45 |
| Ad-hoc testing during development | ~$2.00 |
| **Total development** | **~$5.61** |

## Per-Query Cost

| Step | Tokens | Cost |
|---|---|---|
| Embed query (`text-embedding-3-small`) | ~20 tokens | $0.0000004 |
| LLM generation (Gemini 2.0 Flash via OpenRouter) | ~2,500 in + ~250 out | ~$0.0009 |
| Pinecone search | — | $0.00 (free tier) |
| **Total per query (cold)** | | **~$0.001** |

With caching (embedding LRU + answer TTL), repeated queries cost **$0.00**.

## Production Projections

| Users/month | Queries/day | Embedding | LLM | Pinecone | Total/month |
|---|---|---|---|---|---|
| 100 | 300 | $0.45 | $2.70 | $0 (free tier) | **$3.15** |
| 1,000 | 3,000 | $4.50 | $27.00 | $0 (free tier) | **$31.50** |
| 10,000 | 30,000 | $45.00 | $270.00 | $70 (starter) | **$385.00** |
| 100,000 | 300,000 | $450.00 | $2,700.00 | $230 (standard) | **$3,380.00** |

*Assumptions: Gemini 2.0 Flash pricing. 50% cache hit rate at higher tiers would halve LLM costs.*

## Cost Optimization Levers

| Lever | Savings | Trade-off |
|---|---|---|
| Answer caching (implemented, 1hr TTL) | ~30% LLM cost | Stale answers |
| Embedding cache (implemented, 512-entry LRU) | ~20% embedding cost | Memory |
| Swap to cheaper model via OpenRouter | Variable | Quality change |
| Reduce context budget (already intent-aware) | ~33% input tokens | Already optimized |
| Local embedding model | 100% embedding cost | Lower retrieval quality |
