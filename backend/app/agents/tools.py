"""
LangChain tools available to the pupil agent.

Six tools expose distinct capabilities so the LangGraph ReAct loop
can decide *which* to invoke based on the pupil's message:

  retrieve_context        — semantic search over teacher-uploaded lesson chunks
  get_pupil_memories      — similarity search over this pupil's long-term memories
  get_conversation_history — load the last N messages for a given conversation
  list_lessons            — list lesson titles available to the pupil
  search_live_transcript  — semantic search over live/recent transcript chunks
  get_full_transcript     — get the full ordered transcript for a session
"""
from __future__ import annotations

from typing import Annotated

from langchain_core.tools import tool
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.domain import Conversation, Lesson, LessonChunk, Message, PupilMemory, TranscriptChunk


# ---------------------------------------------------------------------------
# Tool 1 — Retrieval (pgvector similarity search)
# ---------------------------------------------------------------------------

async def _embed(text: str, http_client) -> list[float]:
    """Call Ollama embed endpoint and return a float vector."""
    response = await http_client.post(
        f"{settings.ollama_base_url}/api/embeddings",
        json={"model": settings.ollama_embed_model, "prompt": text},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["embedding"]


async def retrieve_context_func(
    query: str,
    db: AsyncSession,
    http_client,
    k: int = 5,
) -> str:
    """Embed *query* and return the top-k lesson chunk texts joined by newlines."""
    vector = await _embed(query, http_client)
    # pgvector cosine similarity — lower distance = more similar
    result = await db.execute(
        select(LessonChunk.content)
        .order_by(LessonChunk.embedding.cosine_distance(vector))
        .limit(k)
    )
    rows = result.scalars().all()
    if not rows:
        return "No relevant lesson content found."
    return "\n\n---\n\n".join(rows)


# ---------------------------------------------------------------------------
# Tool 2 — Conversation memory
# ---------------------------------------------------------------------------

async def get_conversation_history_func(
    conversation_id: int,
    db: AsyncSession,
) -> list[dict[str, str]]:
    """Return the last MEMORY_WINDOW messages for *conversation_id* as dicts."""
    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.desc())
        .limit(settings.memory_window)
    )
    messages = list(reversed(result.scalars().all()))
    return [{"role": m.role.value, "content": m.content} for m in messages]


# ---------------------------------------------------------------------------
# Tool 3 — Lesson listing
# ---------------------------------------------------------------------------

async def list_lessons_func(db: AsyncSession) -> list[str]:
    """Return a list of available lesson titles."""
    result = await db.execute(select(Lesson.title).order_by(Lesson.created_at.desc()))
    titles = result.scalars().all()
    if not titles:
        return ["No lessons have been uploaded yet."]
    return list(titles)


# ---------------------------------------------------------------------------
# Tool 4 — Long-term pupil memory (similarity retrieval)
# ---------------------------------------------------------------------------

async def get_pupil_memories_func(
    query: str,
    pupil_id: int,
    db: AsyncSession,
    http_client,
    k: int = 5,
) -> list[str]:
    """
    Embed *query* and return the top-k long-term memory facts for *pupil_id*
    that are most semantically similar to the query.
    Returns plain strings, e.g. ["struggles with quadratic equations"].
    """
    vector = await _embed(query, http_client)
    result = await db.execute(
        select(PupilMemory.memory)
        .where(PupilMemory.pupil_id == pupil_id)
        .order_by(PupilMemory.embedding.cosine_distance(vector))
        .limit(k)
    )
    rows = result.scalars().all()
    return list(rows)


async def load_all_pupil_memories_func(
    pupil_id: int,
    db: AsyncSession,
) -> list[str]:
    """
    Load ALL long-term memories for *pupil_id* ordered by recency.
    Used at session start to build the system prompt — kept short intentionally.
    """
    result = await db.execute(
        select(PupilMemory.memory)
        .where(PupilMemory.pupil_id == pupil_id)
        .order_by(PupilMemory.created_at.desc())
        .limit(20)
    )
    return list(result.scalars().all())


async def save_pupil_memories_func(
    pupil_id: int,
    memories: list[str],
    db: AsyncSession,
    http_client,
) -> None:
    """Embed and persist a list of new memory strings for *pupil_id*."""
    for memory_text in memories:
        vector = await _embed(memory_text, http_client)
        db.add(PupilMemory(
            pupil_id=pupil_id,
            memory=memory_text,
            embedding=vector,
        ))
    await db.flush()


# ---------------------------------------------------------------------------
# Tool 6 — Live transcript search (similarity)
# ---------------------------------------------------------------------------

async def search_live_transcript_func(
    query: str,
    session_id: int,
    db: AsyncSession,
    http_client,
    k: int = 5,
) -> str:
    """
    Embed *query* and return the top-k transcript chunks for *session_id*
    that are most semantically similar.
    """
    vector = await _embed(query, http_client)
    result = await db.execute(
        select(TranscriptChunk.content, TranscriptChunk.timestamp_ms)
        .where(TranscriptChunk.session_id == session_id)
        .order_by(TranscriptChunk.embedding.cosine_distance(vector))
        .limit(k)
    )
    rows = result.all()
    if not rows:
        return "No transcript content found for this session."
    parts = []
    for content, ts_ms in rows:
        mins, secs = divmod(ts_ms // 1000, 60)
        parts.append(f"[{mins:02d}:{secs:02d}] {content}")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Tool 7 — Full transcript retrieval
# ---------------------------------------------------------------------------

async def get_full_transcript_func(
    session_id: int,
    db: AsyncSession,
) -> str:
    """Return the full ordered transcript for *session_id*."""
    result = await db.execute(
        select(TranscriptChunk.content, TranscriptChunk.timestamp_ms)
        .where(TranscriptChunk.session_id == session_id)
        .order_by(TranscriptChunk.timestamp_ms)
    )
    rows = result.all()
    if not rows:
        return "No transcript available for this session yet."
    parts = []
    for content, ts_ms in rows:
        mins, secs = divmod(ts_ms // 1000, 60)
        parts.append(f"[{mins:02d}:{secs:02d}] {content}")
    return "\n".join(parts)
