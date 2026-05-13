"""
Live lesson session endpoints.

POST /session/start              — teacher starts a live session
WS   /ws/session/{id}/audio      — teacher streams audio for transcription
POST /session/{id}/end           — teacher ends session, triggers per-pupil summaries
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
from app.services.chroma_client import transcript_chunks_col
from app.services.transcription import transcribe_chunk
from app.services import slide_sync
from app.services.live_matcher import match_prompt_cards, match_tappable_terms

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/session", tags=["sessions"])

# Connected pupil WebSocket clients per session — {session_id: set(ws)}
_pupil_subscribers: dict[int, set[WebSocket]] = {}

# Connected teacher WebSocket clients per session. Separate from pupils so we
# never accidentally broadcast a pupil's quiz answer to other pupils — only
# the teacher's live answer board needs to see them.
_teacher_subscribers: dict[int, set[WebSocket]] = {}

# Per-session asyncio locks that serialise prompt-card generation.
# If a bucket arrives while the previous Gemma call is still running the new
# bucket is silently skipped — this prevents pile-ups during fast speech.
_prompt_locks: dict[int, asyncio.Lock] = {}

# Most-recent prompt cards per session — sent immediately to late joiners.
_latest_prompt_cards: dict[int, list[dict]] = {}

# Per-session asyncio locks that serialise tappable-term generation.
# Same skip-if-busy pattern as prompt cards.
_tappable_locks: dict[int, asyncio.Lock] = {}

# Cumulative tappable terms per session, keyed by lowercased term so re-runs
# can revise an existing entry's explanation. Sent immediately to late joiners
# so they don't have to wait for a fresh batch before seeing dotted underlines.
_session_tappable_terms: dict[int, dict[str, dict]] = {}

# Per-session cache of (glossary, prompt_card_library) for the lesson linked
# to the session. Loaded lazily on first match; we DO NOT hit the DB on every
# transcript chunk. Cleared when the session ends.
_session_lesson_features: dict[int, tuple[list, list]] = {}

# Per-session rolling window of recently-broadcast prompt-card ids, used to
# prevent the same card from re-firing in consecutive batches. Length 4 ≈
# one minute of cooldown at the current bucket cadence — long enough that
# the variety on screen stays fresh, short enough that the small library
# can recycle without going silent.
from collections import deque  # local import keeps top-of-file tidy
_session_recent_card_ids: dict[int, deque] = {}


def _get_subscribers(session_id: int) -> set[WebSocket]:
    return _pupil_subscribers.setdefault(session_id, set())


async def _broadcast(subscribers: set[WebSocket], payload: dict) -> None:
    """Send *payload* to every subscriber, dropping dead connections.

    Shared by the matcher functions below so each one doesn't reinvent the
    dead-WS cleanup loop. Iteration is safe against concurrent modification
    because we collect dead sockets into a separate list first.
    """
    dead: list[WebSocket] = []
    for ws in subscribers:
        try:
            await ws.send_json(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        subscribers.discard(ws)


def _get_teacher_subscribers(session_id: int) -> set[WebSocket]:
    return _teacher_subscribers.setdefault(session_id, set())


async def _broadcast(subscribers: set[WebSocket], payload: dict) -> None:
    """Send *payload* to every WS in *subscribers*, dropping dead connections.

    Shared helper so the per-event broadcast functions don't each reinvent the
    dead-WS cleanup loop. Safe against modification during iteration because
    we build the dead list separately.
    """
    dead: list[WebSocket] = []
    for ws in subscribers:
        try:
            await ws.send_json(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        subscribers.discard(ws)


async def broadcast_to_pupils(session_id: int, payload: dict) -> None:
    """Public: push *payload* to every pupil WS subscribed to *session_id*.

    Used by other modules (quiz endpoints, grader) that need to fan events out
    over the existing session websocket without owning the subscriber set.
    """
    await _broadcast(_get_subscribers(session_id), payload)


async def broadcast_to_teacher(session_id: int, payload: dict) -> None:
    """Public: push *payload* to the teacher's session WS (if connected).

    Used to stream pupil answers and grader verdicts onto the live answer
    board without exposing per-pupil data to other pupils.
    """
    await _broadcast(_get_teacher_subscribers(session_id), payload)


async def _get_lesson_features(session_id: int) -> tuple[list, list]:
    """Return ``(glossary, prompt_card_library)`` for the lesson tied to this
    session, cached after first SUCCESSFUL load.

    Lazy-loads from SQLite. Critically, we only cache when the lesson has
    actual precomputed data — empty results (worker hasn't finished yet,
    or all retries exhausted) are NOT cached. Otherwise a teacher who
    opens a session early would be stuck with empty features for the
    whole lesson even after regen completes.

    Cost of re-querying SQLite on every transcript chunk is negligible
    compared to the work the matcher is about to do.
    """
    cached = _session_lesson_features.get(session_id)
    if cached is not None:
        return cached

    # Local import to avoid pulling AsyncSessionLocal into this module's top
    # level; the DB session is short-lived and ephemeral.
    from app.core.database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Lesson.glossary, Lesson.prompt_cards)
            .where(Lesson.session_id == session_id)
        )
        row = result.one_or_none()
    glossary = (row[0] or []) if row else []
    prompt_cards = (row[1] or []) if row else []

    # Only memoise once we have something to remember. An empty result is
    # treated as transient — next call will re-check the DB. This costs us
    # one extra SELECT per chunk while precompute is still running, then
    # zero forever once the lesson is fully prepared.
    if glossary or prompt_cards:
        _session_lesson_features[session_id] = (glossary, prompt_cards)
        logger.warning(
            "[matcher] session=%d lesson features now loaded "
            "(%d glossary, %d cards)",
            session_id, len(glossary), len(prompt_cards),
        )
    return glossary, prompt_cards


async def _match_and_broadcast_tappable_terms(
    session_id: int, bucket_text: str
) -> None:
    """Match the lesson glossary against *bucket_text* and broadcast any
    newly-seen terms to all pupil subscribers.

    Cumulative on the server: the per-session ``_session_tappable_terms``
    map remembers everything we've broadcast already so late-joining
    pupils can be sent the full set, and so a term the teacher repeats
    later doesn't generate a second "tappable_terms" event.
    """
    glossary, _ = await _get_lesson_features(session_id)
    if not glossary:
        return
    matches = match_tappable_terms(bucket_text, glossary)
    if not matches:
        return

    store = _session_tappable_terms.setdefault(session_id, {})
    new_terms = []
    for entry in matches:
        key = entry["term"].lower()
        if key in store:
            continue  # already broadcast earlier in this session
        store[key] = entry
        new_terms.append(entry)
    if not new_terms:
        return

    payload = {"type": "tappable_terms", "terms": new_terms}
    subscribers = _get_subscribers(session_id)
    # WARNING level so it actually shows up under uvicorn's default config —
    # INFO gets dropped, and silence makes "are matchers firing?" impossible
    # to answer from the log alone. The volume here is naturally bounded
    # (each term broadcasts at most once per session).
    logger.warning(
        "[tappable] broadcast session=%d subscribers=%d new_terms=%d: %s",
        session_id, len(subscribers), len(new_terms),
        [t["term"] for t in new_terms],
    )
    await _broadcast(subscribers, payload)


async def _match_and_broadcast_prompt_cards(
    session_id: int, bucket_text: str
) -> None:
    """Semantic-match against the lesson's pre-computed prompt-card library
    and broadcast the top hits, with a per-session cooldown so the same
    card doesn't fire two batches in a row.
    """
    _, prompt_card_library = await _get_lesson_features(session_id)
    if not prompt_card_library:
        return

    recent_ids = _session_recent_card_ids.setdefault(session_id, deque(maxlen=4))
    cards = await match_prompt_cards(
        bucket_text,
        prompt_card_library,
        recently_shown_ids=set(recent_ids),
    )
    if not cards:
        return

    for card in cards:
        recent_ids.append(card["id"])

    _latest_prompt_cards[session_id] = cards
    payload = {"type": "prompt_cards", "cards": cards}
    subscribers = _get_subscribers(session_id)
    logger.warning(
        "[prompt_cards] broadcast session=%d subscribers=%d cards=%d: %s",
        session_id, len(subscribers), len(cards),
        [c["text"] for c in cards],
    )
    await _broadcast(subscribers, payload)


# Back-compat aliases so existing callers in endpoints_teacher.py (and the
# legacy /session/ws/{id}/audio path below) don't need to be touched.
# These names will go away in a follow-up cleanup.
_generate_and_broadcast_tappable_terms = _match_and_broadcast_tappable_terms
_generate_and_broadcast_prompt_cards = _match_and_broadcast_prompt_cards


# ---------------------------------------------------------------------------
# POST /session/open
# ---------------------------------------------------------------------------

@router.post("/open", response_model=SessionResponse)
async def open_session(
    body: SessionCreate,
    db: AsyncSession = Depends(get_db),
):
    """Teacher opens a lesson so pupils can join the waiting room.
    The session is created with *open* status; transcription has not started yet.
    Call POST /session/start to transition the same session to *live* (transcribing).
    """
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
        status=SessionStatus.open,
    )
    db.add(session)
    await db.flush()

    lesson.session_id = session.id

    await db.commit()
    await db.refresh(session)
    return session


# POST /session/start
# ---------------------------------------------------------------------------

@router.post("/start", response_model=SessionResponse)
async def start_session(
    body: SessionCreate,
    db: AsyncSession = Depends(get_db),
):
    """Legacy: creates a new session directly in *live* state (open + transcribing together).
    Kept for backward compatibility. Prefer POST /session/open then the WS transcription
    endpoint which auto-promotes the session to *live*.
    """
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
    # Verify session exists and is active (open or live)
    result = await db.execute(
        select(LessonSession).where(LessonSession.id == session_id)
    )
    session = result.scalar_one_or_none()
    if not session or session.status == SessionStatus.ended:
        await websocket.close(code=4004, reason="Session not found or not live")
        return

    await websocket.accept()
    session_start = time.time()
    logger.info("Teacher audio stream connected for session %d", session_id)

    # ------------------------------------------------------------------
    # Bucket state — accumulates utterances before embedding for ChromaDB.
    # SQLite receives each utterance immediately so the pupil agent can
    # query it in real-time without waiting for the bucket to fill.
    # ------------------------------------------------------------------
    bucket_utterances: list[str] = []
    bucket_start_ms: int | None = None   # timestamp_ms of first utterance
    bucket_opened_at: float = time.time()

    # ------------------------------------------------------------------
    # Prompt-card accumulator — fires every CARD_INTERVAL utterances,
    # independent of the ChromaDB bucket so cards appear quickly even
    # during short sessions.
    # ------------------------------------------------------------------
    _CARD_INTERVAL = 5
    card_utterances: list[str] = []

    async def flush_bucket() -> str | None:
        """Embed the accumulated bucket and write to ChromaDB only."""
        nonlocal bucket_utterances, bucket_start_ms, bucket_opened_at
        if not bucket_utterances:
            return None
        import uuid as _uuid
        joined = " ".join(bucket_utterances)
        vector = await ollama_client.embed(joined)
        # Store embedding in ChromaDB (SQLite write already happened per-utterance)
        transcript_chunks_col().add(
            ids=[str(_uuid.uuid4())],
            embeddings=[vector],
            documents=[joined],
            metadatas=[{
                "session_id": str(session_id),
                "timestamp_ms": str(bucket_start_ms or 0),
            }],
        )
        # Infer which slide/page the teacher is on and update shared state.
        # Awaited inline — adds ~30-50 ms but keeps ordering deterministic.
        await slide_sync.sync_slide_from_transcript(joined, session_id, db)
        logger.debug(
            "Flushed transcript bucket for session %d: %d utterances / %d words",
            session_id, len(bucket_utterances),
            sum(len(u.split()) for u in bucket_utterances),
        )
        bucket_utterances = []
        bucket_start_ms = None
        bucket_opened_at = time.time()
        return joined

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

            # ── Immediate SQLite write — no embedding, no bucket wait ───
            # This ensures the pupil agent can query the transcript in
            # real-time even before the ChromaDB bucket is flushed.
            db.add(TranscriptChunk(
                session_id=session_id,
                content=result.text,
                timestamp_ms=timestamp_ms,
            ))
            await db.commit()

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
            logger.info(
                "[broadcast] session=%d subscribers=%d chars=%d",
                session_id, len(subscribers), len(result.text),
            )
            dead: list[WebSocket] = []
            for ws in subscribers:
                try:
                    await ws.send_json(broadcast.model_dump())
                except Exception as send_err:
                    logger.warning(
                        "[broadcast] session=%d send failed, marking dead: %s",
                        session_id, send_err,
                    )
                    dead.append(ws)
            for ws in dead:
                subscribers.discard(ws)
            if dead:
                logger.info(
                    "[broadcast] session=%d removed %d dead -> total subscribers=%d",
                    session_id, len(dead), len(subscribers),
                )

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
                flushed_text = await flush_bucket()
                if flushed_text:
                    asyncio.create_task(
                        _generate_and_broadcast_prompt_cards(session_id, flushed_text)
                    )
                    # Bucket flush already covers the recent speech; reset
                    # the card-utterance accumulator to avoid double-firing.
                    card_utterances.clear()
                    continue

            # ── Prompt-card interval trigger ───────────────────────────
            card_utterances.append(result.text)
            if len(card_utterances) >= _CARD_INTERVAL:
                card_text = " ".join(card_utterances)
                card_utterances.clear()
                asyncio.create_task(
                    _generate_and_broadcast_prompt_cards(session_id, card_text)
                )

    except WebSocketDisconnect:
        logger.info("Teacher audio stream disconnected for session %d", session_id)
        # Flush any remaining utterances so nothing is lost on disconnect.
        try:
            flushed_text = await flush_bucket()
            if flushed_text:
                asyncio.create_task(
                    _generate_and_broadcast_prompt_cards(session_id, flushed_text)
                )
            elif card_utterances:
                # Bucket was empty (already flushed) but there are card utterances
                # that haven't fired yet — generate cards from those.
                card_text = " ".join(card_utterances)
                card_utterances.clear()
                asyncio.create_task(
                    _generate_and_broadcast_prompt_cards(session_id, card_text)
                )
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
    logger.info(
        "[subscribe] session=%d ADD -> total subscribers=%d",
        session_id, len(subscribers),
    )

    # Immediately deliver the latest prompt cards so late-joining pupils
    # don't have to wait for the next bucket flush.
    latest_cards = _latest_prompt_cards.get(session_id)
    if latest_cards:
        try:
            await websocket.send_json({"type": "prompt_cards", "cards": latest_cards})
        except Exception:
            pass

    # Same for cumulative tappable terms so any historical chunk already in
    # the pupil's REST-loaded transcript can be wrapped with dotted underlines.
    tappable_store = _session_tappable_terms.get(session_id)
    if tappable_store:
        try:
            await websocket.send_json({
                "type": "tappable_terms",
                "terms": list(tappable_store.values()),
            })
        except Exception:
            pass

    try:
        # Keep connection alive; pupil only receives, doesn't send
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        subscribers.discard(websocket)
        logger.info(
            "[subscribe] session=%d DISCONNECT -> total subscribers=%d",
            session_id, len(subscribers),
        )
    except Exception as exc:
        # Any other error would otherwise leave a zombie WS in the set, which
        # still gets iterated by the broadcast loop until a send finally errors.
        subscribers.discard(websocket)
        logger.warning(
            "[subscribe] session=%d ERROR %s -> total subscribers=%d",
            session_id, exc, len(subscribers),
        )


# ---------------------------------------------------------------------------
# WS /ws/session/{session_id}/teacher — teacher subscribes to quiz events
# ---------------------------------------------------------------------------

@router.websocket("/ws/{session_id}/teacher")
async def subscribe_teacher(
    websocket: WebSocket,
    session_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Teacher connects to receive live quiz events for this session.

    Distinct from the pupil /subscribe channel so pupil-specific events
    (quiz_answer_received, quiz_attempt_graded) never reach other pupils.
    """
    result = await db.execute(
        select(LessonSession).where(LessonSession.id == session_id)
    )
    session = result.scalar_one_or_none()
    if not session:
        await websocket.close(code=4004, reason="Session not found")
        return

    await websocket.accept()
    subscribers = _get_teacher_subscribers(session_id)
    subscribers.add(websocket)
    logger.info(
        "[teacher-ws] session=%d ADD -> total teacher subscribers=%d",
        session_id, len(subscribers),
    )

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        subscribers.discard(websocket)
        logger.info(
            "[teacher-ws] session=%d DISCONNECT -> total teacher subscribers=%d",
            session_id, len(subscribers),
        )
    except Exception as exc:
        subscribers.discard(websocket)
        logger.warning(
            "[teacher-ws] session=%d ERROR %s -> total teacher subscribers=%d",
            session_id, exc, len(subscribers),
        )


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

    # Trigger background per-pupil summary generation.
    # (Auto-quiz at session end is removed — quizzes are teacher-driven live.)
    # Import here to avoid circular imports.
    from app.services.summary import generate_session_artifacts

    asyncio.create_task(generate_session_artifacts(session_id))

    # Clean up prompt-card state for this session
    _prompt_locks.pop(session_id, None)
    _latest_prompt_cards.pop(session_id, None)

    # Clean up tappable-terms state for this session
    _tappable_locks.pop(session_id, None)
    _session_tappable_terms.pop(session_id, None)

    # Clean up live-matcher state for this session
    _session_lesson_features.pop(session_id, None)
    _session_recent_card_ids.pop(session_id, None)

    # Clean up slide-sync state
    slide_sync.clear_slide_state(session_id)

    # Clean up subscriber connections (both pupils and teacher).
    for sub_dict in (_pupil_subscribers, _teacher_subscribers):
        subscribers = sub_dict.pop(session_id, set())
        for ws in list(subscribers):  # copy to avoid Set changed size during iteration
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
