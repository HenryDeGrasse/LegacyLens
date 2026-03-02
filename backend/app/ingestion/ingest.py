"""Full ingestion pipeline: scan → parse → chunk → embed → upsert."""

from __future__ import annotations

import sys
import time
from pathlib import Path

from app.ingestion.scanner import scan_directory, get_file_stats
from app.ingestion.fortran_parser import parse_file, RoutineInfo
from app.ingestion.chunker import chunk_codebase, Chunk
from app.ingestion.embedder import embed_chunks
from app.ingestion.loader import upsert_to_pinecone


def run_ingestion(source_dir: str, dry_run: bool = False):
    """Run the full ingestion pipeline.

    Args:
        source_dir: Path to SPICE toolkit source.
        dry_run: If True, stop before embedding/upserting.
    """
    start_time = time.time()

    # Step 1: Scan
    print("=" * 60)
    print("STEP 1: Scanning source files")
    print("=" * 60)
    f_files = scan_directory(source_dir, [".f"])
    inc_files = scan_directory(source_dir, [".inc"])
    stats = get_file_stats(f_files + inc_files)
    print(f"  .f files:  {len(f_files)}")
    print(f"  .inc files: {len(inc_files)}")
    print(f"  Total LOC:  {stats['total_loc']:,}")

    # Step 2: Parse
    print("\n" + "=" * 60)
    print("STEP 2: Parsing Fortran 77 source")
    print("=" * 60)
    all_routines: list[RoutineInfo] = []
    parse_errors = 0
    for f in f_files:
        try:
            routines = parse_file(f)
            all_routines.extend(routines)
        except Exception as e:
            parse_errors += 1
            print(f"  Error parsing {f}: {e}")

    main_routines = [r for r in all_routines if r.kind != "ENTRY"]
    entry_points = [r for r in all_routines if r.kind == "ENTRY"]
    print(f"  Routines:     {len(main_routines)}")
    print(f"  Entry points: {len(entry_points)}")
    print(f"  Parse errors: {parse_errors}")

    # Step 3: Chunk
    print("\n" + "=" * 60)
    print("STEP 3: Chunking codebase")
    print("=" * 60)
    chunks = chunk_codebase(all_routines, inc_files)
    by_type: dict[str, int] = {}
    for c in chunks:
        ct = c.metadata["chunk_type"]
        by_type[ct] = by_type.get(ct, 0) + 1
    print(f"  Total chunks: {len(chunks)}")
    for ct, count in sorted(by_type.items()):
        print(f"    {ct}: {count}")

    if dry_run:
        elapsed = time.time() - start_time
        print(f"\n[DRY RUN] Stopping before embedding. Elapsed: {elapsed:.1f}s")
        return chunks

    # Step 4: Embed
    print("\n" + "=" * 60)
    print("STEP 4: Generating embeddings")
    print("=" * 60)
    embedded = embed_chunks(chunks)

    # Step 5: Upsert
    print("\n" + "=" * 60)
    print("STEP 5: Upserting to Pinecone")
    print("=" * 60)
    upsert_to_pinecone(embedded)

    elapsed = time.time() - start_time
    print("\n" + "=" * 60)
    print(f"INGESTION COMPLETE in {elapsed:.1f}s")
    print("=" * 60)

    return chunks


if __name__ == "__main__":
    source = sys.argv[1] if len(sys.argv) > 1 else "../data/spice"
    dry = "--dry-run" in sys.argv
    run_ingestion(source, dry_run=dry)
