"""
Integration tests for session endpoints.

Tests the HTTP API layer (no WebSocket tests):
  POST  /session/start            — create a live session
  POST  /session/{id}/end         — end a session
  GET   /session/{id}/transcript  — retrieve transcript chunks
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain import (
    Lesson,
    LessonSession,
    Role,
    SessionStatus,
    TranscriptChunk,
    User,
)


# ---------------------------------------------------------------------------
# Helper: wire test DB into app
# ---------------------------------------------------------------------------

def _make_client(async_db: AsyncSession) -> TestClient:
    from app.core.database import get_db
    from app.main import app

    async def override_get_db():
        yield async_db

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


def _clear(client: TestClient) -> None:
    from app.main import app
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# POST /session/start
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestStartSession:

    async def test_creates_live_session(self, db_with_lesson):
        db, lesson = db_with_lesson
        client = _make_client(db)

        try:
            response = client.post("/session/start", json={
                "teacher_id": lesson.teacher_id,
                "lesson_id": lesson.id,
            })
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "live"
            assert data["teacher_id"] == lesson.teacher_id
        finally:
            _clear(client)

    async def test_returns_404_for_nonexistent_lesson(self, db_with_teacher):
        db, teacher = db_with_teacher
        client = _make_client(db)

        try:
            response = client.post("/session/start", json={
                "teacher_id": teacher.id,
                "lesson_id": 99999,
            })
            assert response.status_code == 404
        finally:
            _clear(client)

    async def test_returns_404_for_wrong_teacher(self, db_with_lesson):
        db, lesson = db_with_lesson
        client = _make_client(db)

        try:
            response = client.post("/session/start", json={
                "teacher_id": 99999,
                "lesson_id": lesson.id,
            })
            assert response.status_code == 404
        finally:
            _clear(client)

    async def test_title_defaults_to_lesson_title(self, db_with_lesson):
        db, lesson = db_with_lesson
        client = _make_client(db)

        try:
            response = client.post("/session/start", json={
                "teacher_id": lesson.teacher_id,
                "lesson_id": lesson.id,
            })
            assert response.status_code == 200
            assert lesson.title in response.json()["title"]
        finally:
            _clear(client)

    async def test_custom_title_used_when_provided(self, db_with_lesson):
        db, lesson = db_with_lesson
        client = _make_client(db)

        try:
            response = client.post("/session/start", json={
                "teacher_id": lesson.teacher_id,
                "lesson_id": lesson.id,
                "title": "Custom Session Title",
            })
            assert response.status_code == 200
            assert response.json()["title"] == "Custom Session Title"
        finally:
            _clear(client)


# ---------------------------------------------------------------------------
# POST /session/{id}/end
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestEndSession:

    async def test_ends_live_session(self, db_with_session):
        db, session = db_with_session
        client = _make_client(db)

        try:
            with patch(
                "app.services.summary.generate_session_artifacts",
                new_callable=AsyncMock,
            ):
                response = client.post(f"/session/{session.id}/end")
            assert response.status_code == 200
            assert response.json()["status"] == "ended"
        finally:
            _clear(client)

    async def test_ended_session_has_ended_at_timestamp(self, db_with_session):
        db, session = db_with_session
        client = _make_client(db)

        try:
            with patch(
                "app.services.summary.generate_session_artifacts",
                new_callable=AsyncMock,
            ):
                response = client.post(f"/session/{session.id}/end")
            assert response.json()["ended_at"] is not None
        finally:
            _clear(client)

    async def test_404_for_nonexistent_session(self, async_db):
        client = _make_client(async_db)
        try:
            response = client.post("/session/99999/end")
            assert response.status_code == 404
        finally:
            _clear(client)

    async def test_double_end_returns_400(self, db_with_session):
        db, session = db_with_session
        session.status = SessionStatus.ended
        await db.flush()
        client = _make_client(db)

        try:
            response = client.post(f"/session/{session.id}/end")
            assert response.status_code == 400
        finally:
            _clear(client)


# ---------------------------------------------------------------------------
# GET /session/{id}/transcript
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestGetTranscript:

    async def test_returns_empty_list_when_no_chunks(self, db_with_session):
        db, session = db_with_session
        client = _make_client(db)

        try:
            response = client.get(f"/session/{session.id}/transcript")
            assert response.status_code == 200
            assert response.json() == []
        finally:
            _clear(client)

    async def test_returns_chunks_ordered_by_timestamp(self, db_with_session):
        db, session = db_with_session

        db.add(TranscriptChunk(session_id=session.id, content="Second.", timestamp_ms=2000))
        db.add(TranscriptChunk(session_id=session.id, content="First.", timestamp_ms=1000))
        await db.flush()

        client = _make_client(db)
        try:
            response = client.get(f"/session/{session.id}/transcript")
            assert response.status_code == 200
            chunks = response.json()
            assert len(chunks) == 2
            assert chunks[0]["content"] == "First."
            assert chunks[1]["content"] == "Second."
        finally:
            _clear(client)

    async def test_transcript_items_have_expected_fields(self, db_with_session):
        db, session = db_with_session
        db.add(TranscriptChunk(session_id=session.id, content="Hello class.", timestamp_ms=0))
        await db.flush()

        client = _make_client(db)
        try:
            response = client.get(f"/session/{session.id}/transcript")
            item = response.json()[0]
            assert "id" in item
            assert "session_id" in item
            assert "content" in item
            assert "timestamp_ms" in item
        finally:
            _clear(client)
