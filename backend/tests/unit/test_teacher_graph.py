"""
Unit tests for teacher_graph.py tools — particularly the get_lesson_summaries tool.
"""
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain import Lesson, User, Role


@pytest.mark.unit
class TestGetLessonSummariesTool:
    """Tests for the get_lesson_summaries tool that accesses persistent summaries."""

    async def test_get_lesson_summaries_with_search_term(
        self, db_with_teacher: tuple[AsyncSession, User]
    ):
        """Test getting summaries by searching for a specific lesson title."""
        db, teacher = db_with_teacher

        lessons = [
            Lesson(
                title="Quadratic Equations",
                teacher_id=teacher.id,
                file_path="/tmp/quad.pdf",
                summary="This lesson covers ax² + bx + c = 0 and solving methods.",
                summary_generated_at=datetime.now(),
            ),
            Lesson(
                title="Linear Functions",
                teacher_id=teacher.id,
                file_path="/tmp/linear.pdf",
                summary="Introduction to y = mx + b and graphing.",
                summary_generated_at=datetime.now(),
            ),
        ]
        for lesson in lessons:
            db.add(lesson)
        await db.flush()

        from app.agents.teacher_graph import _build_tools

        tools = _build_tools(db, teacher.id)
        get_lesson_summaries_tool = next(t for t in tools if t.name == "get_lesson_summaries")

        result = await get_lesson_summaries_tool.ainvoke({"lesson_title": "quadratic"})

        assert "Quadratic Equations" in result
        assert "ax² + bx + c = 0" in result
        assert "Linear Functions" not in result

    async def test_get_lesson_summaries_without_search_term(
        self, db_with_teacher: tuple[AsyncSession, User]
    ):
        """Test listing all lesson summaries when no search term provided."""
        db, teacher = db_with_teacher

        for i in range(3):
            lesson = Lesson(
                title=f"Lesson {i}",
                teacher_id=teacher.id,
                file_path=f"/tmp/lesson{i}.pdf",
                summary=f"Summary for lesson {i}",
                summary_generated_at=datetime.now(),
            )
            db.add(lesson)
        await db.flush()

        from app.agents.teacher_graph import _build_tools

        tools = _build_tools(db, teacher.id)
        get_lesson_summaries_tool = next(t for t in tools if t.name == "get_lesson_summaries")

        result = await get_lesson_summaries_tool.ainvoke({})

        assert "Lesson 0" in result
        assert "Lesson 1" in result
        assert "Lesson 2" in result
        assert "3 Lessons Available" in result

    async def test_get_lesson_summaries_returns_empty_if_no_match(
        self, db_with_teacher: tuple[AsyncSession, User]
    ):
        """Test returns appropriate message when no lessons match search."""
        db, teacher = db_with_teacher

        lesson = Lesson(
            title="Algebra",
            teacher_id=teacher.id,
            file_path="/tmp/algebra.pdf",
            summary="Algebra fundamentals.",
            summary_generated_at=datetime.now(),
        )
        db.add(lesson)
        await db.flush()

        from app.agents.teacher_graph import _build_tools

        tools = _build_tools(db, teacher.id)
        get_lesson_summaries_tool = next(t for t in tools if t.name == "get_lesson_summaries")

        result = await get_lesson_summaries_tool.ainvoke({"lesson_title": "geometry"})

        assert "No summary found" in result
        assert "geometry" in result

    async def test_get_lesson_summaries_skips_lessons_without_summaries(
        self, db_with_teacher: tuple[AsyncSession, User]
    ):
        """Test that lessons without summaries are excluded."""
        db, teacher = db_with_teacher

        lesson_with_summary = Lesson(
            title="Complete Lesson",
            teacher_id=teacher.id,
            file_path="/tmp/complete.pdf",
            summary="This lesson has a summary.",
            summary_generated_at=datetime.now(),
        )
        lesson_without_summary = Lesson(
            title="Incomplete Lesson",
            teacher_id=teacher.id,
            file_path="/tmp/incomplete.pdf",
            summary=None,
            summary_generated_at=None,
        )
        db.add(lesson_with_summary)
        db.add(lesson_without_summary)
        await db.flush()

        from app.agents.teacher_graph import _build_tools

        tools = _build_tools(db, teacher.id)
        get_lesson_summaries_tool = next(t for t in tools if t.name == "get_lesson_summaries")

        result = await get_lesson_summaries_tool.ainvoke({})

        assert "Complete Lesson" in result
        assert "Incomplete Lesson" not in result

    async def test_get_lesson_summaries_truncates_preview(
        self, db_with_teacher: tuple[AsyncSession, User]
    ):
        """Test that list view truncates summaries to 200 chars."""
        db, teacher = db_with_teacher

        long_summary = "x" * 300

        lesson = Lesson(
            title="Long Lesson",
            teacher_id=teacher.id,
            file_path="/tmp/long.pdf",
            summary=long_summary,
            summary_generated_at=datetime.now(),
        )
        db.add(lesson)
        await db.flush()

        from app.agents.teacher_graph import _build_tools

        tools = _build_tools(db, teacher.id)
        get_lesson_summaries_tool = next(t for t in tools if t.name == "get_lesson_summaries")

        result = await get_lesson_summaries_tool.ainvoke({})

        assert "..." in result
        assert result.count("x") == 200

    async def test_get_lesson_summaries_tool_in_graph_context(
        self, db_with_teacher: tuple[AsyncSession, User]
    ):
        """Test that tool is properly registered in the teacher graph."""
        db, teacher = db_with_teacher

        lesson = Lesson(
            title="Graph Test Lesson",
            teacher_id=teacher.id,
            file_path="/tmp/test.pdf",
            summary="Test summary for graph",
            summary_generated_at=datetime.now(),
        )
        db.add(lesson)
        await db.flush()

        from app.agents.teacher_graph import _build_tools

        tools = _build_tools(db, teacher.id)
        tool_names = [t.name for t in tools]

        assert "get_lesson_summaries" in tool_names
        assert "search_lesson_content" in tool_names
        assert len(tools) >= 6
