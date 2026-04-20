"""
Post-session artifact generation.

Called when a teacher ends a live session — generates:
1. Per-pupil personalised summaries with understanding scores
2. Auto-generated quiz questions from the transcript
"""
from __future__ import annotations

import json
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.models.domain import (
    Conversation,
    LessonSession,
    Message,
    MessageRole,
    PupilSessionSummary,
    QuizQuestion,
    TranscriptChunk,
)
from app.services import ollama_client
from app.core.config import settings

logger = logging.getLogger(__name__)


async def _get_transcript_text(session_id: int, db: AsyncSession) -> str:
    """Load and concatenate all transcript chunks for a session."""
    result = await db.execute(
        select(TranscriptChunk.content, TranscriptChunk.timestamp_ms)
        .where(TranscriptChunk.session_id == session_id)
        .order_by(TranscriptChunk.timestamp_ms)
    )
    rows = result.all()
    if not rows:
        return ""
    parts = []
    for content, ts_ms in rows:
        mins, secs = divmod(ts_ms // 1000, 60)
        parts.append(f"[{mins:02d}:{secs:02d}] {content}")
    return "\n".join(parts)


async def _generate_quiz_questions(session_id: int, transcript: str, db: AsyncSession) -> None:
    """Ask the LLM to generate 3-5 quiz questions from the transcript."""
    if not transcript:
        return

    prompt = (
        "Based on this lesson transcript, generate 3-5 quiz questions to test "
        "student understanding. Each question should have a clear correct answer.\n\n"
        f"Transcript:\n{transcript[:8000]}\n\n"
        "Reply with ONLY a JSON array of objects, each with 'question' and 'answer' keys.\n"
        'Example: [{"question": "What is photosynthesis?", "answer": "The process by which plants convert sunlight into energy"}]'
    )

    try:
        raw = await ollama_client.generate_full(
            messages=[{"role": "user", "content": prompt}],
            model=settings.ollama_model_teacher,
        )
        start = raw.find("[")
        end = raw.rfind("]") + 1
        if start != -1 and end > start:
            questions = json.loads(raw[start:end])
            for q in questions:
                if isinstance(q, dict) and "question" in q and "answer" in q:
                    db.add(QuizQuestion(
                        session_id=session_id,
                        question_text=q["question"],
                        correct_answer=q["answer"],
                    ))
            await db.flush()
    except Exception as e:
        logger.warning("Quiz generation failed for session %d: %s", session_id, e)


async def _generate_pupil_summary(
    session_id: int,
    pupil_id: int,
    transcript: str,
    pupil_messages: list[str],
    session_duration_seconds: int,
    db: AsyncSession,
) -> None:
    """Generate a personalised summary for one pupil."""
    pupil_context = "\n".join(f"- Pupil: {m}" for m in pupil_messages) if pupil_messages else "No questions asked."

    prompt = (
        "You are an educational AI assistant. A lesson just ended.\n\n"
        f"Lesson transcript (teacher):\n{transcript[:6000]}\n\n"
        f"Pupil's questions during the lesson:\n{pupil_context}\n\n"
        "Generate a personalised summary for this pupil. Include:\n"
        "1. Key takeaways from the lesson (3-5 bullet points)\n"
        "2. Areas where the pupil seemed confused (based on their questions)\n"
        "3. Helpful tips and pointers for further study\n"
        "4. Suggested next steps\n\n"
        "Also assess the pupil's understanding on a scale from 0.0 to 1.0 "
        "and list the main topics discussed.\n\n"
        "Reply with a JSON object with keys: 'summary', 'understanding_score' (float 0-1), "
        "'key_topics' (list of strings), 'topics_discussed' (list of strings).\n"
    )

    try:
        raw = await ollama_client.generate_full(
            messages=[{"role": "user", "content": prompt}],
            model=settings.ollama_model_teacher,
        )
        # Parse JSON from response
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start != -1 and end > start:
            data = json.loads(raw[start:end])
        else:
            data = {"summary": raw}

        db.add(PupilSessionSummary(
            session_id=session_id,
            pupil_id=pupil_id,
            summary_text=data.get("summary", raw),
            key_topics=data.get("key_topics"),
            understanding_score=data.get("understanding_score"),
            questions_asked=len(pupil_messages),
            topics_discussed=data.get("topics_discussed"),
            session_duration_seconds=session_duration_seconds,
        ))
        await db.flush()
    except Exception as e:
        logger.warning(
            "Summary generation failed for session %d, pupil %d: %s",
            session_id, pupil_id, e,
        )


async def generate_session_artifacts(session_id: int) -> None:
    """
    Background task: generate quiz questions and per-pupil summaries.
    Uses its own DB session since it runs after the endpoint returns.
    """
    async with AsyncSessionLocal() as db:
        try:
            # Load session info
            result = await db.execute(
                select(LessonSession).where(LessonSession.id == session_id)
            )
            session = result.scalar_one_or_none()
            if not session:
                logger.error("Session %d not found for artifact generation", session_id)
                return

            # Calculate duration
            duration_seconds = 0
            if session.started_at and session.ended_at:
                duration_seconds = int((session.ended_at - session.started_at).total_seconds())

            # Get transcript
            transcript = await _get_transcript_text(session_id, db)
            if not transcript:
                logger.info("No transcript for session %d, skipping artifacts", session_id)
                return

            # Generate quiz questions
            await _generate_quiz_questions(session_id, transcript, db)

            # Find all pupils who participated (had conversations in this session)
            pupil_result = await db.execute(
                select(Conversation.pupil_id)
                .where(Conversation.session_id == session_id)
                .distinct()
            )
            pupil_ids = [r[0] for r in pupil_result.all()]

            # Generate per-pupil summaries
            for pupil_id in pupil_ids:
                # Get this pupil's messages during the session
                msg_result = await db.execute(
                    select(Message.content)
                    .join(Conversation, Message.conversation_id == Conversation.id)
                    .where(
                        Conversation.session_id == session_id,
                        Conversation.pupil_id == pupil_id,
                        Message.role == MessageRole.user,
                    )
                    .order_by(Message.created_at)
                )
                pupil_messages = list(msg_result.scalars().all())

                await _generate_pupil_summary(
                    session_id, pupil_id, transcript, pupil_messages,
                    duration_seconds, db,
                )

            await db.commit()
            logger.info(
                "Session %d artifacts generated: quiz + %d pupil summaries",
                session_id, len(pupil_ids),
            )

        except Exception as e:
            logger.error("Artifact generation failed for session %d: %s", session_id, e)
            await db.rollback()
