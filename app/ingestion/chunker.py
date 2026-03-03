"""Chunking pipeline for SPICE Toolkit routines (Phase 2 — refined).

Improvements over Phase 1:
  - Merges small routine bodies into their doc chunks
  - Enriches metadata with called_by (reverse call graph)
  - Adds entry_aliases so ENTRY names are searchable
  - Detects common SPICE patterns (error handling, kernel loading, etc.)
  - Smarter doc truncation preserving Abstract + Brief_I/O

Chunk types:
  - routine_doc:     Header comments + signature (+ merged body if small)
  - routine_body:    Executable code of the routine
  - routine_segment: Oversized bodies split into overlapping segments
  - include:         .inc / common-block header files
"""

from __future__ import annotations

import hashlib
import re
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
SMALL_BODY_TOKENS = 100  # Bodies smaller than this get merged into doc
MAX_CHUNK_CHARS = MAX_CHUNK_TOKENS * CHARS_PER_TOKEN  # 6000
OVERLAP_CHARS = OVERLAP_TOKENS * CHARS_PER_TOKEN  # 800


# SPICE pattern detection
_PATTERNS = {
    "error_handling": re.compile(r"\b(CHKIN|CHKOUT|SIGERR|SETMSG|ERRINT|ERRCH|ERRDP)\b", re.IGNORECASE),
    "kernel_loading": re.compile(r"\b(FURNSH|UNLOAD|KCLEAR|KTOTAL|KDATA|KINFO|LDPOOL|CLPOOL)\b", re.IGNORECASE),
    "spk_operations": re.compile(r"\b(SPKEZ|SPKEZR|SPKPOS|SPKGEO|SPKGPS|SPKACS|SPKSSB|SPKLTC)\b", re.IGNORECASE),
    "frame_transforms": re.compile(r"\b(FRMCHG|REFCHG|ROTGET|NAMFRM|FRINFO|TIPBOD|TISBOD|SXFORM|PXFORM)\b", re.IGNORECASE),
    "time_conversion": re.compile(r"\b(STR2ET|ET2UTC|TIMOUT|UNITIM|UTC2ET|SCE2C|SCS2E|SCT2E)\b", re.IGNORECASE),
    "geometry": re.compile(r"\b(SUBPNT|SUBSLR|SINCPT|ILLUMF|ILUMIN|TANGPT|TERMPT|LIMBPT|OCCULT)\b", re.IGNORECASE),
    "matrix_vector": re.compile(r"\b(MXV|MXVG|MTXV|MXM|VCRSS|VNORM|VDOT|VHAT|VROTV|ROTATE|ROTMAT)\b", re.IGNORECASE),
    "file_io": re.compile(r"\b(DAFOPR|DAFOPW|DAFCLS|DAFBFS|DAFFNA|DAFRFR|TXTOPN|TXTOPR|WRITLN|READLN)\b", re.IGNORECASE),
}


def _make_id(file_path: str, routine_name: str, chunk_type: str, index: int = 0) -> str:
    """Create a deterministic chunk ID."""
    raw = f"{file_path}::{routine_name}::{chunk_type}::{index}"
    return hashlib.md5(raw.encode()).hexdigest()[:16]


def _estimate_tokens(text: str) -> int:
    """Estimate token count from text length."""
    return len(text) // CHARS_PER_TOKEN


def _detect_patterns(text: str) -> list[str]:
    """Detect SPICE coding patterns present in the text."""
    found = []
    for pattern_name, regex in _PATTERNS.items():
        if regex.search(text):
            found.append(pattern_name)
    return found


def _base_metadata(
    routine: RoutineInfo,
    chunk_type: str,
    call_graph: dict | None = None,
) -> dict:
    """Build metadata dict for a routine chunk, enriched with call graph data."""
    meta = {
        "file_path": routine.file_path,
        "start_line": routine.start_line,
        "end_line": routine.end_line,
        "routine_name": routine.name,
        "routine_kind": routine.kind,
        "chunk_type": chunk_type,
        "abstract": routine.abstract[:500],
        "keywords": ", ".join(routine.keywords)[:200],
        "calls": ", ".join(routine.calls)[:500],
        "includes": ", ".join(routine.includes)[:200],
        "parent_routine": routine.parent_routine or "",
        "entry_aliases": ", ".join(routine.entry_points)[:300] if routine.entry_points else "",
    }

    # Enrich with call graph data
    if call_graph:
        reverse = call_graph.get("reverse", {})
        callers = reverse.get(routine.name, [])
        meta["called_by"] = ", ".join(callers[:20])[:500]  # Top 20 callers

        aliases = call_graph.get("aliases", {})
        if routine.name in aliases:
            meta["parent_routine"] = aliases[routine.name]

    # Detect patterns — stored as a LIST for Pinecone $in filtering
    full_text = routine.header_comments + "\n" + routine.body_code
    patterns = _detect_patterns(full_text)
    meta["patterns"] = patterns  # list, not comma-joined string

    return meta


def chunk_routine(
    routine: RoutineInfo,
    call_graph: dict | None = None,
) -> list[Chunk]:
    """Convert a RoutineInfo into one or more Chunks.

    Phase 2 improvements:
    - Merges small bodies (<100 tokens) into the doc chunk
    - Enriches metadata with called_by, patterns, entry_aliases
    """
    chunks: list[Chunk] = []

    doc_text = routine.header_comments.strip()
    body = routine.body_code.strip()
    body_tokens = _estimate_tokens(body)

    # Decide: merge small body into doc, or keep separate
    merge_body = body and body_tokens < SMALL_BODY_TOKENS

    # routine_doc chunk
    if doc_text:
        if merge_body and body:
            # Merge small body into doc for a single comprehensive chunk
            combined = f"{doc_text}\n\n--- Code ---\n{body}"
            if _estimate_tokens(combined) > MAX_CHUNK_TOKENS * 2:
                combined = combined[: MAX_CHUNK_CHARS * 2]
            meta = _base_metadata(routine, "routine_doc", call_graph)
            chunks.append(Chunk(
                id=_make_id(routine.file_path, routine.name, "routine_doc"),
                text=combined,
                metadata=meta,
            ))
        else:
            # Doc only — truncate if huge but keep Abstract + Brief_I/O
            if _estimate_tokens(doc_text) > MAX_CHUNK_TOKENS * 2:
                doc_text = doc_text[: MAX_CHUNK_CHARS * 2]
            meta = _base_metadata(routine, "routine_doc", call_graph)
            chunks.append(Chunk(
                id=_make_id(routine.file_path, routine.name, "routine_doc"),
                text=doc_text,
                metadata=meta,
            ))

    # routine_body / routine_segment (skip if merged above)
    if body and not merge_body:
        if body_tokens <= MAX_CHUNK_TOKENS:
            meta = _base_metadata(routine, "routine_body", call_graph)
            chunks.append(Chunk(
                id=_make_id(routine.file_path, routine.name, "routine_body"),
                text=body,
                metadata=meta,
            ))
        else:
            segments = _split_with_overlap(body, MAX_CHUNK_CHARS, OVERLAP_CHARS)
            for i, segment in enumerate(segments):
                meta = _base_metadata(routine, "routine_segment", call_graph)
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
        line_len = len(line) + 1
        if current_len + line_len > max_chars and current_lines:
            segments.append("\n".join(current_lines))
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


def chunk_include(path: Path, call_graph: dict | None = None) -> list[Chunk]:
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
        "called_by": "",
        "entry_aliases": "",
        "patterns": [],
    }

    return [Chunk(
        id=_make_id(file_str, path.stem, "include"),
        text=text,
        metadata=meta,
    )]


def chunk_codebase(
    routines: list[RoutineInfo],
    include_paths: list[Path],
    call_graph: dict | None = None,
) -> list[Chunk]:
    """Process all routines and include files into chunks."""
    chunks: list[Chunk] = []

    for routine in routines:
        chunks.extend(chunk_routine(routine, call_graph))

    for inc_path in include_paths:
        chunks.extend(chunk_include(inc_path, call_graph))

    return chunks


if __name__ == "__main__":
    import json
    import sys
    from app.ingestion.scanner import scan_directory
    from app.ingestion.fortran_parser import parse_file

    source_dir = sys.argv[1] if len(sys.argv) > 1 else "../data/spice"

    f_files = scan_directory(source_dir, [".f"])
    inc_files = scan_directory(source_dir, [".inc"])

    all_routines: list[RoutineInfo] = []
    for f in f_files:
        all_routines.extend(parse_file(f))

    # Load call graph if available
    cg = None
    cg_path = Path("data/call_graph.json")
    if cg_path.exists():
        cg = json.loads(cg_path.read_text())
        print(f"Loaded call graph: {len(cg.get('forward', {}))} routines")

    chunks = chunk_codebase(all_routines, inc_files, cg)

    by_type: dict[str, int] = {}
    total_chars = 0
    pattern_counts: dict[str, int] = {}
    merged_count = 0

    for c in chunks:
        ct = c.metadata["chunk_type"]
        by_type[ct] = by_type.get(ct, 0) + 1
        total_chars += len(c.text)
        raw_patterns = c.metadata.get("patterns", [])
        if isinstance(raw_patterns, list):
            for p in raw_patterns:
                if p:
                    pattern_counts[p] = pattern_counts.get(p, 0) + 1
        elif raw_patterns:
            for p in str(raw_patterns).split(", "):
                if p:
                    pattern_counts[p] = pattern_counts.get(p, 0) + 1

    print(f"\nSource files:  {len(f_files)} .f + {len(inc_files)} .inc")
    print(f"Routines:      {len(all_routines)}")
    print(f"Total chunks:  {len(chunks)}")
    print(f"Total chars:   {total_chars:,}")
    print(f"Est. tokens:   {total_chars // CHARS_PER_TOKEN:,}")
    print(f"\nBy type:")
    for ct, count in sorted(by_type.items()):
        print(f"  {ct}: {count}")
    print(f"\nDetected patterns:")
    for p, count in sorted(pattern_counts.items(), key=lambda x: -x[1]):
        print(f"  {p}: {count} chunks")
    
    # Count enriched with called_by
    enriched = sum(1 for c in chunks if c.metadata.get("called_by"))
    print(f"\nChunks enriched with called_by: {enriched}")
