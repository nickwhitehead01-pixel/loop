"""
Unit tests for app.services.lesson_summary_worker.

Tests the per-lesson helper functions (_try_summarise, _try_precompute)
with mocked DB sessions and Ollama clients.  The poll loop itself
(start_summary_worker) is not tested here — it's an infinite loop.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain import Lesson, LessonChunk, Role, User
from app.services.lesson_summary_worker import (
    PRECOMPUTE_MAX_ATTEMPTS,
    _try_precompute,
    _try_summarise,
)


# ---------------------------------------------------------------------------
# _try_summarise
# ---------------------------------------------------------------------------

class TestTrySummarise:

    async def test_successful_summary_saved(self, db_with_lesson):
        db, lesson = db_with_lesson
        lesson_id = lesson.id

        # Add a chunk so summarise_lesson can find content
        chunk = LessonChunk(lesson_id=lesson_id, content="Important history content.", chunk_index=0)
        db.add(chunk)
        await db.flush()

        good_summary = "A rich summary of the lesson."

        # Patch AsyncSessionLocal so the worker opens our test DB session
        async def fake_session_cm():
            return db

        class FakeSessionLocal:
            async def __aenter__(self):
                return db
            async def __aexit__(self, *args):
                pass

        with patch("app.services.lesson_summary_worker.summarise_lesson",
                   new_callable=AsyncMock, return_value=good_summary):
            with patch("app.services.lesson_summary_worker.AsyncSessionLocal",
                       return_value=FakeSessionLocal()):
                await _try_summarise(lesson_id)

        await db.refresh(lesson)
        assert lesson.summary == good_summary
        assert lesson.summary_generated_at is not None

    async def test_unusable_summary_not_saved(self, db_with_lesson):
        db, lesson = db_with_lesson
        lesson_id = lesson.id

        class FakeSessionLocal:
            async def __aenter__(self):
                return db
            async def __aexit__(self, *args):
                pass

        with patch("app.services.lesson_summary_worker.summarise_lesson",
                   new_callable=AsyncMock, return_value="Summary generation failed."):
            with patch("app.services.lesson_summary_worker.AsyncSessionLocal",
                       return_value=FakeSessionLocal()):
                await _try_summarise(lesson_id)

        await db.refresh(lesson)
        assert lesson.summary is None

    async def test_exception_does_not_propagate(self, db_with_lesson):
        db, lesson = db_with_lesson

        class FakeSessionLocal:
            async def __aenter__(self):
                return db
            async def __aexit__(self, *args):
                pass

        with patch("app.services.lesson_summary_worker.summarise_lesson",
                   new_callable=AsyncMock, side_effect=RuntimeError("LLM down")):
            with patch("app.services.lesson_summary_worker.AsyncSessionLocal",
                       return_value=FakeSessionLocal()):
                # Should not raise
                await _try_summarise(lesson.id)


# ---------------------------------------------------------------------------
# _try_precompute
# ---------------------------------------------------------------------------

class TestTryPrecompute:

    async def test_skips_when_max_attempts_reached(self, db_with_lesson):
        db, lesson = db_with_lesson
        lesson.precomputed_features_attempts = PRECOMPUTE_MAX_ATTEMPTS
        await db.flush()

        class FakeSessionLocal:
            async def __aenter__(self):
                return db
            async def __aexit__(self, *args):
                pass

        with patch("app.services.lesson_summary_worker.precompute_features",
                   new_callable=AsyncMock) as mock_precompute:
            with patch("app.services.lesson_summary_worker.AsyncSessionLocal",
                       return_value=FakeSessionLocal()):
                await _try_precompute(lesson.id)

        mock_precompute.assert_not_called()

    async def test_skips_when_no_chunks(self, db_with_lesson):
        db, lesson = db_with_lesson
        # No chunks added

        class FakeSessionLocal:
            async def __aenter__(self):
                return db
            async def __aexit__(self, *args):
                pass

        with patch("app.services.lesson_summary_worker.precompute_features",
                   new_callable=AsyncMock) as mock_precompute:
            with patch("app.services.lesson_summary_worker.AsyncSessionLocal",
                       return_value=FakeSessionLocal()):
                await _try_precompute(lesson.id)

        mock_precompute.assert_not_called()

    async def test_successful_precompute_sets_fields(self, db_with_lesson):
        db, lesson = db_with_lesson
        lesson_id = lesson.id

        chunk = LessonChunk(lesson_id=lesson_id, content="Cell biology content.", chunk_index=0)
        db.add(chunk)
        await db.flush()

        glossary = [{"term": "Mitosis", "explanation": "Cell division."}]
        cards = [{"id": "card_abc", "question": "Why?", "triggers": ["division"],
                  "color": "blue", "trigger_embedding": [0.1] * 768}]

        class FakeSessionLocal:
            async def __aenter__(self):
                return db
            async def __aexit__(self, *args):
                pass

        with patch("app.services.lesson_summary_worker.precompute_features",
                   new_callable=AsyncMock, return_value=(glossary, cards)):
            with patch("app.services.lesson_summary_worker.AsyncSessionLocal",
                       return_value=FakeSessionLocal()):
                await _try_precompute(lesson_id)

        await db.refresh(lesson)
        assert lesson.glossary == glossary
        assert lesson.prompt_cards == cards
        assert lesson.precomputed_features_at is not None
        assert lesson.precomputed_features_attempts == 0

    async def test_precompute_failure_increments_attempts(self, db_with_lesson):
        db, lesson = db_with_lesson
        lesson_id = lesson.id
        initial_attempts = lesson.precomputed_features_attempts

        chunk = LessonChunk(lesson_id=lesson_id, content="Some content.", chunk_index=0)
        db.add(chunk)
        await db.flush()

        class FakeSessionLocal:
            async def __aenter__(self):
                return db
            async def __aexit__(self, *args):
                pass

        with patch("app.services.lesson_summary_worker.precompute_features",
                   new_callable=AsyncMock, side_effect=RuntimeError("Gemma crashed")):
            with patch("app.services.lesson_summary_worker.AsyncSessionLocal",
                       return_value=FakeSessionLocal()):
                await _try_precompute(lesson_id)

        await db.refresh(lesson)
        assert lesson.precomputed_features_attempts == initial_attempts + 1
        assert lesson.precomputed_features_at is None
