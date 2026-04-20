"""Per-session semantic answer cache — avoids redundant LLM calls for identical questions.

A question whose embedding falls within `semantic_cache_threshold` cosine similarity
of an existing cache entry is served immediately from the cache.  Hit counts are tracked
so teachers / admins can inspect which questions are asked most frequently.
"""
from __future__ import annotations

import logging

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.domain import SemanticCache
from app.services import ollama_client

logger = logging.getLogger(__name__)


async def lookup(
    question: str,
    db: AsyncSession,
    session_id: int | None = None,
    threshold: float | None = None,
) -> str | None:
    """Return cached answer if similarity >= threshold, else None.

    Increments `hit_count` on a cache hit so usage can be tracked.
    If `session_id` is supplied, the lookup is scoped to that session only.
    """
    if threshold is None:
        threshold = settings.semantic_cache_threshold

    vector = await ollama_client.embed(question)

    stmt = (
        select(
            SemanticCache.id,
            SemanticCache.answer,
            SemanticCache.embedding.cosine_distance(vector).label("distance"),
        )
        .order_by("distance")
        .limit(1)
    )
    if session_id is not None:
        stmt = stmt.where(SemanticCache.session_id == session_id)

    result = await db.execute(stmt)
    row = result.one_or_none()
    if row is None:
        return None

    similarity = 1.0 - float(row.distance)
    if similarity >= threshold:
        await db.execute(
            update(SemanticCache)
            .where(SemanticCache.id == row.id)
            .values(hit_count=SemanticCache.hit_count + 1)
        )
        await db.flush()
        logger.info("Semantic cache HIT (sim=%.3f): %.60s", similarity, question)
        return row.answer

    return None


async def store(
    question: str,
    answer: str,
    db: AsyncSession,
    session_id: int | None = None,
) -> None:
    """Embed and persist a new question/answer pair in the cache."""
    vector = await ollama_client.embed(question)
    db.add(SemanticCache(
        session_id=session_id,
        question=question,
        embedding=vector,
        answer=answer,
    ))
    await db.flush()
    logger.info("Semantic cache STORE: %.60s", question)
