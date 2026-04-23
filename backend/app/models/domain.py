from __future__ import annotations

import enum
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Role(str, enum.Enum):
    pupil = "pupil"
    teacher = "teacher"


class MessageRole(str, enum.Enum):
    user = "user"
    assistant = "assistant"


class SessionStatus(str, enum.Enum):
    live = "live"
    ended = "ended"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    role: Mapped[Role] = mapped_column(Enum(Role), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    conversations: Mapped[list[Conversation]] = relationship(
        back_populates="pupil", cascade="all, delete-orphan",
        foreign_keys="[Conversation.pupil_id]",
    )
    lessons: Mapped[list[Lesson]] = relationship(
        back_populates="teacher", cascade="all, delete-orphan"
    )
    memories: Mapped[list[PupilMemory]] = relationship(
        back_populates="pupil", cascade="all, delete-orphan"
    )
    sessions: Mapped[list[LessonSession]] = relationship(
        back_populates="teacher", cascade="all, delete-orphan"
    )
    teacher_conversations: Mapped[list["TeacherConversation"]] = relationship(
        back_populates="teacher", cascade="all, delete-orphan",
    )
    teacher_memories: Mapped[list["TeacherMemory"]] = relationship(
        back_populates="teacher", cascade="all, delete-orphan",
    )


class LessonSession(Base):
    """A live classroom session — ties together transcript, materials, conversations, and summaries."""
    __tablename__ = "lesson_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    teacher_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[SessionStatus] = mapped_column(
        Enum(SessionStatus), nullable=False, default=SessionStatus.live
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    teacher: Mapped[User] = relationship(back_populates="sessions")
    transcript_chunks: Mapped[list[TranscriptChunk]] = relationship(
        back_populates="session", cascade="all, delete-orphan",
        order_by="TranscriptChunk.timestamp_ms",
    )
    lessons: Mapped[list[Lesson]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    conversations: Mapped[list[Conversation]] = relationship(
        back_populates="session",
        foreign_keys="[Conversation.session_id]",
    )
    summaries: Mapped[list[PupilSessionSummary]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    quiz_questions: Mapped[list[QuizQuestion]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class TranscriptChunk(Base):
    """A sentence-level chunk of live transcription with its embedding."""
    __tablename__ = "transcript_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    session_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("lesson_sessions.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(768), nullable=False)
    timestamp_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    session: Mapped[LessonSession] = relationship(back_populates="transcript_chunks")


class Lesson(Base):
    __tablename__ = "lessons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    teacher_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    session_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("lesson_sessions.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    file_path: Mapped[str] = mapped_column(String(512), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_generated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    teacher: Mapped[User] = relationship(back_populates="lessons")
    session: Mapped[LessonSession | None] = relationship(back_populates="lessons")
    files: Mapped[list[LessonFile]] = relationship(
        back_populates="lesson", cascade="all, delete-orphan"
    )
    chunks: Mapped[list[LessonChunk]] = relationship(
        back_populates="lesson", cascade="all, delete-orphan",
        order_by="LessonChunk.chunk_index",
    )


class LessonFile(Base):
    """One physical uploaded file belonging to a lesson."""
    __tablename__ = "lesson_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    lesson_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("lessons.id", ondelete="CASCADE"), nullable=False, index=True
    )
    original_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    file_path: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    lesson: Mapped["Lesson"] = relationship(back_populates="files")


class LessonChunk(Base):
    __tablename__ = "lesson_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    lesson_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("lessons.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # nomic-embed-text produces 768-dimensional vectors
    embedding: Mapped[list[float]] = mapped_column(Vector(768), nullable=False)

    lesson: Mapped[Lesson] = relationship(back_populates="chunks")


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    pupil_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    session_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("lesson_sessions.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    pupil: Mapped[User] = relationship(back_populates="conversations")
    session: Mapped[LessonSession | None] = relationship(
        back_populates="conversations", foreign_keys=[session_id]
    )
    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.created_at",
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    conversation_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[MessageRole] = mapped_column(Enum(MessageRole), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class PupilMemory(Base):
    """
    Long-term episodic memory for a pupil — distilled facts stored across sessions.
    Each row is a single atomic fact, e.g. "struggles with quadratic equations"
    or "prefers visual analogies over worked examples".
    Stored with a vector embedding so the agent can do similarity retrieval
    rather than blindly loading all memories into context.
    """
    __tablename__ = "pupil_memories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    pupil_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    memory: Mapped[str] = mapped_column(Text, nullable=False)
    # Embedding of the memory text — used for similarity retrieval
    embedding: Mapped[list[float]] = mapped_column(Vector(768), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    pupil: Mapped[User] = relationship(back_populates="memories")


# ---------------------------------------------------------------------------
# Session summaries & quizzes
# ---------------------------------------------------------------------------

class PupilSessionSummary(Base):
    """Per-pupil AI-generated summary after a lesson session."""
    __tablename__ = "pupil_session_summaries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    session_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("lesson_sessions.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    pupil_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    summary_text: Mapped[str] = mapped_column(Text, nullable=False)
    key_topics: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    understanding_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    questions_asked: Mapped[int] = mapped_column(Integer, default=0)
    topics_discussed: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    session_duration_seconds: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    session: Mapped[LessonSession] = relationship(back_populates="summaries")


class QuizQuestion(Base):
    """Auto-generated quiz question from a lesson session's transcript."""
    __tablename__ = "quiz_questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    session_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("lesson_sessions.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    correct_answer: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    session: Mapped[LessonSession] = relationship(back_populates="quiz_questions")
    attempts: Mapped[list[QuizAttempt]] = relationship(
        back_populates="question", cascade="all, delete-orphan"
    )


class QuizAttempt(Base):
    """A pupil's answer to a quiz question."""
    __tablename__ = "quiz_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    question_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("quiz_questions.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    pupil_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    pupil_answer: Mapped[str] = mapped_column(Text, nullable=False)
    is_correct: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    question: Mapped[QuizQuestion] = relationship(back_populates="attempts")


# ---------------------------------------------------------------------------
# Teacher memory & conversations (Phase 7)
# ---------------------------------------------------------------------------

class TeacherConversation(Base):
    """A multi-turn chat session between a teacher and the AI teaching assistant."""
    __tablename__ = "teacher_conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    teacher_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    teacher: Mapped[User] = relationship(back_populates="teacher_conversations")
    messages: Mapped[list["TeacherMessage"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan",
        order_by="TeacherMessage.created_at",
    )


class TeacherMessage(Base):
    """A single turn in a teacher agent conversation."""
    __tablename__ = "teacher_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    conversation_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("teacher_conversations.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    role: Mapped[MessageRole] = mapped_column(Enum(MessageRole), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    conversation: Mapped[TeacherConversation] = relationship(back_populates="messages")


class TeacherMemory(Base):
    """
    Long-term episodic memory for a teacher — distilled facts stored across sessions.
    Each row is a single atomic fact, e.g. "class struggles with algebra"
    or "teacher prefers concise bullet-point summaries".
    """
    __tablename__ = "teacher_memories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    teacher_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    memory: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(768), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    teacher: Mapped[User] = relationship(back_populates="teacher_memories")


# ---------------------------------------------------------------------------
# Semantic answer cache (Phase 7b)
# ---------------------------------------------------------------------------

class SemanticCache(Base):
    """
    Per-session semantic answer cache.
    Questions whose embedding is within `semantic_cache_threshold` cosine
    similarity of an existing entry are served from cache without an LLM call.
    """
    __tablename__ = "semantic_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    session_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("lesson_sessions.id", ondelete="CASCADE"),
        nullable=True, index=True,
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(768), nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    hit_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

