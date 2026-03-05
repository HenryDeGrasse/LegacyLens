"""BM25 keyword index for hybrid search.

Builds an in-memory BM25 index from chunk metadata stored in the call
graph JSON and Pinecone metadata. At query time, returns the top-K
chunk IDs ranked by keyword relevance. Combined with vector search via
Reciprocal Rank Fusion (RRF) for hybrid retrieval.

The index is built lazily on first query and cached for the process
lifetime.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

from rank_bm25 import BM25Okapi

logger = logging.getLogger(__name__)

# ── Tokenizer ────────────────────────────────────────────────────────

_WORD_RE = re.compile(r"[A-Za-z0-9_]{2,}")


def _tokenize(text: str) -> list[str]:
    """Simple whitespace + punctuation tokenizer. Lowercased."""
    return [w.lower() for w in _WORD_RE.findall(text)]


# ── BM25 Document ────────────────────────────────────────────────────

@dataclass
class BM25Doc:
    """A document in the BM25 index with enough metadata for RRF merge."""
    chunk_id: str
    routine_name: str
    chunk_type: str
    tokens: list[str]


# ── Singleton index ──────────────────────────────────────────────────

_bm25: BM25Okapi | None = None
_bm25_docs: list[BM25Doc] = []
_bm25_lock = Lock()


def _build_bm25_corpus() -> tuple[BM25Okapi, list[BM25Doc]]:
    """Build BM25 index from call_graph.json + any cached chunk texts.

    We use routine names, their callees, callers, and file paths as
    the searchable corpus. This gives keyword recall for queries like
    "SPKEZ" or "error handling CHKIN" without needing the full chunk
    text (which lives in Pinecone, not locally).
    """
    cg_path = None
    candidates = [
        Path("data/call_graph.json"),
        Path(__file__).parent.parent.parent / "data" / "call_graph.json",
        Path("/app/data/call_graph.json"),
    ]
    for p in candidates:
        if p.exists():
            cg_path = p
            break

    if cg_path is None:
        logger.warning("BM25: call_graph.json not found, index will be empty")
        return BM25Okapi([["_empty_"]]), [BM25Doc("", "", "", ["_empty_"])]

    data = json.loads(cg_path.read_text())
    forward = data.get("forward", {})
    reverse = data.get("reverse", {})
    aliases = data.get("aliases", {})
    routine_files = data.get("routine_files", {})

    docs: list[BM25Doc] = []
    corpus: list[list[str]] = []

    for routine_name, callees in forward.items():
        callers = reverse.get(routine_name, [])
        file_path = routine_files.get(routine_name, "")

        # Build a pseudo-document from structural metadata
        text_parts = [
            routine_name,
            routine_name,  # double-weight the name
            file_path,
            " ".join(callees[:30]),
            " ".join(callers[:30]),
        ]

        # Add alias info if this routine has entry points
        entry_names = [alias for alias, parent in aliases.items() if parent == routine_name]
        if entry_names:
            text_parts.append(" ".join(entry_names))

        text = " ".join(text_parts)
        tokens = _tokenize(text)

        doc = BM25Doc(
            chunk_id=f"bm25::{routine_name}",
            routine_name=routine_name,
            chunk_type="routine_doc",
            tokens=tokens,
        )
        docs.append(doc)
        corpus.append(tokens)

    # Also index aliases as separate documents pointing to parent
    for alias, parent in aliases.items():
        callers = reverse.get(alias, [])
        file_path = routine_files.get(alias, routine_files.get(parent, ""))

        text_parts = [alias, alias, parent, file_path, " ".join(callers[:20])]
        tokens = _tokenize(text)

        doc = BM25Doc(
            chunk_id=f"bm25::{alias}",
            routine_name=alias,
            chunk_type="routine_doc",
            tokens=tokens,
        )
        docs.append(doc)
        corpus.append(tokens)

    logger.info(f"BM25: indexed {len(docs)} documents from call graph")
    index = BM25Okapi(corpus)
    return index, docs


def get_bm25() -> tuple[BM25Okapi, list[BM25Doc]]:
    """Return cached BM25 index, building on first access."""
    global _bm25, _bm25_docs
    if _bm25 is not None:
        return _bm25, _bm25_docs
    with _bm25_lock:
        if _bm25 is not None:
            return _bm25, _bm25_docs
        _bm25, _bm25_docs = _build_bm25_corpus()
        return _bm25, _bm25_docs


# ── Query ────────────────────────────────────────────────────────────


@dataclass
class BM25Result:
    """A single BM25 search result."""
    routine_name: str
    score: float
    chunk_type: str


def bm25_search(query: str, top_k: int = 20) -> list[BM25Result]:
    """Search the BM25 index. Returns routine names ranked by BM25 score."""
    bm25, docs = get_bm25()
    tokens = _tokenize(query)
    if not tokens:
        return []

    scores = bm25.get_scores(tokens)

    # Get top-K by score
    indexed_scores = [(i, s) for i, s in enumerate(scores) if s > 0]
    indexed_scores.sort(key=lambda x: -x[1])

    results: list[BM25Result] = []
    seen_names: set[str] = set()
    for idx, score in indexed_scores[:top_k]:
        doc = docs[idx]
        if doc.routine_name not in seen_names:
            seen_names.add(doc.routine_name)
            results.append(BM25Result(
                routine_name=doc.routine_name,
                score=score,
                chunk_type=doc.chunk_type,
            ))

    return results


# ── Reciprocal Rank Fusion ───────────────────────────────────────────

def reciprocal_rank_fusion(
    vector_names: list[str],
    bm25_names: list[str],
    k: int = 60,
) -> list[str]:
    """Merge two ranked lists using RRF.

    Returns routine names ordered by combined RRF score.
    The constant k=60 is standard (Cormack et al. 2009).
    """
    scores: dict[str, float] = {}

    for rank, name in enumerate(vector_names):
        scores[name] = scores.get(name, 0.0) + 1.0 / (k + rank + 1)

    for rank, name in enumerate(bm25_names):
        scores[name] = scores.get(name, 0.0) + 1.0 / (k + rank + 1)

    # Sort by combined score descending
    ranked = sorted(scores.keys(), key=lambda n: -scores[n])
    return ranked
