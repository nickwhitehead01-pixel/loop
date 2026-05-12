from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Role(str, enum.Enum):
    pupil = "pupil"
    teacher = "teacher"


class MessageRole(str, enum.Enum):
    user = "user"
    assistant = "assistant"


class SessionStatus(str, enum.Enum):
    open = "open"    # lesson opened by teacher; pupils can join waiting room
    live = "live"    # teacher started transcription
    ended = "ended"


class QuizMode(str, enum.Enum):
    one_at_a_time = "one_at_a_time"  # teacher reviews and sends each question individually
    batch = "batch"                  # teacher queues several drafts then sends them together


class QuizQuestionStatus(str, enum.Enum):
    draft = "draft"      # created but not yet sent to pupils
    sent = "sent"        # broadcast to pupils; accepting answers
    closed = "closed"    # timer expired; no further answers accepted


class QuizQuestionSource(str, enum.Enum):
    ai_suggested = "ai_suggested"   # accepted from the LLM verbatim
    ai_edited = "ai_edited"         # LLM suggestion modified by the teacher
    teacher_manual = "teacher_manual"  # written entirely by the teacher


class QuizGrade(str, enum.Enum):
    correct = "correct"
    partial = "partial"
    incorrect = "incorrect"


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
    quiz: Mapped[Quiz | None] = relationship(
        back_populates="session", cascade="all, delete-orphan",
        uselist=False,
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


class Quiz(Base):
    """A live quiz for a single lesson session.

    One per session (enforced by the unique constraint on session_id). Created
    when the teacher hits 'Start Quiz' and groups together every question they
    ask during the lesson — whether sent one at a time or queued in a batch.
    """
    __tablename__ = "quizzes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    session_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("lesson_sessions.id", ondelete="CASCADE"),
        nullable=False, unique=True, index=True,
    )
    mode: Mapped[QuizMode] = mapped_column(
        Enum(QuizMode), nullable=False, default=QuizMode.one_at_a_time
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    session: Mapped[LessonSession] = relationship(back_populates="quiz")
    questions: Mapped[list[QuizQuestion]] = relationship(
        back_populates="quiz", cascade="all, delete-orphan",
        order_by="QuizQuestion.id",
    )


class QuizQuestion(Base):
    """A single question within a quiz — drafted, then sent, then closed."""
    __tablename__ = "quiz_questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    quiz_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("quizzes.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    # Denormalised session_id so existing per-session queries don't need to
    # join through Quiz. Kept in sync at creation time.
    session_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("lesson_sessions.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    correct_answer: Mapped[str] = mapped_column(Text, nullable=False)
    topic_tag: Mapped[str | None] = mapped_column(String(120), nullable=True)
    status: Mapped[QuizQuestionStatus] = mapped_column(
        Enum(QuizQuestionStatus), nullable=False, default=QuizQuestionStatus.draft
    )
    source: Mapped[QuizQuestionSource] = mapped_column(
        Enum(QuizQuestionSource), nullable=False
    )
    time_limit_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    quiz: Mapped[Quiz] = relationship(back_populates="questions")
    attempts: Mapped[list[QuizAttempt]] = relationship(
        back_populates="question", cascade="all, delete-orphan"
    )


class QuizAttempt(Base):
    """A pupil's answer to a quiz question, with the LLM grader's verdict."""
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
    # Grading happens in a batch after the question closes — until then this
    # is nullable. Once the grader runs it is set to correct / partial / incorrect.
    grade: Mapped[QuizGrade | None] = mapped_column(
        Enum(QuizGrade), nullable=True, index=True
    )
    grader_rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
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
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    hit_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

