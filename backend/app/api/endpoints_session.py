"""
Live lesson session endpoints.

POST /session/start              — teacher starts a live session
WS   /ws/session/{id}/audio      — teacher streams audio for transcription
POST /session/{id}/end           — teacher ends session, triggers summaries + quiz
GET  /session/{id}/transcript    — get full transcript for a session
"""
from __future__ import annotations

import asyncio
import logging
import time
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy import select, func as sa_func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.domain import (
    Lesson,
    LessonSession,
    SessionStatus,
    TranscriptChunk,
)
from app.models.schemas import (
    SessionCreate,
    SessionResponse,
    TranscriptBroadcast,
    TranscriptChunkResponse,
)
from app.services import ollama_client
from app.services.transcription import transcribe_chunk

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/session", tags=["sessions"])

# Connected pupil WebSocket clients per session — {session_id: set(ws)}
_pupil_subscribers: dict[int, set[WebSocket]] = {}


def _get_subscribers(session_id: int) -> set[WebSocket]:
    return _pupil_subscribers.setdefault(session_id, set())


# ---------------------------------------------------------------------------
# POST /session/start
# ---------------------------------------------------------------------------

@router.post("/start", response_model=SessionResponse)
async def start_session(
    body: SessionCreate,
    db: AsyncSession = Depends(get_db),
):
    lesson_result = await db.execute(
        select(Lesson).where(
            Lesson.id == body.lesson_id,
            Lesson.teacher_id == body.teacher_id,
        )
    )
    lesson = lesson_result.scalar_one_or_none()
    if lesson is None:
        raise HTTPException(status_code=404, detail="Lesson not found for this teacher")

    session = LessonSession(
        teacher_id=body.teacher_id,
        title=body.title or f"Session: {lesson.title}",
        status=SessionStatus.live,
    )
    db.add(session)
    await db.flush()

    lesson.session_id = session.id

    await db.commit()
    await db.refresh(session)
    return session


# ---------------------------------------------------------------------------
# WS /ws/session/{session_id}/audio — teacher streams mic audio
# ---------------------------------------------------------------------------

@router.websocket("/ws/{session_id}/audio")
async def audio_stream(
    websocket: WebSocket,
    session_id: int,
    db: AsyncSession = Depends(get_db),
):
    """
    Teacher connects and sends binary audio frames.
    Each frame is transcribed, embedded, stored, and broadcast to pupils.
    """
    # Verify session exists and is live
    result = await db.execute(
        select(LessonSession).where(LessonSession.id == session_id)
    )
    session = result.scalar_one_or_none()
    if not session or session.status != SessionStatus.live:
        await websocket.close(code=4004, reason="Session not found or not live")
        return

    await websocket.accept()
    session_start = time.time()
    logger.info("Teacher audio stream connected for session %d", session_id)

    # ------------------------------------------------------------------
    # Bucket state — accumulates small VAD utterances before embedding.
    # The frontend VAD (and live captions) are completely unaffected; we
    # still ACK every utterance immediately.  Only the embed + DB write
    # is deferred until the bucket is large enough to be useful for RAG.
    # ------------------------------------------------------------------
    bucket_utterances: list[str] = []
    bucket_start_ms: int | None = None   # timestamp_ms of first utterance
    bucket_opened_at: float = time.time()

    async def flush_bucket() -> None:
        nonlocal bucket_utterances, bucket_start_ms, bucket_opened_at
        if not bucket_utterances:
            return
        joined = " ".join(bucket_utterances)
        vector = await ollama_client.embed(joined)
        db.add(TranscriptChunk(
            session_id=session_id,
            content=joined,
            embedding=vector,
            timestamp_ms=bucket_start_ms or 0,
        ))
        await db.flush()
        await db.commit()
        logger.debug(
            "Flushed transcript bucket for session %d: %d utterances / %d words",
            session_id, len(bucket_utterances),
            sum(len(u.split()) for u in bucket_utterances),
        )
        bucket_utterances = []
        bucket_start_ms = None
        bucket_opened_at = time.time()

    try:
        while True:
            audio_bytes = await websocket.receive_bytes()
            if not audio_bytes:
                continue

            # Transcribe
            try:
                result = await transcribe_chunk(audio_bytes)
            except Exception as e:
                logger.warning("Transcription error: %s", e)
                await websocket.send_json({"type": "error", "detail": str(e)})
                continue

            if not result.text:
                continue

            timestamp_ms = int((time.time() - session_start) * 1000)

            # ── Live caption path (unchanged) ──────────────────────────
            # Acknowledge to teacher immediately so captions stay snappy.
            await websocket.send_json({
                "type": "transcript",
                "content": result.text,
                "timestamp_ms": timestamp_ms,
            })

            # Broadcast to subscribed pupils
            broadcast = TranscriptBroadcast(
                content=result.text,
                timestamp_ms=timestamp_ms,
            )
            subscribers = _get_subscribers(session_id)
            dead: list[WebSocket] = []
            for ws in subscribers:
                try:
                    await ws.send_json(broadcast.model_dump())
                except Exception:
                    dead.append(ws)
            for ws in dead:
                subscribers.discard(ws)

            # ── Bucket accumulation ────────────────────────────────────
            bucket_utterances.append(result.text)
            if bucket_start_ms is None:
                bucket_start_ms = timestamp_ms

            bucket_words = sum(len(u.split()) for u in bucket_utterances)
            bucket_age = time.time() - bucket_opened_at
            if (
                bucket_words >= settings.transcript_bucket_min_words
                or bucket_age >= settings.transcript_bucket_max_seconds
            ):
                await flush_bucket()

    except WebSocketDisconnect:
        logger.info("Teacher audio stream disconnected for session %d", session_id)
        # Flush any remaining utterances so nothing is lost on disconnect.
        try:
            await flush_bucket()
        except Exception as flush_err:
            logger.warning("Could not flush bucket on disconnect for session %d: %s", session_id, flush_err)
    except Exception as e:
        logger.error("Audio stream error for session %d: %s", session_id, e)
        try:
            await flush_bucket()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# WS /ws/session/{session_id}/subscribe — pupils subscribe to live transcript
# ---------------------------------------------------------------------------

@router.websocket("/ws/{session_id}/subscribe")
async def subscribe_transcript(
    websocket: WebSocket,
    session_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Pupil connects to receive live transcript broadcasts."""
    result = await db.execute(
        select(LessonSession).where(LessonSession.id == session_id)
    )
    session = result.scalar_one_or_none()
    if not session:
        await websocket.close(code=4004, reason="Session not found")
        return

    await websocket.accept()
    subscribers = _get_subscribers(session_id)
    subscribers.add(websocket)
    logger.info("Pupil subscribed to session %d transcript", session_id)

    try:
        # Keep connection alive; pupil only receives, doesn't send
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        subscribers.discard(websocket)
        logger.info("Pupil unsubscribed from session %d", session_id)


# ---------------------------------------------------------------------------
# POST /session/{session_id}/end
# ---------------------------------------------------------------------------

@router.post("/{session_id}/end", response_model=SessionResponse)
async def end_session(
    session_id: int,
    db: AsyncSession = Depends(get_db),
):
    """End a live session. Triggers summary + quiz generation in background."""
    result = await db.execute(
        select(LessonSession).where(LessonSession.id == session_id)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.status == SessionStatus.ended:
        raise HTTPException(status_code=400, detail="Session already ended")

    session.status = SessionStatus.ended
    session.ended_at = sa_func.now()
    await db.flush()
    await db.commit()
    await db.refresh(session)

    # Trigger background summary + quiz generation
    # Import here to avoid circular imports
    from app.services.summary import generate_session_artifacts

    asyncio.create_task(generate_session_artifacts(session_id))

    # Clean up subscriber connections
    subscribers = _pupil_subscribers.pop(session_id, set())
    for ws in subscribers:
        try:
            await ws.send_json({"type": "session_ended"})
            await ws.close()
        except Exception:
            pass

    return session


# ---------------------------------------------------------------------------
# GET /session/{session_id}/transcript
# ---------------------------------------------------------------------------

@router.get("/{session_id}/transcript", response_model=list[TranscriptChunkResponse])
async def get_transcript(
    session_id: int,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(TranscriptChunk)
        .where(TranscriptChunk.session_id == session_id)
        .order_by(TranscriptChunk.timestamp_ms)
    )
    return result.scalars().all()


# ---------------------------------------------------------------------------
# GET /session/{session_id}
# ---------------------------------------------------------------------------

@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(
    session_id: int,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(LessonSession).where(LessonSession.id == session_id)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session
