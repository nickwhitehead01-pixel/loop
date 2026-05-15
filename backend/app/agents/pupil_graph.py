"""
Pupil agent — direct-invoke pattern optimised for gemma4:e2b.

    Model:   gemma4:e2b
    Pattern: keyword dispatch → single retrieval → one LLM call (no ReAct loop)

Flow:
    user_message
         │
         ▼
    semantic cache? ──── HIT ──── yield cached answer
         │
        MISS
         │
         ▼
    embed user_message (shared vector)
         │
         ├─ similarity search → top-3 pupil memories  (system prompt)
         ├─ keyword dispatch  → ONE retrieval tool     ([CONTEXT] block)
         │
         ▼
    single llm.astream(messages) call
         │
         ▼
    yield tokens → persist → background memory extraction
"""
from __future__ import annotations

import asyncio
import json as _json
import logging
from typing import AsyncIterator

import httpx
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_ollama import ChatOllama
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.tools import (
    _embed as _embed_fn,
    get_conversation_history_func,
    get_full_transcript_func,
    list_lessons_func,
    save_pupil_memories_func,
)
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.domain import LessonChunk, Message, MessageRole, PupilMemory, TranscriptChunk
from app.services import ollama_client
from app.services import semantic_cache as _sem_cache
from app.services import slide_sync as _slide_sync
from app.services.chroma_client import lesson_chunks_col, pupil_memories_col, transcript_chunks_col

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Keyword dispatch — one tool per turn, evaluated in priority order.
# The first matching bucket wins; default is retrieve_context (RAG).
# ---------------------------------------------------------------------------

# Maximum words per RAG/transcript chunk sent to the LLM.  Caps prompt-eval
# cost without cutting off genuine content — 150 words per chunk is enough
# to convey the key concept; 2 chunks keeps [CONTEXT] under ~300 tokens.
_MAX_CHUNK_WORDS = 150

_TRANSCRIPT_KEYWORDS = {"transcript", "said", "spoken", "recap", "everything", "audio", "recording"}
_FULL_RECAP_KEYWORDS  = {"full recap", "entire lesson", "whole lesson", "everything said"}
_LIST_KEYWORDS        = {"what lessons", "which lessons", "available lessons", "topics available", "list lessons"}
# Live keywords: route to get_full_transcript (SQLite, always current) so
# the pupil can ask about speech that hasn't yet been flushed to ChromaDB.
_LIVE_KEYWORDS        = {"just said", "just now", "right now", "currently", "latest", "just mentioned"}


def _dispatch_tool(user_message: str, session_id: int | None) -> str:
    """Return the name of the single retrieval tool to invoke this turn.

    Transcript tools only activate when session_id is present.
    Without a session_id, transcript keyword queries fall through to
    retrieve_context so the agent searches lesson chunks — which includes
    any transcript files the teacher uploaded alongside lesson materials.
    """
    msg = user_message.lower()
    if session_id:
        # Live/recency queries — must check before _TRANSCRIPT_KEYWORDS
        if any(phrase in msg for phrase in _LIVE_KEYWORDS):
            logger.info("Dispatch → get_full_transcript (live keyword, session=%d)", session_id)
            return "get_full_transcript"
        if any(phrase in msg for phrase in _FULL_RECAP_KEYWORDS):
            logger.info("Dispatch → get_full_transcript (full recap, session=%d)", session_id)
            return "get_full_transcript"
        if any(kw in msg for kw in _TRANSCRIPT_KEYWORDS):
            logger.info("Dispatch → search_transcript (transcript keyword, session=%d)", session_id)
            return "search_transcript"
    if any(phrase in msg for phrase in _LIST_KEYWORDS):
        logger.info("Dispatch → list_lessons")
        return "list_lessons"
    logger.info("Dispatch → retrieve_context (session_id=%s)", session_id)
    return "retrieve_context"


# ---------------------------------------------------------------------------
# System prompt — lean role description only (no tool listing)
# ---------------------------------------------------------------------------

_BASE_SYSTEM = """You are a supportive personal AI tutor for a pupil with special educational needs.
A [CONTEXT] block containing lesson material will be provided — use it to anchor your answers to what has been taught.
If the lesson material alone does not fully answer the question, draw on your broader knowledge of the lesson subject to explain or elaborate. Do not introduce topics outside the lesson subject.
Personalise your approach using the pupil facts listed below.

RESPONSE FORMAT — BLUF (Bottom Line Up Front):
Answer in exactly 2 sentences and no more than 30 words total.
State the direct answer first, then add one short supporting detail or encouragement.
Never use bullet points, headers, or lists."""


def _build_system_prompt(
    memories: list[str],
    context: str,
    current_slide: "_slide_sync.SlidePosition | None" = None,
    lesson_subject: str | None = None,
) -> str:
    parts = [_BASE_SYSTEM]
    if memories:
        block = "\n".join(f"- {m}" for m in memories)
        parts.append(f"\nWhat you know about this pupil:\n{block}")
    if current_slide is not None:
        parts.append(
            f"\n[LESSON POSITION]\n"
            f"The teacher is currently on slide {current_slide.slide_number} "
            f"of '{current_slide.lesson_title}'."
        )
    if lesson_subject:
        parts.append(f"\n[LESSON SUBJECT]\nThis lesson is about: {lesson_subject}. Stay within this subject area.")
    if context:
        parts.append(f"\n[CONTEXT]\n{context}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Background memory extraction — runs after the response is committed.
# Opens its own session so it never holds the request session open.
# ---------------------------------------------------------------------------

async def _extract_and_store_memories(
    pupil_id: int,
    user_message: str,
    assistant_content: str,
    http_client: httpx.AsyncClient,
) -> None:
    try:
        extraction_prompt = (
            f"Pupil: {user_message}\n"
            f"Tutor: {assistant_content}\n\n"
            "Extract 1-3 short atomic facts about this pupil's learning "
            "(e.g. struggles, preferences, progress, misconceptions). "
            "Reply with a JSON array of strings only.\n"
            'Example: ["struggles with long division", "prefers step-by-step worked examples"]'
        )
        raw = await ollama_client.generate_full(
            messages=[{"role": "user", "content": extraction_prompt}],
            model=settings.ollama_model_pupil,
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
            await save_pupil_memories_func(pupil_id, new_memories, mem_db, http_client)
            await mem_db.commit()
    except Exception:
        logger.debug("Memory extraction failed for pupil %d — continuing", pupil_id, exc_info=True)


# ---------------------------------------------------------------------------
# Public entry point — called by the WebSocket endpoint
# ---------------------------------------------------------------------------

# Module-level singleton — avoids constructing a new ChatOllama object on
# every message. temperature=0.7: warmer than the teacher agent (0.4) so
# tutoring responses feel natural and varied rather than robotic.
# num_ctx=1536: caps Ollama's KV cache to the actual prompt ceiling.
# Full prompt (base + memories + context + 4 history msgs + user) peaks at
# ~870 tokens; 1536 fits it with safe headroom and cuts KV allocation vs
# the previous 2048, reducing prompt-eval time on Apple Silicon.
_llm = ChatOllama(
    model=settings.ollama_model_pupil,
    base_url=settings.ollama_base_url,
    temperature=0.7,
    streaming=True,
    num_ctx=1536,
)


async def run_pupil_agent(
    user_message: str,
    conversation_id: int,
    pupil_id: int,
    db: AsyncSession,
    http_client: httpx.AsyncClient,
    session_id: int | None = None,
) -> AsyncIterator[str]:
    """
    Run the pupil agent and yield response tokens one at a time.
    Uses direct-invoke: one retrieval, one LLM call, no ReAct loop.
    """
    db.add(Message(
        conversation_id=conversation_id,
        role=MessageRole.user,
        content=user_message,
    ))
    await db.flush()

    # --- Single embed for the whole turn ---
    # Used by cache lookup, memory retrieval, RAG, and cache store.
    # One round-trip to nomic-embed-text instead of the previous three.
    shared_vector: list[float] = await _embed_fn(user_message, http_client)

    # --- Semantic cache lookup (reuses shared_vector) ---
    _cached = await _sem_cache.lookup(
        user_message, db, session_id=session_id, vector=shared_vector
    )
    if _cached:
        db.add(Message(
            conversation_id=conversation_id,
            role=MessageRole.assistant,
            content=_cached,
        ))
        await db.commit()
        yield _cached
        return

    tool_name = _dispatch_tool(user_message, session_id)

    # Fetch memories (using shared vector) + history in parallel
    def _query_memories():
        col = pupil_memories_col()
        results = col.query(
            query_embeddings=[shared_vector],
            n_results=3,
            where={"pupil_id": str(pupil_id)},
            include=["documents"],
        )
        return list((results.get("documents") or [[]])[0])

    # Fetch memories (shared vector), history, and active lesson id in parallel.
    # _query_memories uses ChromaDB (sync, runs in thread pool).
    # get_conversation_history_func and the lesson query use the same DB session
    # but are both lightweight SELECTs that queue safely on the aiosqlite backend.
    async def _get_active_lesson_info() -> tuple[int | None, str | None]:
        if session_id is None:
            return (None, None)
        from app.models.domain import Lesson as _Lesson
        result = await db.execute(
            select(_Lesson.id, _Lesson.title).where(_Lesson.session_id == session_id)
        )
        row = result.one_or_none()
        return (row.id, row.title) if row else (None, None)

    prior_memories, history, (active_lesson_id, lesson_subject) = await asyncio.gather(
        asyncio.get_event_loop().run_in_executor(None, _query_memories),
        get_conversation_history_func(conversation_id, db),
        _get_active_lesson_info(),
    )

    context = ""
    try:
        if tool_name == "retrieve_context":
            # Pass shared vector directly — no second embed call
            col = lesson_chunks_col()
            query_kwargs: dict = {
                "query_embeddings": [shared_vector],
                "n_results": 3,
                "include": ["documents"],
            }
            # Scope to the active lesson when we know which one it is.
            if active_lesson_id is not None:
                query_kwargs["where"] = {"lesson_id": str(active_lesson_id)}
            results = col.query(**query_kwargs)
            rows = (results.get("documents") or [[]])[0]
            rows = [" ".join(r.split()[:_MAX_CHUNK_WORDS]) for r in rows]
            context = "\n\n---\n\n".join(rows) if rows else ""
        elif tool_name == "search_transcript":
            # Pass shared vector directly — no second embed call
            col = transcript_chunks_col()
            results = col.query(
                query_embeddings=[shared_vector],
                n_results=3,
                where={"session_id": str(session_id)},
                include=["documents", "metadatas"],
            )
            docs = (results.get("documents") or [[]])[0]
            metas = (results.get("metadatas") or [[]])[0]
            if docs:
                parts = []
                for doc, meta in zip(docs, metas):
                    ts_ms = int(meta.get("timestamp_ms", 0))
                    mins, secs = divmod(ts_ms // 1000, 60)
                    truncated = " ".join(doc.split()[:_MAX_CHUNK_WORDS])
                    parts.append(f"[{mins:02d}:{secs:02d}] {truncated}")
                context = "\n\n".join(parts)
        elif tool_name == "get_full_transcript":
            context = await get_full_transcript_func(session_id, db)
        elif tool_name == "list_lessons":
            titles = await list_lessons_func(db)
            context = "\n".join(f"- {t}" for t in titles)
    except Exception:
        logger.exception("Retrieval failed for tool %s — continuing without context", tool_name)

    if not context and lesson_subject:
        context = (
            f"No specific lesson material was found for this question. "
            f"Answer using your general knowledge of: {lesson_subject}."
        )

    system_prompt = _build_system_prompt(
        prior_memories,
        context,
        current_slide=_slide_sync.get_current_slide(session_id) if session_id else None,
        lesson_subject=lesson_subject,
    )

    # --- Build message list (history already fetched in gather above) ---
    recent = history[:-1][-4:]
    lc_messages: list[BaseMessage] = [SystemMessage(content=system_prompt)]
    for m in recent:
        cls = HumanMessage if m["role"] == "user" else AIMessage
        lc_messages.append(cls(content=m["content"]))
    lc_messages.append(HumanMessage(content=user_message))

    # Single LLM call — no ReAct loop, no tool schemas.
    full_response: list[str] = []

    try:
        async for chunk in _llm.astream(lc_messages):
            token = chunk.content
            if token:
                full_response.append(token)
                yield token
    finally:
        assistant_content = "".join(full_response)
        if assistant_content:
            db.add(Message(
                conversation_id=conversation_id,
                role=MessageRole.assistant,
                content=assistant_content,
            ))
            await db.flush()

        try:
            await _sem_cache.store(
                user_message, assistant_content, db,
                session_id=session_id, vector=shared_vector,
            )
        except Exception:
            logger.debug("Semantic cache store failed", exc_info=True)

        await db.commit()

        # --- Background memory extraction (does not block the response) ---
        if assistant_content:
            asyncio.create_task(
                _extract_and_store_memories(pupil_id, user_message, assistant_content, http_client)
            )
