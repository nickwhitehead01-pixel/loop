"""
Teacher agent — simplified direct tool invocation pattern.

    Model:   gemma4:e2b
    Tools:   Direct invocation based on user message keywords
             get_lesson_summaries | search_lesson_content | get_class_analytics
             | get_student_profile | get_teacher_memories | search_transcript

Simpler than ReAct: parse user message → call appropriate tools → include results
in context → invoke LLM once. Guarantees tool results are incorporated into response.
"""
from __future__ import annotations

import asyncio
import json as _json
import logging
from typing import AsyncIterator

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_ollama import ChatOllama
from sqlalchemy import func as sa_func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal

from app.core.config import settings
from app.models.domain import (
    Lesson,
    LessonChunk,
    MessageRole,
    PupilSessionSummary,
    TeacherConversation,
    TeacherMemory,
    TeacherMessage,
    TranscriptChunk,
    User,
)
from app.services import ollama_client

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Base system prompt
# ---------------------------------------------------------------------------

_BASE_SYSTEM = """You are a warm, collaborative Teaching Assistant helping a teacher manage their classroom. You are a peer and a sounding board.

You have access to tools to:
- get_lesson_summaries: retrieve AI-analysed summaries of uploaded lesson materials (pass lesson_title to search, or leave empty to list all)
- search_lesson_content: search uploaded lesson materials for detailed content
- get_class_analytics: get aggregated pupil performance metrics (optionally by session_id)
- get_student_profile: get an individual pupil's progress and summaries
- get_teacher_memories: recall facts about this teacher and class from previous sessions
- search_transcript: search past lesson transcripts by topic (optionally by session_id)

Core Directives:
1. Use Tools Proactively: When the teacher asks about lessons, student progress, class analytics, or transcripts, use the appropriate tool immediately. These tools provide current, accurate data.
2. Synthesize, Don't Dump: When you use a tool, don't just read the data back like a robot. Weave it into your conversation naturally.
3. Be Conversational: Be warm, supportive, and collaborative. Ask clarifying questions if needed, offer insights, and suggest next steps based on data you retrieve.
4. Respect Privacy: When discussing pupil data, summarise trends rather than exposing raw messages.
"""


def _build_system_prompt(memories: list[str]) -> str:
    if not memories:
        return _BASE_SYSTEM
    block = "\n".join(f"- {m}" for m in memories)
    return _BASE_SYSTEM + f"\n\nWhat you know about this teacher and class:\n{block}\n"


# ---------------------------------------------------------------------------
# Tool implementations for direct invocation
# These are called directly from _detect_and_invoke_tool, not through LangGraph
# ---------------------------------------------------------------------------

async def get_lesson_summaries_impl(db: AsyncSession, teacher_id: int) -> str:
    """Retrieve lesson summaries for this teacher."""
    result = await db.execute(
        select(Lesson.id, Lesson.title, Lesson.summary, Lesson.created_at)
        .where(Lesson.teacher_id == teacher_id)
        .where(Lesson.summary.isnot(None))
        .order_by(Lesson.created_at.desc())
        .limit(10)
    )
    rows = result.all()
    if not rows:
        return "No lesson summaries available yet."
    parts = [f"📚 **{len(rows)} Lessons Available:**\n"]
    for lesson_id, title, summary, created_at in rows:
        summary_preview = summary[:300] + "..." if len(summary) > 300 else summary
        parts.append(f"\n**{title}** ({created_at.strftime('%Y-%m-%d')})\n{summary_preview}")
    return "\n".join(parts)


async def search_lesson_content_impl(db: AsyncSession, user_message: str) -> str:
    """Search lesson content by semantic similarity."""
    vector = await ollama_client.embed(user_message)
    result = await db.execute(
        select(LessonChunk.content)
        .order_by(LessonChunk.embedding.cosine_distance(vector))
        .limit(5)
    )
    rows = result.scalars().all()
    return "\n\n---\n\n".join(rows) if rows else "No relevant lesson content found."


async def get_class_analytics_impl(db: AsyncSession) -> str:
    """Get class-wide analytics."""
    stmt = select(
        sa_func.count(PupilSessionSummary.id).label("total_pupils"),
        sa_func.avg(PupilSessionSummary.understanding_score).label("avg_score"),
        sa_func.sum(PupilSessionSummary.questions_asked).label("total_questions"),
    )
    result = await db.execute(stmt)
    row = result.one()
    avg = f"{float(row.avg_score):.2f}" if row.avg_score else "N/A"
    return (
        f"📊 **Class Analytics**\n"
        f"Total pupils: {row.total_pupils or 0}\n"
        f"Average understanding score: {avg}/10\n"
        f"Total questions asked: {row.total_questions or 0}"
    )


# ---------------------------------------------------------------------------
# Public entry point — called by the WebSocket endpoint
# ---------------------------------------------------------------------------

# Tool invocation keywords for direct dispatch
TOOL_KEYWORDS = {
    "get_lesson_summaries": ["lesson", "summary", "material", "upload", "cover", "content", "taught", "covered"],
    "search_lesson_content": ["search", "find", "look for", "example", "specific", "contain"],
    "get_class_analytics": ["analytics", "performance", "progress", "score", "understanding", "questions"],
    "get_student_profile": ["student", "pupil", "progress", "profile", "performance"],
    "get_teacher_memories": ["remember", "recall", "memory", "know about", "class"],
    "search_transcript": ["transcript", "said", "spoken", "audio", "recording"],
}


async def _extract_and_store_memories(teacher_id: int, user_message: str, assistant_content: str) -> None:
    """Extract 1-3 facts from this turn and persist them. Runs in a background task with its own session."""
    try:
        extraction_prompt = (
            f"Teacher message: {user_message}\n"
            f"Assistant response: {assistant_content}\n\n"
            "Extract 1-3 short atomic facts about this teacher, their class, or teaching preferences. "
            "Reply with a JSON array of strings.\n"
            'Example: ["class struggles with algebra", "prefers visual explanations"]'
        )
        raw = await ollama_client.generate_full(
            messages=[{"role": "user", "content": extraction_prompt}],
            model=settings.ollama_model_teacher,
            format="json",
        )
        new_memories: list[str] = _json.loads(raw)
        if isinstance(new_memories, dict):
            new_memories = list(new_memories.values())[0]
        if not isinstance(new_memories, list):
            return
        new_memories = [m for m in new_memories if isinstance(m, str) and m.strip()]
        if not new_memories:
            return
        async with AsyncSessionLocal() as mem_db:
            for mem_text in new_memories:
                vector = await ollama_client.embed(mem_text)
                mem_db.add(TeacherMemory(
                    teacher_id=teacher_id,
                    memory=mem_text,
                    embedding=vector,
                ))
            await mem_db.commit()
    except Exception:
        pass  # memory extraction must never surface errors


async def _detect_and_invoke_tool(user_message: str, db: AsyncSession, teacher_id: int) -> str | None:
    """
    Detect which tool the user message is asking for and invoke it directly.
    Returns tool results if invoked, None if no tool detected.
    """
    msg_lower = user_message.lower()

    # Check each tool's keywords
    for tool_name, keywords in TOOL_KEYWORDS.items():
        if any(kw in msg_lower for kw in keywords):
            logger.info(f"Detected tool: {tool_name}")
            try:
                if tool_name == "get_lesson_summaries":
                    result = await db.execute(
                        select(Lesson.id, Lesson.title, Lesson.summary, Lesson.created_at)
                        .where(Lesson.teacher_id == teacher_id)
                        .where(Lesson.summary.isnot(None))
                        .order_by(Lesson.created_at.desc())
                        .limit(10)
                    )
                    rows = result.all()
                    if not rows:
                        return "No lesson summaries available yet."
                    parts = [f"📚 **{len(rows)} Lessons Available:**\n"]
                    for lesson_id, title, summary, created_at in rows:
                        summary_preview = summary[:300] + "..." if len(summary) > 300 else summary
                        parts.append(f"\n**{title}** ({created_at.strftime('%Y-%m-%d')})\n{summary_preview}")
                    return "\n".join(parts)

                elif tool_name == "search_lesson_content":
                    vector = await ollama_client.embed(user_message)
                    result = await db.execute(
                        select(LessonChunk.content)
                        .order_by(LessonChunk.embedding.cosine_distance(vector))
                        .limit(5)
                    )
                    rows = result.scalars().all()
                    return "\n\n---\n\n".join(rows) if rows else "No relevant lesson content found."

                elif tool_name == "get_class_analytics":
                    stmt = select(
                        sa_func.count(PupilSessionSummary.id).label("total_pupils"),
                        sa_func.avg(PupilSessionSummary.understanding_score).label("avg_score"),
                        sa_func.sum(PupilSessionSummary.questions_asked).label("total_questions"),
                    )
                    result = await db.execute(stmt)
                    row = result.one()
                    avg = f"{float(row.avg_score):.2f}" if row.avg_score else "N/A"
                    return (
                        f"📊 **Class Analytics**\n"
                        f"Total pupils: {row.total_pupils or 0}\n"
                        f"Average understanding score: {avg}/10\n"
                        f"Total questions asked: {row.total_questions or 0}"
                    )

                elif tool_name == "get_student_profile":
                    result = await db.execute(
                        select(User.id, User.name)
                        .where(User.role == "pupil")
                        .order_by(User.name)
                    )
                    rows = result.all()
                    if not rows:
                        return "No pupils found."
                    pupil_list = "\n".join(f"- ID {uid}: {name}" for uid, name in rows)
                    return f"Available pupils:\n{pupil_list}"

                elif tool_name == "get_teacher_memories":
                    vector = await ollama_client.embed(user_message)
                    result = await db.execute(
                        select(TeacherMemory.memory)
                        .where(TeacherMemory.teacher_id == teacher_id)
                        .order_by(TeacherMemory.embedding.cosine_distance(vector))
                        .limit(5)
                    )
                    rows = result.scalars().all()
                    return "\n".join(f"- {m}" for m in rows) if rows else "No relevant memories found."

                elif tool_name == "search_transcript":
                    vector = await ollama_client.embed(user_message)
                    result = await db.execute(
                        select(
                            TranscriptChunk.content,
                            TranscriptChunk.timestamp_ms,
                            TranscriptChunk.session_id,
                        )
                        .order_by(TranscriptChunk.embedding.cosine_distance(vector))
                        .limit(5)
                    )
                    rows = result.all()
                    if not rows:
                        return "No transcript content found."
                    parts = []
                    for content, ts_ms, sid in rows:
                        mins, secs = divmod(ts_ms // 1000, 60)
                        parts.append(f"[Session {sid} — {mins:02d}:{secs:02d}] {content}")
                    return "\n\n".join(parts)

            except Exception as e:
                logger.error(f"Error invoking tool {tool_name}: {e}", exc_info=True)
                return f"Error retrieving {tool_name}: {str(e)}"

    return None


async def run_teacher_agent(
    user_message: str,
    conversation_id: int,
    teacher_id: int,
    db: AsyncSession,
) -> AsyncIterator[str]:
    """Run the teacher agent with direct tool invocation. Persists messages and extracts memories."""
    db.add(TeacherMessage(
        conversation_id=conversation_id,
        role=MessageRole.user,
        content=user_message,
    ))
    await db.flush()

    # Load long-term teacher memories (most recent first, capped at 5)
    mem_result = await db.execute(
        select(TeacherMemory.memory)
        .where(TeacherMemory.teacher_id == teacher_id)
        .order_by(TeacherMemory.created_at.desc())
        .limit(5)
    )
    prior_memories = list(mem_result.scalars().all())

    # Try to detect and invoke appropriate tool
    tool_result = await _detect_and_invoke_tool(user_message, db, teacher_id)

    # Build system prompt with tool results if available
    system_lines = [_BASE_SYSTEM]
    if prior_memories:
        block = "\n".join(f"- {m}" for m in prior_memories)
        system_lines.append(f"\nWhat you know about this teacher and class:\n{block}\n")

    if tool_result:
        system_lines.append(f"\n[DATA RETRIEVED FROM TOOLS]:\n{tool_result}\n")

    system_prompt = "".join(system_lines)

    # Load recent conversation history
    hist_result = await db.execute(
        select(TeacherMessage)
        .where(TeacherMessage.conversation_id == conversation_id)
        .order_by(TeacherMessage.created_at.desc())
        .limit(settings.memory_window)
    )
    history = list(reversed(hist_result.scalars().all()))
    # Exclude the message we just inserted, then cap to last 10 messages
    recent = history[:-1][-10:]
    lc_messages: list[BaseMessage] = [SystemMessage(content=system_prompt)]
    for m in recent:
        cls = HumanMessage if m.role == MessageRole.user else AIMessage
        lc_messages.append(cls(content=m.content))
    lc_messages.append(HumanMessage(content=user_message))

    # Invoke LLM directly (no ReAct loop)
    llm = ChatOllama(
        model=settings.ollama_model_teacher,
        base_url=settings.ollama_base_url,
        temperature=0.4,
        streaming=True,
    )

    full_response: list[str] = []

    try:
        async for chunk in llm.astream(lc_messages):
            token = chunk.content
            if token:
                full_response.append(token)
                yield token
    finally:
        assistant_content = "".join(full_response)
        if assistant_content:
            db.add(TeacherMessage(
                conversation_id=conversation_id,
                role=MessageRole.assistant,
                content=assistant_content,
            ))
            await db.flush()

        await db.commit()

        # Fire memory extraction in the background — does not block the response
        if assistant_content:
            asyncio.create_task(
                _extract_and_store_memories(teacher_id, user_message, assistant_content)
            )
