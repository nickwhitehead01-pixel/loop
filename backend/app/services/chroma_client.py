"""
ChromaDB persistent client — single module-level instance shared across the app.

Five collections store all vector embeddings for the platform:
  lesson_chunks     — 500-token chunks from uploaded lesson documents
  transcript_chunks — live classroom transcription sentence buckets
  pupil_memories    — long-term episodic facts per pupil
  teacher_memories  — long-term episodic facts per teacher
  semantic_cache    — per-session answer cache keyed by question embedding

All collections use cosine distance so that (1 - distance) gives a [0,1]
similarity score.
"""
from __future__ import annotations

import chromadb
from chromadb.config import Settings as ChromaSettings

from app.core.config import settings

# ---------------------------------------------------------------------------
# Persistent client — data survives server restarts in settings.chroma_dir
# ---------------------------------------------------------------------------

_client: chromadb.PersistentClient | None = None


def get_client() -> chromadb.PersistentClient:
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(
            path=settings.chroma_dir,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
    return _client


# ---------------------------------------------------------------------------
# Collection accessors — lazy, idempotent (get_or_create)
# ---------------------------------------------------------------------------

_COSINE = {"hnsw:space": "cosine"}


def lesson_chunks_col() -> chromadb.Collection:
    return get_client().get_or_create_collection("lesson_chunks", metadata=_COSINE)


def transcript_chunks_col() -> chromadb.Collection:
    return get_client().get_or_create_collection("transcript_chunks", metadata=_COSINE)


def pupil_memories_col() -> chromadb.Collection:
    return get_client().get_or_create_collection("pupil_memories", metadata=_COSINE)


def teacher_memories_col() -> chromadb.Collection:
    return get_client().get_or_create_collection("teacher_memories", metadata=_COSINE)


def semantic_cache_col() -> chromadb.Collection:
    return get_client().get_or_create_collection("semantic_cache", metadata=_COSINE)


def init_collections() -> None:
    """Call at application startup to ensure all collections exist."""
    lesson_chunks_col()
    transcript_chunks_col()
    pupil_memories_col()
    teacher_memories_col()
    semantic_cache_col()
