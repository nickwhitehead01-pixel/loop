from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# ── Users ──────────────────────────────────────────────────────────────────

class UserCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    role: Literal["pupil", "teacher"]


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    role: str
    created_at: datetime


# ── Lessons ────────────────────────────────────────────────────────────────

class LessonCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    teacher_id: int


class LessonFileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    original_filename: str
    file_path: str
    created_at: datetime


class LessonResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    teacher_id: int
    file_path: str
    file_count: int = 1
    summary: str | None = None
    summary_generated_at: datetime | None = None
    created_at: datetime


# ── Chat ───────────────────────────────────────────────────────────────────

class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatStreamResponse(BaseModel):
    """Single token chunk sent over WebSocket."""

    token: str
    done: bool = False


# ── Pupil long-term memory ─────────────────────────────────────────────────

class PupilMemoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    pupil_id: int
    memory: str
    created_at: datetime


# ── Pupil / Teacher analytics ──────────────────────────────────────────────

class StudentProgress(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    pupil_id: int
    pupil_name: str
    message_count: int
    last_active: datetime | None


# ── Lesson Sessions (live transcription) ───────────────────────────────────

class SessionCreate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    teacher_id: int
    lesson_id: int


class SessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    teacher_id: int
    title: str
    status: str
    started_at: datetime
    ended_at: datetime | None = None


class TranscriptChunkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    session_id: int
    content: str
    timestamp_ms: int
    created_at: datetime


class TranscriptBroadcast(BaseModel):
    """Sent to connected pupils over WebSocket when new transcript arrives."""
    type: str = "transcript"
    content: str
    timestamp_ms: int


# ── Pupil Session Summaries ────────────────────────────────────────────────

class PupilSessionSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    session_id: int
    pupil_id: int
    summary_text: str
    key_topics: list[str] | None = None
    understanding_score: float | None = None
    questions_asked: int = 0
    topics_discussed: list[str] | None = None
    session_duration_seconds: int = 0
    created_at: datetime


# ── Quizzes ────────────────────────────────────────────────────────────────

class QuizQuestionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    session_id: int
    question_text: str
    correct_answer: str
    created_at: datetime


class QuizAnswerSubmit(BaseModel):
    pupil_answer: str = Field(..., min_length=1)


class QuizAttemptResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    question_id: int
    pupil_id: int
    pupil_answer: str
    is_correct: bool
    created_at: datetime


# ── Session Analytics (teacher view) ───────────────────────────────────────

class SessionAnalytics(BaseModel):
    session_id: int
    title: str
    total_pupils: int
    avg_understanding_score: float | None = None
    total_questions_asked: int = 0
    quiz_completion_rate: float | None = None

