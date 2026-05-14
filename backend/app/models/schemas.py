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
    # Processing state — surfaced so the UI can show a step-by-step
    # status indicator instead of a single "in progress" spinner that
    # disappears the moment the summary lands.
    chunk_count: int = 0
    glossary_count: int = 0
    prompt_card_count: int = 0
    precomputed_features_at: datetime | None = None
    precomputed_features_attempts: int = 0


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

class PupilProgress(BaseModel):
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


# ── Tappable terms (vocabulary the pupil can tap to expand) ────────────────


class TappableTerm(BaseModel):
    """A single term Gemma has flagged as worth explaining.

    The pupil app draws a dotted underline beneath any occurrence of *term*
    in the live transcript; tapping reveals *explanation*.
    """

    term: str
    explanation: str


class TappableTermsBroadcast(BaseModel):
    """Pushed to subscribed pupils whenever a new batch of tappable terms
    is generated. The client merges by lowercased term and re-renders.
    """

    type: str = "tappable_terms"
    terms: list[TappableTerm]


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
    quiz_id: int
    session_id: int
    question_text: str
    # correct_answer is intentionally omitted from the pupil-facing schema —
    # exposing it would let a pupil read the answer before submitting. Teacher
    # endpoints use a separate schema that includes it.
    topic_tag: str | None = None
    status: str
    time_limit_seconds: int
    sent_at: datetime | None = None
    closed_at: datetime | None = None
    created_at: datetime


class QuizAnswerSubmit(BaseModel):
    pupil_answer: str = Field(..., min_length=1)


# ── Teacher-facing quiz schemas ────────────────────────────────────────────
#
# These are separated from the pupil schemas above because they expose
# `correct_answer` — pupils must never see it before submitting.

class QuizStart(BaseModel):
    mode: str = Field(..., pattern="^(one_at_a_time|batch)$")


class QuizSuggestion(BaseModel):
    """An LLM-drafted question, not yet persisted."""
    question_text: str
    correct_answer: str
    topic_tag: str | None = None


class TeacherQuizQuestionCreate(BaseModel):
    question_text: str = Field(..., min_length=1)
    correct_answer: str = Field(..., min_length=1)
    topic_tag: str | None = None
    source: str = Field(..., pattern="^(ai_suggested|ai_edited|teacher_manual)$")
    time_limit_seconds: int = Field(default=20, ge=5, le=120)


class TeacherQuizQuestionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    quiz_id: int
    session_id: int
    question_text: str
    correct_answer: str
    topic_tag: str | None = None
    status: str
    source: str
    time_limit_seconds: int
    sent_at: datetime | None = None
    closed_at: datetime | None = None
    created_at: datetime


class QuizResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    session_id: int
    mode: str
    started_at: datetime
    questions: list[TeacherQuizQuestionResponse] = []


class QuizAttemptResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    question_id: int
    pupil_id: int
    pupil_answer: str
    # Null until the grader runs after the question closes.
    grade: str | None = None
    grader_rationale: str | None = None
    submitted_at: datetime
    created_at: datetime


# ── Session Analytics (teacher view) ───────────────────────────────────────

class SessionAnalytics(BaseModel):
    session_id: int
    title: str
    total_pupils: int
    avg_understanding_score: float | None = None
    total_questions_asked: int = 0
    quiz_completion_rate: float | None = None

