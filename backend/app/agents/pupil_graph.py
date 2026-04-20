"""
Pupil agent — a ReAct-style LangGraph StateGraph.

    Model:   gemma4:e4b
    Tools:   retrieve_context | get_conversation_history | list_lessons

Graph structure:
    ┌────────────┐
    │   START    │
    └─────┬──────┘
          ▼
    ┌─────────────┐   tool call?   ┌────────────┐
    │   agent     │ ─────────────▶ │  tools     │
    │  (LLM node) │ ◀──────────── │  (execute) │
    └─────┬───────┘                └────────────┘
          │  no tool call (final answer)
          ▼
       ┌──────┐
       │  END │
       └──────┘

The WS endpoint calls `run_pupil_agent` which runs the full graph and
yields token strings as they are generated (streaming via astream_events).
"""
from __future__ import annotations

from typing import AsyncIterator, TypedDict

import httpx
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_ollama import ChatOllama
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.tools import (
    get_conversation_history_func,
    get_full_transcript_func,
    list_lessons_func,
    load_all_pupil_memories_func,
    retrieve_context_func,
    save_pupil_memories_func,
    search_live_transcript_func,
)
from app.core.config import settings
from app.models.domain import Message, MessageRole
from app.services import ollama_client
from app.services import semantic_cache as _sem_cache
from langchain_core.tools import tool


# ---------------------------------------------------------------------------
# Graph state
# ---------------------------------------------------------------------------

class PupilState(TypedDict):
    messages: list[BaseMessage]


# ---------------------------------------------------------------------------
# Tool wrappers — async closures capturing db/http/pupil_id per-request.
# LangChain's @tool and LangGraph's ToolNode both support async def natively.
# ---------------------------------------------------------------------------

def build_tools(
    db: AsyncSession,
    http_client: httpx.AsyncClient,
    pupil_id: int = 0,
    session_id: int | None = None,
):
    """Return the LangChain tools with db/http/pupil_id/session_id already bound."""
    _pupil_id = pupil_id
    _session_id = session_id

    @tool
    async def retrieve_context(query: str) -> str:
        """Search through teacher-uploaded lesson materials for content relevant to the query."""
        return await retrieve_context_func(query, db, http_client)

    @tool
    async def list_lessons() -> str:
        """List all lesson titles that teachers have uploaded."""
        titles = await list_lessons_func(db)
        return "\n".join(f"- {t}" for t in titles)

    @tool
    async def search_transcript(query: str) -> str:
        """Search the live lesson transcript for content relevant to the query. Use this when the pupil asks about something the teacher said."""
        if not _session_id:
            return "No live session is active."
        return await search_live_transcript_func(query, _session_id, db, http_client)

    @tool
    async def get_full_transcript() -> str:
        """Get the complete lesson transcript in chronological order. Use when the pupil wants a recap of the full lesson."""
        if not _session_id:
            return "No live session is active."
        return await get_full_transcript_func(_session_id, db)

    tools = [retrieve_context, list_lessons]
    if _session_id:
        tools.extend([search_transcript, get_full_transcript])
    return tools


# ---------------------------------------------------------------------------
# System prompt builder (includes long-term memories)
# ---------------------------------------------------------------------------

BASE_SYSTEM_PROMPT = """You are a supportive, encouraging personal AI tutor.
Your job is to help the pupil understand the lesson materials their teacher has uploaded.

You have access to these tools:
- retrieve_context: search lesson materials for content relevant to the pupil's question
- list_lessons: see what lessons are available
- search_transcript: search what the teacher said during the live lesson (only available during/after a live session)
- get_full_transcript: get the complete lesson transcript (only available during/after a live session)

Guidelines:
- Use the provided facts about this pupil (in the system prompt) to personalise your approach
- When the pupil asks about something the teacher said, use search_transcript
- Always ground your answers in the lesson content when possible — use retrieve_context
- If the pupil asks what topics are available, use list_lessons
- Explain concepts clearly without being condescending
- Encourage the pupil and acknowledge their progress
- Keep responses focused and appropriately concise
"""


def build_system_prompt(memories: list[str]) -> str:
    """Inject the pupil's long-term memories into the system prompt."""
    if not memories:
        return BASE_SYSTEM_PROMPT
    memory_block = "\n".join(f"- {m}" for m in memories)
    return (
        BASE_SYSTEM_PROMPT
        + f"\n\nWhat you know about this pupil from previous sessions:\n{memory_block}\n"
    )


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------

def build_pupil_graph(
    db: AsyncSession,
    http_client: httpx.AsyncClient,
    pupil_id: int = 0,
    session_id: int | None = None,
):
    tools = build_tools(db, http_client, pupil_id=pupil_id, session_id=session_id)

    llm = ChatOllama(
        model=settings.ollama_model_pupil,
        base_url=settings.ollama_base_url,
        temperature=0.7,
        streaming=True,
    ).bind_tools(tools)

    tool_node = ToolNode(tools)

    async def agent_node(state: PupilState) -> dict:
        response = await llm.ainvoke(state["messages"])
        return {"messages": state["messages"] + [response]}

    def should_continue(state: PupilState) -> str:
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and last.tool_calls:
            return "tools"
        return END

    graph = StateGraph(PupilState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tool_node)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")

    return graph.compile()


# ---------------------------------------------------------------------------
# Public entry point — called by the WebSocket endpoint
# ---------------------------------------------------------------------------

async def run_pupil_agent(
    user_message: str,
    conversation_id: int,
    pupil_id: int,
    db: AsyncSession,
    http_client: httpx.AsyncClient,
    session_id: int | None = None,
) -> AsyncIterator[str]:
    """
    Run the pupil agent and yield response tokens one at a time.
    Persists the user message and assistant reply, then runs a background
    memory-extraction step to update long-term pupil memories.
    """
    # Persist the incoming user message
    db.add(Message(
        conversation_id=conversation_id,
        role=MessageRole.user,
        content=user_message,
    ))
    await db.flush()

    # --- Semantic cache lookup ---
    _cached = await _sem_cache.lookup(user_message, db, session_id=session_id)
    if _cached:
        db.add(Message(
            conversation_id=conversation_id,
            role=MessageRole.assistant,
            content=_cached,
        ))
        await db.commit()
        yield _cached
        return

    # Load long-term memories from previous sessions
    prior_memories = await load_all_pupil_memories_func(pupil_id, db)
    system_prompt = build_system_prompt(prior_memories)

    # Load short-term conversation history as LangChain messages
    history = await get_conversation_history_func(conversation_id, db)
    # Cap to last 6 messages (3 turns) to avoid context window bloat on 16GB
    recent = history[:-1][-6:]
    lc_messages: list[BaseMessage] = [SystemMessage(content=system_prompt)]
    for m in recent:
        cls = HumanMessage if m["role"] == "user" else AIMessage
        lc_messages.append(cls(content=m["content"]))
    lc_messages.append(HumanMessage(content=user_message))

    app_graph = build_pupil_graph(db, http_client, pupil_id=pupil_id, session_id=session_id)

    initial_state: PupilState = {
        "messages": lc_messages,
    }

    full_response: list[str] = []

    try:
        async for event in app_graph.astream_events(initial_state, version="v2"):
            kind = event["event"]
            if kind == "on_chat_model_stream":
                chunk = event["data"]["chunk"]
                token = chunk.content
                if token:
                    full_response.append(token)
                    yield token
    finally:
        # Persist whatever was generated -- even partial on WS disconnect
        assistant_content = "".join(full_response)
        if assistant_content:
            db.add(Message(
                conversation_id=conversation_id,
                role=MessageRole.assistant,
                content=assistant_content,
            ))
            await db.flush()

        # --- Long-term memory extraction ---
        # Ask gemma3:12b to distill 1-3 new facts from this turn.
        # Failures must never prevent commit.
        try:
            import json as _json

            extraction_prompt = (
                f"Previous message from pupil: {user_message}\n"
                f"Tutor response: {assistant_content}\n\n"
                "Extract 1-3 short atomic facts about this pupil's learning "
                "(e.g. struggles, preferences, progress, misconceptions). "
                "Reply with a JSON array of strings.\n"
                'Example: ["struggles with long division", "prefers step-by-step worked examples"]'
            )
            raw = await ollama_client.generate_full(
                messages=[{"role": "user", "content": extraction_prompt}],
                model=settings.ollama_model_pupil,
                format="json",
            )
            new_memories: list[str] = _json.loads(raw)
            # Handle both {"memories": [...]} wrapper and plain [...] formats
            if isinstance(new_memories, dict):
                new_memories = list(new_memories.values())[0]
            if isinstance(new_memories, list) and new_memories:
                new_memories = [m for m in new_memories if isinstance(m, str) and m.strip()]
                if new_memories:
                    await save_pupil_memories_func(pupil_id, new_memories, db, http_client)
        except Exception:
            pass  # memory extraction failure must never prevent commit

        # --- Store in semantic cache ---
        try:
            await _sem_cache.store(user_message, assistant_content, db, session_id=session_id)
        except Exception:
            pass

        await db.commit()
