"""
Generate "tappable terms" from a transcript bucket — words and short phrases
the pupil might find unfamiliar or worth expanding.

The pupil app renders each match with a dotted underline; tapping reveals the
short, plain-language explanation produced here.

Same minimal-prompt approach as `prompt_card_service`: only the current
bucket text is passed to Gemma, no conversation history or memory.
"""
from __future__ import annotations

import json
import logging

from app.core.config import settings
from app.services import ollama_client

logger = logging.getLogger(__name__)

# Up to 5 terms, 1-4 words each. Prefer the things that are *worth* explaining
# in this excerpt — domain-specific terms, unusual verbs, proper nouns —
# over everyday hard words that the pupil meets all the time.
_SYSTEM_PROMPT = (
    "You help a tutor pick which words or short phrases in a lesson "
    "transcript might confuse a pupil aged 11–15 (some with SEN). "
    "Pick up to 5 items from the text. Each item is 1–4 words. Prefer:\n"
    "- domain-specific or technical terms\n"
    "- unusual or vivid verbs/adjectives\n"
    "- proper nouns of historical/cultural significance\n"
    "- concepts the pupil may not have met yet\n"
    "Skip: common everyday words, anything obvious from context, anything "
    "you cannot explain confidently.\n"
    "For each: a one- or two-sentence plain explanation, friendly but not "
    "patronising. Use simple words. No jargon in the explanation.\n"
    "Return ONLY a JSON object of the form: "
    '{"terms": [{"term": "...", "explanation": "..."}, ...]} '
    "with at most 5 entries. If nothing is worth explaining, return "
    '{"terms": []}.'
)

_MAX_TERMS = 5
_MAX_WORDS_PER_TERM = 4


def _extract_terms(raw: str) -> list[dict]:
    """Parse the raw LLM output into ``[{"term": str, "explanation": str}, ...]``.

    Tolerates a few shapes Gemma sometimes produces:
      - canonical:      {"terms": [{"term": ..., "explanation": ...}, ...]}
      - bare array:     [{"term": ..., "explanation": ...}, ...]
      - dict-of-terms:  {"term1": "explanation1", "term2": "explanation2"}
    """
    stripped = raw.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        stripped = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    parsed = json.loads(stripped)

    rows: list[dict] = []
    if isinstance(parsed, dict) and isinstance(parsed.get("terms"), list):
        rows = parsed["terms"]
    elif isinstance(parsed, list):
        rows = parsed
    elif isinstance(parsed, dict):
        # Treat dict as {term: explanation}
        rows = [{"term": k, "explanation": v} for k, v in parsed.items()]

    cleaned: list[dict] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        term = str(r.get("term", "")).strip()
        explanation = str(r.get("explanation", "")).strip()
        if not term or not explanation:
            continue
        # Cap phrase length so we never end up underlining whole sentences.
        if len(term.split()) > _MAX_WORDS_PER_TERM:
            continue
        cleaned.append({"term": term, "explanation": explanation})
        if len(cleaned) >= _MAX_TERMS:
            break
    return cleaned


async def generate_tappable_terms(bucket_text: str) -> list[dict]:
    """Return up to 5 tappable terms derived from *bucket_text*.

    Each entry is ``{"term": str, "explanation": str}``. Returns an empty
    list when Gemma can't find anything worth flagging or the response is
    unparseable — the pupil app simply doesn't underline anything new on
    that tick.
    """
    word_count = len(bucket_text.split())
    logger.warning("Generating tappable terms for bucket (%d words)", word_count)
    try:
        raw = await ollama_client.generate_full(
            messages=[{"role": "user", "content": bucket_text}],
            model=settings.ollama_model_tappable,
            system=_SYSTEM_PROMPT,
            format="json",
        )
        logger.debug("Tappable terms raw response: %r", raw)
        terms = _extract_terms(raw)
        if not terms:
            logger.info("No tappable terms returned for this bucket.")
            return []
        logger.info("Tappable terms: %s", [t["term"] for t in terms])
        return terms
    except Exception as exc:
        logger.warning("Tappable terms generation failed: %s", exc)
        return []
