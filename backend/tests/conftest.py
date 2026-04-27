"""
Shared test fixtures for teacher and pupil agents, endpoints, and services.
"""
from datetime import datetime
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.models.domain import (
    Lesson,
    LessonChunk,
    LessonFile,
    LessonSession,
    Role,
    SessionStatus,
    TeacherConversation,
    TeacherMessage,
    TranscriptChunk,
    User,
)


# ──────────────────────────────────────────────────────────────────────────
# Database fixtures
# ──────────────────────────────────────────────────────────────────────────

@pytest.fixture
async def async_db():
    """Create an in-memory SQLite test database."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        echo=False,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    SessionLocal = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async with SessionLocal() as session:
        yield session
        await session.rollback()

    await engine.dispose()


@pytest.fixture
async def db_with_teacher(async_db: AsyncSession) -> tuple[AsyncSession, User]:
    """Fixture providing a DB session and a pre-created teacher user."""
    teacher = User(name="Test Teacher", role=Role.teacher)
    async_db.add(teacher)
    await async_db.flush()
    await async_db.refresh(teacher)
    return async_db, teacher


@pytest.fixture
async def db_with_session(db_with_teacher: tuple[AsyncSession, User]) -> tuple[AsyncSession, LessonSession]:
    """Fixture providing a DB session and a pre-created lesson session."""
    db, teacher = db_with_teacher
    session = LessonSession(
        teacher_id=teacher.id,
        title="Test Lesson Session",
        status=SessionStatus.live,
    )
    db.add(session)
    await db.flush()
    await db.refresh(session)
    return db, session


@pytest.fixture
async def db_with_lesson(db_with_teacher: tuple[AsyncSession, User]) -> tuple[AsyncSession, Lesson]:
    """Fixture providing a DB session and a pre-created lesson."""
    db, teacher = db_with_teacher
    lesson = Lesson(
        title="Test Lesson",
        teacher_id=teacher.id,
        file_path="/tmp/test_lesson.pdf",
    )
    db.add(lesson)
    await db.flush()
    await db.refresh(lesson)
    return db, lesson


# ──────────────────────────────────────────────────────────────────────────
# Ollama client mocks
# ──────────────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_ollama_embed():
    """Mock ollama_client.embed to return a fixed 768-dim vector."""
    fixed_vector = [0.1] * 768
    with patch("app.services.ollama_client.embed", new_callable=AsyncMock) as mock:
        mock.return_value = fixed_vector
        yield mock


@pytest.fixture
def mock_ollama_generate_full():
    """Mock ollama_client.generate_full for summary generation."""
    mock_summary = """# Test Lesson Summary

## Topic
Introduction to Quadratic Equations

## Key Concepts
- Definition of quadratic equations
- Standard form: ax² + bx + c = 0
- Discriminant and nature of roots
- Solving by factorization
- Completing the square

## Learning Objectives
Students should understand what quadratic equations are and how to solve them using multiple methods.

## Suggested Discussion Questions
1. Why is the coefficient 'a' never zero in ax² + bx + c = 0?
2. How does the discriminant help predict the type of solutions?
3. When would you use factorization vs. completing the square?
"""
    with patch("app.services.ollama_client.generate_full", new_callable=AsyncMock) as mock:
        mock.return_value = mock_summary
        yield mock


# ──────────────────────────────────────────────────────────────────────────
# File upload fixtures
# ──────────────────────────────────────────────────────────────────────────

@pytest.fixture
def sample_pdf_bytes():
    """Return minimal valid PDF bytes for testing."""
    pdf = b"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Count 1 /Kids [3 0 R] >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >> /MediaBox [0 0 612 792] /Contents 4 0 R >>
endobj
4 0 obj
<< >>
stream
BT
/F1 12 Tf
50 750 Td
(Sample PDF Content) Tj
ET
endstream
endobj
xref
0 5
0000000000 65535 f
0000000009 00000 n
0000000074 00000 n
0000000133 00000 n
0000000281 00000 n
trailer
<< /Size 5 /Root 1 0 R >>
startxref
351
%%EOF"""
    return pdf


@pytest.fixture
def sample_txt_bytes():
    """Return sample text file bytes."""
    return b"This is a test lesson about quadratic equations.\n\nQuadratic equations have the form ax^2 + bx + c = 0."


# ──────────────────────────────────────────────────────────────────────────
# FastAPI test client fixtures
# ──────────────────────────────────────────────────────────────────────────

@pytest.fixture
def app_client():
    """Return a test client for the FastAPI app."""
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


# ──────────────────────────────────────────────────────────────────────────
# Pytest configuration
# ──────────────────────────────────────────────────────────────────────────

def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers", "unit: fast in-memory test, no external dependencies"
    )
    config.addinivalue_line(
        "markers", "integration: requires running database and services"
    )
    config.addinivalue_line(
        "markers", "slow: execution time > 2 seconds"
    )
