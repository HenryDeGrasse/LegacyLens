"""LLM answer generation using retrieved context."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from openai import OpenAI

from app.config import settings


@dataclass
class AnswerResponse:
    """Structured response from the answer generator."""

    answer: str
    citations: list[dict] = field(default_factory=list)
    model: str = ""
    usage: dict = field(default_factory=dict)


SYSTEM_PROMPT = """\
You are a SPICE Toolkit expert assistant. You help developers understand NASA's SPICE \
Fortran 77 codebase by answering questions using the provided code context.

Rules:
1. Use ONLY the provided code context to answer. Do not make up information.
2. Always cite your sources using [file_path:start_line-end_line] format.
3. If the context doesn't contain enough information to fully answer, say so explicitly.
4. Explain Fortran 77 constructs in modern terms when helpful.
5. Be precise about routine names, arguments, and behavior.
6. When describing what a routine does, reference its Abstract if available.
7. For dependency questions, list the CALL targets found in the context.
8. Format code references with backticks: `ROUTINE_NAME`.
"""


def generate_answer(query: str, context: str) -> AnswerResponse:
    """Generate a grounded answer from retrieved context.

    Args:
        query: The user's natural language question.
        context: Assembled context from retrieved chunks.

    Returns:
        AnswerResponse with answer text, citations, and usage info.
    """
    client = OpenAI(api_key=settings.openai_api_key)

    user_prompt = f"Question: {query}\n\nCode Context:\n{context}"

    response = client.chat.completions.create(
        model=settings.llm_model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.1,
        max_tokens=2000,
    )

    answer_text = response.choices[0].message.content or ""

    # Extract citations from the answer (pattern: [file_path:line-line])
    citation_pattern = re.compile(r"\[([^:\]]+):(\d+)-(\d+)\]")
    citations = []
    for match in citation_pattern.finditer(answer_text):
        citations.append({
            "file_path": match.group(1),
            "start_line": int(match.group(2)),
            "end_line": int(match.group(3)),
        })

    return AnswerResponse(
        answer=answer_text,
        citations=citations,
        model=response.model,
        usage={
            "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
            "completion_tokens": response.usage.completion_tokens if response.usage else 0,
            "total_tokens": response.usage.total_tokens if response.usage else 0,
        },
    )
