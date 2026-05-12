"""
Teacher-driven live quiz endpoints.

A quiz is opened by the teacher during a lesson, holds 1..N questions that
the teacher can suggest (via LLM) / edit / write manually, and sends each
question to pupils with a per-question time limit. Grading runs as a batch
after each question closes (added in a later step).

Endpoints in this module:

    POST /session/{sid}/quiz/start          — create the quiz for this session
    POST /session/{sid}/quiz/suggest        — LLM drafts the next question
    POST /session/{sid}/quiz/questions      — teacher creates a draft question
    GET  /session/{sid}/quiz                — full quiz state (teacher view)
    POST /quiz/questions/{qid}/send         — broadcast a draft to pupils
    POST /quiz/questions/{qid}/close        — close acceptance (no grading yet)

WebSocket broadcasting to pupils and the LLM grader are wired up in
subsequent build steps; this module currently only mutates DB state.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import AsyncSessionLocal, get_db
from app.models.domain import (
    LessonSession,
    Quiz,
    QuizMode,
    QuizQuestion,
    QuizQuestionSource,
    QuizQuestionStatus,
    SessionStatus,
    TranscriptChunk,
)
from app.models.schemas import (
    QuizResponse,
    QuizStart,
    QuizSuggestion,
    TeacherQuizQuestionCreate,
    TeacherQuizQuestionResponse,
)
from app.services import ollama_client

logger = logging.getLogger(__name__)

router = APIRouter(tags=["quiz"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _load_session(session_id: int, db: AsyncSession) -> LessonSession:
    result = await db.execute(
        select(LessonSession).where(LessonSession.id == session_id)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


async def _load_quiz_for_session(
    session_id: int, db: AsyncSession, *, with_questions: bool = False
) -> Quiz | None:
    stmt = select(Quiz).where(Quiz.session_id == session_id)
    if with_questions:
        stmt = stmt.options(selectinload(Quiz.questions))
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def _load_question(question_id: int, db: AsyncSession) -> QuizQuestion:
    result = await db.execute(
        select(QuizQuestion).where(QuizQuestion.id == question_id)
    )
    question = result.scalar_one_or_none()
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")
    return question


async def _full_transcript_text(session_id: int, db: AsyncSession) -> str:
    """Concatenate every transcript chunk for the session, in time order.

    The suggester uses the full lesson-so-far (decided in planning): teachers
    can quiz on anything they've covered, not just the last few minutes.
    """
    result = await db.execute(
        select(TranscriptChunk.content)
        .where(TranscriptChunk.session_id == session_id)
        .order_by(TranscriptChunk.timestamp_ms)
    )
    return "\n".join(row[0] for row in result.all())


# ---------------------------------------------------------------------------
# POST /session/{sid}/quiz/start
# ---------------------------------------------------------------------------

@router.post(
    "/session/{session_id}/quiz/start",
    response_model=QuizResponse,
)
async def start_quiz(
    session_id: int,
    body: QuizStart,
    db: AsyncSession = Depends(get_db),
):
    """Open the (single) quiz for this session.

    Idempotent: if a quiz already exists for the session it is returned
    unchanged — the teacher cannot accidentally reset their queued questions
    by tapping Start a second time. The mode is fixed at creation time.
    """
    session = await _load_session(session_id, db)
    if session.status == SessionStatus.ended:
        raise HTTPException(
            status_code=409,
            detail="Cannot start a quiz on an ended session",
        )

    existing = await _load_quiz_for_session(session_id, db, with_questions=True)
    if existing:
        return existing

    quiz = Quiz(
        session_id=session_id,
        mode=QuizMode(body.mode),
    )
    db.add(quiz)
    await db.flush()
    await db.refresh(quiz)
    # Eager-load the (empty) questions list so the response model is happy.
    await db.refresh(quiz, attribute_names=["questions"])
    await db.commit()
    return quiz


# ---------------------------------------------------------------------------
# GET /session/{sid}/quiz
# ---------------------------------------------------------------------------

@router.get(
    "/session/{session_id}/quiz",
    response_model=QuizResponse,
)
async def get_quiz(
    session_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Teacher view of the quiz — includes draft questions and correct answers."""
    quiz = await _load_quiz_for_session(session_id, db, with_questions=True)
    if not quiz:
        raise HTTPException(status_code=404, detail="No quiz started for this session")
    return quiz


# ---------------------------------------------------------------------------
# POST /session/{sid}/quiz/suggest
# ---------------------------------------------------------------------------

_SUGGEST_PROMPT = (
    "You are helping a teacher quiz their class during a live lesson.\n"
    "Given the lesson transcript so far, draft ONE short question that checks "
    "whether pupils have understood a key idea from the lesson. The question "
    "should have a clear, unambiguous correct answer that can be expressed in "
    "one sentence.\n\n"
    "Lesson transcript so far:\n"
    "{transcript}\n\n"
    "Reply with ONLY a JSON object with these keys:\n"
    '  "question_text"   — the question to ask the class\n'
    '  "correct_answer"  — the correct answer, one short sentence\n'
    '  "topic_tag"       — 1-3 words naming the concept being tested '
    "(e.g. \"photosynthesis\", \"long division\")\n\n"
    "Do not wrap the JSON in markdown. Do not add commentary."
)


def _parse_suggestion(raw: str) -> QuizSuggestion:
    """Extract the first JSON object from the LLM response."""
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start == -1 or end <= start:
        raise ValueError("No JSON object found in suggester response")
    data = json.loads(raw[start:end])
    return QuizSuggestion(
        question_text=str(data["question_text"]).strip(),
        correct_answer=str(data["correct_answer"]).strip(),
        topic_tag=(str(data["topic_tag"]).strip() if data.get("topic_tag") else None),
    )


@router.post(
    "/session/{session_id}/quiz/suggest",
    response_model=QuizSuggestion,
)
async def suggest_question(
    session_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Ask the LLM for a draft question based on the full lesson transcript.

    Does NOT persist anything. The teacher reviews the suggestion in the UI
    and may accept (POST /questions with source=ai_suggested), edit
    (source=ai_edited), or discard it.
    """
    # Confirm session exists / quiz has been started — the suggester only
    # makes sense once both are true.
    await _load_session(session_id, db)
    quiz = await _load_quiz_for_session(session_id, db)
    if not quiz:
        raise HTTPException(
            status_code=409,
            detail="Start a quiz before requesting suggestions",
        )

    transcript = await _full_transcript_text(session_id, db)
    if not transcript.strip():
        raise HTTPException(
            status_code=409,
            detail="No transcript yet — say something for the class first!",
        )

    prompt = _SUGGEST_PROMPT.format(transcript=transcript[:12000])
    try:
        raw = await ollama_client.generate_full(
            messages=[{"role": "user", "content": prompt}],
            model=settings.ollama_model_teacher,
            format="json",
        )
        return _parse_suggestion(raw)
    except Exception as e:
        logger.exception("Quiz suggestion failed for session %d", session_id)
        raise HTTPException(
            status_code=502,
            detail=f"LLM suggestion failed: {e}",
        )


# ---------------------------------------------------------------------------
# POST /session/{sid}/quiz/questions
# ---------------------------------------------------------------------------

@router.post(
    "/session/{session_id}/quiz/questions",
    response_model=TeacherQuizQuestionResponse,
)
async def create_question(
    session_id: int,
    body: TeacherQuizQuestionCreate,
    db: AsyncSession = Depends(get_db),
):
    """Persist a draft question. Status begins as 'draft' regardless of source.

    The teacher sends it to pupils explicitly via POST /quiz/questions/{qid}/send.
    """
    quiz = await _load_quiz_for_session(session_id, db)
    if not quiz:
        raise HTTPException(
            status_code=409,
            detail="Start a quiz before adding questions",
        )

    question = QuizQuestion(
        quiz_id=quiz.id,
        session_id=session_id,
        question_text=body.question_text.strip(),
        correct_answer=body.correct_answer.strip(),
        topic_tag=body.topic_tag.strip() if body.topic_tag else None,
        source=QuizQuestionSource(body.source),
        time_limit_seconds=body.time_limit_seconds,
        status=QuizQuestionStatus.draft,
    )
    db.add(question)
    await db.flush()
    await db.refresh(question)
    await db.commit()
    return question


# ---------------------------------------------------------------------------
# POST /quiz/questions/{qid}/send
# ---------------------------------------------------------------------------

@router.post(
    "/quiz/questions/{question_id}/send",
    response_model=TeacherQuizQuestionResponse,
)
async def send_question(
    question_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Broadcast a draft to pupils and start its timer.

    Pupil delivery via the session WebSocket is wired in a later step; for
    now this just transitions the question to 'sent' and stamps sent_at.
    Pupils' POST .../answer endpoint already gates on status == sent.
    """
    question = await _load_question(question_id, db)
    if question.status != QuizQuestionStatus.draft:
        raise HTTPException(
            status_code=409,
            detail=f"Question is not a draft (status: {question.status.value})",
        )

    question.status = QuizQuestionStatus.sent
    question.sent_at = datetime.now(timezone.utc)
    await db.flush()
    await db.refresh(question)
    await db.commit()

    # Broadcast the open question to every pupil in the session. The payload
    # intentionally omits correct_answer — pupils never see it. deadline_ts is
    # the unix-millis time after which the client should stop accepting input,
    # giving each pupil's countdown a single source of truth (server clock).
    from app.api.endpoints_session import broadcast_to_pupils
    deadline_ms = int(
        (question.sent_at.timestamp() + question.time_limit_seconds) * 1000
    )
    await broadcast_to_pupils(question.session_id, {
        "type": "quiz_question_opened",
        "question": {
            "id": question.id,
            "quiz_id": question.quiz_id,
            "session_id": question.session_id,
            "question_text": question.question_text,
            "topic_tag": question.topic_tag,
            "time_limit_seconds": question.time_limit_seconds,
            "deadline_ms": deadline_ms,
        },
    })

    # Schedule the auto-close. If the teacher manually closes first, the
    # auto-close finds the question already in 'closed' status and no-ops.
    asyncio.create_task(_auto_close_after(question.id, question.time_limit_seconds))

    return question


async def _auto_close_after(question_id: int, delay_seconds: int) -> None:
    """Background task: wait `delay_seconds`, then close the question if it's
    still open. Uses its own DB session because the request session is gone
    by the time this fires.
    """
    await asyncio.sleep(delay_seconds)
    async with AsyncSessionLocal() as db:
        try:
            question = await _load_question(question_id, db)
        except HTTPException:
            return
        if question.status != QuizQuestionStatus.sent:
            return  # teacher closed it manually first, or session ended
        await _close_question_internal(question, db)


async def _close_question_internal(
    question: QuizQuestion, db: AsyncSession
) -> None:
    """Shared close path used by both the manual endpoint and the auto-close
    timer. Transitions status, broadcasts to pupils, fires the grader.
    """
    question.status = QuizQuestionStatus.closed
    question.closed_at = datetime.now(timezone.utc)
    await db.flush()
    await db.refresh(question)
    await db.commit()

    # Tell pupils to stop accepting input on this question.
    from app.api.endpoints_session import broadcast_to_pupils
    await broadcast_to_pupils(question.session_id, {
        "type": "quiz_question_closed",
        "question_id": question.id,
    })

    # Fire the batch grader. Grades land on the teacher board afterwards via
    # the grader's own broadcast (see quiz_grader.py).
    from app.services.quiz_grader import grade_attempts_for_question
    asyncio.create_task(grade_attempts_for_question(question.id))


# ---------------------------------------------------------------------------
# POST /quiz/questions/{qid}/close
# ---------------------------------------------------------------------------

@router.post(
    "/quiz/questions/{question_id}/close",
    response_model=TeacherQuizQuestionResponse,
)
async def close_question(
    question_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Close a sent question — no further pupil answers are accepted.

    Usually triggered automatically when the time_limit_seconds elapses, but
    the teacher can also call this manually to cut a question short.
    """
    question = await _load_question(question_id, db)
    if question.status != QuizQuestionStatus.sent:
        raise HTTPException(
            status_code=409,
            detail=f"Question is not currently sent (status: {question.status.value})",
        )

    await _close_question_internal(question, db)
    return question
