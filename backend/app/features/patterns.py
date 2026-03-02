"""Pattern detection: find routines matching SPICE coding patterns.

Searches Pinecone for chunks tagged with specific patterns and returns
matching routines with relevance info.
"""

from __future__ import annotations

from openai import OpenAI
from pinecone import Pinecone

from app.config import settings


AVAILABLE_PATTERNS = [
    "error_handling",
    "kernel_loading",
    "spk_operations",
    "frame_transforms",
    "time_conversion",
    "geometry",
    "matrix_vector",
    "file_io",
]

PATTERN_DESCRIPTIONS = {
    "error_handling": "Error handling routines using CHKIN/CHKOUT/SIGERR/SETMSG",
    "kernel_loading": "Kernel management routines using FURNSH/UNLOAD/KCLEAR/LDPOOL",
    "spk_operations": "SPK ephemeris operations using SPKEZ/SPKEZR/SPKPOS/SPKGEO",
    "frame_transforms": "Reference frame transformations using FRMCHG/NAMFRM/SXFORM/PXFORM",
    "time_conversion": "Time conversion routines using STR2ET/ET2UTC/TIMOUT/UNITIM",
    "geometry": "Geometry computations using SUBPNT/SINCPT/ILLUMF/TANGPT/TERMPT",
    "matrix_vector": "Matrix/vector operations using MXV/VCRSS/VNORM/VDOT/ROTATE",
    "file_io": "File I/O operations using DAFOPR/DAFCLS/TXTOPN/WRITLN/READLN",
}

PATTERN_EXAMPLES = {
    "error_handling": "How does SPICE handle errors? Show me the error checking pattern.",
    "kernel_loading": "How do I load SPICE kernels? What's the kernel management flow?",
    "spk_operations": "How do I compute spacecraft positions? Show SPK reading routines.",
    "frame_transforms": "How are reference frames transformed? Show frame rotation code.",
    "time_conversion": "How do I convert between time formats in SPICE?",
    "geometry": "How do I compute sub-observer points or surface intercepts?",
    "matrix_vector": "Show me matrix and vector math utilities in SPICE.",
    "file_io": "How does SPICE read and write data files?",
}


def list_patterns() -> list[dict]:
    """List all available SPICE patterns with descriptions and examples."""
    return [
        {
            "name": p,
            "description": PATTERN_DESCRIPTIONS.get(p, ""),
            "example_query": PATTERN_EXAMPLES.get(p, ""),
        }
        for p in AVAILABLE_PATTERNS
    ]


def search_pattern(pattern_name: str, query: str = "", top_k: int = 10) -> dict:
    """Search for routines matching a specific pattern.

    Args:
        pattern_name: One of the AVAILABLE_PATTERNS.
        query: Optional natural language query to refine search.
        top_k: Number of results to return.

    Returns:
        Dict with matching routines and their metadata.
    """
    if pattern_name not in AVAILABLE_PATTERNS:
        return {
            "error": f"Unknown pattern '{pattern_name}'. Available: {AVAILABLE_PATTERNS}",
            "results": [],
        }

    pc = Pinecone(api_key=settings.pinecone_api_key)
    index = pc.Index(settings.pinecone_index)

    # Use the pattern description as the query if none provided
    search_text = query if query else PATTERN_DESCRIPTIONS.get(pattern_name, pattern_name)

    client = OpenAI(api_key=settings.openai_api_key)
    embed_resp = client.embeddings.create(
        input=search_text,
        model=settings.embedding_model,
        dimensions=settings.embedding_dimensions,
    )
    query_vec = embed_resp.data[0].embedding

    # Search with pattern filter
    results = index.query(
        vector=query_vec,
        top_k=top_k,
        filter={"patterns": {"$eq": pattern_name}},
        include_metadata=True,
    )

    # Deduplicate by routine name, keeping best score
    seen_routines: dict[str, dict] = {}
    for match in results.matches:
        meta = match.metadata or {}
        routine_name = meta.get("routine_name", "unknown")
        if routine_name not in seen_routines or match.score > seen_routines[routine_name]["score"]:
            seen_routines[routine_name] = {
                "routine_name": routine_name,
                "score": match.score,
                "file_path": meta.get("file_path", "unknown"),
                "abstract": meta.get("abstract", ""),
                "chunk_type": meta.get("chunk_type", ""),
                "start_line": meta.get("start_line", 0),
                "end_line": meta.get("end_line", 0),
                "calls": meta.get("calls", ""),
                "called_by": meta.get("called_by", ""),
                "all_patterns": meta.get("patterns", ""),
            }

    routines = sorted(seen_routines.values(), key=lambda r: -r["score"])

    return {
        "pattern": pattern_name,
        "description": PATTERN_DESCRIPTIONS.get(pattern_name, ""),
        "query": search_text,
        "total_results": len(routines),
        "results": routines,
    }
