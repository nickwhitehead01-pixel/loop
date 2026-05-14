"""
Semantic slide auto-sync.

During a live lesson the teacher's audio is bucketed and transcribed.
sync_slide_from_transcript() embeds each bucket and finds the closest
lesson chunk in ChromaDB.  If the distance is below SYNC_DISTANCE_THRESHOLD
the inferred slide/page number is stored in _slide_state so every subsequent
pupil-agent call can see which slide the teacher is currently on.

Design notes:
- State is in-memory only — a server restart clears it (acceptable; sessions
  are typically < 1 hour and the sync re-establishes itself within the first
  bucket of speech).
- slide_number = 0 means "position unknown" (DOCX / TXT files); those matches
  are silently skipped so the state is never polluted with a meaningless value.
- ChromaDB uses cosine distance (0 = identical, 2 = opposite).  The threshold
  of 0.35 is deliberately conservative so off-topic teacher speech (greetings,
  admin) does not produce false syncs.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain import Lesson
from app.services.chroma_client import lesson_chunks_col
from app.services.ollama_client import embed

logger = logging.getLogger(__name__)

# Cosine distance below which we accept the match as a reliable sync signal.
# Tune down (e.g. 0.25) to reduce false positives; tune up (e.g. 0.45) if
# sync rarely triggers on expected content.
SYNC_DISTANCE_THRESHOLD: float = 0.35


@dataclass(frozen=True)
class SlidePosition:
    """The most recently inferred lesson position for a session."""
    lesson_id: int
    lesson_title: str
    slide_number: int


# Per-session current position: {session_id: SlidePosition}
_slide_state: dict[int, SlidePosition] = {}


async def sync_slide_from_transcript(
    bucket_text: str,
    session_id: int,
    db: AsyncSession,
) -> None:
    """
    Infer the current slide/page number from *bucket_text* and update
    _slide_state[session_id] if a confident match is found.

    Called inline from flush_bucket() in endpoints_session.py.
    Failures are logged and swallowed — sync is best-effort and must
    never abort the transcription pipeline.
    """
    try:
        # Find all lessons attached to this session (id + title)
        result = await db.execute(
            select(Lesson.id, Lesson.title).where(Lesson.session_id == session_id)
        )
        lessons = result.all()  # list of (id, title)
        if not lessons:
            return
        lesson_ids = [row[0] for row in lessons]
        lesson_title_by_id = {row[0]: row[1] for row in lessons}

        # Embed the bucket text (reuses nomic-embed-text — same model as chunk embeddings)
        vector = await embed(bucket_text)

        col = lesson_chunks_col()

        # Build the lesson_id filter
        if len(lesson_ids) == 1:
            where_clause: dict = {"lesson_id": str(lesson_ids[0])}
        else:
            where_clause = {"lesson_id": {"$in": [str(lid) for lid in lesson_ids]}}

        results = col.query(
            query_embeddings=[vector],
            n_results=1,
            where=where_clause,
            include=["metadatas", "distances"],
        )

        distances = (results.get("distances") or [[]])[0]
        metadatas = (results.get("metadatas") or [[]])[0]

        if not distances or not metadatas:
            return

        distance = distances[0]
        meta = metadatas[0]
        slide_number_str = meta.get("slide_number", "0")
        matched_lesson_id_str = meta.get("lesson_id", "0")

        try:
            slide_number = int(slide_number_str)
        except (ValueError, TypeError):
            slide_number = 0

        try:
            matched_lesson_id = int(matched_lesson_id_str)
        except (ValueError, TypeError):
            matched_lesson_id = 0

        # Skip unknowns and low-confidence matches
        if slide_number == 0 or matched_lesson_id == 0:
            return
        if distance > SYNC_DISTANCE_THRESHOLD:
            logger.debug(
                "[slide_sync] session=%d — no sync (distance=%.3f > threshold=%.2f)",
                session_id,
                distance,
                SYNC_DISTANCE_THRESHOLD,
            )
            return

        lesson_title = lesson_title_by_id.get(matched_lesson_id, f"Lesson {matched_lesson_id}")
        new_position = SlidePosition(
            lesson_id=matched_lesson_id,
            lesson_title=lesson_title,
            slide_number=slide_number,
        )
        previous = _slide_state.get(session_id)
        _slide_state[session_id] = new_position

        if previous != new_position:
            logger.info(
                "[slide_sync] session=%d → '%s' slide %d (distance=%.3f)",
                session_id,
                lesson_title,
                slide_number,
                distance,
            )

    except Exception as exc:
        logger.warning("[slide_sync] session=%d failed (skipping)", session_id, exc_info=True)


def get_current_slide(session_id: int) -> SlidePosition | None:
    """Return the most recently inferred slide position, or None if not yet synced."""
    return _slide_state.get(session_id)


def clear_slide_state(session_id: int) -> None:
    """Remove slide state for a session. Call when the session ends."""
    _slide_state.pop(session_id, None)
