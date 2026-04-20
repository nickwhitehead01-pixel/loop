"""
Teacher RAG pipeline.

Not a LangGraph graph — this is a sequential pipeline (no decision loop
needed). It uses gemma4:e2b for reasoning on structured document analysis.

process_lesson   — ingest PDF bytes into pgvector
summarise_lesson — fetch all chunks → gemma4:e2b → structured summary
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.domain import LessonChunk
from app.services import ollama_client
from app.services.vector_store import ingest_lesson


# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

_SUMMARISE_SYSTEM = """You are an expert educational assistant helping teachers.
Given lesson content extracted from a PDF, produce a structured summary with:

1. **Topic** — one sentence describing the lesson subject
2. **Key concepts** — bullet list of the 5–8 most important ideas
3. **Learning objectives** — what pupils should understand after studying this
4. **Suggested discussion questions** — 3 questions a teacher could ask the class

Be concise and use language appropriate for secondary school level.
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def process_lesson(
    lesson_id: int,
    pdf_bytes: bytes,
    db: AsyncSession,
    filename: str = "upload.pdf",
) -> int:
    """
    Parse *pdf_bytes* (PDF, DOCX, PPTX, or TXT), embed all chunks,
    and store them in lesson_chunks. Returns the number of chunks created.
    """
    return await ingest_lesson(lesson_id, pdf_bytes, db, filename=filename)


async def summarise_lesson(
    lesson_id: int,
    db: AsyncSession,
) -> str:
    """
    Fetch all chunks for *lesson_id*, concatenate them, and ask gemma4:e2b
    to produce a structured lesson summary.
    Returns the summary as a string.
    """
    result = await db.execute(
        select(LessonChunk.content)
        .where(LessonChunk.lesson_id == lesson_id)
        .order_by(LessonChunk.id)
    )
    chunks = result.scalars().all()

    if not chunks:
        return "No content found for this lesson."

    # Concatenate all chunks — gemma4:e2b has a 32K context window
    full_text = "\n\n".join(chunks)

    # Truncate to ~12 000 chars to stay safely within context limits
    if len(full_text) > 12_000:
        full_text = full_text[:12_000] + "\n\n[content truncated]"

    messages = [{"role": "user", "content": f"Lesson content:\n\n{full_text}"}]

    summary = await ollama_client.generate_full(
        messages=messages,
        model=settings.ollama_model_teacher,
        system=_SUMMARISE_SYSTEM,
    )
    return summary

