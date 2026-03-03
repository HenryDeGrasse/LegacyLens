"""LegacyLens FastAPI application."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.responses import StreamingResponse

app = FastAPI(
    title="LegacyLens",
    description="RAG-powered system for querying NASA's SPICE Toolkit Fortran codebase",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Static files ────────────────────────────────────────────────────
_static_candidates = [
    Path(__file__).parent.parent / "static",
    Path("/app/static"),
    Path("static"),
]
_static_dir = next((p for p in _static_candidates if p.exists()), None)
if _static_dir:
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")


class QueryRequest(BaseModel):
    question: str
    top_k: int = 10


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
    return {"status": "ok"}


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
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/stats")
async def stats():
    """Get Pinecone index statistics."""
    try:
        from pinecone import Pinecone
        from app.config import settings

        pc = Pinecone(api_key=settings.pinecone_api_key)
        index = pc.Index(settings.pinecone_index)
        index_stats = index.describe_index_stats()

        return {
            "total_vectors": index_stats.total_vector_count,
            "dimension": index_stats.dimension,
            "index_name": settings.pinecone_index,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class DependencyRequest(BaseModel):
    routine_name: str
    depth: int = 1


@app.post("/dependencies")
async def dependencies(request: DependencyRequest):
    """Get forward and reverse call dependencies for a routine."""
    try:
        from app.features.dependencies import get_dependencies
        return get_dependencies(request.routine_name, depth=request.depth)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class ImpactRequest(BaseModel):
    routine_name: str
    depth: int = 2


@app.post("/impact")
async def impact(request: ImpactRequest):
    """Analyze the blast radius of changing a routine."""
    try:
        from app.features.impact import get_impact
        return get_impact(request.routine_name, depth=request.depth)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/patterns")
async def patterns():
    """List available SPICE coding patterns."""
    try:
        from app.features.patterns import list_patterns
        return {"patterns": list_patterns()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class PatternSearchRequest(BaseModel):
    pattern: str
    query: str = ""
    top_k: int = 10


@app.post("/patterns/search")
async def pattern_search(request: PatternSearchRequest):
    """Search for routines matching a specific SPICE pattern."""
    try:
        from app.features.patterns import search_pattern
        return search_pattern(request.pattern, query=request.query, top_k=request.top_k)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class ExplainRequest(BaseModel):
    routine_name: str


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
        raise HTTPException(status_code=500, detail=str(e))


class DocgenRequest(BaseModel):
    routine_name: str


@app.post("/docgen")
async def docgen(request: DocgenRequest):
    """Generate Markdown documentation for a SPICE routine."""
    try:
        from app.features.docgen import generate_doc
        return generate_doc(request.routine_name)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── SSE streaming endpoint ──────────────────────────────────────────


class StreamRequest(BaseModel):
    question: str
    top_k: int = 10


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
            yield _sse_event("error", json.dumps({"message": str(e)}))

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
    return {"message": "LegacyLens API", "docs": "/docs"}
