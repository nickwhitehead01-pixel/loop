"""
Teacher endpoints — lesson management, analytics, chat assistant, and live transcription.

POST /teacher/lessons                              — upload lesson (PDF/DOCX/PPTX/TXT)
GET  /teacher/lessons                              — list uploaded lessons
GET  /teacher/lessons/{lesson_id}                  — lesson detail with summary
GET  /teacher/sessions                             — list all sessions
GET  /teacher/sessions/{session_id}/analytics      — aggregated pupil performance
GET  /teacher/students                             — list all pupils
GET  /teacher/students/{pupil_id}/progress         — per-pupil history across sessions
WS   /teacher/ws/{teacher_id}/chat                 — streaming AI chat assistant
WS   /teacher/ws/{teacher_id}/transcribe/{session_id} — live classroom transcription (Whisper)
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, WebSocket, WebSocketDisconnect
from sqlalchemy import select, func as sa_func
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.teacher_graph import run_teacher_agent
from app.agents.teacher_rag import process_lesson, summarise_lesson
from app.core.config import settings
from app.core.database import get_db
from app.services import transcription
from app.models.domain import (
    Conversation,
    Lesson,
    LessonSession,
    Message,
    PupilSessionSummary,
    QuizAttempt,
    QuizQuestion,
    Role,
    TeacherConversation,
    TranscriptChunk,
    User,
)
from app.models.schemas import (
    LessonResponse,
    SessionAnalytics,
    SessionResponse,
    StudentProgress,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/teacher", tags=["teacher"])


# ---------------------------------------------------------------------------
# Lessons
# ---------------------------------------------------------------------------

@router.post("/lessons", response_model=LessonResponse)
async def upload_lesson(
    file: UploadFile = File(...),
    title: str = Form(...),
    teacher_id: int = Form(...),
    session_id: int | None = Form(None),
    db: AsyncSession = Depends(get_db),
):
    """Upload a lesson file (PDF, DOCX, PPTX, TXT). Chunks, embeds, and stores."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")

    # Save the uploaded file
    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    file_path = upload_dir / file.filename
    content = await file.read()
    file_path.write_bytes(content)

    # Create lesson record
    lesson = Lesson(
        title=title,
        teacher_id=teacher_id,
        session_id=session_id,
        file_path=str(file_path),
    )
    db.add(lesson)
    await db.flush()
    await db.refresh(lesson)

    # Ingest: parse, chunk, embed, store
    await process_lesson(lesson.id, content, db, filename=file.filename)

    await db.commit()
    await db.refresh(lesson)
    return lesson


@router.get("/lessons", response_model=list[LessonResponse])
async def list_lessons(
    teacher_id: int | None = None,
    db: AsyncSession = Depends(get_db),
):
    query = select(Lesson).order_by(Lesson.created_at.desc())
    if teacher_id is not None:
        query = query.where(Lesson.teacher_id == teacher_id)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/lessons/{lesson_id}")
async def get_lesson_detail(
    lesson_id: int,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Lesson).where(Lesson.id == lesson_id))
    lesson = result.scalar_one_or_none()
    if not lesson:
        raise HTTPException(status_code=404, detail="Lesson not found")

    summary = await summarise_lesson(lesson_id, db)

    return {
        "id": lesson.id,
        "title": lesson.title,
        "teacher_id": lesson.teacher_id,
        "session_id": lesson.session_id,
        "file_path": lesson.file_path,
        "created_at": lesson.created_at,
        "summary": summary,
    }


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

@router.get("/sessions", response_model=list[SessionResponse])
async def list_sessions(
    teacher_id: int | None = None,
    db: AsyncSession = Depends(get_db),
):
    query = select(LessonSession).order_by(LessonSession.started_at.desc())
    if teacher_id is not None:
        query = query.where(LessonSession.teacher_id == teacher_id)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/sessions/{session_id}/analytics", response_model=SessionAnalytics)
async def session_analytics(
    session_id: int,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(LessonSession).where(LessonSession.id == session_id)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Aggregate summary data
    summary_result = await db.execute(
        select(
            sa_func.count(PupilSessionSummary.id).label("total_pupils"),
            sa_func.avg(PupilSessionSummary.understanding_score).label("avg_score"),
            sa_func.sum(PupilSessionSummary.questions_asked).label("total_questions"),
        ).where(PupilSessionSummary.session_id == session_id)
    )
    row = summary_result.one()

    # Quiz completion rate
    quiz_result = await db.execute(
        select(sa_func.count(QuizQuestion.id))
        .where(QuizQuestion.session_id == session_id)
    )
    total_questions = quiz_result.scalar() or 0

    completion_rate = None
    if total_questions > 0 and row.total_pupils:
        attempt_result = await db.execute(
            select(sa_func.count(sa_func.distinct(QuizAttempt.pupil_id)))
            .join(QuizQuestion, QuizAttempt.question_id == QuizQuestion.id)
            .where(QuizQuestion.session_id == session_id)
        )
        pupils_attempted = attempt_result.scalar() or 0
        completion_rate = pupils_attempted / row.total_pupils if row.total_pupils else 0

    return SessionAnalytics(
        session_id=session_id,
        title=session.title,
        total_pupils=row.total_pupils or 0,
        avg_understanding_score=float(row.avg_score) if row.avg_score else None,
        total_questions_asked=row.total_questions or 0,
        quiz_completion_rate=completion_rate,
    )


# ---------------------------------------------------------------------------
# Students
# ---------------------------------------------------------------------------

@router.get("/students", response_model=list[StudentProgress])
async def list_students(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(
            User.id.label("pupil_id"),
            User.name.label("pupil_name"),
            sa_func.count(Message.id).label("message_count"),
            sa_func.max(Message.created_at).label("last_active"),
        )
        .outerjoin(Conversation, Conversation.pupil_id == User.id)
        .outerjoin(Message, Message.conversation_id == Conversation.id)
        .where(User.role == Role.pupil)
        .group_by(User.id, User.name)
        .order_by(User.name)
    )
    rows = result.all()
    return [
        StudentProgress(
            pupil_id=r.pupil_id,
            pupil_name=r.pupil_name,
            message_count=r.message_count or 0,
            last_active=r.last_active,
        )
        for r in rows
    ]


@router.get("/students/{pupil_id}/progress")
async def student_progress(
    pupil_id: int,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(User).where(User.id == pupil_id, User.role == Role.pupil)
    )
    pupil = result.scalar_one_or_none()
    if not pupil:
        raise HTTPException(status_code=404, detail="Pupil not found")

    # Session summaries
    summaries_result = await db.execute(
        select(PupilSessionSummary)
        .where(PupilSessionSummary.pupil_id == pupil_id)
        .order_by(PupilSessionSummary.created_at.desc())
    )
    summaries = summaries_result.scalars().all()

    # Quiz performance
    quiz_result = await db.execute(
        select(
            sa_func.count(QuizAttempt.id).label("total_attempts"),
            sa_func.count(QuizAttempt.id).filter(QuizAttempt.is_correct.is_(True)).label("correct"),
        ).where(QuizAttempt.pupil_id == pupil_id)
    )
    quiz_row = quiz_result.one()

    # Message count
    msg_result = await db.execute(
        select(sa_func.count(Message.id))
        .join(Conversation, Message.conversation_id == Conversation.id)
        .where(Conversation.pupil_id == pupil_id)
    )
    total_messages = msg_result.scalar() or 0

    return {
        "pupil_id": pupil_id,
        "pupil_name": pupil.name,
        "total_messages": total_messages,
        "session_summaries": [
            {
                "session_id": s.session_id,
                "summary_text": s.summary_text,
                "understanding_score": s.understanding_score,
                "questions_asked": s.questions_asked,
                "key_topics": s.key_topics,
                "created_at": s.created_at,
            }
            for s in summaries
        ],
        "quiz_performance": {
            "total_attempts": quiz_row.total_attempts or 0,
            "correct_answers": quiz_row.correct or 0,
            "accuracy": (
                (quiz_row.correct or 0) / quiz_row.total_attempts
                if quiz_row.total_attempts
                else None
            ),
        },
    }


# ---------------------------------------------------------------------------
# Teacher AI assistant — WebSocket streaming chat (Phase 7)
# ---------------------------------------------------------------------------

@router.websocket("/ws/{teacher_id}/chat")
async def teacher_chat(
    websocket: WebSocket,
    teacher_id: int,
    db: AsyncSession = Depends(get_db),
):
    """
    Streaming chat for the teacher AI assistant.

    Client sends JSON:  {"message": "...", "conversation_id": 1}
    Server streams:     {"token": "...", "done": false}
    Final frame:        {"token": "", "done": true}

    If `conversation_id` is omitted a new conversation is created and its id
    is returned to the client before streaming begins:
        {"type": "conversation_created", "conversation_id": <id>}
    """
    await websocket.accept()
    logger.info("Teacher %d connected to chat", teacher_id)
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

            if not user_message:
                await websocket.send_json({"error": "Empty message"})
                continue

            if not conversation_id:
                conv = TeacherConversation(teacher_id=teacher_id)
                db.add(conv)
                await db.flush()
                conversation_id = conv.id
                await websocket.send_json({
                    "type": "conversation_created",
                    "conversation_id": conversation_id,
                })

            async for token in run_teacher_agent(
                user_message=user_message,
                conversation_id=conversation_id,
                teacher_id=teacher_id,
                db=db,
            ):
                await websocket.send_json({"token": token, "done": False})
            await websocket.send_json({"token": "", "done": True})

    except WebSocketDisconnect:
        logger.info("Teacher %d disconnected", teacher_id)


# ---------------------------------------------------------------------------
# Teacher live transcription — WebSocket (Phase 8+)
# ---------------------------------------------------------------------------

@router.websocket("/ws/{teacher_id}/transcribe/{session_id}")
async def teacher_transcribe(
    websocket: WebSocket,
    teacher_id: int,
    session_id: int,
    db: AsyncSession = Depends(get_db),
):
    """
    Live transcription for the teacher's classroom speech.

    Client sends binary audio chunks via WebSocket.
    Server streams back transcript results as JSON:
        {"type": "transcript", "text": "...", "language": "en", "timestamp_ms": 5000}

    Used during live lessons — teacher's voice is transcribed, embedded,
    and stored in TranscriptChunk for all pupils' agents to search/reference.
    """
    await websocket.accept()
    logger.info("Teacher %d starting live transcription for session %d", teacher_id, session_id)

    try:
        while True:
            message = await websocket.receive()

            # Binary audio chunk
            if "bytes" in message and message["bytes"]:
                audio_bytes = message["bytes"]
                try:
                    result = await transcription.transcribe_chunk(audio_bytes)
                    logger.info(
                        "Transcribed [lang=%s]: %.60s",
                        result.language,
                        result.text,
                    )

                    # Embed and store in TranscriptChunk
                    if result.text.strip():
                        from app.services import ollama_client

                        vector = await ollama_client.embed(result.text)
                        chunk = TranscriptChunk(
                            session_id=session_id,
                            content=result.text,
                            embedding=vector,
                            timestamp_ms=0,  # TODO: track cumulative timestamp
                        )
                        db.add(chunk)
                        await db.flush()

                        # Send back to teacher (and eventually broadcast to pupils)
                        await websocket.send_json({
                            "type": "transcript",
                            "text": result.text,
                            "language": result.language,
                            "timestamp_ms": 0,
                        })
                    else:
                        await websocket.send_json({
                            "type": "silent",
                            "message": "[silence detected]",
                        })

                except Exception as exc:
                    logger.error("Transcription error: %s", exc)
                    await websocket.send_json({"type": "error", "detail": str(exc)})
                    await db.rollback()

            # Text message: control command (optional)
            elif "text" in message:
                try:
                    data = json.loads(message["text"])
                    if data.get("type") == "end_session":
                        await db.commit()
                        await websocket.send_json({"type": "session_ended"})
                        break
                except (json.JSONDecodeError, KeyError):
                    pass

    except WebSocketDisconnect:
        await db.commit()
        logger.info("Teacher %d stopped transcribing session %d", teacher_id, session_id)

