"""Pinecone vector database loading."""

from __future__ import annotations

from pinecone import Pinecone, ServerlessSpec

from app.config import settings
from app.ingestion.chunker import Chunk


def get_or_create_index():
    """Get or create the Pinecone index."""
    pc = Pinecone(api_key=settings.pinecone_api_key)

    # Check if index exists
    existing = [idx.name for idx in pc.list_indexes()]
    if settings.pinecone_index not in existing:
        print(f"Creating Pinecone index: {settings.pinecone_index}")
        pc.create_index(
            name=settings.pinecone_index,
            dimension=settings.embedding_dimensions,
            metric="cosine",
            spec=ServerlessSpec(cloud="aws", region="us-east-1"),
        )
        print("Index created. Waiting for it to be ready...")
        import time
        time.sleep(10)  # Wait for index to initialize
    else:
        print(f"Using existing Pinecone index: {settings.pinecone_index}")

    return pc.Index(settings.pinecone_index)


def upsert_to_pinecone(
    embedded_chunks: list[tuple[Chunk, list[float]]],
    batch_size: int = 100,
):
    """Upsert embedded chunks to Pinecone.

    Args:
        embedded_chunks: List of (chunk, embedding) tuples.
        batch_size: Vectors per upsert call.
    """
    index = get_or_create_index()

    total = len(embedded_chunks)
    print(f"Upserting {total} vectors to Pinecone...")

    for i in range(0, total, batch_size):
        batch = embedded_chunks[i : i + batch_size]
        vectors = []
        for chunk, embedding in batch:
            # Pinecone metadata values must be strings, numbers, booleans, or lists of strings
            meta = {}
            for k, v in chunk.metadata.items():
                if isinstance(v, (str, int, float, bool)):
                    meta[k] = v
                elif isinstance(v, list):
                    meta[k] = ", ".join(str(x) for x in v)
                else:
                    meta[k] = str(v)

            vectors.append({
                "id": chunk.id,
                "values": embedding,
                "metadata": meta,
            })

        index.upsert(vectors=vectors)

        done = min(i + batch_size, total)
        if done % 500 == 0 or done == total:
            print(f"  Upserted {done}/{total} vectors")

    # Verify
    stats = index.describe_index_stats()
    print(f"\nPinecone index stats:")
    print(f"  Total vectors: {stats.total_vector_count}")
    print(f"  Dimension: {stats.dimension}")
