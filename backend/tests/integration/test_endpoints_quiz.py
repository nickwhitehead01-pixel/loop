"""
Integration tests for teacher-facing quiz endpoints.

Tests:
  POST /session/{sid}/quiz/start      — create quiz (idempotent)
  GET  /session/{sid}/quiz            — retrieve quiz state
  POST /session/{sid}/quiz/questions  — create a draft question
  POST /quiz/questions/{qid}/send     — transition draft → sent
  POST /quiz/questions/{qid}/close    — transition sent → closed
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain import (
    LessonSession,
    Quiz,
    QuizMode,
    QuizQuestion,
    QuizQuestionSource,
    QuizQuestionStatus,
    SessionStatus,
    Role,
    TranscriptChunk,
    User,
)


# ---------------------------------------------------------------------------
# Helpers
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
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def db_with_quiz(db_with_session):
    db, session = db_with_session
    quiz = Quiz(session_id=session.id, mode=QuizMode.one_at_a_time)
    db.add(quiz)
    await db.flush()
    await db.refresh(quiz)
    return db, session, quiz


@pytest.fixture
async def db_with_draft_question(db_with_quiz):
    db, session, quiz = db_with_quiz
    question = QuizQuestion(
        quiz_id=quiz.id,
        session_id=session.id,
        question_text="What is photosynthesis?",
        correct_answer="The process plants use to make food from sunlight.",
        source=QuizQuestionSource.teacher_manual,
        status=QuizQuestionStatus.draft,
        time_limit_seconds=30,
    )
    db.add(question)
    await db.flush()
    await db.refresh(question)
    return db, session, quiz, question


# ---------------------------------------------------------------------------
# POST /session/{sid}/quiz/start
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestStartQuiz:

    async def test_creates_quiz_for_live_session(self, db_with_session):
        db, session = db_with_session
        client = _make_client(db)
        try:
            response = client.post(
                f"/session/{session.id}/quiz/start",
                json={"mode": "one_at_a_time"},
            )
            assert response.status_code == 200
            data = response.json()
            assert data["session_id"] == session.id
        finally:
            _clear(client)

    async def test_idempotent_returns_existing_quiz(self, db_with_quiz):
        db, session, quiz = db_with_quiz
        client = _make_client(db)
        try:
            response1 = client.post(
                f"/session/{session.id}/quiz/start",
                json={"mode": "one_at_a_time"},
            )
            response2 = client.post(
                f"/session/{session.id}/quiz/start",
                json={"mode": "batch"},  # different mode — ignored
            )
            assert response1.status_code == 200
            assert response2.status_code == 200
            assert response1.json()["id"] == response2.json()["id"]
        finally:
            _clear(client)

    async def test_returns_409_for_ended_session(self, db_with_session):
        db, session = db_with_session
        session.status = SessionStatus.ended
        await db.flush()
        client = _make_client(db)
        try:
            response = client.post(
                f"/session/{session.id}/quiz/start",
                json={"mode": "one_at_a_time"},
            )
            assert response.status_code == 409
        finally:
            _clear(client)

    async def test_returns_404_for_nonexistent_session(self, async_db: AsyncSession):
        client = _make_client(async_db)
        try:
            response = client.post(
                "/session/99999/quiz/start",
                json={"mode": "one_at_a_time"},
            )
            assert response.status_code == 404
        finally:
            _clear(client)


# ---------------------------------------------------------------------------
# GET /session/{sid}/quiz
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestGetQuiz:

    async def test_returns_quiz_with_questions(self, db_with_draft_question):
        db, session, quiz, question = db_with_draft_question
        client = _make_client(db)
        try:
            response = client.get(f"/session/{session.id}/quiz")
            assert response.status_code == 200
            data = response.json()
            assert data["id"] == quiz.id
            assert len(data["questions"]) == 1
            assert data["questions"][0]["question_text"] == "What is photosynthesis?"
        finally:
            _clear(client)

    async def test_returns_404_when_no_quiz(self, db_with_session):
        db, session = db_with_session
        client = _make_client(db)
        try:
            response = client.get(f"/session/{session.id}/quiz")
            assert response.status_code == 404
        finally:
            _clear(client)


# ---------------------------------------------------------------------------
# POST /session/{sid}/quiz/questions
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestCreateQuestion:

    async def test_creates_draft_question(self, db_with_quiz):
        db, session, quiz = db_with_quiz
        client = _make_client(db)
        try:
            response = client.post(
                f"/session/{session.id}/quiz/questions",
                json={
                    "question_text": "What is a cell?",
                    "correct_answer": "The basic unit of life.",
                    "source": "teacher_manual",
                    "time_limit_seconds": 20,
                },
            )
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "draft"
            assert data["question_text"] == "What is a cell?"
            assert data["correct_answer"] == "The basic unit of life."
        finally:
            _clear(client)

    async def test_returns_409_when_no_quiz_started(self, db_with_session):
        db, session = db_with_session
        client = _make_client(db)
        try:
            response = client.post(
                f"/session/{session.id}/quiz/questions",
                json={
                    "question_text": "Q?",
                    "correct_answer": "A.",
                    "source": "teacher_manual",
                },
            )
            assert response.status_code == 409
        finally:
            _clear(client)


# ---------------------------------------------------------------------------
# POST /quiz/questions/{qid}/send
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestSendQuestion:

    async def test_transitions_draft_to_sent(self, db_with_draft_question):
        db, session, quiz, question = db_with_draft_question
        client = _make_client(db)
        try:
            response = client.post(f"/quiz/questions/{question.id}/send")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "sent"
            assert data["sent_at"] is not None
        finally:
            _clear(client)

    async def test_sending_already_sent_question_returns_409(self, db_with_draft_question):
        db, session, quiz, question = db_with_draft_question
        question.status = QuizQuestionStatus.sent
        await db.flush()
        client = _make_client(db)
        try:
            response = client.post(f"/quiz/questions/{question.id}/send")
            assert response.status_code == 409
        finally:
            _clear(client)

    async def test_returns_404_for_nonexistent_question(self, async_db: AsyncSession):
        client = _make_client(async_db)
        try:
            response = client.post("/quiz/questions/99999/send")
            assert response.status_code == 404
        finally:
            _clear(client)


# ---------------------------------------------------------------------------
# POST /quiz/questions/{qid}/close
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestCloseQuestion:

    async def test_transitions_sent_to_closed(self, db_with_draft_question):
        db, session, quiz, question = db_with_draft_question
        question.status = QuizQuestionStatus.sent
        await db.flush()
        client = _make_client(db)
        try:
            with patch(
                "app.services.quiz_grader.grade_attempts_for_question",
                new_callable=AsyncMock,
            ):
                response = client.post(f"/quiz/questions/{question.id}/close")
            assert response.status_code == 200
            assert response.json()["status"] == "closed"
        finally:
            _clear(client)

    async def test_closing_draft_question_returns_409(self, db_with_draft_question):
        db, session, quiz, question = db_with_draft_question
        # question is in 'draft' status — cannot be closed directly
        client = _make_client(db)
        try:
            response = client.post(f"/quiz/questions/{question.id}/close")
            assert response.status_code == 409
        finally:
            _clear(client)
