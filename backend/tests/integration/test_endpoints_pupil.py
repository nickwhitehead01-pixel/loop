"""
Integration tests for pupil endpoints.

Tests:
  GET  /pupil/{id}/sessions                    — list all sessions
  GET  /pupil/{id}/lessons                     — list all lessons
  GET  /pupil/{id}/sessions/{sid}/summary      — 404 when no summary
  POST /pupil/{id}/quiz/{qid}/answer           — submit answer to sent question
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain import (
    Lesson,
    LessonSession,
    QuizAttempt,
    QuizQuestion,
    QuizQuestionSource,
    QuizQuestionStatus,
    Quiz,
    QuizMode,
    Role,
    SessionStatus,
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
# GET /pupil/{id}/sessions
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestListPupilSessions:

    async def test_returns_empty_list_when_no_sessions(self, async_db: AsyncSession):
        client = _make_client(async_db)
        try:
            response = client.get("/pupil/1/sessions")
            assert response.status_code == 200
            assert response.json() == []
        finally:
            _clear(client)

    async def test_returns_sessions_newest_first(self, db_with_teacher):
        from datetime import datetime, timezone, timedelta
        db, teacher = db_with_teacher

        now = datetime.now(tz=timezone.utc)
        s1 = LessonSession(
            teacher_id=teacher.id, title="Old", status=SessionStatus.ended,
            started_at=now - timedelta(hours=2),
        )
        s2 = LessonSession(
            teacher_id=teacher.id, title="New", status=SessionStatus.live,
            started_at=now,
        )
        db.add(s1)
        db.add(s2)
        await db.flush()

        client = _make_client(db)
        try:
            response = client.get(f"/pupil/{teacher.id}/sessions")
            assert response.status_code == 200
            sessions = response.json()
            assert len(sessions) == 2
            # Newest first
            assert sessions[0]["title"] == "New"
        finally:
            _clear(client)

    async def test_session_item_has_expected_fields(self, db_with_session):
        db, session = db_with_session
        # pupil_id is irrelevant for this endpoint (not filtered)
        client = _make_client(db)
        try:
            response = client.get("/pupil/1/sessions")
            item = response.json()[0]
            assert "id" in item
            assert "title" in item
            assert "status" in item
        finally:
            _clear(client)


# ---------------------------------------------------------------------------
# GET /pupil/{id}/lessons
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestListPupilLessons:

    async def test_returns_empty_list_when_no_lessons(self, async_db: AsyncSession):
        client = _make_client(async_db)
        try:
            response = client.get("/pupil/1/lessons")
            assert response.status_code == 200
            assert response.json() == []
        finally:
            _clear(client)

    async def test_returns_all_lessons(self, db_with_lesson):
        db, lesson = db_with_lesson
        client = _make_client(db)
        try:
            response = client.get("/pupil/1/lessons")
            assert response.status_code == 200
            lessons = response.json()
            assert len(lessons) >= 1
            titles = [l["title"] for l in lessons]
            assert lesson.title in titles
        finally:
            _clear(client)


# ---------------------------------------------------------------------------
# GET /pupil/{id}/sessions/{sid}/summary
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestGetPupilSummary:

    async def test_returns_404_when_no_summary(self, db_with_session):
        db, session = db_with_session
        client = _make_client(db)
        try:
            response = client.get(f"/pupil/1/sessions/{session.id}/summary")
            assert response.status_code == 404
        finally:
            _clear(client)


# ---------------------------------------------------------------------------
# POST /pupil/{id}/quiz/{qid}/answer
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestSubmitQuizAnswer:

    @pytest.fixture
    async def db_with_sent_question(self, db_with_session):
        db, session = db_with_session

        quiz = Quiz(session_id=session.id, mode=QuizMode.one_at_a_time)
        db.add(quiz)
        await db.flush()
        await db.refresh(quiz)

        question = QuizQuestion(
            quiz_id=quiz.id,
            session_id=session.id,
            question_text="What is 2 + 2?",
            correct_answer="4",
            source=QuizQuestionSource.teacher_manual,
            status=QuizQuestionStatus.sent,
            time_limit_seconds=20,
        )
        db.add(question)
        await db.flush()
        await db.refresh(question)

        return db, session, question

    async def test_submit_answer_to_sent_question(self, db_with_sent_question):
        db, session, question = db_with_sent_question
        client = _make_client(db)
        try:
            response = client.post(
                f"/pupil/1/quiz/{question.id}/answer",
                json={"pupil_answer": "Four"},
            )
            assert response.status_code == 200
            data = response.json()
            assert data["question_id"] == question.id
            assert data["pupil_id"] == 1
            assert data["pupil_answer"] == "Four"
            assert data["grade"] is None  # graded later
        finally:
            _clear(client)

    async def test_submit_to_draft_question_returns_409(self, db_with_session):
        db, session = db_with_session

        quiz = Quiz(session_id=session.id, mode=QuizMode.one_at_a_time)
        db.add(quiz)
        await db.flush()
        await db.refresh(quiz)

        question = QuizQuestion(
            quiz_id=quiz.id,
            session_id=session.id,
            question_text="What is gravity?",
            correct_answer="A force.",
            source=QuizQuestionSource.teacher_manual,
            status=QuizQuestionStatus.draft,
            time_limit_seconds=20,
        )
        db.add(question)
        await db.flush()
        await db.refresh(question)

        client = _make_client(db)
        try:
            response = client.post(
                f"/pupil/1/quiz/{question.id}/answer",
                json={"pupil_answer": "Some answer"},
            )
            assert response.status_code == 409
        finally:
            _clear(client)

    async def test_submit_to_nonexistent_question_returns_404(self, async_db: AsyncSession):
        client = _make_client(async_db)
        try:
            response = client.post(
                "/pupil/1/quiz/99999/answer",
                json={"pupil_answer": "Some answer"},
            )
            assert response.status_code == 404
        finally:
            _clear(client)

    async def test_get_session_quiz_returns_sent_questions(self, db_with_sent_question):
        db, session, question = db_with_sent_question
        client = _make_client(db)
        try:
            response = client.get(f"/pupil/1/sessions/{session.id}/quiz")
            assert response.status_code == 200
            questions = response.json()
            assert len(questions) == 1
            assert questions[0]["question_text"] == "What is 2 + 2?"
            # Correct answer must NOT be in pupil-facing response
            assert "correct_answer" not in questions[0]
        finally:
            _clear(client)
