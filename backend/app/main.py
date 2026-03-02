"""LegacyLens FastAPI application."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

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


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok"}


@app.post("/query", response_model=QueryResponse)
async def query(request: QueryRequest):
    """Query the SPICE Toolkit codebase with natural language."""
    try:
        from app.retrieval.search import retrieve
        from app.retrieval.context import assemble_context
        from app.retrieval.generator import generate_answer

        # Retrieve
        chunks = retrieve(request.question, top_k=request.top_k)
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
                text=meta.get("text", "")[:1000],  # Truncate for API response
            ))

        return QueryResponse(
            answer=response.answer,
            citations=response.citations,
            chunks=chunk_infos,
            usage=response.usage,
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
