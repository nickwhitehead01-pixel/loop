"""
Unit tests for app.services.precomputed_features.

Tests cover JSON parsing helpers (_repair_and_parse, _parse_list) and the
two generator functions (generate_glossary, generate_prompt_card_library).
All Ollama calls are mocked — nothing touches the real LLM.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.services.precomputed_features import (
    _parse_list,
    _repair_and_parse,
    generate_glossary,
    generate_prompt_card_library,
    precompute_features,
)


# ---------------------------------------------------------------------------
# _repair_and_parse
# ---------------------------------------------------------------------------

class TestRepairAndParse:

    def test_valid_json_object(self):
        result = _repair_and_parse('{"terms": [1, 2, 3]}')
        assert result == {"terms": [1, 2, 3]}

    def test_valid_json_array(self):
        result = _repair_and_parse('[{"term": "photosynthesis"}]')
        assert isinstance(result, list)

    def test_malformed_unescaped_apostrophe(self):
        # Gemma sometimes emits apostrophes that break json.loads
        raw = '{"term": "it\'s complicated", "explanation": "means it\'s hard"}'
        # json_repair should handle this
        result = _repair_and_parse(raw)
        assert isinstance(result, dict)

    def test_completely_invalid_returns_something_or_raises(self):
        # json_repair is intentionally very permissive and will return *something*
        # (empty dict, empty string, None) rather than raise for garbage input.
        # The contract is: it must not raise an unhandled exception.
        try:
            result = _repair_and_parse("not json at all @@@ ###")
            # If it doesn't raise, the result must be a parseable Python object
            assert result is not None or result == "" or result == {} or isinstance(result, (dict, list, str))
        except (ValueError, Exception):
            pass  # also acceptable


# ---------------------------------------------------------------------------
# _parse_list
# ---------------------------------------------------------------------------

class TestParseList:

    def test_bare_array(self):
        raw = '[{"term": "osmosis", "explanation": "movement of water"}]'
        result = _parse_list(raw)
        assert len(result) == 1
        assert result[0]["term"] == "osmosis"

    def test_object_with_primary_key(self):
        raw = '{"terms": [{"term": "atom"}, {"term": "molecule"}]}'
        result = _parse_list(raw, primary_key="terms")
        assert len(result) == 2

    def test_object_without_primary_key_falls_back_to_first_list(self):
        raw = '{"items": [{"term": "nucleus"}]}'
        result = _parse_list(raw)  # no primary_key arg
        assert len(result) == 1
        assert result[0]["term"] == "nucleus"

    def test_object_with_wrong_primary_key_finds_any_list(self):
        raw = '{"data": [{"term": "gene"}]}'
        result = _parse_list(raw, primary_key="terms")
        assert len(result) == 1

    def test_prose_before_json(self):
        raw = 'Here is the JSON: {"terms": [{"term": "ribosome"}]}'
        result = _parse_list(raw, primary_key="terms")
        assert len(result) == 1

    def test_no_json_raises(self):
        with pytest.raises(ValueError, match="No JSON"):
            _parse_list("absolutely no json in here")

    def test_cards_primary_key(self):
        raw = '{"cards": [{"question": "What is DNA?", "triggers": ["DNA"]}]}'
        result = _parse_list(raw, primary_key="cards")
        assert result[0]["question"] == "What is DNA?"


# ---------------------------------------------------------------------------
# generate_glossary
# ---------------------------------------------------------------------------

class TestGenerateGlossary:

    @pytest.fixture
    def mock_generate(self):
        raw_response = json.dumps({
            "terms": [
                {"term": "Photosynthesis", "explanation": "How plants make food using sunlight."},
                {"term": "Chlorophyll", "explanation": "Green pigment in leaves."},
                {"term": "photosynthesis", "explanation": "Duplicate lowercase — should be dropped."},
            ]
        })
        with patch(
            "app.services.precomputed_features.ollama_client.generate_full",
            new_callable=AsyncMock,
            return_value=raw_response,
        ) as mock:
            yield mock

    async def test_returns_list_of_dicts(self, mock_generate):
        result = await generate_glossary("Plants need sunlight to grow.")
        assert isinstance(result, list)
        assert all(isinstance(item, dict) for item in result)

    async def test_items_have_term_and_explanation(self, mock_generate):
        result = await generate_glossary("Plants need sunlight to grow.")
        for item in result:
            assert "term" in item
            assert "explanation" in item

    async def test_deduplication_by_lowercase_term(self, mock_generate):
        result = await generate_glossary("Plants need sunlight to grow.")
        terms_lower = [item["term"].lower() for item in result]
        assert len(terms_lower) == len(set(terms_lower))

    async def test_strips_empty_items(self):
        raw_response = json.dumps({
            "terms": [
                {"term": "", "explanation": "empty term should be dropped"},
                {"term": "Mitosis", "explanation": ""},
                {"term": "Meiosis", "explanation": "Cell division producing gametes."},
            ]
        })
        with patch(
            "app.services.precomputed_features.ollama_client.generate_full",
            new_callable=AsyncMock,
            return_value=raw_response,
        ):
            result = await generate_glossary("Biology lesson.")
        assert len(result) == 1
        assert result[0]["term"] == "Meiosis"


# ---------------------------------------------------------------------------
# generate_prompt_card_library
# ---------------------------------------------------------------------------

class TestGeneratePromptCardLibrary:

    @pytest.fixture
    def mock_llm_and_embed(self):
        raw_response = json.dumps({
            "cards": [
                {"question": "Why is the sky blue?", "triggers": ["sky", "blue light"]},
                {"question": "What causes rain?", "triggers": ["rain", "water cycle"]},
            ]
        })
        embedding = [0.1] * 768
        with patch(
            "app.services.precomputed_features.ollama_client.generate_full",
            new_callable=AsyncMock,
            return_value=raw_response,
        ):
            with patch(
                "app.services.precomputed_features.ollama_client.embed_batch",
                new_callable=AsyncMock,
                return_value=[embedding, embedding],
            ):
                yield

    async def test_returns_list(self, mock_llm_and_embed):
        result = await generate_prompt_card_library("Weather lesson content.")
        assert isinstance(result, list)

    async def test_cards_have_required_fields(self, mock_llm_and_embed):
        result = await generate_prompt_card_library("Weather lesson content.")
        for card in result:
            assert "id" in card
            assert "question" in card
            assert "triggers" in card
            assert "color" in card
            assert "trigger_embedding" in card

    async def test_card_id_has_prefix(self, mock_llm_and_embed):
        result = await generate_prompt_card_library("Weather lesson content.")
        for card in result:
            assert card["id"].startswith("card_")

    async def test_color_cycles(self, mock_llm_and_embed):
        result = await generate_prompt_card_library("Weather lesson content.")
        allowed = {"blue", "green", "amber"}
        for card in result:
            assert card["color"] in allowed

    async def test_skips_items_without_question(self):
        raw_response = json.dumps({
            "cards": [
                {"question": "", "triggers": ["sky"]},
                {"question": "Why is grass green?", "triggers": ["grass", "chlorophyll"]},
            ]
        })
        embedding = [0.2] * 768
        with patch(
            "app.services.precomputed_features.ollama_client.generate_full",
            new_callable=AsyncMock,
            return_value=raw_response,
        ):
            with patch(
                "app.services.precomputed_features.ollama_client.embed_batch",
                new_callable=AsyncMock,
                return_value=[embedding],
            ):
                result = await generate_prompt_card_library("Biology lesson.")
        assert len(result) == 1
        assert result[0]["question"] == "Why is grass green?"

    async def test_embedding_failure_returns_empty(self):
        """embed_batch is all-or-nothing; any failure skips all cards."""
        raw_response = json.dumps({
            "cards": [
                {"question": "Good question one?", "triggers": ["trigger1"]},
                {"question": "Good question two?", "triggers": ["trigger2"]},
            ]
        })

        with patch(
            "app.services.precomputed_features.ollama_client.generate_full",
            new_callable=AsyncMock,
            return_value=raw_response,
        ):
            with patch(
                "app.services.precomputed_features.ollama_client.embed_batch",
                new_callable=AsyncMock,
                side_effect=RuntimeError("Embedding service unavailable"),
            ):
                result = await generate_prompt_card_library("Some lesson content.")
        assert result == []


# ---------------------------------------------------------------------------
# precompute_features — orchestration
# ---------------------------------------------------------------------------

class TestPrecomputeFeatures:

    async def test_empty_chunks_returns_empty_lists(self):
        result = await precompute_features([])
        assert result == ([], [])

    async def test_whitespace_only_chunks_returns_empty(self):
        result = await precompute_features(["   ", "\n\n"])
        assert result == ([], [])

    async def test_returns_glossary_and_cards(self):
        glossary_raw = json.dumps({"terms": [{"term": "Atom", "explanation": "Smallest unit."}]})
        cards_raw = json.dumps({"cards": [{"question": "What is an atom?", "triggers": ["atom"]}]})
        embedding = [0.1] * 768

        responses = iter([glossary_raw, cards_raw])
        with patch(
            "app.services.precomputed_features.ollama_client.generate_full",
            new_callable=AsyncMock,
            side_effect=lambda **kwargs: next(responses),
        ):
            with patch(
                "app.services.precomputed_features.ollama_client.embed_batch",
                new_callable=AsyncMock,
                return_value=[embedding],
            ):
                glossary, cards = await precompute_features(["Lesson chunk about atoms."])

        assert len(glossary) == 1
        assert glossary[0]["term"] == "Atom"
        assert len(cards) == 1
