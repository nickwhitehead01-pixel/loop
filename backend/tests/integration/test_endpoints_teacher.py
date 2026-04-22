"""
Integration tests for teacher endpoints — lesson upload, detail retrieval, summary persistence.
"""
from io import BytesIO
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain import Lesson, LessonFile, LessonChunk, User, Role


@pytest.mark.integration
class TestLessonUploadEndpoint:
    """Tests for POST /teacher/lessons — document upload and summary persistence."""

    async def test_upload_lesson_persists_summary(
        self, async_db: AsyncSession, mock_ollama_embed, mock_ollama_generate_full
    ):
        """Test that uploading a lesson generates and persists the summary."""
        from app.main import app
        from app.core.database import get_db

        teacher = User(name="Test Teacher", role=Role.teacher)
        async_db.add(teacher)
        await async_db.flush()
        await async_db.refresh(teacher)

        async def mock_get_db():
            yield async_db

        app.dependency_overrides[get_db] = mock_get_db
        client = TestClient(app)

        file_content = b"This is a test lesson about quadratic equations."
        files = {"files": ("test_lesson.txt", BytesIO(file_content), "text/plain")}
        data = {
            "title": "Test Lesson",
            "teacher_id": teacher.id,
        }

        with patch("app.api.endpoints_teacher.process_lesson", new_callable=AsyncMock) as mock_process:
            mock_process.return_value = 3

            response = client.post("/teacher/lessons", files=files, data=data)

        assert response.status_code == 200
        response_data = response.json()
        assert "id" in response_data
        lesson_id = response_data["id"]

        from sqlalchemy import select
        result = await async_db.execute(select(Lesson).where(Lesson.id == lesson_id))
        saved_lesson = result.scalar_one_or_none()

        assert saved_lesson is not None
        assert saved_lesson.title == "Test Lesson"
        assert saved_lesson.teacher_id == teacher.id
        assert saved_lesson.summary is not None
        assert "quadratic" in saved_lesson.summary.lower()
        assert saved_lesson.summary_generated_at is not None

        app.dependency_overrides.clear()

    async def test_upload_lesson_fails_gracefully_if_summary_generation_fails(
        self, async_db: AsyncSession, mock_ollama_embed
    ):
        """Test that upload succeeds even if summary generation fails."""
        from app.main import app
        from app.core.database import get_db

        teacher = User(name="Test Teacher", role=Role.teacher)
        async_db.add(teacher)
        await async_db.flush()
        await async_db.refresh(teacher)

        async def mock_get_db():
            yield async_db

        app.dependency_overrides[get_db] = mock_get_db
        client = TestClient(app)

        file_content = b"This is a test lesson."
        files = {"files": ("test_lesson.txt", BytesIO(file_content), "text/plain")}
        data = {
            "title": "Test Lesson",
            "teacher_id": teacher.id,
        }

        with patch("app.api.endpoints_teacher.process_lesson", new_callable=AsyncMock) as mock_process:
            mock_process.return_value = 2
            with patch("app.api.endpoints_teacher.summarise_lesson", new_callable=AsyncMock) as mock_summarise:
                mock_summarise.side_effect = Exception("LLM service down")

                response = client.post("/teacher/lessons", files=files, data=data)

        assert response.status_code == 200
        response_data = response.json()
        lesson_id = response_data["id"]

        from sqlalchemy import select
        result = await async_db.execute(select(Lesson).where(Lesson.id == lesson_id))
        saved_lesson = result.scalar_one_or_none()

        assert saved_lesson is not None
        assert saved_lesson.title == "Test Lesson"
        assert saved_lesson.summary is None
        assert saved_lesson.summary_generated_at is None

        app.dependency_overrides.clear()

    async def test_upload_lesson_includes_file_count(
        self, async_db: AsyncSession, mock_ollama_embed, mock_ollama_generate_full
    ):
        """Test that upload response includes file_count."""
        from app.main import app
        from app.core.database import get_db

        teacher = User(name="Test Teacher", role=Role.teacher)
        async_db.add(teacher)
        await async_db.flush()
        await async_db.refresh(teacher)

        async def mock_get_db():
            yield async_db

        app.dependency_overrides[get_db] = mock_get_db
        client = TestClient(app)

        files = [
            ("files", ("file1.txt", BytesIO(b"Content 1"), "text/plain")),
            ("files", ("file2.txt", BytesIO(b"Content 2"), "text/plain")),
        ]
        data = {
            "title": "Multi-File Lesson",
            "teacher_id": teacher.id,
        }

        with patch("app.api.endpoints_teacher.process_lesson", new_callable=AsyncMock):
            response = client.post("/teacher/lessons", files=files, data=data)

        assert response.status_code == 200
        assert response.json()["file_count"] == 2

        app.dependency_overrides.clear()


@pytest.mark.integration
class TestLessonDetailEndpoint:
    """Tests for GET /teacher/lessons/{lesson_id} — detail retrieval and summary handling."""

    async def test_get_lesson_detail_returns_persisted_summary(
        self, async_db: AsyncSession
    ):
        """Test that getting lesson detail returns the persisted summary."""
        from app.main import app
        from app.core.database import get_db

        teacher = User(name="Test Teacher", role=Role.teacher)
        async_db.add(teacher)
        await async_db.flush()

        lesson = Lesson(
            title="Test Lesson",
            teacher_id=teacher.id,
            file_path="/tmp/test.pdf",
            summary="This is a persisted summary about quadratic equations.",
            summary_generated_at=datetime.now(),
        )
        async_db.add(lesson)
        await async_db.flush()
        await async_db.refresh(lesson)

        async def mock_get_db():
            yield async_db

        app.dependency_overrides[get_db] = mock_get_db
        client = TestClient(app)

        response = client.get(f"/teacher/lessons/{lesson.id}")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == lesson.id
        assert data["summary"] == "This is a persisted summary about quadratic equations."
        assert data["summary_generated_at"] is not None

        app.dependency_overrides.clear()

    async def test_get_lesson_detail_generates_summary_if_missing(
        self, async_db: AsyncSession, mock_ollama_embed, mock_ollama_generate_full
    ):
        """Test that getting detail generates summary on-the-fly if not persisted."""
        from app.main import app
        from app.core.database import get_db

        teacher = User(name="Test Teacher", role=Role.teacher)
        async_db.add(teacher)
        await async_db.flush()

        lesson = Lesson(
            title="Test Lesson",
            teacher_id=teacher.id,
            file_path="/tmp/test.pdf",
            summary=None,
            summary_generated_at=None,
        )
        async_db.add(lesson)
        await async_db.flush()
        await async_db.refresh(lesson)

        chunk = LessonChunk(
            lesson_id=lesson.id,
            content="Test content about quadratic equations",
            embedding=[0.1] * 768,
        )
        async_db.add(chunk)
        await async_db.flush()

        async def mock_get_db():
            yield async_db

        app.dependency_overrides[get_db] = mock_get_db
        client = TestClient(app)

        response = client.get(f"/teacher/lessons/{lesson.id}")

        assert response.status_code == 200
        data = response.json()
        assert data["summary"] is not None
        assert "quadratic" in data["summary"].lower()

        from sqlalchemy import select
        result = await async_db.execute(select(Lesson).where(Lesson.id == lesson.id))
        updated_lesson = result.scalar_one_or_none()
        assert updated_lesson.summary is not None
        assert updated_lesson.summary_generated_at is not None

        app.dependency_overrides.clear()

    async def test_get_lesson_detail_includes_chunks(
        self, async_db: AsyncSession
    ):
        """Test that lesson detail includes indexed chunks."""
        from app.main import app
        from app.core.database import get_db

        teacher = User(name="Test Teacher", role=Role.teacher)
        async_db.add(teacher)
        await async_db.flush()

        lesson = Lesson(
            title="Test Lesson",
            teacher_id=teacher.id,
            file_path="/tmp/test.pdf",
            summary="Test summary",
            summary_generated_at=datetime.now(),
        )
        async_db.add(lesson)
        await async_db.flush()

        for i in range(3):
            chunk = LessonChunk(
                lesson_id=lesson.id,
                content=f"Chunk {i}",
                embedding=[0.1] * 768,
            )
            async_db.add(chunk)
        await async_db.flush()

        async def mock_get_db():
            yield async_db

        app.dependency_overrides[get_db] = mock_get_db
        client = TestClient(app)

        response = client.get(f"/teacher/lessons/{lesson.id}")

        assert response.status_code == 200
        data = response.json()
        assert len(data["chunks"]) == 3
        assert data["chunk_count"] == 3
        assert data["chunks"][0]["content"] == "Chunk 0"

        app.dependency_overrides.clear()

    async def test_get_lesson_detail_returns_404_for_nonexistent(
        self, async_db: AsyncSession
    ):
        """Test that requesting a nonexistent lesson returns 404."""
        from app.main import app
        from app.core.database import get_db

        async def mock_get_db():
            yield async_db

        app.dependency_overrides[get_db] = mock_get_db
        client = TestClient(app)

        response = client.get("/teacher/lessons/999999")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

        app.dependency_overrides.clear()
