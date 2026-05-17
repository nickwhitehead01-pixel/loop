"""
Background worker for post-upload lesson analysis.

Two responsibilities, in order:

1. Summarise newly-uploaded lessons → populates Lesson.summary.
2. Generate pre-computed live-lesson features (glossary + prompt-card
   library) → populates Lesson.glossary and Lesson.prompt_cards.

Step (2) is the heavier of the two — three Gemma calls per lesson
(glossary, prompt cards, plus an embedding per card). Doing this off the
live path is the whole point of the pre-compute architecture: by the time
the teacher starts a session, the matcher service can look up answers
from these fields with zero LLM round-trips.

The worker polls every POLL_INTERVAL_SECONDS seconds. Failures are
retried up to PRECOMPUTE_MAX_ATTEMPTS times per lesson, after which the
lesson is left in its current state and skipped on subsequent passes —
this prevents a permanently-broken upload (corrupted PDF, Gemma OOM on
a specific document) from spinning the worker forever.

The worker never crashes the FastAPI process.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import or_, select

from app.agents.teacher_rag import summarise_lesson
from app.core.database import AsyncSessionLocal
from app.models.domain import Lesson, LessonChunk
from app.services.precomputed_features import (
    generate_glossary,
    generate_prompt_cards_streaming,
    pre_answer_prompt_cards,
)

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 10

# Cap for total precompute attempts per lesson. Five gives transient Gemma
# issues plenty of room to recover (5 attempts × 10s = ~50s of retry window
# on top of whatever Gemma was doing) without burning cycles indefinitely
# on something that's genuinely broken.
PRECOMPUTE_MAX_ATTEMPTS = 5


# ---------------------------------------------------------------------------
# Per-lesson stages
# ---------------------------------------------------------------------------

async def _try_summarise(lesson_id: int) -> None:
    """Attempt to summarise *lesson_id* if it doesn't already have a summary.

    Owns its own DB session because the worker calls this from a poll-loop
    context with no request scope.
    """
    async with AsyncSessionLocal() as db:
        try:
            logger.info("Summary worker: generating summary for lesson %d", lesson_id)
            summary = await summarise_lesson(lesson_id, db)

            if summary and summary not in (
                "No content found for this lesson.", "Summary generation failed."
            ):
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


async def _try_precompute(lesson_id: int) -> None:
    """Attempt to generate the glossary + prompt-card library for *lesson_id*.

    On success, both fields are populated and `precomputed_features_at` is
    stamped. On failure, `precomputed_features_attempts` is incremented and
    we leave the JSON columns as-is; if a previous attempt produced a partial
    glossary we keep it rather than wiping it.
    """
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Lesson).where(Lesson.id == lesson_id))
        lesson = result.scalar_one_or_none()
        if not lesson:
            return
        if lesson.precomputed_features_attempts >= PRECOMPUTE_MAX_ATTEMPTS:
            # Catatonic-Gemma guard — don't re-enter the loop for this lesson.
            return

        # Load lesson chunk content. We feed text to the precompute service,
        # not chunk objects, so it doesn't need a DB session itself.
        chunks_result = await db.execute(
            select(LessonChunk.content)
            .where(LessonChunk.lesson_id == lesson_id)
            .order_by(LessonChunk.chunk_index)
        )
        chunk_texts = [row[0] for row in chunks_result.all() if row[0]]
        if not chunk_texts:
            logger.info(
                "Precompute worker: lesson %d has no chunk content — skipping",
                lesson_id,
            )
            return

        try:
            logger.info(
                "Precompute worker: generating features for lesson %d (attempt %d/%d)",
                lesson_id,
                lesson.precomputed_features_attempts + 1,
                PRECOMPUTE_MAX_ATTEMPTS,
            )
            content = "\n\n".join(c for c in chunk_texts if c.strip())

            # Run glossary concurrently with streaming card generation so
            # both Gemma calls are in flight at the same time.
            glossary_task = asyncio.create_task(generate_glossary(content))

            # Stream cards one-by-one, persisting each to DB immediately so
            # the SSE endpoint can forward it to the teacher's browser in
            # real time — giving a visible sign of progress during the wait.
            accumulated_cards: list[dict] = []
            async for card in generate_prompt_cards_streaming(content):
                accumulated_cards.append(card)
                # Reassign (not mutate) so SQLAlchemy detects the change.
                lesson.prompt_cards = list(accumulated_cards)
                await db.commit()
                logger.debug(
                    "Precompute worker: lesson %d — card %d persisted (%s)",
                    lesson_id, len(accumulated_cards), card.get("question", "")[:60],
                )

            glossary = await glossary_task

        except Exception:
            lesson.precomputed_features_attempts += 1
            await db.commit()
            logger.exception(
                "Precompute worker: failed for lesson %d (attempt %d/%d)",
                lesson_id,
                lesson.precomputed_features_attempts,
                PRECOMPUTE_MAX_ATTEMPTS,
            )
            return

        lesson.glossary = glossary
        # prompt_cards already updated incrementally; reassign once more with
        # the final list to ensure the last commit captured every card.
        lesson.prompt_cards = list(accumulated_cards)
        lesson.precomputed_features_at = datetime.now(tz=timezone.utc)
        # Reset attempts on success — if the teacher later re-uploads or we
        # add a force-regenerate endpoint, this gets a clean slate.
        lesson.precomputed_features_attempts = 0
        await db.commit()
        logger.info(
            "Precompute worker: lesson %d done — glossary=%d, cards=%d",
            lesson_id, len(glossary), len(accumulated_cards),
        )
        # Fire-and-forget: seed the semantic cache with pre-generated answers
        # for each prompt card. The inter-card sleep inside pre_answer_prompt_cards
        # keeps Ollama's queue open for live pupil requests throughout.
        asyncio.create_task(pre_answer_prompt_cards(lesson_id, accumulated_cards))
        logger.info(
            "Precompute worker: triggered card pre-answer for lesson %d (%d cards)",
            lesson_id, len(accumulated_cards),
        )


# ---------------------------------------------------------------------------
# Poll loop
# ---------------------------------------------------------------------------

async def _process_pending_lessons() -> None:
    """One pass: find lessons that need summary OR precompute work, and run it.

    A lesson is "pending" if it has at least one chunk AND either:
      - summary is null, OR
      - precomputed_features_at is null AND attempts < cap.
    """
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Lesson.id, Lesson.summary, Lesson.precomputed_features_at,
                   Lesson.precomputed_features_attempts)
            .where(
                Lesson.id.in_(
                    select(LessonChunk.lesson_id).distinct()
                )
            )
            .where(
                or_(
                    Lesson.summary.is_(None),
                    Lesson.precomputed_features_at.is_(None),
                )
            )
            .order_by(Lesson.created_at)
        )
        pending = result.all()

    for lesson_id, summary, precomputed_at, attempts in pending:
        if summary is None:
            await _try_summarise(lesson_id)
        if precomputed_at is None and attempts < PRECOMPUTE_MAX_ATTEMPTS:
            await _try_precompute(lesson_id)


async def start_summary_worker() -> None:
    """Persistent background task. Polls for pending lessons every
    POLL_INTERVAL_SECONDS seconds. Designed to run for the lifetime of the
    FastAPI process; cancellation is handled in the lifespan shutdown.
    """
    logger.info(
        "Lesson worker started (poll=%ds, max precompute attempts=%d)",
        POLL_INTERVAL_SECONDS, PRECOMPUTE_MAX_ATTEMPTS,
    )
    while True:
        try:
            await _process_pending_lessons()
        except Exception:
            logger.exception("Lesson worker: unexpected error in poll loop")
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
