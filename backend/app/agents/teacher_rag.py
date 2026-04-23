"""
Teacher RAG pipeline.

Not a LangGraph graph — this is a sequential pipeline (no decision loop
needed). It uses gemma4:e2b for reasoning on structured document analysis.

process_lesson   — ingest document bytes into pgvector (500/50 token chunks)
summarise_lesson — iterate chunks → gemma4:e2b 2-sentence summary per chunk
                   → concatenate → return full lesson summary string
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.domain import LessonChunk
from app.services import ollama_client
from app.services.vector_store import ingest_lesson

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

_CHUNK_SUMMARY_SYSTEM = """You are a concise educational assistant.
Given a passage from a lesson document, write EXACTLY 2 sentences that capture
the most important idea in this passage. Be direct and clear — suitable for a
teacher who wants a quick overview. Do not add headings or bullet points."""


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
    Parse *pdf_bytes* (PDF, DOCX, PPTX, or TXT), embed all 500/50-token chunks,
    and store them in lesson_chunks. Returns the number of chunks created.
    """
    return await ingest_lesson(lesson_id, pdf_bytes, db, filename=filename)


async def summarise_lesson(
    lesson_id: int,
    db: AsyncSession,
) -> str:
    """
    Iterate over every chunk for *lesson_id* in order, ask gemma4:e2b for a
    2-sentence summary of each chunk, then concatenate into a full lesson summary.

    Keeping each Gemma call small (one 500-token chunk at a time) avoids
    hitting the context window on memory-constrained hardware.
    """
    result = await db.execute(
        select(LessonChunk.content)
        .where(LessonChunk.lesson_id == lesson_id)
        .order_by(LessonChunk.chunk_index, LessonChunk.id)
    )
    chunks = result.scalars().all()

    if not chunks:
        return "No content found for this lesson."

    mini_summaries: list[str] = []
    for i, chunk_text in enumerate(chunks):
        try:
            messages = [{"role": "user", "content": f"Passage:\n\n{chunk_text}"}]
            mini = await ollama_client.generate_full(
                messages=messages,
                model=settings.ollama_model_teacher,
                system=_CHUNK_SUMMARY_SYSTEM,
            )
            if mini and mini.strip():
                mini_summaries.append(mini.strip())
        except Exception:
            logger.warning(
                "Chunk summary failed for lesson %d chunk %d — skipping",
                lesson_id, i,
            )

    if not mini_summaries:
        return "Summary generation failed."

    return "\n\n".join(mini_summaries)

