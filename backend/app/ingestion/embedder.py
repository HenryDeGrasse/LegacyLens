"""Embedding generation for chunks using OpenAI text-embedding-3-small."""

from __future__ import annotations

import json
import time
from pathlib import Path

from openai import OpenAI

from app.config import settings
from app.ingestion.chunker import Chunk


CHECKPOINT_FILE = Path("data/embed_checkpoint.json")


def _load_checkpoint() -> set[str]:
    """Load set of already-embedded chunk IDs."""
    if CHECKPOINT_FILE.exists():
        data = json.loads(CHECKPOINT_FILE.read_text())
        return set(data.get("completed_ids", []))
    return set()


def _save_checkpoint(completed_ids: set[str]):
    """Save checkpoint of completed chunk IDs."""
    CHECKPOINT_FILE.parent.mkdir(parents=True, exist_ok=True)
    CHECKPOINT_FILE.write_text(json.dumps({
        "completed_ids": list(completed_ids),
        "count": len(completed_ids),
    }))


def embed_chunks(
    chunks: list[Chunk],
    batch_size: int = 100,
    resume: bool = True,
) -> list[tuple[Chunk, list[float]]]:
    """Embed all chunks using OpenAI text-embedding-3-small.

    Args:
        chunks: List of Chunk objects to embed.
        batch_size: Number of chunks per API call.
        resume: Whether to resume from checkpoint.

    Returns:
        List of (chunk, embedding) tuples.
    """
    client = OpenAI(api_key=settings.openai_api_key)

    # Load checkpoint
    completed_ids = _load_checkpoint() if resume else set()
    pending = [c for c in chunks if c.id not in completed_ids]

    print(f"Total chunks: {len(chunks)}")
    print(f"Already embedded: {len(completed_ids)}")
    print(f"Pending: {len(pending)}")

    results: list[tuple[Chunk, list[float]]] = []
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

        # Collect results
        for j, embedding_data in enumerate(response.data):
            results.append((batch[j], embedding_data.embedding))
            completed_ids.add(batch[j].id)

        total_tokens += response.usage.total_tokens

        # Progress
        done = min(i + batch_size, len(pending))
        cost = total_tokens * 0.02 / 1_000_000
        print(f"  Embedded {done}/{len(pending)} chunks | Tokens: {total_tokens:,} | Cost: ${cost:.4f}")

        # Save checkpoint every 5 batches
        if (i // batch_size) % 5 == 0:
            _save_checkpoint(completed_ids)

    # Final checkpoint
    _save_checkpoint(completed_ids)

    cost = total_tokens * 0.02 / 1_000_000
    print(f"\nEmbedding complete:")
    print(f"  Chunks embedded: {len(results)}")
    print(f"  Total tokens: {total_tokens:,}")
    print(f"  Estimated cost: ${cost:.4f}")

    return results
