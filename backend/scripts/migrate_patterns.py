"""Migrate patterns metadata from comma-separated strings to lists.

This avoids re-embedding (saves $0.16 and ~10 min). Only updates metadata.
Pinecone's update() changes metadata in-place without touching vectors.
"""

import os
import sys
from pathlib import Path

# Load .env
for line in Path(os.path.join(os.path.dirname(__file__), '..', '.env')).read_text().splitlines():
    if '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        os.environ.setdefault(k.strip(), v.strip())

from pinecone import Pinecone

from app.config import settings
from app.ingestion.scanner import scan_directory
from app.ingestion.fortran_parser import parse_file
from app.ingestion.chunker import chunk_codebase

import json


def main():
    print("Building chunks locally to get correct metadata...")

    source_dir = sys.argv[1] if len(sys.argv) > 1 else "../data/spice"

    # Load call graph
    cg_path = Path("data/call_graph.json")
    cg = json.loads(cg_path.read_text()) if cg_path.exists() else None

    # Parse and chunk
    f_files = scan_directory(source_dir, [".f"])
    inc_files = scan_directory(source_dir, [".inc"])
    routines = []
    for f in f_files:
        routines.extend(parse_file(f))

    chunks = chunk_codebase(routines, inc_files, cg)

    # Build id → patterns mapping
    id_to_patterns = {}
    for c in chunks:
        patterns = c.metadata.get("patterns", [])
        if isinstance(patterns, list):
            id_to_patterns[c.id] = patterns
        elif patterns:
            id_to_patterns[c.id] = [p.strip() for p in patterns.split(",") if p.strip()]
        else:
            id_to_patterns[c.id] = []

    print(f"Chunks with patterns to migrate: {sum(1 for v in id_to_patterns.values() if v)}")
    print(f"Multi-pattern chunks (were broken): {sum(1 for v in id_to_patterns.values() if len(v) > 1)}")

    # Connect to Pinecone
    pc = Pinecone(api_key=settings.pinecone_api_key)
    index = pc.Index(settings.pinecone_index)

    # Batch update metadata
    batch_size = 100
    ids = list(id_to_patterns.keys())
    updated = 0
    errors = 0

    for i in range(0, len(ids), batch_size):
        batch_ids = ids[i:i + batch_size]
        updates = []
        for vid in batch_ids:
            updates.append({
                "id": vid,
                "set_metadata": {"patterns": id_to_patterns[vid]},
            })

        # Pinecone doesn't have batch update — do individual updates
        for upd in updates:
            try:
                index.update(
                    id=upd["id"],
                    set_metadata=upd["set_metadata"],
                )
                updated += 1
            except Exception as e:
                errors += 1
                if errors <= 5:
                    print(f"  Error updating {upd['id']}: {e}")

        if (i + batch_size) % 500 == 0 or i + batch_size >= len(ids):
            print(f"  Updated {min(i + batch_size, len(ids))}/{len(ids)} vectors ({errors} errors)")

    print(f"\nMigration complete: {updated} updated, {errors} errors")

    # Verify
    from openai import OpenAI
    client = OpenAI(api_key=settings.openai_api_key)
    r = client.embeddings.create(input="SPKEZ", model=settings.embedding_model, dimensions=settings.embedding_dimensions)
    vec = r.data[0].embedding

    res = index.query(vector=vec, top_k=1, filter={"routine_name": {"$eq": "SPKEZ"}}, include_metadata=True)
    if res.matches:
        meta = res.matches[0].metadata
        print(f"\nVerification — SPKEZ patterns: {repr(meta.get('patterns'))}")
        assert isinstance(meta.get("patterns"), list), "Migration failed — patterns still a string!"
        print("✅ Patterns are now stored as lists!")

    # Test $in filter
    res2 = index.query(vector=vec, top_k=3, filter={"patterns": {"$in": ["spk_operations"]}}, include_metadata=True)
    print(f"\n$in filter test for 'spk_operations': {len(res2.matches)} results")
    for m in res2.matches:
        print(f"  {m.metadata.get('routine_name')}: {m.metadata.get('patterns')}")


if __name__ == "__main__":
    main()
