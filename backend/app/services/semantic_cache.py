"""Per-session semantic answer cache — avoids redundant LLM calls for identical questions.

A question whose embedding falls within `semantic_cache_threshold` cosine similarity
of an existing cache entry is served immediately from the cache.  Hit counts are tracked
so teachers / admins can inspect which questions are asked most frequently.

Embeddings are stored in ChromaDB (cosine distance); the SemanticCache SQLite table
retains metadata (question text, answer, hit_count) for analytics queries.
"""
from __future__ import annotations

import asyncio
import logging
import uuid

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.domain import SemanticCache
from app.services import ollama_client
from app.services.chroma_client import semantic_cache_col

logger = logging.getLogger(__name__)


async def lookup(
    question: str,
    db: AsyncSession,
    session_id: int | None = None,
    threshold: float | None = None,
    vector: list[float] | None = None,
) -> str | None:
    """Return cached answer if similarity >= threshold, else None.

    Increments `hit_count` on the SQLite row on a cache hit.
    If `session_id` is supplied, the lookup is scoped to that session only.
    If *vector* is supplied it is used directly — no embed call is made,
    saving a round-trip when the caller already has the embedding.
    """
    if threshold is None:
        threshold = settings.semantic_cache_threshold

    if vector is None:
        vector = await ollama_client.embed(question)
    col = semantic_cache_col()

    where_filter: dict | None = None
    if session_id is not None:
        where_filter = {"session_id": str(session_id)}

    try:
        kwargs: dict = {
            "query_embeddings": [vector],
            "n_results": 1,
            "include": ["distances", "metadatas"],
        }
        if where_filter:
            kwargs["where"] = where_filter
        results = col.query(**kwargs)
    except Exception:
        # Collection empty or query error — treat as miss
        logger.debug("SemanticCache lookup error — treating as miss", exc_info=True)
        return None

    distances = (results.get("distances") or [[]])[0]
    metadatas = (results.get("metadatas") or [[]])[0]

    if not distances or not metadatas:
        return None

    # ChromaDB cosine distance: 0 = identical, 1 = orthogonal
    similarity = 1.0 - float(distances[0])
    if similarity < threshold:
        return None

    meta = metadatas[0]
    answer = meta.get("answer", "")
    sqlite_id = meta.get("sqlite_id")

    if sqlite_id:
        await db.execute(
            update(SemanticCache)
            .where(SemanticCache.id == int(sqlite_id))
            .values(hit_count=SemanticCache.hit_count + 1)
        )
        await db.flush()

    logger.info("Semantic cache HIT (sim=%.3f): %.60s", similarity, question)
    return answer


async def store(
    question: str,
    answer: str,
    db: AsyncSession,
    session_id: int | None = None,
    vector: list[float] | None = None,
) -> None:
    """Embed and persist a new question/answer pair in ChromaDB + SQLite.

    If *vector* is supplied it is used directly — no embed call is made.
    """
    if vector is None:
        vector = await ollama_client.embed(question)

    # Persist metadata row in SQLite for analytics
    cache_row = SemanticCache(
        session_id=session_id,
        question=question,
        answer=answer,
    )
    db.add(cache_row)
    await db.flush()  # assigns cache_row.id

    # Store embedding + metadata in ChromaDB
    col = semantic_cache_col()
    meta: dict = {
        "answer": answer,
        "sqlite_id": str(cache_row.id),
        "hit_count": 0,
    }
    if session_id is not None:
        meta["session_id"] = str(session_id)

    await asyncio.to_thread(
        lambda: col.add(
            ids=[str(uuid.uuid4())],
            embeddings=[vector],
            documents=[question],
            metadatas=[meta],
        )
    )
    logger.info("Semantic cache STORE: %.60s", question)
