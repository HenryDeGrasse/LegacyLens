"""Code Explanation: generate plain English explanations of routines.

Retrieves the routine_doc + routine_body from Pinecone, then uses GPT-4o-mini
to produce a structured, cited explanation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from openai import OpenAI
from pinecone import Pinecone

from app.config import settings
from app.ingestion.call_graph import load_call_graph


EXPLAIN_SYSTEM_PROMPT = """\
You are an expert Fortran 77 code analyst specializing in NASA's SPICE Toolkit.
Your job is to explain routines in clear, modern language that a developer unfamiliar
with Fortran or SPICE can understand.

Structure your explanation with these sections:
1. **Purpose** — What does this routine do in one paragraph?
2. **Inputs & Outputs** — Table of parameters with types and descriptions.
3. **Algorithm** — Step-by-step walkthrough of the logic.
4. **Dependencies** — What other routines it calls and why.
5. **Usage Context** — When would a developer use this routine?
6. **Modern Equivalent** — What would this look like in Python/C++ (brief hint).

Rules:
- Cite source using [file:start_line-end_line] format.
- Explain Fortran 77 idioms (COMMON blocks, EQUIVALENCE, fixed-form) in modern terms.
- If code uses CHKIN/CHKOUT, explain it as "enter/exit error scope".
- Keep explanations accessible to someone who doesn't know Fortran.
"""


@dataclass
class ExplainResponse:
    """Structured explanation of a routine."""

    routine_name: str
    explanation: str
    file_path: str
    start_line: int
    end_line: int
    calls: list[str] = field(default_factory=list)
    called_by: list[str] = field(default_factory=list)
    patterns: list[str] = field(default_factory=list)
    usage: dict = field(default_factory=dict)


def explain_routine(routine_name: str) -> ExplainResponse:
    """Generate a comprehensive explanation of a SPICE routine.

    Retrieves the routine's doc and body chunks from Pinecone,
    loads call graph context, and generates an LLM explanation.
    """
    name = routine_name.upper()

    # Get call graph info
    try:
        graph = load_call_graph()
        actual_name = graph.aliases.get(name, name)
        calls = graph.forward.get(actual_name, [])
        callers = list(graph.callers_of(name, depth=1))
        file_path = graph.routine_files.get(actual_name, graph.routine_files.get(name, "unknown"))
    except Exception:
        actual_name = name
        calls = []
        callers = []
        file_path = "unknown"

    # Retrieve chunks from Pinecone
    pc = Pinecone(api_key=settings.pinecone_api_key)
    index = pc.Index(settings.pinecone_index)

    # Embed the routine name for filtered search
    client = OpenAI(api_key=settings.openai_api_key)
    embed_resp = client.embeddings.create(
        input=name,
        model=settings.embedding_model,
        dimensions=settings.embedding_dimensions,
    )
    query_vec = embed_resp.data[0].embedding

    # Fetch all chunks for this routine (and parent if ENTRY)
    search_names = [name]
    if actual_name != name:
        search_names.append(actual_name)

    all_chunks = []
    for search_name in search_names:
        results = index.query(
            vector=query_vec,
            top_k=5,
            filter={"routine_name": {"$eq": search_name}},
            include_metadata=True,
        )
        all_chunks.extend(results.matches)

    if not all_chunks:
        return ExplainResponse(
            routine_name=name,
            explanation=f"No code found for routine '{name}' in the index.",
            file_path=file_path,
            start_line=0,
            end_line=0,
        )

    # Assemble context from chunks
    context_parts = []
    start_line = 0
    end_line = 0
    patterns = set()

    for chunk in all_chunks:
        meta = chunk.metadata or {}
        text = meta.get("text", "")
        chunk_type = meta.get("chunk_type", "")
        if not start_line:
            start_line = meta.get("start_line", 0)
            end_line = meta.get("end_line", 0)
            file_path = meta.get("file_path", file_path)

        for p in meta.get("patterns", "").split(", "):
            if p:
                patterns.add(p)

        context_parts.append(f"--- {chunk_type} ---\n{text}")

    context = "\n\n".join(context_parts)

    # Add call graph context
    dep_context = f"\n\nDependency Info:\n- Calls: {', '.join(calls)}\n- Called by: {', '.join(callers)}"
    if name in (graph.aliases if 'graph' in dir() else {}):
        dep_context += f"\n- This is an ENTRY point in {actual_name}"

    full_context = context + dep_context

    # Generate explanation
    response = client.chat.completions.create(
        model=settings.llm_model,
        messages=[
            {"role": "system", "content": EXPLAIN_SYSTEM_PROMPT},
            {"role": "user", "content": f"Explain the routine `{name}` from the SPICE Toolkit.\n\nCode Context:\n{full_context}"},
        ],
        temperature=0.1,
        max_tokens=3000,
    )

    explanation = response.choices[0].message.content or ""

    return ExplainResponse(
        routine_name=name,
        explanation=explanation,
        file_path=file_path,
        start_line=start_line,
        end_line=end_line,
        calls=calls,
        called_by=callers,
        patterns=list(patterns),
        usage={
            "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
            "completion_tokens": response.usage.completion_tokens if response.usage else 0,
            "total_tokens": response.usage.total_tokens if response.usage else 0,
        },
    )
