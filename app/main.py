"""LegacyLens FastAPI application."""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.responses import StreamingResponse

logger = logging.getLogger("legacylens")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Validate configuration and pre-warm resources on startup."""
    from app.config import settings

    errors: list[str] = []

    if not settings.openai_api_key:
        errors.append("OPENAI_API_KEY is not set (needed for embeddings)")
    elif not settings.openai_api_key.startswith("sk-"):
        errors.append("OPENAI_API_KEY doesn't look valid (expected sk-...)")

    # LLM: either OpenRouter or OpenAI
    if settings.openrouter_api_key:
        logger.info(
            f"LLM provider: OpenRouter, model={settings.llm_model}"
        )
    elif settings.openai_api_key:
        logger.info(
            f"LLM provider: OpenAI (direct), model={settings.llm_model}"
        )
    else:
        errors.append("No LLM API key set (need OPENROUTER_API_KEY or OPENAI_API_KEY)")

    if not settings.pinecone_api_key:
        errors.append("PINECONE_API_KEY is not set")

    if not settings.pinecone_index:
        errors.append("PINECONE_INDEX is not set")

    if errors:
        for err in errors:
            logger.error(f"STARTUP CHECK FAILED: {err}")
        logger.error(
            "Fix the above issues in .env or environment variables. "
            "The app will start but queries will fail."
        )
    else:
        logger.info("Startup validation passed — all API keys configured")

    # Non-blocking: try to warm the call graph
    from app.services import get_call_graph
    cg = get_call_graph()
    if cg:
        n_routines = len(cg.get("forward", {}))
        n_aliases = len(cg.get("aliases", {}))
        logger.info(f"Call graph loaded: {n_routines} routines, {n_aliases} entry aliases")
    else:
        logger.warning("Call graph not found — /dependencies and /impact will fail")

    yield  # application runs here


app = FastAPI(
    title="LegacyLens",
    description="RAG-powered system for querying NASA's SPICE Toolkit Fortran codebase",
    version="0.1.0",
    lifespan=lifespan,
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
_RATE_MAX_IPS = 10000  # max tracked IPs to prevent memory leak


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
        # Prevent memory leak from many unique IPs
        if len(_rate_buckets) > _RATE_MAX_IPS:
            oldest_ip = next(iter(_rate_buckets))
            del _rate_buckets[oldest_ip]
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
    session_id: str | None = Field(default=None, description="Session ID for multi-turn conversation")


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


@app.post("/api/session")
def create_session():
    """Create a new conversation session. Returns a session_id for multi-turn queries."""
    from app.retrieval.generator import get_conversation_store
    store = get_conversation_store()
    return {"session_id": store.new_session_id()}


@app.get("/health")
async def health():
    """Health check endpoint."""
    from app.services import get_call_graph
    cg = get_call_graph()
    return {
        "status": "ok",
        "version": "0.10.0",
        "call_graph_loaded": cg is not None,
    }


# ── Cached routine name list for autocomplete ───────────────────────
# Previously rebuilt sorted(set(forward + aliases)) on every request.
# Now cached once on first access (call graph is immutable at runtime).
import bisect as _bisect

_routine_names: list[str] | None = None
_routine_names_lock = __import__("threading").Lock()


def _get_routine_names() -> list[str]:
    """Return cached sorted list of all routine + alias names."""
    global _routine_names
    if _routine_names is not None:
        return _routine_names
    with _routine_names_lock:
        if _routine_names is not None:
            return _routine_names
        from app.services import get_call_graph
        cg = get_call_graph()
        if not cg:
            return []
        forward = cg.get("forward", {})
        aliases = cg.get("aliases", {})
        _routine_names = sorted(set(list(forward.keys()) + list(aliases.keys())))
        return _routine_names


@app.get("/api/routines")
async def list_routines(q: str = "", limit: int = 50):
    """Return routine names for autocomplete.

    If `q` is provided, returns fuzzy-matching names.
    Otherwise returns the first `limit` routines alphabetically.
    """
    # Clamp limit to prevent abuse
    limit = max(1, min(limit, 100))

    all_names = _get_routine_names()
    if not all_names:
        return {"routines": [], "total": 0}

    if q:
        query_upper = q.strip().upper()[:100]  # Cap query length
        # O(log n) prefix search via bisect on pre-sorted list
        lo = _bisect.bisect_left(all_names, query_upper)
        hi = _bisect.bisect_right(all_names, query_upper + "\xff")
        prefix = all_names[lo:hi]
        # Substring match (still linear, but skips prefix hits via set)
        prefix_set = set(prefix)
        substring = [n for n in all_names if query_upper in n and n not in prefix_set]
        matches = (prefix + substring)[:limit]
    else:
        matches = all_names[:limit]

    return {
        "routines": matches,
        "total": len(all_names),
    }


@app.post("/query", response_model=QueryResponse)
def query(request: QueryRequest):
    """Query the SPICE Toolkit codebase with natural language."""
    try:
        from app.retrieval.router import route_query, QueryIntent, _OUT_OF_SCOPE_RESPONSE
        from app.retrieval.search import retrieve_routed
        from app.retrieval.context import assemble_context
        from app.retrieval.generator import generate_answer

        # Route the query
        routed = route_query(request.question)

        # Handle out-of-scope queries without API calls
        if routed.intent == QueryIntent.OUT_OF_SCOPE:
            return QueryResponse(
                answer=_OUT_OF_SCOPE_RESPONSE,
                citations=[],
                chunks=[],
                usage={},
                routing={
                    "intent": routed.intent.name,
                    "routine_names": [],
                    "patterns": [],
                    "prefer_doc": False,
                },
                cached=False,
            )

        # Retrieve using routed strategy
        chunks = retrieve_routed(routed, top_k=request.top_k)
        if not chunks:
            raise HTTPException(status_code=404, detail="No relevant chunks found")

        # Assemble context with intent-aware budget
        from app.retrieval.router import QueryIntent
        ctx_budget = {
            QueryIntent.DEPENDENCY: 2000,
            QueryIntent.IMPACT: 2500,
        }.get(routed.intent)
        context = assemble_context(chunks, max_tokens=ctx_budget)

        # Generate answer (with conversation history if session_id provided)
        response = generate_answer(request.question, context, session_id=request.session_id)

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
def dependencies(request: DependencyRequest):
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
def impact(request: ImpactRequest):
    """Analyze the blast radius of changing a routine."""
    try:
        from app.features.impact import get_impact
        return get_impact(request.routine_name, depth=request.depth)
    except Exception as e:
        logger.exception("Request failed")
        raise HTTPException(status_code=500, detail="Internal server error.")


class MetricsRequest(BaseModel):
    routine_name: str = Field(..., min_length=1, max_length=100)


@app.post("/metrics")
def metrics(request: MetricsRequest):
    """Compute code complexity metrics for a SPICE routine.

    Returns LOC breakdown, cyclomatic complexity, nesting depth,
    parameter count, and dependency stats. No LLM call needed.
    """
    try:
        from app.features.metrics import get_metrics
        return get_metrics(request.routine_name)
    except Exception as e:
        logger.exception("Request failed")
        raise HTTPException(status_code=500, detail="Internal server error.")


@app.get("/patterns")
def patterns():
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
def pattern_search(request: PatternSearchRequest):
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
def explain(request: ExplainRequest):
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
def docgen(request: DocgenRequest):
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
    session_id: str | None = Field(default=None, description="Session ID for multi-turn conversation")


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
            from app.retrieval.router import route_query, QueryIntent, _OUT_OF_SCOPE_RESPONSE
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

            # Handle out-of-scope queries without API calls
            if routed.intent == QueryIntent.OUT_OF_SCOPE:
                yield _sse_event("token", json.dumps({"t": _OUT_OF_SCOPE_RESPONSE}))
                yield _sse_event("done", json.dumps({"cached": False, "answer_length": len(_OUT_OF_SCOPE_RESPONSE)}))
                return

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

            # Stream LLM tokens (with conversation history)
            answer_len = 0
            cached = False
            for token, resp in generate_answer_stream(request.question, context, session_id=request.session_id):
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
