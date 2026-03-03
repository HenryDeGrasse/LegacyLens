"""Embedding generation for chunks using OpenAI text-embedding-3-small.

The checkpoint stores (chunk_id → embedding_vector) so we can:
  1. Skip re-embedding for unchanged chunks
  2. Return ALL embeddings (cached + new) for upsert
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from openai import OpenAI

from app.config import settings
from app.ingestion.chunker import Chunk


CHECKPOINT_FILE = Path("data/embed_checkpoint.json")


def _load_checkpoint() -> dict[str, list[float]]:
    """Load mapping of chunk_id → embedding from checkpoint."""
    if CHECKPOINT_FILE.exists():
        data = json.loads(CHECKPOINT_FILE.read_text())
        # Support both old format (just IDs) and new format (ID→vector)
        if "embeddings" in data:
            return data["embeddings"]
        # Old format: just a set of IDs, no vectors stored
        return {cid: [] for cid in data.get("completed_ids", [])}
    return {}


def _save_checkpoint(embeddings: dict[str, list[float]]):
    """Save checkpoint with embeddings."""
    CHECKPOINT_FILE.parent.mkdir(parents=True, exist_ok=True)
    CHECKPOINT_FILE.write_text(json.dumps({
        "embeddings": embeddings,
        "count": len(embeddings),
    }))


def embed_chunks(
    chunks: list[Chunk],
    batch_size: int = 100,
    resume: bool = True,
) -> list[tuple[Chunk, list[float]]]:
    """Embed all chunks, returning ALL (chunk, embedding) pairs.

    Uses checkpoint to skip re-embedding, but always returns the full
    set so the caller can upsert everything.
    """
    client = OpenAI(api_key=settings.openai_api_key)

    # Load checkpoint (id → vector)
    cached = _load_checkpoint() if resume else {}

    # Separate chunks into cached (with vectors) vs pending
    results: list[tuple[Chunk, list[float]]] = []
    pending: list[Chunk] = []

    for c in chunks:
        vec = cached.get(c.id, [])
        if vec:  # Have a cached vector
            results.append((c, vec))
        else:
            pending.append(c)

    print(f"Total chunks: {len(chunks)}")
    print(f"Already embedded: {len(results)}")
    print(f"Pending: {len(pending)}")

    total_tokens = 0

    for i in range(0, len(pending), batch_size):
        batch = pending[i : i + batch_size]
        texts = [c.text for c in batch]

        # Retry with exponential backoff
        for attempt in range(4):
            try:
                response = client.embeddings.create(
                    input=texts,
                    model=settings.embedding_model,
                    dimensions=settings.embedding_dimensions,
                )
                break
            except Exception as e:
                if attempt == 3:
                    raise
                wait = 2 ** (attempt + 1)
                print(f"  Retry {attempt + 1}/3 after {wait}s: {e}")
                time.sleep(wait)

        for j, embedding_data in enumerate(response.data):
            vec = embedding_data.embedding
            results.append((batch[j], vec))
            cached[batch[j].id] = vec

        total_tokens += response.usage.total_tokens
        done = min(i + batch_size, len(pending))
        cost = total_tokens * 0.02 / 1_000_000
        print(f"  Embedded {done}/{len(pending)} chunks | Tokens: {total_tokens:,} | Cost: ${cost:.4f}")

        # Save checkpoint every 5 batches
        if (i // batch_size) % 5 == 0:
            _save_checkpoint(cached)

    # Final checkpoint
    if pending:
        _save_checkpoint(cached)

    cost = total_tokens * 0.02 / 1_000_000
    print(f"\nEmbedding complete:")
    print(f"  Chunks embedded: {len(pending)}")
    print(f"  Total tokens: {total_tokens:,}")
    print(f"  Estimated cost: ${cost:.4f}")

    return results
