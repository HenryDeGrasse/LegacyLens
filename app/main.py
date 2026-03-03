"""LegacyLens FastAPI application."""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.responses import StreamingResponse

logger = logging.getLogger("legacylens")

app = FastAPI(
    title="LegacyLens",
    description="RAG-powered system for querying NASA's SPICE Toolkit Fortran codebase",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Simple in-memory rate limiter ────────────────────────────────
_rate_buckets: dict[str, list[float]] = defaultdict(list)
_RATE_LIMIT = 30       # requests per window
_RATE_WINDOW = 60.0    # seconds


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    """Sliding-window rate limiter per client IP on mutating endpoints."""
    if request.method == "POST":
        client_ip = request.client.host if request.client else "unknown"
        now = time.time()
        bucket = _rate_buckets[client_ip]
        # Prune old entries
        _rate_buckets[client_ip] = bucket = [t for t in bucket if now - t < _RATE_WINDOW]
        if len(bucket) >= _RATE_LIMIT:
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded. Try again shortly."},
            )
        bucket.append(now)
    return await call_next(request)


# ── Static file path detection ──────────────────────────────────────
import os as _os

_static_candidates = [
    Path(__file__).parent.parent / "static",
    Path("/app/static"),
    Path("static"),
]
_static_dir = next((p for p in _static_candidates if p.exists()), None)
logger.info(f"Static dir detection: cwd={_os.getcwd()}, found={_static_dir}")


_MAX_QUERY_LEN = 2000


class QueryRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=_MAX_QUERY_LEN)
    top_k: int = Field(default=10, ge=1, le=50)


class ChunkInfo(BaseModel):
    id: str
    score: float
    routine_name: str
    chunk_type: str
    file_path: str
    start_line: int | str
    end_line: int | str
    text: str


class QueryResponse(BaseModel):
    answer: str
    citations: list[dict]
    chunks: list[ChunkInfo]
    usage: dict
    routing: dict = {}
    cached: bool = False


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {
        "status": "ok",
        "version": "0.9.0-web",
    }


@app.post("/query", response_model=QueryResponse)
async def query(request: QueryRequest):
    """Query the SPICE Toolkit codebase with natural language."""
    try:
        from app.retrieval.router import route_query
        from app.retrieval.search import retrieve_routed
        from app.retrieval.context import assemble_context
        from app.retrieval.generator import generate_answer

        # Route the query
        routed = route_query(request.question)

        # Retrieve using routed strategy
        chunks = retrieve_routed(routed, top_k=request.top_k)
        if not chunks:
            raise HTTPException(status_code=404, detail="No relevant chunks found")

        # Assemble context
        context = assemble_context(chunks)

        # Generate answer
        response = generate_answer(request.question, context)

        # Format chunks for response
        chunk_infos = []
        for c in chunks:
            meta = c.metadata
            chunk_infos.append(ChunkInfo(
                id=c.id,
                score=c.score,
                routine_name=meta.get("routine_name", "unknown"),
                chunk_type=meta.get("chunk_type", "unknown"),
                file_path=meta.get("file_path", "unknown"),
                start_line=meta.get("start_line", 0),
                end_line=meta.get("end_line", 0),
                text=(c.text or meta.get("text", ""))[:1000],
            ))

        return QueryResponse(
            answer=response.answer,
            citations=response.citations,
            chunks=chunk_infos,
            usage=response.usage,
            routing={
                "intent": routed.intent.name,
                "routine_names": routed.routine_names,
                "patterns": routed.patterns,
                "prefer_doc": routed.prefer_doc,
            },
            cached=response.cached,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Query failed")
        raise HTTPException(status_code=500, detail="Internal error processing query.")


@app.get("/stats")
async def stats():
    """Get Pinecone index statistics."""
    try:
        from app.services import get_index
        from app.config import settings

        index = get_index()
        index_stats = index.describe_index_stats()

        return {
            "total_vectors": index_stats.total_vector_count,
            "dimension": index_stats.dimension,
            "index_name": settings.pinecone_index,
        }
    except Exception as e:
        logger.exception("Stats fetch failed")
        raise HTTPException(status_code=500, detail="Failed to fetch index stats.")


class DependencyRequest(BaseModel):
    routine_name: str = Field(..., min_length=1, max_length=100)
    depth: int = Field(default=1, ge=1, le=10)


@app.post("/dependencies")
async def dependencies(request: DependencyRequest):
    """Get forward and reverse call dependencies for a routine."""
    try:
        from app.features.dependencies import get_dependencies
        return get_dependencies(request.routine_name, depth=request.depth)
    except Exception as e:
        logger.exception("Request failed")
        raise HTTPException(status_code=500, detail="Internal server error.")


class ImpactRequest(BaseModel):
    routine_name: str = Field(..., min_length=1, max_length=100)
    depth: int = Field(default=2, ge=1, le=10)


@app.post("/impact")
async def impact(request: ImpactRequest):
    """Analyze the blast radius of changing a routine."""
    try:
        from app.features.impact import get_impact
        return get_impact(request.routine_name, depth=request.depth)
    except Exception as e:
        logger.exception("Request failed")
        raise HTTPException(status_code=500, detail="Internal server error.")


@app.get("/patterns")
async def patterns():
    """List available SPICE coding patterns."""
    try:
        from app.features.patterns import list_patterns
        return {"patterns": list_patterns()}
    except Exception as e:
        logger.exception("Request failed")
        raise HTTPException(status_code=500, detail="Internal server error.")


class PatternSearchRequest(BaseModel):
    pattern: str = Field(..., min_length=1, max_length=100)
    query: str = Field(default="", max_length=_MAX_QUERY_LEN)
    top_k: int = Field(default=10, ge=1, le=50)


@app.post("/patterns/search")
async def pattern_search(request: PatternSearchRequest):
    """Search for routines matching a specific SPICE pattern."""
    try:
        from app.features.patterns import search_pattern
        return search_pattern(request.pattern, query=request.query, top_k=request.top_k)
    except Exception as e:
        logger.exception("Request failed")
        raise HTTPException(status_code=500, detail="Internal server error.")


class ExplainRequest(BaseModel):
    routine_name: str = Field(..., min_length=1, max_length=100)


@app.post("/explain")
async def explain(request: ExplainRequest):
    """Generate a detailed explanation of a SPICE routine."""
    try:
        from app.features.explain import explain_routine
        result = explain_routine(request.routine_name)
        return {
            "routine_name": result.routine_name,
            "explanation": result.explanation,
            "file_path": result.file_path,
            "start_line": result.start_line,
            "end_line": result.end_line,
            "calls": result.calls,
            "called_by": result.called_by,
            "patterns": result.patterns,
            "usage": result.usage,
        }
    except Exception as e:
        logger.exception("Request failed")
        raise HTTPException(status_code=500, detail="Internal server error.")


class DocgenRequest(BaseModel):
    routine_name: str = Field(..., min_length=1, max_length=100)


@app.post("/docgen")
async def docgen(request: DocgenRequest):
    """Generate Markdown documentation for a SPICE routine."""
    try:
        from app.features.docgen import generate_doc
        return generate_doc(request.routine_name)
    except Exception as e:
        logger.exception("Request failed")
        raise HTTPException(status_code=500, detail="Internal server error.")


# ── SSE streaming endpoint ──────────────────────────────────────────


class StreamRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=_MAX_QUERY_LEN)
    top_k: int = Field(default=10, ge=1, le=50)


def _sse_event(event: str, data: str) -> str:
    return f"event: {event}\ndata: {data}\n\n"


@app.post("/api/stream")
async def stream_query(request: StreamRequest):
    """Stream a RAG query response as Server-Sent Events.

    Events:
      routing  — {intent, routine_names, patterns}
      chunks   — [{routine_name, chunk_type, file_path, score, text}, ...]
      token    — partial answer token
      done     — {cached, answer_length}
      error    — {message}
    """
    def generate():
        try:
            from app.retrieval.router import route_query, QueryIntent
            from app.retrieval.search import retrieve_routed
            from app.retrieval.context import assemble_context
            from app.retrieval.generator import generate_answer_stream

            # Route
            routed = route_query(request.question)
            yield _sse_event("routing", json.dumps({
                "intent": routed.intent.name,
                "routine_names": routed.routine_names,
                "patterns": routed.patterns,
            }))

            # Retrieve
            chunks = retrieve_routed(routed, top_k=request.top_k)
            chunk_data = []
            for c in (chunks or []):
                meta = c.metadata
                chunk_data.append({
                    "routine_name": meta.get("routine_name", "unknown"),
                    "chunk_type": meta.get("chunk_type", "unknown"),
                    "file_path": meta.get("file_path", "unknown"),
                    "start_line": meta.get("start_line", 0),
                    "end_line": meta.get("end_line", 0),
                    "score": round(c.score, 3),
                    "text": (c.text or meta.get("text", ""))[:2000],
                })
            yield _sse_event("chunks", json.dumps(chunk_data))

            if not chunks:
                yield _sse_event("error", json.dumps({"message": "No relevant chunks found"}))
                return

            # Context assembly with intent-aware budget
            ctx_budget = {
                QueryIntent.DEPENDENCY: 2000,
                QueryIntent.IMPACT: 2500,
            }.get(routed.intent)
            context = assemble_context(chunks, max_tokens=ctx_budget)

            # Stream LLM tokens
            answer_len = 0
            cached = False
            for token, resp in generate_answer_stream(request.question, context):
                if resp is not None:
                    cached = resp.cached
                    if cached:
                        # Cached: send full answer as one token event
                        yield _sse_event("token", json.dumps({"t": resp.answer}))
                        answer_len = len(resp.answer)
                elif token is not None:
                    yield _sse_event("token", json.dumps({"t": token}))
                    answer_len += len(token)

            yield _sse_event("done", json.dumps({
                "cached": cached,
                "answer_length": answer_len,
            }))

        except Exception as e:
            logger.exception("Streaming query failed")
            yield _sse_event("error", json.dumps({"message": "An error occurred processing the query."}))

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ── Root route (serves web UI) ──────────────────────────────────────

@app.get("/")
async def root():
    """Serve the web UI."""
    candidates = [
        Path(__file__).parent.parent / "static" / "index.html",
        Path("/app/static/index.html"),
        Path("static/index.html"),
    ]
    for p in candidates:
        if p.exists():
            return FileResponse(str(p), media_type="text/html")
    return {
        "message": "LegacyLens API",
        "docs": "/docs",
    }


# ── Static files mount (MUST be after all routes) ──────────────────
# FastAPI mounts are checked before routes, so mounting at "/" would
# shadow all routes. Mount at "/static" after routes are defined.
if _static_dir:
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")
