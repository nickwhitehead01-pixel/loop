"""
Background worker: auto-summarise newly uploaded lessons.

start_summary_worker() — starts a persistent asyncio task that polls for
                         Lesson rows lacking a summary and calls
                         summarise_lesson() (per-chunk 2-sentence approach)
                         to populate Lesson.summary and
                         Lesson.summary_generated_at.

The worker runs every POLL_INTERVAL_SECONDS seconds. If Ollama is
unavailable or summarisation fails for a lesson, that lesson is skipped and
retried on the next poll cycle. The worker never crashes the FastAPI process.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.agents.teacher_rag import summarise_lesson
from app.core.database import AsyncSessionLocal
from app.models.domain import Lesson, LessonChunk

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 10


async def _process_pending_lessons() -> None:
    """Single poll pass: find all unsummarised lessons that have chunks and summarise them."""
    async with AsyncSessionLocal() as db:
        # Find lessons where summary is NULL and at least one chunk exists
        result = await db.execute(
            select(Lesson.id)
            .where(Lesson.summary.is_(None))
            .where(
                Lesson.id.in_(
                    select(LessonChunk.lesson_id).distinct()
                )
            )
            .order_by(Lesson.created_at)
        )
        pending_ids = result.scalars().all()

    for lesson_id in pending_ids:
        async with AsyncSessionLocal() as db:
            try:
                logger.info("Summary worker: generating summary for lesson %d", lesson_id)
                summary = await summarise_lesson(lesson_id, db)

                if summary and summary not in ("No content found for this lesson.", "Summary generation failed."):
                    result = await db.execute(select(Lesson).where(Lesson.id == lesson_id))
                    lesson = result.scalar_one_or_none()
                    if lesson and lesson.summary is None:
                        lesson.summary = summary
                        lesson.summary_generated_at = datetime.now(tz=timezone.utc)
                        await db.commit()
                        logger.info("Summary worker: saved summary for lesson %d", lesson_id)
                else:
                    logger.warning(
                        "Summary worker: unusable summary for lesson %d — will retry next cycle",
                        lesson_id,
                    )
            except Exception:
                logger.exception(
                    "Summary worker: failed to summarise lesson %d — will retry next cycle",
                    lesson_id,
                )


async def start_summary_worker() -> None:
    """
    Persistent background task. Polls for unsummarised lessons every
    POLL_INTERVAL_SECONDS seconds. Designed to run for the lifetime of the
    FastAPI process; cancelation is handled cleanly in the lifespan shutdown.
    """
    logger.info("Lesson summary worker started (poll interval: %ds)", POLL_INTERVAL_SECONDS)
    while True:
        try:
            await _process_pending_lessons()
        except Exception:
            logger.exception("Summary worker: unexpected error in poll loop")
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
