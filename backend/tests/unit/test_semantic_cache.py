"""
Unit tests for app.services.semantic_cache.lookup and .store.

ChromaDB and Ollama are fully mocked — these tests run entirely in-memory.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import semantic_cache


class TestLookup:

    async def test_cache_miss_no_results(self, async_db: AsyncSession):
        col_mock = MagicMock()
        col_mock.query.return_value = {"distances": [[]], "metadatas": [[]]}

        with patch("app.services.semantic_cache.ollama_client.embed", new_callable=AsyncMock,
                   return_value=[0.1] * 768):
            with patch("app.services.semantic_cache.semantic_cache_col", return_value=col_mock):
                result = await semantic_cache.lookup("What is DNA?", async_db)

        assert result is None

    async def test_cache_miss_below_threshold(self, async_db: AsyncSession):
        # Distance of 0.9 → similarity = 0.1, below default threshold (~0.85)
        col_mock = MagicMock()
        col_mock.query.return_value = {
            "distances": [[0.9]],
            "metadatas": [[{"answer": "cached", "sqlite_id": None}]],
        }

        with patch("app.services.semantic_cache.ollama_client.embed", new_callable=AsyncMock,
                   return_value=[0.1] * 768):
            with patch("app.services.semantic_cache.semantic_cache_col", return_value=col_mock):
                result = await semantic_cache.lookup("What is DNA?", async_db, threshold=0.85)

        assert result is None

    async def test_cache_hit_returns_answer(self, async_db: AsyncSession):
        # Distance 0.05 → similarity 0.95, above threshold
        col_mock = MagicMock()
        col_mock.query.return_value = {
            "distances": [[0.05]],
            "metadatas": [[{"answer": "DNA is the molecule of life.", "sqlite_id": None}]],
        }

        with patch("app.services.semantic_cache.ollama_client.embed", new_callable=AsyncMock,
                   return_value=[0.1] * 768):
            with patch("app.services.semantic_cache.semantic_cache_col", return_value=col_mock):
                result = await semantic_cache.lookup(
                    "What is DNA?", async_db, threshold=0.85
                )

        assert result == "DNA is the molecule of life."

    async def test_chroma_exception_treated_as_miss(self, async_db: AsyncSession):
        col_mock = MagicMock()
        col_mock.query.side_effect = RuntimeError("Collection empty")

        with patch("app.services.semantic_cache.ollama_client.embed", new_callable=AsyncMock,
                   return_value=[0.1] * 768):
            with patch("app.services.semantic_cache.semantic_cache_col", return_value=col_mock):
                result = await semantic_cache.lookup("Any question?", async_db)

        assert result is None

    async def test_session_scoped_lookup_passes_where_filter(self, async_db: AsyncSession):
        col_mock = MagicMock()
        col_mock.query.return_value = {"distances": [[]], "metadatas": [[]]}

        with patch("app.services.semantic_cache.ollama_client.embed", new_callable=AsyncMock,
                   return_value=[0.1] * 768):
            with patch("app.services.semantic_cache.semantic_cache_col", return_value=col_mock):
                await semantic_cache.lookup("Q?", async_db, session_id=42)

        call_kwargs = col_mock.query.call_args.kwargs
        assert call_kwargs.get("where") == {"session_id": "42"}


class TestStore:

    async def test_store_creates_sqlite_row(self, async_db: AsyncSession):
        col_mock = MagicMock()
        col_mock.add.return_value = None

        with patch("app.services.semantic_cache.ollama_client.embed", new_callable=AsyncMock,
                   return_value=[0.1] * 768):
            with patch("app.services.semantic_cache.semantic_cache_col", return_value=col_mock):
                await semantic_cache.store(
                    "What is an electron?",
                    "An electron is a subatomic particle.",
                    async_db,
                )

        # The SQLite row should have been added via db.add
        from app.models.domain import SemanticCache as CacheModel
        from sqlalchemy import select
        result = await async_db.execute(select(CacheModel))
        rows = result.scalars().all()
        assert len(rows) == 1
        assert rows[0].question == "What is an electron?"
        assert rows[0].answer == "An electron is a subatomic particle."

    async def test_store_with_session_id(self, async_db: AsyncSession):
        col_mock = MagicMock()

        with patch("app.services.semantic_cache.ollama_client.embed", new_callable=AsyncMock,
                   return_value=[0.1] * 768):
            with patch("app.services.semantic_cache.semantic_cache_col", return_value=col_mock):
                await semantic_cache.store(
                    "Why is the sky blue?",
                    "Light scattering.",
                    async_db,
                    session_id=7,
                )

        from app.models.domain import SemanticCache as CacheModel
        from sqlalchemy import select
        result = await async_db.execute(select(CacheModel))
        row = result.scalars().first()
        assert row.session_id == 7
