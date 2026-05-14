"""
Unit tests for app.services.slide_sync.

Tests the in-memory state management helpers (get_current_slide,
clear_slide_state) and the async sync_slide_from_transcript function.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import slide_sync as _sync
from app.services.slide_sync import (
    SlidePosition,
    clear_slide_state,
    get_current_slide,
    sync_slide_from_transcript,
)


# ---------------------------------------------------------------------------
# Helpers to isolate per-test state
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clear_state():
    """Reset global _slide_state before and after each test."""
    _sync._slide_state.clear()
    yield
    _sync._slide_state.clear()


# ---------------------------------------------------------------------------
# get_current_slide / clear_slide_state
# ---------------------------------------------------------------------------

class TestSlideStateAccessors:

    def test_get_returns_none_when_unset(self):
        assert get_current_slide(99) is None

    def test_get_returns_set_position(self):
        pos = SlidePosition(lesson_id=1, lesson_title="History", slide_number=3)
        _sync._slide_state[1] = pos
        assert get_current_slide(1) == pos

    def test_clear_removes_entry(self):
        _sync._slide_state[5] = SlidePosition(1, "Biology", 7)
        clear_slide_state(5)
        assert get_current_slide(5) is None

    def test_clear_nonexistent_is_noop(self):
        clear_slide_state(999)  # should not raise

    def test_multiple_sessions_independent(self):
        _sync._slide_state[10] = SlidePosition(1, "Maths", 2)
        _sync._slide_state[20] = SlidePosition(2, "English", 5)
        assert get_current_slide(10).slide_number == 2
        assert get_current_slide(20).slide_number == 5
        clear_slide_state(10)
        assert get_current_slide(10) is None
        assert get_current_slide(20) is not None


# ---------------------------------------------------------------------------
# sync_slide_from_transcript
# ---------------------------------------------------------------------------

class TestSyncSlideFromTranscript:

    @pytest.fixture
    def mock_db(self):
        """Return a minimal AsyncSession-like mock."""
        db = AsyncMock(spec=AsyncSession)
        return db

    async def test_no_lessons_returns_without_setting_state(self, mock_db):
        # DB returns no lessons for this session
        empty_result = MagicMock()
        empty_result.all.return_value = []
        mock_db.execute = AsyncMock(return_value=empty_result)

        await sync_slide_from_transcript("some transcript text", session_id=1, db=mock_db)
        assert get_current_slide(1) is None

    async def test_high_distance_skips_sync(self, mock_db):
        lessons_result = MagicMock()
        lessons_result.all.return_value = [(1, "History of Rome")]
        mock_db.execute = AsyncMock(return_value=lessons_result)

        embedding = [0.1] * 768

        # ChromaDB returns a distance above the threshold (0.35)
        chroma_result = {
            "distances": [[0.9]],
            "metadatas": [[{"slide_number": "5", "lesson_id": "1"}]],
        }
        mock_col = MagicMock()
        mock_col.query.return_value = chroma_result

        with patch("app.services.slide_sync.embed", new_callable=AsyncMock, return_value=embedding):
            with patch("app.services.slide_sync.lesson_chunks_col", return_value=mock_col):
                await sync_slide_from_transcript("hello", session_id=2, db=mock_db)

        assert get_current_slide(2) is None

    async def test_zero_slide_number_skips_sync(self, mock_db):
        lessons_result = MagicMock()
        lessons_result.all.return_value = [(1, "History")]
        mock_db.execute = AsyncMock(return_value=lessons_result)

        embedding = [0.1] * 768
        chroma_result = {
            "distances": [[0.1]],  # good distance
            "metadatas": [[{"slide_number": "0", "lesson_id": "1"}]],
        }
        mock_col = MagicMock()
        mock_col.query.return_value = chroma_result

        with patch("app.services.slide_sync.embed", new_callable=AsyncMock, return_value=embedding):
            with patch("app.services.slide_sync.lesson_chunks_col", return_value=mock_col):
                await sync_slide_from_transcript("text", session_id=3, db=mock_db)

        assert get_current_slide(3) is None

    async def test_successful_sync_updates_state(self, mock_db):
        lessons_result = MagicMock()
        lessons_result.all.return_value = [(7, "The French Revolution")]
        mock_db.execute = AsyncMock(return_value=lessons_result)

        embedding = [0.1] * 768
        chroma_result = {
            "distances": [[0.2]],  # below 0.35 threshold
            "metadatas": [[{"slide_number": "4", "lesson_id": "7"}]],
        }
        mock_col = MagicMock()
        mock_col.query.return_value = chroma_result

        with patch("app.services.slide_sync.embed", new_callable=AsyncMock, return_value=embedding):
            with patch("app.services.slide_sync.lesson_chunks_col", return_value=mock_col):
                await sync_slide_from_transcript("Bastille was stormed", session_id=4, db=mock_db)

        pos = get_current_slide(4)
        assert pos is not None
        assert pos.slide_number == 4
        assert pos.lesson_id == 7
        assert pos.lesson_title == "The French Revolution"

    async def test_exception_is_swallowed(self, mock_db):
        mock_db.execute = AsyncMock(side_effect=RuntimeError("DB gone"))
        # Should not raise — failures are logged and swallowed
        await sync_slide_from_transcript("text", session_id=5, db=mock_db)
        assert get_current_slide(5) is None
