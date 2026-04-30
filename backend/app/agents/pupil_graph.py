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

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Keyword dispatch — one tool per turn, evaluated in priority order.
# The first matching bucket wins; default is retrieve_context (RAG).
# ---------------------------------------------------------------------------

_TRANSCRIPT_KEYWORDS = {"transcript", "said", "spoken", "recap", "everything", "audio", "recording"}
_FULL_RECAP_KEYWORDS  = {"full recap", "entire lesson", "whole lesson", "everything said"}
_LIST_KEYWORDS        = {"what lessons", "which lessons", "available lessons", "topics available", "list lessons"}


def _dispatch_tool(user_message: str, session_id: int | None) -> str:
    """Return the name of the single retrieval tool to invoke this turn."""
    msg = user_message.lower()
    if session_id:
        if any(phrase in msg for phrase in _FULL_RECAP_KEYWORDS):
            return "get_full_transcript"
        if any(kw in msg for kw in _TRANSCRIPT_KEYWORDS):
            return "search_transcript"
    if any(phrase in msg for phrase in _LIST_KEYWORDS):
        return "list_lessons"
    return "retrieve_context"


# ---------------------------------------------------------------------------
# System prompt — lean role description only (no tool listing)
# ---------------------------------------------------------------------------

_BASE_SYSTEM = """You are a supportive personal AI tutor for a pupil with special educational needs.
Use the CONTEXT block provided to ground your answers in the lesson material.
Be clear, patient, encouraging, and concise.
Personalise your approach using the pupil facts listed below."""


def _build_system_prompt(memories: list[str], context: str) -> str:
    parts = [_BASE_SYSTEM]
    if memories:
        block = "\n".join(f"- {m}" for m in memories)
        parts.append(f"\nWhat you know about this pupil:\n{block}")
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
        pass  # must never surface errors


# ---------------------------------------------------------------------------
# Public entry point — called by the WebSocket endpoint
# ---------------------------------------------------------------------------

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
    # Persist the incoming user message
    db.add(Message(
        conversation_id=conversation_id,
        role=MessageRole.user,
        content=user_message,
    ))
    await db.flush()

    # --- Semantic cache lookup ---
    _cached = await _sem_cache.lookup(user_message, db, session_id=session_id)
    if _cached:
        db.add(Message(
            conversation_id=conversation_id,
            role=MessageRole.assistant,
            content=_cached,
        ))
        await db.commit()
        yield _cached
        return

    # --- Embed once, share vector across memory + context retrieval ---
    tool_name = _dispatch_tool(user_message, session_id)

    # Embed once — shared across memory similarity search and context retrieval
    shared_vector: list[float] = await _embed_fn(user_message, http_client)

    # Fetch memories (using shared vector) + history in parallel
    mem_query = db.execute(
        select(PupilMemory.memory)
        .where(PupilMemory.pupil_id == pupil_id)
        .order_by(PupilMemory.embedding.cosine_distance(shared_vector))
        .limit(3)
    )
    hist_query = get_conversation_history_func(conversation_id, db)
    mem_result, history = await asyncio.gather(mem_query, hist_query)
    prior_memories = list(mem_result.scalars().all())

    context = ""
    try:
        if tool_name == "retrieve_context":
            # Pass shared vector directly — no second embed call
            rows = (await db.execute(
                select(LessonChunk.content)
                .order_by(LessonChunk.embedding.cosine_distance(shared_vector))
                .limit(3)
            )).scalars().all()
            context = "\n\n---\n\n".join(rows) if rows else ""
        elif tool_name == "search_transcript":
            # Pass shared vector directly — no second embed call
            rows = (await db.execute(
                select(TranscriptChunk.content, TranscriptChunk.timestamp_ms)
                .where(TranscriptChunk.session_id == session_id)
                .order_by(TranscriptChunk.embedding.cosine_distance(shared_vector))
                .limit(3)
            )).all()
            if rows:
                parts = []
                for content, ts_ms in rows:
                    mins, secs = divmod(ts_ms // 1000, 60)
                    parts.append(f"[{mins:02d}:{secs:02d}] {content}")
                context = "\n\n".join(parts)
        elif tool_name == "get_full_transcript":
            context = await get_full_transcript_func(session_id, db)
        elif tool_name == "list_lessons":
            titles = await list_lessons_func(db)
            context = "\n".join(f"- {t}" for t in titles)
    except Exception:
        logger.exception("Retrieval failed for tool %s — continuing without context", tool_name)

    system_prompt = _build_system_prompt(prior_memories, context)

    # --- Build message list (history already fetched in gather above) ---
    recent = history[:-1][-6:]
    lc_messages: list[BaseMessage] = [SystemMessage(content=system_prompt)]
    for m in recent:
        cls = HumanMessage if m["role"] == "user" else AIMessage
        lc_messages.append(cls(content=m["content"]))
    lc_messages.append(HumanMessage(content=user_message))

    # --- Single LLM call — no ReAct loop, no tool schemas ---
    llm = ChatOllama(
        model=settings.ollama_model_pupil,
        base_url=settings.ollama_base_url,
        temperature=0.7,
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
            db.add(Message(
                conversation_id=conversation_id,
                role=MessageRole.assistant,
                content=assistant_content,
            ))
            await db.flush()

        # --- Store in semantic cache ---
        try:
            await _sem_cache.store(user_message, assistant_content, db, session_id=session_id)
        except Exception:
            pass

        await db.commit()

        # --- Background memory extraction (does not block the response) ---
        if assistant_content:
            asyncio.create_task(
                _extract_and_store_memories(pupil_id, user_message, assistant_content, http_client)
            )
