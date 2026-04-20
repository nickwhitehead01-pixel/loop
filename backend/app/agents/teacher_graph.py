"""
Teacher agent — a ReAct-style LangGraph StateGraph.

    Model:   gemma4:e4b
    Tools:   search_lesson_content | get_class_analytics | get_student_profile
             | get_teacher_memories | search_transcript

Mirrors pupil_graph.py patterns: streaming via astream_events,
long-term memory extraction after each turn written to TeacherMemory.
"""
from __future__ import annotations

import json as _json
import logging
from typing import AsyncIterator, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langchain_ollama import ChatOllama
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from sqlalchemy import func as sa_func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.domain import (
    LessonChunk,
    MessageRole,
    PupilSessionSummary,
    TeacherConversation,
    TeacherMemory,
    TeacherMessage,
    TranscriptChunk,
    User,
)
from app.services import ollama_client

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Graph state
# ---------------------------------------------------------------------------

class TeacherState(TypedDict):
    messages: list[BaseMessage]


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_BASE_SYSTEM = """You are a smart teaching assistant helping a teacher manage their classroom.

You have access to tools to:
- search_lesson_content: search uploaded lesson materials for content
- get_class_analytics: get aggregated pupil performance metrics (optionally by session_id)
- get_student_profile: get an individual pupil's progress and summaries
- get_teacher_memories: recall facts about this teacher and class from previous sessions
- search_transcript: search past lesson transcripts by topic (optionally by session_id)

Guidelines:
- Be concise, professional, and actionable
- Always use tools when answering questions about pupil performance or lesson content
- Suggest concrete next steps based on data from tools
- Respect pupil privacy — summarise data, never expose raw message content
"""


def _build_system_prompt(memories: list[str]) -> str:
    if not memories:
        return _BASE_SYSTEM
    block = "\n".join(f"- {m}" for m in memories)
    return _BASE_SYSTEM + f"\n\nWhat you know about this teacher and class:\n{block}\n"


# ---------------------------------------------------------------------------
# Tool builders — db and teacher_id captured per-request
# ---------------------------------------------------------------------------

def _build_tools(db: AsyncSession, teacher_id: int):

    @tool
    async def search_lesson_content(query: str) -> str:
        """Search uploaded lesson materials for content relevant to the query."""
        vector = await ollama_client.embed(query)
        result = await db.execute(
            select(LessonChunk.content)
            .order_by(LessonChunk.embedding.cosine_distance(vector))
            .limit(5)
        )
        rows = result.scalars().all()
        return "\n\n---\n\n".join(rows) if rows else "No relevant lesson content found."

    @tool
    async def get_class_analytics(session_id: int | None = None) -> str:
        """Get aggregated pupil performance metrics. Pass session_id to filter to one session."""
        stmt = select(
            sa_func.count(PupilSessionSummary.id).label("total_pupils"),
            sa_func.avg(PupilSessionSummary.understanding_score).label("avg_score"),
            sa_func.sum(PupilSessionSummary.questions_asked).label("total_questions"),
        )
        if session_id:
            stmt = stmt.where(PupilSessionSummary.session_id == session_id)
        result = await db.execute(stmt)
        row = result.one()
        avg = f"{float(row.avg_score):.2f}" if row.avg_score else "N/A"
        return (
            f"Total pupils: {row.total_pupils or 0}\n"
            f"Average understanding score: {avg}\n"
            f"Total questions asked: {row.total_questions or 0}"
        )

    @tool
    async def get_student_profile(pupil_id: int) -> str:
        """Get an individual pupil's recent progress, summaries and performance scores."""
        pupil_result = await db.execute(select(User).where(User.id == pupil_id))
        pupil = pupil_result.scalar_one_or_none()
        if not pupil:
            return f"No pupil found with id {pupil_id}."
        summaries_result = await db.execute(
            select(PupilSessionSummary)
            .where(PupilSessionSummary.pupil_id == pupil_id)
            .order_by(PupilSessionSummary.created_at.desc())
            .limit(3)
        )
        summaries = summaries_result.scalars().all()
        parts = [f"Pupil: {pupil.name}"]
        for s in summaries:
            parts.append(f"\nSummary: {s.summary_text[:300]}")
            if s.understanding_score is not None:
                parts.append(f"Understanding: {s.understanding_score:.1f}/10")
            if s.questions_asked:
                parts.append(f"Questions asked: {s.questions_asked}")
        if not summaries:
            parts.append("No session summaries available yet.")
        return "\n".join(parts)

    @tool
    async def get_teacher_memories(query: str) -> str:
        """Recall facts about this teacher and class relevant to the query."""
        vector = await ollama_client.embed(query)
        result = await db.execute(
            select(TeacherMemory.memory)
            .where(TeacherMemory.teacher_id == teacher_id)
            .order_by(TeacherMemory.embedding.cosine_distance(vector))
            .limit(5)
        )
        rows = result.scalars().all()
        return "\n".join(f"- {m}" for m in rows) if rows else "No relevant memories found."

    @tool
    async def search_transcript(query: str, session_id: int | None = None) -> str:
        """Search lesson transcripts for content relevant to the query. Optionally filter by session_id."""
        vector = await ollama_client.embed(query)
        stmt = (
            select(
                TranscriptChunk.content,
                TranscriptChunk.timestamp_ms,
                TranscriptChunk.session_id,
            )
            .order_by(TranscriptChunk.embedding.cosine_distance(vector))
            .limit(5)
        )
        if session_id:
            stmt = stmt.where(TranscriptChunk.session_id == session_id)
        result = await db.execute(stmt)
        rows = result.all()
        if not rows:
            return "No transcript content found."
        parts = []
        for content, ts_ms, sid in rows:
            mins, secs = divmod(ts_ms // 1000, 60)
            parts.append(f"[Session {sid} — {mins:02d}:{secs:02d}] {content}")
        return "\n\n".join(parts)

    return [
        search_lesson_content,
        get_class_analytics,
        get_student_profile,
        get_teacher_memories,
        search_transcript,
    ]


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------

def build_teacher_graph(db: AsyncSession, teacher_id: int):
    tools = _build_tools(db, teacher_id)
    llm = ChatOllama(
        model=settings.ollama_model_teacher,
        base_url=settings.ollama_base_url,
        temperature=0.4,
        streaming=True,
    ).bind_tools(tools)
    tool_node = ToolNode(tools)

    async def agent_node(state: TeacherState) -> dict:
        response = await llm.ainvoke(state["messages"])
        return {"messages": state["messages"] + [response]}

    def should_continue(state: TeacherState) -> str:
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and last.tool_calls:
            return "tools"
        return END

    graph = StateGraph(TeacherState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tool_node)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")
    return graph.compile()


# ---------------------------------------------------------------------------
# Public entry point — called by the WebSocket endpoint
# ---------------------------------------------------------------------------

async def run_teacher_agent(
    user_message: str,
    conversation_id: int,
    teacher_id: int,
    db: AsyncSession,
) -> AsyncIterator[str]:
    """Run the teacher agent and yield response tokens. Persists messages and extracts memories."""
    db.add(TeacherMessage(
        conversation_id=conversation_id,
        role=MessageRole.user,
        content=user_message,
    ))
    await db.flush()

    # Load long-term teacher memories (most recent first, capped at 20)
    mem_result = await db.execute(
        select(TeacherMemory.memory)
        .where(TeacherMemory.teacher_id == teacher_id)
        .order_by(TeacherMemory.created_at.desc())
        .limit(20)
    )
    prior_memories = list(mem_result.scalars().all())
    system_prompt = _build_system_prompt(prior_memories)

    # Load recent conversation history (cap at memory_window turns)
    hist_result = await db.execute(
        select(TeacherMessage)
        .where(TeacherMessage.conversation_id == conversation_id)
        .order_by(TeacherMessage.created_at.desc())
        .limit(settings.memory_window)
    )
    history = list(reversed(hist_result.scalars().all()))
    # Exclude the message we just inserted, then cap to last 6 messages (3 turns)
    recent = history[:-1][-6:]
    lc_messages: list[BaseMessage] = [SystemMessage(content=system_prompt)]
    for m in recent:
        cls = HumanMessage if m.role == MessageRole.user else AIMessage
        lc_messages.append(cls(content=m.content))
    lc_messages.append(HumanMessage(content=user_message))

    app_graph = build_teacher_graph(db, teacher_id=teacher_id)
    full_response: list[str] = []

    try:
        async for event in app_graph.astream_events({"messages": lc_messages}, version="v2"):
            if event["event"] == "on_chat_model_stream":
                token = event["data"]["chunk"].content
                if token:
                    full_response.append(token)
                    yield token
    finally:
        assistant_content = "".join(full_response)
        if assistant_content:
            db.add(TeacherMessage(
                conversation_id=conversation_id,
                role=MessageRole.assistant,
                content=assistant_content,
            ))
            await db.flush()

        # Memory extraction — distil 1-3 new facts from this turn
        try:
            extraction_prompt = (
                f"Teacher message: {user_message}\n"
                f"Assistant response: {assistant_content}\n\n"
                "Extract 1-3 short atomic facts about this teacher, their class, or teaching preferences. "
                "Reply with a JSON array of strings.\n"
                'Example: ["class struggles with algebra", "prefers concise bullet summaries"]'
            )
            raw = await ollama_client.generate_full(
                messages=[{"role": "user", "content": extraction_prompt}],
                model=settings.ollama_model_teacher,
                format="json",
            )
            new_memories: list[str] = _json.loads(raw)
            if isinstance(new_memories, dict):
                new_memories = list(new_memories.values())[0]
            if isinstance(new_memories, list):
                new_memories = [m for m in new_memories if isinstance(m, str) and m.strip()]
                for mem_text in new_memories:
                    vector = await ollama_client.embed(mem_text)
                    db.add(TeacherMemory(
                        teacher_id=teacher_id,
                        memory=mem_text,
                        embedding=vector,
                    ))
                await db.flush()
        except Exception:
            pass  # memory extraction must never block commit

        await db.commit()
