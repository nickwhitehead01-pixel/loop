"""
Pupil endpoints — chat, sessions, summaries, quizzes.

WS   /ws/pupil/{pupil_id}/chat               — streaming chat with the AI tutor
GET  /pupil/{pupil_id}/sessions               — list sessions the pupil participated in
GET  /pupil/{pupil_id}/sessions/{sid}/summary — personal summary for a session
GET  /pupil/{pupil_id}/sessions/{sid}/quiz    — quiz questions for a session
POST /pupil/{pupil_id}/quiz/{qid}/answer      — submit a quiz answer
GET  /pupil/{pupil_id}/lessons                — list available lessons
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.pupil_graph import run_pupil_agent
from app.core.database import get_db
from app.models.domain import (
    Conversation,
    Lesson,
    LessonSession,
    PupilSessionSummary,
    QuizAttempt,
    QuizQuestion,
    QuizQuestionStatus,
    SessionStatus,
)
from app.models.schemas import (
    LessonResponse,
    PupilSessionSummaryResponse,
    QuizAnswerSubmit,
    QuizAttemptResponse,
    QuizQuestionResponse,
)
from app.services.ollama_client import get_client as get_http_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pupil", tags=["pupil"])


# ---------------------------------------------------------------------------
# WebSocket chat
# ---------------------------------------------------------------------------

@router.websocket("/ws/{pupil_id}/chat")
async def pupil_chat(
    websocket: WebSocket,
    pupil_id: int,
    db: AsyncSession = Depends(get_db),
):
    """
    Streaming chat with the pupil AI tutor.

    Client sends JSON: {"message": "...", "conversation_id": 1, "session_id": null}
    Server streams back JSON: {"token": "...", "done": false}
    Final message: {"token": "", "done": true}
    """
    await websocket.accept()
    http_client = get_http_client()
    logger.info("Pupil %d connected to chat", pupil_id)

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({"error": "Invalid JSON"})
                continue

            user_message = data.get("message", "").strip()
            conversation_id = data.get("conversation_id")
            session_id = data.get("session_id")

            # Auto-resolve session_id if the client didn't provide one.
            # Priority: (1) live session, (2) most recent session of any status.
            # This means pupils never need to enter a session ID, and can ask
            # about transcripts both during and after a lesson.
            if not session_id:
                live_result = await db.execute(
                    select(LessonSession.id)
                    .where(LessonSession.status == SessionStatus.live)
                    .order_by(LessonSession.started_at.desc())
                    .limit(1)
                )
                live_id = live_result.scalar_one_or_none()
                if live_id is not None:
                    session_id = live_id
                    logger.info("Auto-resolved live session %d for pupil %d", session_id, pupil_id)
                else:
                    # No live session — fall back to the most recent session.
                    recent_result = await db.execute(
                        select(LessonSession.id)
                        .order_by(LessonSession.started_at.desc())
                        .limit(1)
                    )
                    recent_id = recent_result.scalar_one_or_none()
                    if recent_id is not None:
                        session_id = recent_id
                        logger.info(
                            "Auto-resolved recent session %d for pupil %d (no live session)",
                            session_id, pupil_id,
                        )

            if not user_message:
                await websocket.send_json({"error": "Empty message"})
                continue

            # Auto-create conversation if none provided
            if not conversation_id:
                conv = Conversation(pupil_id=pupil_id, session_id=session_id)
                db.add(conv)
                await db.flush()
                conversation_id = conv.id
                await websocket.send_json({
                    "type": "conversation_created",
                    "conversation_id": conversation_id,
                })

            async for token in run_pupil_agent(
                user_message=user_message,
                conversation_id=conversation_id,
                pupil_id=pupil_id,
                db=db,
                http_client=http_client,
                session_id=session_id,
            ):
                await websocket.send_json({"token": token, "done": False})

            await websocket.send_json({"token": "", "done": True})

    except WebSocketDisconnect:
        logger.info("Pupil %d disconnected from chat", pupil_id)
    except Exception:
        logger.exception("Chat error for pupil %d", pupil_id)
        try:
            await websocket.close(code=1011)
        except Exception:
            pass  # client already disconnected


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

@router.get("/{pupil_id}/sessions")
async def list_pupil_sessions(
    pupil_id: int,
    db: AsyncSession = Depends(get_db),
):
    """List every lesson session the pupil can join or replay.

    Returns all sessions ordered newest-first so the pupil app can show
    live sessions to attend and ended ones to revisit. The ``pupil_id``
    is kept in the path for symmetry with other pupil routes and to allow
    future per-pupil filtering, but currently does not constrain results.
    """
    result = await db.execute(
        select(LessonSession).order_by(LessonSession.started_at.desc())
    )
    sessions = result.scalars().all()
    return [
        {
            "id": s.id,
            "title": s.title,
            "status": s.status.value,
            "started_at": s.started_at,
            "ended_at": s.ended_at,
        }
        for s in sessions
    ]


# ---------------------------------------------------------------------------
# Summaries
# ---------------------------------------------------------------------------

@router.get(
    "/{pupil_id}/sessions/{session_id}/summary",
    response_model=PupilSessionSummaryResponse,
)
async def get_pupil_summary(
    pupil_id: int,
    session_id: int,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(PupilSessionSummary).where(
            PupilSessionSummary.pupil_id == pupil_id,
            PupilSessionSummary.session_id == session_id,
        )
    )
    summary = result.scalar_one_or_none()
    if not summary:
        raise HTTPException(status_code=404, detail="Summary not found")
    return summary


# ---------------------------------------------------------------------------
# Quizzes
# ---------------------------------------------------------------------------

@router.get(
    "/{pupil_id}/sessions/{session_id}/quiz",
    response_model=list[QuizQuestionResponse],
)
async def get_session_quiz(
    pupil_id: int,
    session_id: int,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(QuizQuestion)
        .where(QuizQuestion.session_id == session_id)
        .order_by(QuizQuestion.id)
    )
    return result.scalars().all()


@router.post(
    "/{pupil_id}/quiz/{question_id}/answer",
    response_model=QuizAttemptResponse,
)
async def submit_quiz_answer(
    pupil_id: int,
    question_id: int,
    body: QuizAnswerSubmit,
    db: AsyncSession = Depends(get_db),
):
    # Verify question exists and is currently accepting answers. Pupils can
    # only submit while the question is in 'sent' status — drafts haven't been
    # broadcast yet, and closed questions are past the deadline.
    result = await db.execute(
        select(QuizQuestion).where(QuizQuestion.id == question_id)
    )
    question = result.scalar_one_or_none()
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")
    if question.status != QuizQuestionStatus.sent:
        raise HTTPException(
            status_code=409,
            detail=f"Question is not accepting answers (status: {question.status.value})",
        )

    # Grade is left null at submit time. The LLM grader runs in a batch once
    # the question closes (see /quiz/questions/{qid}/close in the upcoming
    # teacher endpoints) and populates grade + rationale for every attempt.
    attempt = QuizAttempt(
        question_id=question_id,
        pupil_id=pupil_id,
        pupil_answer=body.pupil_answer,
        grade=None,
        grader_rationale=None,
    )
    db.add(attempt)
    await db.flush()
    await db.refresh(attempt)
    await db.commit()

    # Push the answer onto the teacher's live answer board immediately. Grade
    # is null at this point — the grade chip appears later when the grader
    # runs (post-close) and broadcasts quiz_attempt_graded.
    from app.api.endpoints_session import broadcast_to_teacher
    await broadcast_to_teacher(question.session_id, {
        "type": "quiz_answer_received",
        "attempt": {
            "id": attempt.id,
            "question_id": question_id,
            "pupil_id": pupil_id,
            "pupil_answer": attempt.pupil_answer,
            "submitted_at": attempt.submitted_at.isoformat() if attempt.submitted_at else None,
        },
    })

    return attempt


# ---------------------------------------------------------------------------
# Lessons
# ---------------------------------------------------------------------------

@router.get("/{pupil_id}/lessons", response_model=list[LessonResponse])
async def list_pupil_lessons(
    pupil_id: int,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Lesson).order_by(Lesson.created_at.desc())
    )
    return result.scalars().all()

