"""
Teacher agent — direct tool invocation pattern optimised for gemma4:e2b.

Why this architecture
---------------------
A ReAct loop gives the model control over which tools to call and in what
order.  For a teacher-facing assistant the tradeoff is unfavourable: the
extra LLM round-trips add latency the teacher notices, and the model
sometimes skips tools that contain the most relevant data.  By resolving
tool selection in Python (keyword dispatch) before the LLM is ever invoked,
we guarantee that query results are always present in the context.

Key design decisions
--------------------
1. Keyword dispatch over ReAct
   Tool selection is a cheap string-match in Python, not an LLM inference
   step.  This eliminates one full generate-parse cycle per message and
   makes tool invocation deterministic and auditable.

2. Parallel tool fetches where possible
   Independent data sources (analytics, memories, lesson content) are
   fetched concurrently with asyncio.gather, keeping wall-clock latency
   close to the slowest single query rather than their sum.

3. Single LLM call with pre-populated context
   All retrieved data is assembled into a [DATA] block before the model
   is called once.  The model's only job is synthesis and communication,
   not tool orchestration — which suits smaller models like gemma4:e2b.

4. Background memory extraction
   Teacher-preference facts are persisted after the response streams,
   keeping the critical path free of write latency and ensuring memories
   are available on the next turn without blocking the current one.

Runtime contract
----------------
- Model  : gemma4:e2b (local Ollama), temperature 0.4 for factual accuracy
- Tools  : get_lesson_summaries, search_lesson_content, get_class_analytics,
           get_student_profile, get_teacher_memories, search_transcript
- Pattern: keyword dispatch → parallel fetch → single astream call →
           background memory persist
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
from app.services.chroma_client import lesson_chunks_col, teacher_memories_col, transcript_chunks_col

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Base system prompt
# ---------------------------------------------------------------------------

_BASE_SYSTEM = """You are a warm, collaborative Teaching Assistant helping a teacher manage their classroom.
Relevant data is provided in the [DATA] block below — use it to give accurate, grounded answers.
Be conversational, synthesise insights, and respect pupil privacy by summarising rather than quoting raw data."""


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
    col = lesson_chunks_col()
    results = col.query(query_embeddings=[vector], n_results=5, include=["documents"])
    rows = (results.get("documents") or [[]])[0]
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

# Tool invocation keywords — DB-only tools listed FIRST to avoid blocking embed calls.
# Embed-dependent tools (search_lesson_content, get_teacher_memories, search_transcript)
# only fire if no DB tool matched, keeping Ollama free to start the LLM sooner.
TOOL_KEYWORDS = {
    # --- DB-only (no embed, instant) ---
    "get_lesson_summaries": ["lesson", "summary", "material", "upload", "taught", "covered", "available"],
    "get_class_analytics": ["analytics", "performance", "class score", "understanding", "questions asked"],
    "get_student_profile": ["student", "pupil", "profile"],
    # --- Embed-dependent (queues on Ollama GPU — only reached if above didn't match) ---
    "search_lesson_content": ["search", "find", "look for", "example", "specific", "contain", "content"],
    "get_teacher_memories": ["remember", "recall", "memory", "know about"],
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
                import uuid as _uuid
                mem = TeacherMemory(
                    teacher_id=teacher_id,
                    memory=mem_text,
                )
                mem_db.add(mem)
                await mem_db.flush()
                teacher_memories_col().add(
                    ids=[str(_uuid.uuid4())],
                    embeddings=[vector],
                    documents=[mem_text],
                    metadatas=[{"teacher_id": str(teacher_id), "sqlite_id": str(mem.id)}],
                )
            await mem_db.commit()
    except Exception:
        logger.debug("Memory extraction failed for teacher %d — continuing", teacher_id, exc_info=True)


async def _detect_and_invoke_tool(user_message: str, db: AsyncSession, teacher_id: int) -> str | None:
    """
    Detect which tool the user message is asking for and invoke it directly.
    Returns tool results if invoked, None if no tool detected.
    """
    msg_lower = user_message.lower()

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
                    col = lesson_chunks_col()
                    results = col.query(query_embeddings=[vector], n_results=5, include=["documents"])
                    rows = (results.get("documents") or [[]])[0]
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
                    col = teacher_memories_col()
                    results = col.query(
                        query_embeddings=[vector],
                        n_results=5,
                        where={"teacher_id": str(teacher_id)},
                        include=["documents"],
                    )
                    rows = (results.get("documents") or [[]])[0]
                    return "\n".join(f"- {m}" for m in rows) if rows else "No relevant memories found."

                elif tool_name == "search_transcript":
                    vector = await ollama_client.embed(user_message)
                    col = transcript_chunks_col()
                    results = col.query(
                        query_embeddings=[vector],
                        n_results=5,
                        include=["documents", "metadatas"],
                    )
                    docs = (results.get("documents") or [[]])[0]
                    metas = (results.get("metadatas") or [[]])[0]
                    if not docs:
                        return "No transcript content found."
                    parts = []
                    for doc, meta in zip(docs, metas):
                        ts_ms = int(meta.get("timestamp_ms", 0))
                        sid = meta.get("session_id", "?")
                        mins, secs = divmod(ts_ms // 1000, 60)
                        parts.append(f"[Session {sid} — {mins:02d}:{secs:02d}] {doc}")
                    return "\n\n".join(parts)

            except (ValueError, TypeError) as e:
                logger.warning("Tool %s rejected input: %s", tool_name, e, exc_info=True)
                return f"[Tool error: invalid input for '{tool_name}']"
            except Exception:
                logger.exception("Tool %s invocation failed", tool_name)
                return f"[Tool '{tool_name}' temporarily unavailable]"

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

    # Fetch memories and history in parallel — both are independent DB reads
    mem_result, hist_result = await asyncio.gather(
        db.execute(
            select(TeacherMemory.memory)
            .where(TeacherMemory.teacher_id == teacher_id)
            .order_by(TeacherMemory.created_at.desc())
            .limit(5)
        ),
        db.execute(
            select(TeacherMessage)
            .where(TeacherMessage.conversation_id == conversation_id)
            .order_by(TeacherMessage.created_at.desc())
            .limit(settings.memory_window)
        ),
    )
    prior_memories = list(mem_result.scalars().all())
    history = list(reversed(hist_result.scalars().all()))

    tool_result = await _detect_and_invoke_tool(user_message, db, teacher_id)

    system_lines = [_BASE_SYSTEM]
    if prior_memories:
        block = "\n".join(f"- {m}" for m in prior_memories)
        system_lines.append(f"\nWhat you know about this teacher and class:\n{block}")
    if tool_result:
        system_lines.append(f"\n[DATA]\n{tool_result}")
    system_prompt = "\n".join(system_lines)

    # Cap to last 6 messages (3 turns) — matches pupil agent, prevents prompt bloat
    recent = history[:-1][-6:]
    lc_messages: list[BaseMessage] = [SystemMessage(content=system_prompt)]
    for m in recent:
        cls = HumanMessage if m.role == MessageRole.user else AIMessage
        lc_messages.append(cls(content=m.content))
    lc_messages.append(HumanMessage(content=user_message))

    # Single LLM call — no ReAct loop or tool-schema overhead.
    # temperature=0.4: lower than the pupil agent (0.7) so analytical answers
    # stay factually grounded rather than creatively rephrased.
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
