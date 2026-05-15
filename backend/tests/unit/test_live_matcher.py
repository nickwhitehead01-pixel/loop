"""
Unit tests for app.services.live_matcher.

Tests the three public components:
  - _cosine       (pure-Python dot-product similarity)
  - match_tappable_terms  (regex-based glossary matching)
  - match_prompt_cards    (semantic similarity with mocked embedding)
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services.live_matcher import (
    _cosine,
    match_tappable_terms,
    match_prompt_cards,
)


# ---------------------------------------------------------------------------
# _cosine
# ---------------------------------------------------------------------------

class TestCosine:

    def test_identical_vectors(self):
        v = [1.0, 0.0, 0.0]
        assert abs(_cosine(v, v) - 1.0) < 1e-9

    def test_orthogonal_vectors(self):
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert abs(_cosine(a, b)) < 1e-9

    def test_opposite_vectors(self):
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        assert abs(_cosine(a, b) + 1.0) < 1e-9

    def test_zero_vector_a_returns_zero(self):
        assert _cosine([0.0, 0.0], [1.0, 0.5]) == 0.0

    def test_zero_vector_b_returns_zero(self):
        assert _cosine([1.0, 0.5], [0.0, 0.0]) == 0.0

    def test_empty_vectors_returns_zero(self):
        assert _cosine([], []) == 0.0

    def test_mismatched_lengths_returns_zero(self):
        assert _cosine([1.0, 2.0], [1.0]) == 0.0

    def test_partial_similarity(self):
        a = [1.0, 1.0, 0.0]
        b = [1.0, 0.0, 0.0]
        # cos = 1 / sqrt(2) ≈ 0.707
        result = _cosine(a, b)
        assert 0.7 < result < 0.72


# ---------------------------------------------------------------------------
# match_tappable_terms
# ---------------------------------------------------------------------------

class TestMatchTappableTerms:

    def test_empty_glossary_returns_empty(self):
        assert match_tappable_terms("lots of relevant transcript text", []) == []

    def test_none_glossary_returns_empty(self):
        assert match_tappable_terms("transcript text here", None) == []

    def test_empty_transcript_returns_empty(self):
        glossary = [{"term": "osmosis", "explanation": "Water movement."}]
        assert match_tappable_terms("", glossary) == []

    def test_basic_single_match(self):
        glossary = [{"term": "osmosis", "explanation": "Water movement through membrane."}]
        result = match_tappable_terms("We discussed osmosis in the experiment.", glossary)
        assert len(result) == 1
        assert result[0]["term"] == "osmosis"
        assert "explanation" in result[0]

    def test_case_insensitive_match(self):
        glossary = [{"term": "Mitosis", "explanation": "Cell division."}]
        result = match_tappable_terms("today we learn about MITOSIS", glossary)
        assert len(result) == 1

    def test_deduplication_same_term_multiple_occurrences(self):
        glossary = [{"term": "ion", "explanation": "Charged particle."}]
        result = match_tappable_terms("An ion is formed when an ion loses electrons.", glossary)
        assert len(result) == 1

    def test_multi_word_term_matches(self):
        glossary = [
            {"term": "natural selection", "explanation": "Survival of the fittest."},
            {"term": "selection", "explanation": "Choosing organisms."},
        ]
        result = match_tappable_terms("Darwin described natural selection.", glossary)
        # The multi-word term should match once
        terms = {r["term"].lower() for r in result}
        assert "natural selection" in terms

    def test_no_match_returns_empty(self):
        glossary = [{"term": "mitosis", "explanation": "Cell division."}]
        result = match_tappable_terms("The weather was sunny today.", glossary)
        assert result == []

    def test_multiple_distinct_matches(self):
        glossary = [
            {"term": "nucleus", "explanation": "Control centre of the cell."},
            {"term": "membrane", "explanation": "Layer around the cell."},
        ]
        result = match_tappable_terms(
            "The nucleus is surrounded by the membrane.", glossary
        )
        terms = {r["term"].lower() for r in result}
        assert "nucleus" in terms
        assert "membrane" in terms

    def test_skips_entries_without_explanation(self):
        glossary = [
            {"term": "vacuole", "explanation": ""},
            {"term": "ribosome", "explanation": "Makes proteins."},
        ]
        result = match_tappable_terms("The ribosome and vacuole are organelles.", glossary)
        terms = [r["term"].lower() for r in result]
        assert "ribosome" in terms
        # vacuole has blank explanation so it's excluded from the compiled regex
        assert "vacuole" not in terms

    def test_word_boundary_prevents_partial_match(self):
        glossary = [{"term": "ion", "explanation": "Charged particle."}]
        # "onion" contains "ion" but word boundary should prevent a match
        result = match_tappable_terms("She was chopping an onion.", glossary)
        assert result == []


# ---------------------------------------------------------------------------
# match_prompt_cards
# ---------------------------------------------------------------------------

class TestMatchPromptCards:

    async def test_empty_library_returns_empty(self):
        result = await match_prompt_cards("transcript", [], set())
        assert result == []

    async def test_none_library_returns_empty(self):
        result = await match_prompt_cards("transcript", None, set())
        assert result == []

    async def test_blank_transcript_returns_empty(self):
        library = [{"id": "card_1", "question": "Q?", "color": "blue",
                    "trigger_embedding": [0.1] * 768}]
        result = await match_prompt_cards("   ", library, set())
        assert result == []

    async def test_embed_failure_returns_empty(self):
        library = [{"id": "card_1", "question": "Q?", "color": "blue",
                    "trigger_embedding": [0.1] * 768}]
        with patch(
            "app.services.live_matcher.ollama_client.embed",
            new_callable=AsyncMock,
            side_effect=RuntimeError("embed error"),
        ):
            result = await match_prompt_cards("some transcript", library, set())
        assert result == []

    async def test_card_below_threshold_skipped(self):
        # Use orthogonal vectors to guarantee similarity = 0
        card_embedding = [1.0] + [0.0] * 767
        transcript_embedding = [0.0, 1.0] + [0.0] * 766  # orthogonal

        library = [
            {
                "id": "card_1",
                "question": "What is photosynthesis?",
                "color": "blue",
                "trigger_embedding": card_embedding,
            }
        ]
        with patch(
            "app.services.live_matcher.ollama_client.embed",
            new_callable=AsyncMock,
            return_value=transcript_embedding,
        ):
            result = await match_prompt_cards("some transcript about weather", library, set())
        assert result == []

    async def test_card_above_threshold_returned(self):
        # Use identical vectors to get similarity = 1.0 (well above 0.55)
        embedding = [0.1] * 768
        library = [
            {
                "id": "card_1",
                "question": "What causes rain?",
                "color": "green",
                "trigger_embedding": embedding,
            }
        ]
        with patch(
            "app.services.live_matcher.ollama_client.embed",
            new_callable=AsyncMock,
            return_value=embedding,
        ):
            result = await match_prompt_cards("What causes rain in the UK?", library, set())
        assert len(result) == 1
        assert result[0]["id"] == "card_1"
        assert result[0]["text"] == "What causes rain?"
        assert result[0]["color"] == "green"

    async def test_recently_shown_card_excluded(self):
        embedding = [0.1] * 768
        library = [
            {
                "id": "card_cooldown",
                "question": "Already shown question?",
                "color": "blue",
                "trigger_embedding": embedding,
            }
        ]
        with patch(
            "app.services.live_matcher.ollama_client.embed",
            new_callable=AsyncMock,
            return_value=embedding,
        ):
            result = await match_prompt_cards(
                "relevant transcript", library, {"card_cooldown"}
            )
        assert result == []

    async def test_max_three_cards_returned(self):
        embedding = [0.1] * 768
        library = [
            {"id": f"card_{i}", "question": f"Question {i}?", "color": "blue",
             "trigger_embedding": embedding}
            for i in range(6)
        ]
        with patch(
            "app.services.live_matcher.ollama_client.embed",
            new_callable=AsyncMock,
            return_value=embedding,
        ):
            result = await match_prompt_cards("relevant text about topic", library, set())
        assert len(result) <= 3

    async def test_output_uses_text_not_question_key(self):
        embedding = [0.1] * 768
        library = [
            {"id": "card_x", "question": "What is gravity?", "color": "amber",
             "trigger_embedding": embedding}
        ]
        with patch(
            "app.services.live_matcher.ollama_client.embed",
            new_callable=AsyncMock,
            return_value=embedding,
        ):
            result = await match_prompt_cards("gravity pulls objects down", library, set())
        assert len(result) == 1
        assert "text" in result[0]
        assert "question" not in result[0]
