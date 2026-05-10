"""
Generate context-aware prompt cards from a transcript bucket.

Each call passes only the current bucket text (~200 words) to Gemma —
no conversation history, no memory, no session context — keeping the
prompt minimal to preserve performance.
"""
from __future__ import annotations

import json
import logging

from app.core.config import settings
from app.services import ollama_client

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "Generate exactly 3 short questions (under 12 words each) that a student "
    "might ask about this content. Return ONLY a JSON array of 3 strings, "
    'nothing else. Example: ["What is X?", "How does Y work?", "Why is Z?"]'
)

_COLORS = ["blue", "green", "amber"]


def _extract_questions(raw: str) -> list[str]:
    """
    Parse the raw LLM output into a flat list of question strings.

    Handles three common Gemma response shapes:
      - bare array:          ["Q1", "Q2", "Q3"]
      - wrapped in a dict:   {"questions": ["Q1", ...]}
      - any dict with a list value: {"1": "Q1", "2": "Q2", ...} (flattened)
    """
    # Strip markdown code fences if present (shouldn't happen with format=json,
    # but Gemma can be inconsistent under load)
    stripped = raw.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        stripped = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    parsed = json.loads(stripped)

    if isinstance(parsed, list):
        return [str(q) for q in parsed if str(q).strip()]

    if isinstance(parsed, dict):
        # Try to find a list value (e.g. {"questions": [...]})
        for v in parsed.values():
            if isinstance(v, list):
                return [str(q) for q in v if str(q).strip()]
        # Fall back to dict values as individual questions
        return [str(v) for v in parsed.values() if str(v).strip()]

    return []


async def generate_prompt_cards(bucket_text: str) -> list[dict]:
    """
    Return up to 3 prompt cards derived from *bucket_text*.

    Each card is ``{"text": str, "color": str}``.
    Returns an empty list if generation fails or the output is unparseable.
    """
    logger.warning("Generating prompt cards for bucket (%d words)", len(bucket_text.split()))
    try:
        raw = await ollama_client.generate_full(
            messages=[{"role": "user", "content": bucket_text}],
            model=settings.ollama_model_pupil,
            system=_SYSTEM_PROMPT,
            format="json",
        )
        logger.debug("Prompt card raw response: %r", raw)
        questions = _extract_questions(raw)
        if not questions:
            logger.warning("Prompt card extraction returned no questions. Raw: %r", raw)
            return []
        cards = [
            {"text": q, "color": _COLORS[i % len(_COLORS)]}
            for i, q in enumerate(questions[:3])
        ]
        logger.info("Prompt cards generated: %s", [c["text"] for c in cards])
        return cards
    except Exception as exc:
        logger.warning("Prompt card generation failed: %s", exc)
        return []
