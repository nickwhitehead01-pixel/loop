"""
One-shot lesson analysis: generate the glossary and prompt-card library
that the live transcription handler will later MATCH against rather than
regenerate per chunk.

This runs once per lesson, off the live path, inside the background worker.
Doing the heavy LLM work here rather than during the lesson is what lets the
live path stay fast on consumer hardware — at lesson time we only do cheap
matching (regex for glossary, cosine similarity for prompt cards) with
zero Gemma round-trips.

Output shapes (also documented on the Lesson model):

    glossary:      [{"term": str, "explanation": str}, ...]
    prompt_cards:  [{"id": str, "triggers": [str, ...], "question": str,
                    "color": str, "trigger_embedding": [float, ...]}, ...]
"""
from __future__ import annotations

import json
import logging
import uuid

from app.core.config import settings
from app.services import ollama_client

logger = logging.getLogger(__name__)

# Colors cycled across the generated cards. Same three colors the old
# live-generator used, so the pupil UI sees a consistent palette.
_COLORS = ["blue", "green", "amber"]

# Caps on the size of each precomputed asset. We deliberately ask for a
# generous glossary because teachers consistently report that *what an
# adult considers obvious* trips up 11-year-olds (especially those with
# SEN). Better to flag a word the pupil already knows than miss one they
# don't — they can just ignore the underline.
_TARGET_GLOSSARY_SIZE = (30, 50)        # min, max — soft targets in the prompt
_TARGET_PROMPT_CARD_COUNT = (8, 12)


# ---------------------------------------------------------------------------
# Shared system prompt — locks the model into JSON-only mode
# ---------------------------------------------------------------------------

_JSON_SYSTEM_PROMPT = (
    "You are a JSON API endpoint. "
    "Respond ONLY with valid JSON matching the exact schema shown in the user message. "
    "Do not write any text before or after the JSON. "
    "No explanations, no markdown, no code fences, no commentary."
)


# ---------------------------------------------------------------------------
# Glossary generation
# ---------------------------------------------------------------------------

_GLOSSARY_PROMPT = (
    "REQUIRED OUTPUT FORMAT:\n"
    '{{"terms": [{{"term": "word or phrase", "explanation": "one or two plain sentences"}}, ...]}}\n\n'
    "TASK: Extract {min}-{max} vocabulary terms from the lesson below for pupils aged 11-15. "
    "Include any word a pupil might not know: technical terms, abstract nouns, "
    "unusual verbs, proper nouns of significance, and subject-specific phrases. "
    "When in doubt, include the term. "
    "For each term write a friendly one- or two-sentence explanation a curious 10-year-old would understand.\n\n"
    "LESSON CONTENT:\n"
    "---\n{content}\n---\n\n"
    "Respond with ONLY this JSON (no other text):\n"
    '{{"terms": [{{"term": "...", "explanation": "..."}}, ...]}}'
)


def _repair_and_parse(text: str) -> object:
    """Parse JSON with json_repair as fallback for malformed LLM output.

    Gemma sometimes emits unescaped inner quotes or apostrophes inside
    explanation strings, producing a JSONDecodeError. json_repair handles
    the most common LLM JSON quirks (unescaped quotes, truncated arrays, etc).
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        try:
            from json_repair import repair_json
            repaired = repair_json(text, return_objects=True)
            return repaired
        except Exception:
            raise ValueError(f"JSON parse failed and json_repair could not fix it")


def _parse_list(raw: str, *, primary_key: str | None = None) -> list:
    """Pull a list out of *raw*, tolerating the shapes Gemma actually returns.

    Under Ollama's ``format="json"`` the top-level value is forced to be a
    JSON object, so we usually get ``{"terms": [...]}`` or similar — but
    we also tolerate a bare array (in case format mode is dropped), and a
    dict with the array under any key (in case Gemma renames it).
    Uses json_repair as a fallback when Gemma emits unescaped characters.
    """
    # Strip stray prose around the JSON if the model decided to be chatty.
    obj_start = raw.find("{")
    arr_start = raw.find("[")
    if obj_start == -1 and arr_start == -1:
        raise ValueError("No JSON in LLM response")
    # Whichever opens first is the top-level shape.
    if arr_start != -1 and (obj_start == -1 or arr_start < obj_start):
        end = raw.rfind("]") + 1
        return _repair_and_parse(raw[arr_start:end])
    end = raw.rfind("}") + 1
    parsed = _repair_and_parse(raw[obj_start:end])
    if not isinstance(parsed, dict):
        raise ValueError("Expected dict at top level, got %r" % type(parsed).__name__)
    if primary_key and isinstance(parsed.get(primary_key), list):
        return parsed[primary_key]
    # Last-resort 1: take the first list-valued field.
    for value in parsed.values():
        if isinstance(value, list):
            return value
    # Last-resort 2: the model returned the wrong outer object entirely
    # (e.g. a QA-style {"selected_answer": ..., "reason": ...}). If the raw
    # string still contains an embedded array, extract it directly.
    if arr_start != -1:
        end2 = raw.rfind("]") + 1
        if end2 > arr_start:
            try:
                return _repair_and_parse(raw[arr_start:end2])
            except (json.JSONDecodeError, ValueError):
                pass
    raise ValueError(f"No list found in object (keys: {list(parsed.keys())})")


async def generate_glossary(lesson_content: str) -> list[dict]:
    """Single Gemma call. Returns a sanitised glossary list.

    Anything malformed in the response is silently dropped — better to ship
    a slightly shorter list than fail the whole precompute over one bad item.
    """
    prompt = _GLOSSARY_PROMPT.format(
        min=_TARGET_GLOSSARY_SIZE[0],
        max=_TARGET_GLOSSARY_SIZE[1],
        content=lesson_content[:16000],  # crude cap, fits comfortably in context
    )
    raw = await ollama_client.generate_full(
        messages=[{"role": "user", "content": prompt}],
        model=settings.ollama_model_teacher,
        system=_JSON_SYSTEM_PROMPT,
        format="json",
    )
    parsed = _parse_list(raw, primary_key="terms")
    # Dedupe by lowercased term — Gemma frequently lists the same term two or
    # three times when it spans multiple paragraphs of the source material.
    # First occurrence wins so the most carefully-written explanation is kept.
    seen: set[str] = set()
    cleaned: list[dict] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        term = str(item.get("term", "")).strip()
        explanation = str(item.get("explanation", "")).strip()
        if not term or not explanation:
            continue
        key = term.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append({"term": term, "explanation": explanation})
    # Trim to upper bound in case Gemma went over.
    return cleaned[: _TARGET_GLOSSARY_SIZE[1]]


# ---------------------------------------------------------------------------
# Prompt-card library generation
# ---------------------------------------------------------------------------

_PROMPT_CARDS_PROMPT = (
    # Schema FIRST — repeated at the end after the content.
    "REQUIRED OUTPUT FORMAT:\n"
    '{{"cards": [{{"question": "short pupil question", "triggers": ["phrase1", "phrase2"]}}, ...]}}\n\n'
    "TASK: Create {min}-{max} prompt cards from the lesson below. "
    "Each card is a question a pupil might ask while listening. "
    "Each card has a question (under 12 words, written as a pupil would ask it) "
    "and 2-4 trigger phrases (1-4 words each, taken directly from the lesson text) "
    "that should make the card appear. "
    "Cover DIFFERENT parts of the lesson. Focus on curious 'why' and 'how' questions.\n\n"
    "LESSON CONTENT:\n"
    "---\n{content}\n---\n\n"
    "Respond with ONLY this JSON (no other text):\n"
    '{{"cards": [{{"question": "...", "triggers": ["...", "..."]}}, ...]}}'
)


async def generate_prompt_card_library(lesson_content: str) -> list[dict]:
    """Single Gemma call to draft the card library; then embed each card's
    triggers so the live matcher can do semantic similarity instead of pure
    string matching.

    Each card gets a stable id (uuid4-prefixed) so the live session can use
    a recently-shown-cards cooldown without needing to compare full questions.
    """
    prompt = _PROMPT_CARDS_PROMPT.format(
        min=_TARGET_PROMPT_CARD_COUNT[0],
        max=_TARGET_PROMPT_CARD_COUNT[1],
        content=lesson_content[:16000],
    )
    raw = await ollama_client.generate_full(
        messages=[{"role": "user", "content": prompt}],
        model=settings.ollama_model_teacher,
        system=_JSON_SYSTEM_PROMPT,
        format="json",
    )
    parsed = _parse_list(raw, primary_key="cards")

    cards: list[dict] = []
    for idx, item in enumerate(parsed[: _TARGET_PROMPT_CARD_COUNT[1]]):
        if not isinstance(item, dict):
            continue
        question = str(item.get("question", "")).strip()
        triggers_raw = item.get("triggers", [])
        if not isinstance(triggers_raw, list):
            continue
        triggers = [str(t).strip() for t in triggers_raw if str(t).strip()]
        if not question or not triggers:
            continue

        # Embed the concatenated triggers in one call — the matcher only
        # needs a single embedding vector per card, and "trigger A. trigger B."
        # captures all of them in one shot.
        trigger_text = ". ".join(triggers)
        try:
            embedding = await ollama_client.embed(trigger_text)
        except Exception:
            logger.exception(
                "[precompute] embedding failed for card triggers=%r — skipping",
                triggers,
            )
            continue

        cards.append({
            "id": f"card_{uuid.uuid4().hex[:8]}",
            "question": question,
            "triggers": triggers,
            "color": _COLORS[idx % len(_COLORS)],
            "trigger_embedding": embedding,
        })

    return cards


# ---------------------------------------------------------------------------
# Public entry point used by the worker
# ---------------------------------------------------------------------------

async def precompute_features(lesson_chunks: list[str]) -> tuple[list[dict], list[dict]]:
    """Run both precomputes for a lesson. Returns (glossary, prompt_cards).

    Raises on Gemma/embedding failure so the caller (worker) can decide
    whether to mark the lesson failed or retry on the next poll cycle.
    """
    content = "\n\n".join(c for c in lesson_chunks if c.strip())
    if not content:
        return [], []
    glossary = await generate_glossary(content)
    prompt_cards = await generate_prompt_card_library(content)
    return glossary, prompt_cards
