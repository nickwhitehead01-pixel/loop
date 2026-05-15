"""
Unit tests for app.agents.pupil_graph.

Tests the pure-logic helpers:
  - _dispatch_tool   — keyword routing, no I/O
  - _build_system_prompt — string construction, no I/O
"""
from __future__ import annotations

import pytest

from app.agents.pupil_graph import _build_system_prompt, _dispatch_tool
from app.services.slide_sync import SlidePosition


# ---------------------------------------------------------------------------
# _dispatch_tool
# ---------------------------------------------------------------------------

class TestDispatchTool:

    # --- Defaults ---

    def test_no_keywords_returns_retrieve_context(self):
        assert _dispatch_tool("Tell me about the French Revolution.", session_id=None) == "retrieve_context"

    def test_no_session_id_returns_retrieve_context(self):
        assert _dispatch_tool("What did the teacher just say?", session_id=None) == "retrieve_context"

    # --- Live / recency keywords (require session_id) ---

    def test_just_said_routes_to_full_transcript(self):
        assert _dispatch_tool("What did she just said?", session_id=1) == "get_full_transcript"

    def test_just_now_routes_to_full_transcript(self):
        assert _dispatch_tool("What is happening just now?", session_id=1) == "get_full_transcript"

    def test_right_now_routes_to_full_transcript(self):
        assert _dispatch_tool("What is the teacher saying right now?", session_id=1) == "get_full_transcript"

    def test_currently_routes_to_full_transcript(self):
        assert _dispatch_tool("What is the teacher currently talking about?", session_id=1) == "get_full_transcript"

    def test_latest_routes_to_full_transcript(self):
        assert _dispatch_tool("Show me the latest transcript.", session_id=1) == "get_full_transcript"

    # --- Full recap keywords ---

    def test_full_recap_routes_to_full_transcript(self):
        assert _dispatch_tool("Give me a full recap of the lesson.", session_id=1) == "get_full_transcript"

    def test_entire_lesson_routes_to_full_transcript(self):
        assert _dispatch_tool("Summarise the entire lesson for me.", session_id=1) == "get_full_transcript"

    def test_whole_lesson_routes_to_full_transcript(self):
        assert _dispatch_tool("I missed the whole lesson, what happened?", session_id=1) == "get_full_transcript"

    # --- Transcript keywords ---

    def test_transcript_keyword_routes_to_search_transcript(self):
        assert _dispatch_tool("Search the transcript for Newton.", session_id=1) == "search_transcript"

    def test_said_keyword_routes_to_search_transcript(self):
        assert _dispatch_tool("What was said about photosynthesis?", session_id=1) == "search_transcript"

    def test_recap_keyword_routes_to_search_transcript(self):
        assert _dispatch_tool("Can you give me a recap?", session_id=1) == "search_transcript"

    # --- List keywords (session-independent) ---

    def test_what_lessons_routes_to_list_lessons(self):
        assert _dispatch_tool("What lessons are available?", session_id=None) == "list_lessons"

    def test_which_lessons_routes_to_list_lessons(self):
        assert _dispatch_tool("Which lessons can I access?", session_id=None) == "list_lessons"

    def test_available_lessons_routes_to_list_lessons(self):
        assert _dispatch_tool("Show me available lessons.", session_id=None) == "list_lessons"

    def test_list_lessons_routes_to_list_lessons(self):
        assert _dispatch_tool("list lessons please", session_id=None) == "list_lessons"

    # --- Live keywords take priority over transcript keywords ---

    def test_live_keywords_beat_transcript_keywords(self):
        # "just mentioned" is a live keyword; "said" is a transcript keyword
        # live should win
        result = _dispatch_tool("What did she just mentioned about DNA?", session_id=5)
        assert result == "get_full_transcript"


# ---------------------------------------------------------------------------
# _build_system_prompt
# ---------------------------------------------------------------------------

class TestBuildSystemPrompt:

    def test_basic_no_memories_no_context(self):
        prompt = _build_system_prompt(memories=[], context="")
        assert "supportive personal AI tutor" in prompt
        # No memory / context blocks should appear
        assert "What you know" not in prompt
        assert "\n[CONTEXT]\n" not in prompt

    def test_with_memories_includes_memory_block(self):
        memories = ["struggles with long division", "prefers worked examples"]
        prompt = _build_system_prompt(memories=memories, context="")
        assert "What you know about this pupil:" in prompt
        assert "struggles with long division" in prompt
        assert "prefers worked examples" in prompt

    def test_with_context_includes_context_block(self):
        prompt = _build_system_prompt(memories=[], context="Photosynthesis is the process...")
        assert "[CONTEXT]" in prompt
        assert "Photosynthesis" in prompt

    def test_with_current_slide_includes_slide_info(self):
        slide = SlidePosition(lesson_id=1, lesson_title="Biology Basics", slide_number=7)
        prompt = _build_system_prompt(memories=[], context="", current_slide=slide)
        assert "[LESSON POSITION]" in prompt
        assert "slide 7" in prompt
        assert "Biology Basics" in prompt

    def test_none_slide_omits_slide_block(self):
        prompt = _build_system_prompt(memories=[], context="", current_slide=None)
        assert "[LESSON POSITION]" not in prompt

    def test_all_sections_present_when_all_provided(self):
        slide = SlidePosition(lesson_id=2, lesson_title="Chemistry", slide_number=3)
        prompt = _build_system_prompt(
            memories=["visual learner"],
            context="Atoms are the smallest units.",
            current_slide=slide,
        )
        assert "What you know about this pupil:" in prompt
        assert "[LESSON POSITION]" in prompt
        assert "[CONTEXT]" in prompt

    def test_empty_memories_list_omits_memory_block(self):
        prompt = _build_system_prompt(memories=[], context="Some context.")
        assert "What you know" not in prompt

    def test_lesson_subject_includes_subject_block(self):
        prompt = _build_system_prompt(memories=[], context="", lesson_subject="The Water Cycle")
        assert "[LESSON SUBJECT]" in prompt
        assert "The Water Cycle" in prompt

    def test_no_lesson_subject_omits_subject_block(self):
        prompt = _build_system_prompt(memories=[], context="Some context.", lesson_subject=None)
        assert "[LESSON SUBJECT]" not in prompt

    def test_subject_block_appears_before_context_block(self):
        prompt = _build_system_prompt(
            memories=[],
            context="Water evaporates when heated.",
            lesson_subject="The Water Cycle",
        )
        assert prompt.index("[LESSON SUBJECT]") < prompt.index("\n[CONTEXT]\n")
