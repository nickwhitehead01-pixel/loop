"""
Unit tests for teacher_rag.py — summarise_lesson, process_lesson.
"""
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.teacher_rag import summarise_lesson
from app.models.domain import Lesson, LessonChunk, User, Role


@pytest.mark.unit
class TestSummariseLesson:
    """Tests for the summarise_lesson function."""

    async def test_summarise_lesson_with_empty_chunks(
        self, async_db: AsyncSession, mock_ollama_generate_full
    ):
        """Test summarise_lesson returns 'No content' for a lesson with no chunks."""
        teacher = User(name="Test Teacher", role=Role.teacher)
        async_db.add(teacher)
        await async_db.flush()

        lesson = Lesson(
            title="Empty Lesson",
            teacher_id=teacher.id,
            file_path="/tmp/empty.pdf",
        )
        async_db.add(lesson)
        await async_db.flush()
        await async_db.refresh(lesson)

        result = await summarise_lesson(lesson.id, async_db)
        assert result == "No content found for this lesson."
        mock_ollama_generate_full.assert_not_called()

    async def test_summarise_lesson_with_chunks(
        self, async_db: AsyncSession, mock_ollama_embed, mock_ollama_generate_full
    ):
        """Test summarise_lesson generates a summary when chunks exist."""
        teacher = User(name="Test Teacher", role=Role.teacher)
        async_db.add(teacher)
        await async_db.flush()

        lesson = Lesson(
            title="Test Lesson",
            teacher_id=teacher.id,
            file_path="/tmp/test.pdf",
        )
        async_db.add(lesson)
        await async_db.flush()

        for i in range(3):
            chunk = LessonChunk(
                lesson_id=lesson.id,
                content=f"Lesson content chunk {i}: This is important information.",
                embedding=[0.1] * 768,
            )
            async_db.add(chunk)
        await async_db.flush()

        result = await summarise_lesson(lesson.id, async_db)

        assert mock_ollama_generate_full.called
        mock_summary = mock_ollama_generate_full.return_value
        assert result == mock_summary
        assert "quadratic" in result.lower()

    async def test_summarise_lesson_truncates_long_content(
        self, async_db: AsyncSession, mock_ollama_embed, mock_ollama_generate_full
    ):
        """Test summarise_lesson truncates content > 12k chars."""
        teacher = User(name="Test Teacher", role=Role.teacher)
        async_db.add(teacher)
        await async_db.flush()

        lesson = Lesson(
            title="Test Lesson",
            teacher_id=teacher.id,
            file_path="/tmp/test.pdf",
        )
        async_db.add(lesson)
        await async_db.flush()

        large_content = "x" * 15000
        chunk = LessonChunk(
            lesson_id=lesson.id,
            content=large_content,
            embedding=[0.1] * 768,
        )
        async_db.add(chunk)
        await async_db.flush()

        result = await summarise_lesson(lesson.id, async_db)

        assert mock_ollama_generate_full.called
        call_args = mock_ollama_generate_full.call_args
        messages = call_args.kwargs.get("messages") or call_args[0][0]
        user_msg = messages[0]["content"]
        assert "[content truncated]" in user_msg
        assert len(user_msg) < 15000

    async def test_summarise_lesson_calls_correct_model(
        self, async_db: AsyncSession, mock_ollama_embed, mock_ollama_generate_full
    ):
        """Test summarise_lesson uses the teacher model from settings."""
        teacher = User(name="Test Teacher", role=Role.teacher)
        async_db.add(teacher)
        await async_db.flush()

        lesson = Lesson(
            title="Test Lesson",
            teacher_id=teacher.id,
            file_path="/tmp/test.pdf",
        )
        async_db.add(lesson)
        await async_db.flush()

        chunk = LessonChunk(
            lesson_id=lesson.id,
            content="Test content",
            embedding=[0.1] * 768,
        )
        async_db.add(chunk)
        await async_db.flush()

        result = await summarise_lesson(lesson.id, async_db)

        assert mock_ollama_generate_full.called
        call_kwargs = mock_ollama_generate_full.call_args.kwargs
        assert "model" in call_kwargs
        assert call_kwargs["model"] == "gemma4:e2b"


@pytest.mark.unit
class TestProcessLesson:
    """Tests for the process_lesson function."""

    async def test_process_lesson_embeds_chunks(
        self, async_db: AsyncSession, mock_ollama_embed, sample_pdf_bytes
    ):
        """Test process_lesson creates chunks and embeds them."""
        from app.agents.teacher_rag import process_lesson

        teacher = User(name="Test Teacher", role=Role.teacher)
        async_db.add(teacher)
        await async_db.flush()

        lesson = Lesson(
            title="Test Lesson",
            teacher_id=teacher.id,
            file_path="/tmp/test.pdf",
        )
        async_db.add(lesson)
        await async_db.flush()

        with patch("app.agents.teacher_rag.ingest_lesson", new_callable=AsyncMock) as mock_ingest:
            mock_ingest.return_value = 5

            chunk_count = await process_lesson(
                lesson.id, sample_pdf_bytes, async_db, filename="test.pdf"
            )

        assert chunk_count == 5
        assert mock_ingest.called
