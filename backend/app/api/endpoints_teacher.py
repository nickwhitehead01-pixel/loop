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

import asyncio
from datetime import datetime
from difflib import get_close_matches
import json
import logging
from pathlib import Path
import re
import time

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
import mimetypes
from sqlalchemy import select, func as sa_func
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.teacher_graph import run_teacher_agent
from app.agents.teacher_rag import process_lesson, summarise_lesson
from app.core.config import settings
from app.core.database import AsyncSessionLocal, get_db
from app.services import transcription
from app.services.vector_store import ACCEPTED_EXTENSIONS
from app.models.domain import (
    Conversation,
    Lesson,
    LessonChunk,
    LessonFile,
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


def _build_lesson_glossary(chunks: list[str]) -> dict[str, str]:
    """Build a small canonical-term glossary from lesson content for transcript cleanup."""
    joined = " ".join(chunks)
    phrases = re.findall(r"\b(?:[A-Z][a-z']+)(?:\s+[A-Z][a-z']+){0,2}\b", joined)

    glossary: dict[str, str] = {}
    for phrase in phrases:
        canonical = phrase.strip()
        if len(canonical) < 4:
            continue
        key = re.sub(r"[^a-z0-9]+", "", canonical.lower())
        if len(key) < 4 or key in glossary:
            continue
        glossary[key] = canonical
        if len(glossary) >= 12:
            break

    return glossary


def _fast_rationalize(text: str, glossary: dict[str, str]) -> str:
    """Apply lightweight cleanup and lesson-aware term correction with no model call."""
    cleaned = re.sub(r"\b(?:um+|uh+|er+|ah+)\b", "", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.;:-")
    if not cleaned:
        return ""

    words = cleaned.split()
    corrected: list[str] = []
    index = 0
    glossary_keys = list(glossary.keys())

    while index < len(words):
        replaced = False
        for size in (3, 2, 1):
            if index + size > len(words):
                continue
            phrase = " ".join(words[index:index + size])
            normalized = re.sub(r"[^a-z0-9]+", "", phrase.lower())
            if len(normalized) < 4:
                continue

            matches = get_close_matches(normalized, glossary_keys, n=1, cutoff=0.82)
            if matches:
                corrected.append(glossary[matches[0]])
                index += size
                replaced = True
                break

        if not replaced:
            corrected.append(words[index])
            index += 1

    cleaned = " ".join(corrected)
    cleaned = re.sub(r"\s+([,.;:!?])", r"\1", cleaned)
    if cleaned and cleaned[-1] not in ".!?":
        cleaned = f"{cleaned}."
    if cleaned:
        cleaned = cleaned[0].upper() + cleaned[1:]

    return cleaned

async def _rationalize_and_send(
    websocket: WebSocket,
    chunks: list[str],
    session_title: str,
    lesson_glossary: dict[str, str],
) -> None:
    """Clean up a batch of raw STT chunks quickly and send a rationalized frame."""
    joined = " ".join(chunks)
    cleaned = _fast_rationalize(joined, lesson_glossary)
    try:
        cleaned = cleaned.strip()
        if cleaned:
            await websocket.send_json({
                "type": "rationalized",
                "text": cleaned,
                "replaces": len(chunks),
            })
    except Exception as exc:
        logger.warning("Rationalization failed: %s", exc)


def _incremental_transcript(previous: str, current: str) -> str:
    previous = previous.strip()
    current = current.strip()
    if not current:
        return ""
    if not previous:
        return current
    if current.startswith(previous):
        return current[len(previous):].strip()

    max_overlap = min(len(previous), len(current))
    for overlap in range(max_overlap, 0, -1):
        if previous.endswith(current[:overlap]):
            return current[overlap:].strip()

    return current


async def _persist_transcript_chunk(session_id: int, content: str, timestamp_ms: int) -> None:
    """Persist transcript chunks asynchronously so websocket throughput stays realtime."""
    if not content:
        return

    from app.services import ollama_client

    try:
        vector = await ollama_client.embed(content)
        async with AsyncSessionLocal() as persist_db:
            chunk = TranscriptChunk(
                session_id=session_id,
                content=content,
                embedding=vector,
                timestamp_ms=timestamp_ms,
            )
            persist_db.add(chunk)
            await persist_db.commit()
    except Exception as exc:
        logger.warning(
            "Transcript persistence failed for session %d: %s",
            session_id,
            exc,
        )


# ---------------------------------------------------------------------------
# Lessons
# ---------------------------------------------------------------------------

MAX_FILE_BYTES = 25 * 1024 * 1024  # 25 MB per file


@router.post("/lessons", response_model=LessonResponse)
async def upload_lesson(
    files: list[UploadFile] = File(...),
    title: str = Form(...),
    teacher_id: int = Form(...),
    session_id: int | None = Form(None),
    db: AsyncSession = Depends(get_db),
):
    """Upload one or more lesson files (PDF, DOCX, PPTX, TXT) grouped under a single lesson."""
    if not files:
        raise HTTPException(status_code=400, detail="At least one file is required")

    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)

    # Read all file contents first (validates size before touching the DB)
    file_entries: list[tuple[str, bytes]] = []
    for upload in files:
        if not upload.filename:
            raise HTTPException(status_code=400, detail="Each file must have a filename")
        # Validate file extension early
        ext = "." + upload.filename.rsplit(".", 1)[-1].lower() if "." in upload.filename else ""
        if ext not in ACCEPTED_EXTENSIONS:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid file type '{ext}'. Accepted: {', '.join(sorted(ACCEPTED_EXTENSIONS))}"
            )
        content = await upload.read()
        if len(content) > MAX_FILE_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"{upload.filename} exceeds the 25 MB limit ({len(content) // (1024*1024)} MB)",
            )
        file_entries.append((upload.filename, content))

    # Create the lesson record — use the first filename as the primary file_path
    first_filename, first_content = file_entries[0]
    first_path = upload_dir / first_filename
    first_path.write_bytes(first_content)

    lesson = Lesson(
        title=title,
        teacher_id=teacher_id,
        session_id=session_id,
        file_path=str(first_path),
    )
    db.add(lesson)
    await db.flush()
    await db.refresh(lesson)

    # Persist each file and ingest its chunks
    for filename, content in file_entries:
        dest = upload_dir / filename
        if not dest.exists():
            dest.write_bytes(content)
        db.add(LessonFile(
            lesson_id=lesson.id,
            original_filename=filename,
            file_path=str(dest),
        ))
        try:
            await process_lesson(lesson.id, content, db, filename=filename)
        except Exception as e:
            logger.error(f"Failed to process lesson file {filename}: {e}", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail=f"Failed to process {filename}: {str(e)}"
            )

    await db.commit()

    # Generate and persist the lesson summary after all chunks are created
    try:
        summary = await summarise_lesson(lesson.id, db)
        lesson.summary = summary
        lesson.summary_generated_at = datetime.now()
        db.add(lesson)
        await db.commit()
        logger.info("Summary generated and persisted for lesson %d", lesson.id)
    except Exception as e:
        logger.error(f"Failed to generate summary for lesson {lesson.id}: {e}", exc_info=True)

    await db.commit()
    await db.refresh(lesson)

    # Attach computed file_count for the response
    lesson.file_count = len(file_entries)  # type: ignore[attr-defined]
    return lesson


@router.get("/lessons", response_model=list[LessonResponse])
async def list_lessons(
    teacher_id: int | None = None,
    db: AsyncSession = Depends(get_db),
):
    from app.models.domain import LessonFile as LF
    query = select(Lesson).order_by(Lesson.created_at.desc())
    if teacher_id is not None:
        query = query.where(Lesson.teacher_id == teacher_id)
    result = await db.execute(query)
    lessons = result.scalars().all()

    # Attach file counts
    if lessons:
        ids = [l.id for l in lessons]
        counts_result = await db.execute(
            select(LF.lesson_id, sa_func.count(LF.id).label("cnt"))
            .where(LF.lesson_id.in_(ids))
            .group_by(LF.lesson_id)
        )
        count_map = {row.lesson_id: row.cnt for row in counts_result}
        for lesson in lessons:
            lesson.file_count = count_map.get(lesson.id, 1)  # type: ignore[attr-defined]

    return lessons


@router.get("/lessons/{lesson_id}")
async def get_lesson_detail(
    lesson_id: int,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Lesson).where(Lesson.id == lesson_id))
    lesson = result.scalar_one_or_none()
    if not lesson:
        raise HTTPException(status_code=404, detail="Lesson not found")

    # Use persisted summary if available, otherwise generate on-demand (backward compatibility)
    summary = lesson.summary
    if not summary:
        try:
            summary = await summarise_lesson(lesson_id, db)
            # Persist the generated summary for next time
            if summary:
                lesson.summary = summary
                lesson.summary_generated_at = datetime.now()
                await db.commit()
        except Exception:
            logger.exception("summarise_lesson failed for lesson %d", lesson_id)
            summary = None

    # Load associated files
    files_result = await db.execute(
        select(LessonFile).where(LessonFile.lesson_id == lesson_id).order_by(LessonFile.id)
    )
    files = files_result.scalars().all()

    # Load indexed chunks (content only — skip the embedding vector)
    chunks_result = await db.execute(
        select(LessonChunk.id, LessonChunk.content)
        .where(LessonChunk.lesson_id == lesson_id)
        .order_by(LessonChunk.id)
    )
    chunks = [{"id": row.id, "content": row.content} for row in chunks_result]

    return {
        "id": lesson.id,
        "title": lesson.title,
        "teacher_id": lesson.teacher_id,
        "session_id": lesson.session_id,
        "file_path": lesson.file_path,
        "created_at": lesson.created_at,
        "summary": summary,
        "files": [
            {
                "id": f.id,
                "original_filename": f.original_filename,
                "created_at": f.created_at.isoformat(),
                "url": f"/teacher/files/{f.id}",
            }
            for f in files
        ],
        "chunks": chunks,
        "chunk_count": len(chunks),
    }


@router.delete("/lessons/{lesson_id}", status_code=204)
async def delete_lesson(
    lesson_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Delete a lesson and all its chunks/files (cascaded by FK)."""
    result = await db.execute(select(Lesson).where(Lesson.id == lesson_id))
    lesson = result.scalar_one_or_none()
    if not lesson:
        raise HTTPException(status_code=404, detail="Lesson not found")


@router.post("/lessons/{lesson_id}/files")
async def add_files_to_lesson(
    lesson_id: int,
    files: list[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Append one or more files to an existing lesson and index their content."""
    result = await db.execute(select(Lesson).where(Lesson.id == lesson_id))
    lesson = result.scalar_one_or_none()
    if not lesson:
        raise HTTPException(status_code=404, detail="Lesson not found")

    if not files:
        raise HTTPException(status_code=400, detail="At least one file is required")

    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)

    file_entries: list[tuple[str, bytes]] = []
    for upload in files:
        if not upload.filename:
            raise HTTPException(status_code=400, detail="Each file must have a filename")
        content = await upload.read()
        if len(content) > MAX_FILE_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"{upload.filename} exceeds the 25 MB limit ({len(content) // (1024*1024)} MB)",
            )
        file_entries.append((upload.filename, content))

    added = []
    for filename, content in file_entries:
        dest = upload_dir / filename
        if not dest.exists():
            dest.write_bytes(content)
        lf = LessonFile(
            lesson_id=lesson_id,
            original_filename=filename,
            file_path=str(dest),
        )
        db.add(lf)
        await db.flush()
        await db.refresh(lf)
        await process_lesson(lesson_id, content, db, filename=filename)
        added.append({
            "id": lf.id,
            "original_filename": lf.original_filename,
            "created_at": lf.created_at.isoformat(),
            "url": f"/teacher/files/{lf.id}",
        })

    await db.commit()
    return {"added": added, "count": len(added)}
    await db.delete(lesson)
    await db.commit()


@router.get("/files/{file_id}")
async def serve_lesson_file(
    file_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Stream a lesson file back to the browser with an inline Content-Disposition."""
    result = await db.execute(select(LessonFile).where(LessonFile.id == file_id))
    lesson_file = result.scalar_one_or_none()
    if not lesson_file:
        raise HTTPException(status_code=404, detail="File not found")

    path = Path(lesson_file.file_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="File no longer exists on disk")

    mime, _ = mimetypes.guess_type(lesson_file.original_filename)
    mime = mime or "application/octet-stream"

    # PDF and plain-text files render inline; everything else prompts a download
    inline_types = {"application/pdf", "text/plain"}
    disposition = "inline" if mime in inline_types else "attachment"

    return FileResponse(
        path=str(path),
        media_type=mime,
        headers={"Content-Disposition": f'{disposition}; filename="{lesson_file.original_filename}"'},
    )


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
    last_emitted_text = ""
    session_started_monotonic = time.monotonic()

    # Fetch session title and lesson material for rationalization context
    sess_result = await db.execute(select(LessonSession).where(LessonSession.id == session_id))
    _sess = sess_result.scalar_one_or_none()
    session_title = _sess.title if _sess else "Classroom lesson"

    # Gather a compact lesson glossary linked to this session.
    lesson_glossary: dict[str, str] = {}
    try:
        chunks_result = await db.execute(
            select(LessonChunk.content)
            .join(Lesson, Lesson.id == LessonChunk.lesson_id)
            .where(Lesson.session_id == session_id)
            .limit(12)
        )
        chunk_texts = [row[0] for row in chunks_result.all()]
        lesson_glossary = _build_lesson_glossary(chunk_texts)
    except Exception:
        pass  # Context is optional — never block transcription

    raw_buffer: list[str] = []
    rationalize_in_flight = False

    async def maybe_rationalize() -> None:
        nonlocal raw_buffer, rationalize_in_flight
        if rationalize_in_flight or len(raw_buffer) < 2:
            return

        batch = raw_buffer[:2]
        rationalize_in_flight = True
        try:
            await _rationalize_and_send(websocket, batch, session_title, lesson_glossary)
            raw_buffer = raw_buffer[len(batch):]
        finally:
            rationalize_in_flight = False
            if len(raw_buffer) >= 2:
                asyncio.create_task(maybe_rationalize())

    try:
        while True:
            message = await websocket.receive()

            # Binary audio chunk
            if "bytes" in message and message["bytes"]:
                audio_bytes = message["bytes"]
                try:
                    result = await transcription.transcribe_chunk(audio_bytes)
                    full_text = result.text.strip()
                    emit_text = ""
                    if full_text and full_text != last_emitted_text:
                        emit_text = full_text
                        last_emitted_text = full_text
                    logger.info(
                        "Transcribed [lang=%s chunk_len=%d emit_len=%d]: %.60s",
                        result.language,
                        len(full_text),
                        len(emit_text),
                        full_text,
                    )

                    # Send transcript to the teacher immediately so UI updates do not depend
                    # on embedding or DB latency.
                    timestamp_ms = int((time.monotonic() - session_started_monotonic) * 1000)
                    if emit_text:
                        await websocket.send_json({
                            "type": "transcript",
                            "text": emit_text,
                            "language": result.language,
                            "timestamp_ms": timestamp_ms,
                        })

                        # Persist asynchronously so embedding/database work does not block
                        # the next incoming audio chunk.
                        asyncio.create_task(_persist_transcript_chunk(session_id, emit_text, timestamp_ms))

                        # Buffer raw chunks and rationalize in small serialized batches.
                        raw_buffer.append(emit_text)
                        if len(raw_buffer) > 6:
                            raw_buffer = raw_buffer[-6:]
                        asyncio.create_task(maybe_rationalize())
                    else:
                        await websocket.send_json({
                            "type": "silent",
                            "message": "[silence detected]",
                        })

                except WebSocketDisconnect:
                    await db.commit()
                    logger.info("Teacher %d disconnected during transcription for session %d", teacher_id, session_id)
                    break
                except Exception as exc:
                    logger.error("Transcription error: %s", exc)
                    await db.rollback()
                    try:
                        await websocket.send_json({"type": "error", "detail": str(exc)})
                    except RuntimeError:
                        logger.info("Teacher %d socket already closed for session %d", teacher_id, session_id)
                        break

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

