"""
Retrieval functions called directly by the pupil and teacher agents.

Replaced the old LangGraph @tool pattern — functions are invoked inline
via keyword dispatch rather than being registered in a ReAct loop. No tool
schemas, no intermediate LLM "which tool?" step.

  retrieve_context         — vector search over teacher-uploaded lesson chunks (ChromaDB)
  get_conversation_history — last N messages for the context window
  list_lessons             — lesson titles for "what topics exist?" queries
  get_pupil_memories       — similarity search over this pupil's long-term fact store
  search_live_transcript   — vector search over live transcript (session-scoped)
  get_full_transcript      — ordered transcript text capped by word budget
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.domain import Lesson, Message, PupilMemory, TranscriptChunk
from app.services.chroma_client import lesson_chunks_col, pupil_memories_col, transcript_chunks_col


# ---------------------------------------------------------------------------
# Tool 1 — Retrieval (ChromaDB similarity search)
# ---------------------------------------------------------------------------

async def _embed(text: str, http_client) -> list[float]:
    """Accepts an injected http_client so callers can share one client
    across an entire request rather than opening a new connection per embed call."""
    response = await http_client.post(
        f"{settings.ollama_base_url}/api/embed",
        json={"model": settings.ollama_embed_model, "input": text},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["embeddings"][0]


async def retrieve_context_func(
    query: str,
    db: AsyncSession,
    http_client,
    k: int = 3,
) -> str:
    """Embed *query* and return the top-k lesson chunk texts joined by newlines."""
    vector = await _embed(query, http_client)
    col = lesson_chunks_col()
    results = col.query(query_embeddings=[vector], n_results=k, include=["documents"])
    rows = (results.get("documents") or [[]])[0]
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
    col = pupil_memories_col()
    results = col.query(
        query_embeddings=[vector],
        n_results=k,
        where={"pupil_id": str(pupil_id)},
        include=["documents"],
    )
    return list((results.get("documents") or [[]])[0])


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
    import uuid as _uuid
    col = pupil_memories_col()
    for memory_text in memories:
        vector = await _embed(memory_text, http_client)
        mem = PupilMemory(
            pupil_id=pupil_id,
            memory=memory_text,
        )
        db.add(mem)
        await db.flush()
        col.add(
            ids=[str(_uuid.uuid4())],
            embeddings=[vector],
            documents=[memory_text],
            metadatas=[{"pupil_id": str(pupil_id), "sqlite_id": str(mem.id)}],
        )


# ---------------------------------------------------------------------------
# Tool 6 — Live transcript search (similarity)
# ---------------------------------------------------------------------------

async def search_live_transcript_func(
    query: str,
    session_id: int,
    db: AsyncSession,
    http_client,
    k: int = 3,
) -> str:
    """
    Embed *query* and return the top-k transcript chunks for *session_id*
    that are most semantically similar.
    """
    vector = await _embed(query, http_client)
    col = transcript_chunks_col()
    results = col.query(
        query_embeddings=[vector],
        n_results=k,
        where={"session_id": str(session_id)},
        include=["documents", "metadatas"],
    )
    docs = (results.get("documents") or [[]])[0]
    metas = (results.get("metadatas") or [[]])[0]
    if not docs:
        return "No transcript content found for this session."
    parts = []
    for doc, meta in zip(docs, metas):
        ts_ms = int(meta.get("timestamp_ms", 0))
        mins, secs = divmod(ts_ms // 1000, 60)
        parts.append(f"[{mins:02d}:{secs:02d}] {doc}")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Tool 7 — Full transcript retrieval
# ---------------------------------------------------------------------------

async def get_full_transcript_func(
    session_id: int,
    db: AsyncSession,
    max_words: int = 400,
) -> str:
    """
    Return the most recent transcript chunks for *session_id*, newest-first
    up to *max_words* words, then reversed to chronological order.

    Capped by word budget (not chunk count) to keep context predictable for
    small models — 400 words ≈ 3–4 min of speech at a comfortable teaching pace.
    """
    result = await db.execute(
        select(TranscriptChunk.content, TranscriptChunk.timestamp_ms)
        .where(TranscriptChunk.session_id == session_id)
        .order_by(TranscriptChunk.timestamp_ms.desc())
        .limit(200)   # safety ceiling; word budget will exhaust well before this
    )
    rows = result.all()
    if not rows:
        return "No transcript available for this session yet."

    selected: list[tuple[str, int]] = []
    word_count = 0
    for content, ts_ms in rows:
        chunk_words = len(content.split())
        if word_count + chunk_words > max_words:
            break
        selected.append((content, ts_ms))
        word_count += chunk_words

    # Restore chronological order
    selected.reverse()
    parts = []
    for content, ts_ms in selected:
        mins, secs = divmod(ts_ms // 1000, 60)
        parts.append(f"[{mins:02d}:{secs:02d}] {content}")
    return "\n".join(parts)
