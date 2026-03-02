"""Documentation Generation: produce Markdown docs for routines.

Generates structured Markdown documentation from routine code and metadata,
suitable for a developer reference guide.
"""

from __future__ import annotations

from openai import OpenAI
from pinecone import Pinecone

from app.config import settings
from app.ingestion.call_graph import load_call_graph


DOCGEN_SYSTEM_PROMPT = """\
You are a technical documentation writer for NASA's SPICE Toolkit (Fortran 77).
Generate clean Markdown documentation for the given routine.

Output format:
```markdown
# `ROUTINE_NAME`

> One-line summary from the Abstract.

## Synopsis

```fortran
SUBROUTINE ROUTINE_NAME ( ARG1, ARG2, ... )
```

## Description

2-3 paragraph explanation of what this routine does and when to use it.

## Parameters

| Name | I/O | Type | Description |
|------|-----|------|-------------|
| ARG1 | I   | ...  | ...         |

## Returns

Description of output values.

## Errors

List of exceptions/error conditions this routine can signal.

## Example Usage

Brief pseudocode or Fortran snippet showing typical usage.

## See Also

Related routines (from calls and called_by).

## Source

File: `path/to/file.f` | Lines: start-end
```

Rules:
- Extract parameter info from Brief_I/O and Detailed_Input/Output sections.
- Use modern terminology (explain Fortran idioms).
- Keep it concise but complete.
- Include the file:line reference at the bottom.
"""


def generate_doc(routine_name: str) -> dict:
    """Generate Markdown documentation for a routine.

    Returns:
        Dict with routine_name, markdown doc, and metadata.
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

    client = OpenAI(api_key=settings.openai_api_key)
    embed_resp = client.embeddings.create(
        input=name,
        model=settings.embedding_model,
        dimensions=settings.embedding_dimensions,
    )
    query_vec = embed_resp.data[0].embedding

    # Fetch routine chunks
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
        return {
            "routine_name": name,
            "markdown": f"# `{name}`\n\nNo documentation found for this routine.",
            "file_path": file_path,
        }

    # Assemble context
    context_parts = []
    start_line = 0
    end_line = 0

    for chunk in all_chunks:
        meta = chunk.metadata or {}
        text = meta.get("text", "")
        chunk_type = meta.get("chunk_type", "")
        if not start_line:
            start_line = meta.get("start_line", 0)
            end_line = meta.get("end_line", 0)
            file_path = meta.get("file_path", file_path)
        context_parts.append(f"--- {chunk_type} ---\n{text}")

    context = "\n\n".join(context_parts)
    dep_info = f"\nCalls: {', '.join(calls)}\nCalled by: {', '.join(callers)}"
    if name in (graph.aliases if 'graph' in dir() else {}):
        dep_info += f"\nENTRY point in: {actual_name}"

    # Generate documentation
    response = client.chat.completions.create(
        model=settings.llm_model,
        messages=[
            {"role": "system", "content": DOCGEN_SYSTEM_PROMPT},
            {"role": "user", "content": f"Generate documentation for `{name}`.\n\nSource Code:\n{context}\n\nDependencies:\n{dep_info}\n\nFile: {file_path} | Lines: {start_line}-{end_line}"},
        ],
        temperature=0.1,
        max_tokens=3000,
    )

    markdown = response.choices[0].message.content or ""

    return {
        "routine_name": name,
        "markdown": markdown,
        "file_path": file_path,
        "start_line": start_line,
        "end_line": end_line,
        "calls": calls,
        "called_by": callers,
        "usage": {
            "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
            "completion_tokens": response.usage.completion_tokens if response.usage else 0,
            "total_tokens": response.usage.total_tokens if response.usage else 0,
        },
    }
