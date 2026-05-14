"""
Unit tests for app.services.summary._get_transcript_text.

generate_session_artifacts calls AsyncSessionLocal internally and is tested
via the background worker path; here we focus on the deterministic helper.
"""
from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain import LessonSession, SessionStatus, TranscriptChunk, User, Role
from app.services.summary import _get_transcript_text


class TestGetTranscriptText:

    async def test_returns_empty_string_when_no_chunks(self, db_with_session):
        db, session = db_with_session
        result = await _get_transcript_text(session.id, db)
        assert result == ""

    async def test_single_chunk_formatted_with_timestamp(self, db_with_session):
        db, session = db_with_session

        chunk = TranscriptChunk(
            session_id=session.id,
            content="The lesson begins now.",
            timestamp_ms=0,
        )
        db.add(chunk)
        await db.flush()

        result = await _get_transcript_text(session.id, db)
        assert result == "[00:00] The lesson begins now."

    async def test_multiple_chunks_ordered_by_timestamp(self, db_with_session):
        db, session = db_with_session

        # Add chunks out of order
        db.add(TranscriptChunk(session_id=session.id, content="Third chunk.", timestamp_ms=120000))
        db.add(TranscriptChunk(session_id=session.id, content="First chunk.", timestamp_ms=0))
        db.add(TranscriptChunk(session_id=session.id, content="Second chunk.", timestamp_ms=60000))
        await db.flush()

        result = await _get_transcript_text(session.id, db)
        lines = result.split("\n")
        assert len(lines) == 3
        assert lines[0] == "[00:00] First chunk."
        assert lines[1] == "[01:00] Second chunk."
        assert lines[2] == "[02:00] Third chunk."

    async def test_timestamp_formatting_minutes_and_seconds(self, db_with_session):
        db, session = db_with_session

        # 90 500 ms = 1 min 30 sec
        chunk = TranscriptChunk(
            session_id=session.id, content="Hello.", timestamp_ms=90500
        )
        db.add(chunk)
        await db.flush()

        result = await _get_transcript_text(session.id, db)
        assert "[01:30] Hello." == result

    async def test_nonexistent_session_returns_empty(self, async_db: AsyncSession):
        result = await _get_transcript_text(99999, async_db)
        assert result == ""
