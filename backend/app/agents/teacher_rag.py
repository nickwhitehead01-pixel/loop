"""
Teacher RAG pipeline.

Not a LangGraph graph — this is a sequential pipeline (no decision loop
needed). It uses gemma4:e2b for reasoning on structured document analysis.

process_lesson   — ingest document bytes into ChromaDB (500/50 token chunks)
summarise_lesson — iterate chunks → gemma4:e2b 2-sentence summary per chunk
                   → concatenate → return full lesson summary string
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.domain import LessonChunk
import asyncio

from app.services import ollama_client
from app.services.vector_store import ingest_lesson, ingest_lesson_images

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

_LESSON_SUMMARY_SYSTEM = """You are an expert educational assistant.
Read the provided excerpts from a lesson plan. Write a single, cohesive paragraph (exactly 3 to 4 sentences) summarizing the lesson for a teacher's dashboard.

RULES:
1. State the core mathematical or educational concept being taught.
2. Briefly describe the main activity or method the students will use (e.g., drawing tables, finding patterns).
3. DO NOT repeat yourself.
4. DO NOT mention copyright, licensing, or the Oak National Academy.
5. Write standard paragraph text only. No bullet points, no headings."""


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
    Parse *pdf_bytes* (PDF, DOCX, PPTX, or TXT), embed all 500/50-token text chunks,
    and store them in lesson_chunks. Returns the number of text chunks created.

    Image description is fired as a background asyncio task so the upload
    endpoint returns immediately after text ingestion completes.
    """
    n_text = await ingest_lesson(lesson_id, pdf_bytes, db, filename=filename)
    # Fire image description in the background — never blocks the upload response
    asyncio.create_task(
        ingest_lesson_images(lesson_id, pdf_bytes, filename=filename, chunk_offset=n_text)
    )
    return n_text


async def summarise_lesson(
    lesson_id: int,
    db: AsyncSession,
) -> str:
    """
    Summarise a lesson with a single Gemma call using skeleton sampling.
    Grabs the intro, evenly spaced middle sections, and the conclusion,
    ensuring the AI sees the core content while keeping the prompt under the token limit.
    """
    result = await db.execute(
        select(LessonChunk.content)
        .where(LessonChunk.lesson_id == lesson_id)
        .order_by(LessonChunk.chunk_index, LessonChunk.id)
    )
    chunks = result.scalars().all()

    if not chunks:
        return "No content found for this lesson."

    total = len(chunks)

    # Skeleton sampling: intro + learning objectives + two middle points + penultimate slide
    if total <= 4:
        parts = list(chunks)
    else:
        parts = [
            chunks[0],                      # Title / Intro
            chunks[1],                      # Usually Learning Objectives
            chunks[total // 3],             # 33% through the lesson
            chunks[(total * 2) // 3],       # 66% through the lesson
            chunks[-2],                     # Second to last (avoids copyright slide at -1)
        ]

    skeleton_text = "\n...\n".join(parts)

    # Hard cap to guard against unusually large chunks
    safe_text = skeleton_text[:8_000]

    try:
        messages = [{"role": "user", "content": f"Lesson excerpts:\n\n{safe_text}"}]
        summary = await ollama_client.generate_full(
            messages=messages,
            model=settings.ollama_model_teacher,
            system=_LESSON_SUMMARY_SYSTEM,
        )
        return summary.strip() if summary and summary.strip() else "Summary generation failed."
    except Exception:
        logger.warning("summarise_lesson failed for lesson %d", lesson_id)
        return "Summary generation failed."

