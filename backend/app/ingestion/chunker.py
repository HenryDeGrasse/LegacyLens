"""Chunking pipeline for SPICE Toolkit routines.

Converts parsed RoutineInfo objects into embeddable Chunk objects with metadata.
Chunk types:
  - routine_doc:     Header comments + signature (Abstract, Brief_I/O, etc.)
  - routine_body:    Executable code of the routine
  - routine_segment: Oversized bodies split into overlapping segments
  - include:         .inc / common-block header files
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from app.ingestion.fortran_parser import RoutineInfo


@dataclass
class Chunk:
    """An embeddable chunk of code with metadata."""

    id: str
    text: str
    metadata: dict


# Token estimation: ~4 chars per token (conservative for code)
CHARS_PER_TOKEN = 4
MAX_CHUNK_TOKENS = 1500
OVERLAP_TOKENS = 200
MAX_CHUNK_CHARS = MAX_CHUNK_TOKENS * CHARS_PER_TOKEN  # 6000
OVERLAP_CHARS = OVERLAP_TOKENS * CHARS_PER_TOKEN  # 800


def _make_id(file_path: str, routine_name: str, chunk_type: str, index: int = 0) -> str:
    """Create a deterministic chunk ID."""
    raw = f"{file_path}::{routine_name}::{chunk_type}::{index}"
    return hashlib.md5(raw.encode()).hexdigest()[:16]


def _estimate_tokens(text: str) -> int:
    """Estimate token count from text length."""
    return len(text) // CHARS_PER_TOKEN


def _base_metadata(routine: RoutineInfo, chunk_type: str) -> dict:
    """Build base metadata dict for a routine chunk."""
    return {
        "file_path": routine.file_path,
        "start_line": routine.start_line,
        "end_line": routine.end_line,
        "routine_name": routine.name,
        "routine_kind": routine.kind,
        "chunk_type": chunk_type,
        "abstract": routine.abstract[:500],  # Pinecone metadata limit
        "keywords": ", ".join(routine.keywords)[:200],
        "calls": ", ".join(routine.calls)[:500],
        "includes": ", ".join(routine.includes)[:200],
        "parent_routine": routine.parent_routine or "",
    }


def chunk_routine(routine: RoutineInfo) -> list[Chunk]:
    """Convert a RoutineInfo into one or more Chunks.

    Produces:
    - One routine_doc chunk (header comments)
    - One or more routine_body / routine_segment chunks (executable code)
    """
    chunks: list[Chunk] = []

    # routine_doc: header comments (always create if header exists)
    if routine.header_comments.strip():
        doc_text = routine.header_comments.strip()
        # Truncate if extremely long, keeping the most useful parts
        if _estimate_tokens(doc_text) > MAX_CHUNK_TOKENS * 2:
            # Keep first portion (Abstract, Brief_I/O) and truncate
            doc_text = doc_text[: MAX_CHUNK_CHARS * 2]

        meta = _base_metadata(routine, "routine_doc")
        chunks.append(Chunk(
            id=_make_id(routine.file_path, routine.name, "routine_doc"),
            text=doc_text,
            metadata=meta,
        ))

    # routine_body / routine_segment: executable code
    body = routine.body_code.strip()
    if not body:
        return chunks

    if _estimate_tokens(body) <= MAX_CHUNK_TOKENS:
        # Single body chunk
        meta = _base_metadata(routine, "routine_body")
        chunks.append(Chunk(
            id=_make_id(routine.file_path, routine.name, "routine_body"),
            text=body,
            metadata=meta,
        ))
    else:
        # Split into overlapping segments
        segments = _split_with_overlap(body, MAX_CHUNK_CHARS, OVERLAP_CHARS)
        for i, segment in enumerate(segments):
            meta = _base_metadata(routine, "routine_segment")
            meta["segment_index"] = i
            meta["segment_total"] = len(segments)
            chunks.append(Chunk(
                id=_make_id(routine.file_path, routine.name, "routine_segment", i),
                text=segment,
                metadata=meta,
            ))

    return chunks


def _split_with_overlap(text: str, max_chars: int, overlap_chars: int) -> list[str]:
    """Split text into overlapping segments, preferring line boundaries."""
    lines = text.split("\n")
    segments: list[str] = []
    current_lines: list[str] = []
    current_len = 0

    for line in lines:
        line_len = len(line) + 1  # +1 for newline
        if current_len + line_len > max_chars and current_lines:
            segments.append("\n".join(current_lines))

            # Calculate overlap: keep last N chars worth of lines
            overlap_lines: list[str] = []
            overlap_len = 0
            for prev_line in reversed(current_lines):
                if overlap_len + len(prev_line) + 1 > overlap_chars:
                    break
                overlap_lines.insert(0, prev_line)
                overlap_len += len(prev_line) + 1

            current_lines = overlap_lines
            current_len = overlap_len

        current_lines.append(line)
        current_len += line_len

    if current_lines:
        segments.append("\n".join(current_lines))

    return segments


def chunk_include(path: Path) -> list[Chunk]:
    """Create a chunk for an include file."""
    try:
        text = path.read_text(encoding="latin-1").strip()
    except Exception as e:
        print(f"Warning: Could not read include file {path}: {e}")
        return []

    if not text:
        return []

    file_str = str(path)
    meta = {
        "file_path": file_str,
        "start_line": 1,
        "end_line": text.count("\n") + 1,
        "routine_name": path.stem,
        "routine_kind": "INCLUDE",
        "chunk_type": "include",
        "abstract": f"Include file: {path.name}",
        "keywords": "",
        "calls": "",
        "includes": "",
        "parent_routine": "",
    }

    return [Chunk(
        id=_make_id(file_str, path.stem, "include"),
        text=text,
        metadata=meta,
    )]


def chunk_codebase(
    routines: list[RoutineInfo], include_paths: list[Path]
) -> list[Chunk]:
    """Process all routines and include files into chunks."""
    chunks: list[Chunk] = []

    for routine in routines:
        chunks.extend(chunk_routine(routine))

    for inc_path in include_paths:
        chunks.extend(chunk_include(inc_path))

    return chunks


if __name__ == "__main__":
    import sys
    from app.ingestion.scanner import scan_directory
    from app.ingestion.fortran_parser import parse_file

    source_dir = sys.argv[1] if len(sys.argv) > 1 else "../data/spice"

    # Scan
    f_files = scan_directory(source_dir, [".f"])
    inc_files = scan_directory(source_dir, [".inc"])

    # Parse
    all_routines: list[RoutineInfo] = []
    for f in f_files:
        all_routines.extend(parse_file(f))

    # Chunk
    chunks = chunk_codebase(all_routines, inc_files)

    # Stats
    by_type: dict[str, int] = {}
    total_chars = 0
    for c in chunks:
        ct = c.metadata["chunk_type"]
        by_type[ct] = by_type.get(ct, 0) + 1
        total_chars += len(c.text)

    print(f"Source files:  {len(f_files)} .f + {len(inc_files)} .inc")
    print(f"Routines:      {len(all_routines)}")
    print(f"Total chunks:  {len(chunks)}")
    print(f"Total chars:   {total_chars:,}")
    print(f"Est. tokens:   {total_chars // CHARS_PER_TOKEN:,}")
    print(f"By type:")
    for ct, count in sorted(by_type.items()):
        print(f"  {ct}: {count}")
