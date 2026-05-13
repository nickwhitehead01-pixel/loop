"""
Live-lesson matching for pre-computed pupil features.

Replaces the per-chunk Gemma calls that used to generate prompt cards and
tappable terms on the fly. Both helpers here run against data the
background worker already produced (see precomputed_features.py) so the
live path stays Gemma-free.

Two strategies, one for each feature:

- Glossary (tappable terms):  case-insensitive word-boundary regex against
  the lesson's glossary. Cheap, deterministic, no model call.

- Prompt cards: semantic match. Embed the recent transcript window once
  via nomic-embed-text, then cosine-similarity it against each card's
  pre-computed trigger embedding. One small embedding call per match,
  pure-Python dot products against the small library.
"""
from __future__ import annotations

import logging
import math
import re

from app.services import ollama_client

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Glossary matching (tappable terms)
# ---------------------------------------------------------------------------

# Compile a per-(lesson,version) regex on first use and cache it. Recompiling
# 20 patterns for every transcript chunk would be wasteful even though each
# one is cheap. The cache is keyed by id(glossary) so a re-uploaded lesson
# that gets a new list bypasses stale state automatically.
_GLOSSARY_REGEX_CACHE: dict[int, tuple[re.Pattern, dict[str, dict]]] = {}


def _compile_glossary(glossary: list[dict]) -> tuple[re.Pattern, dict[str, dict]]:
    """Compile a case-insensitive whole-word regex matching any glossary
    term, plus a lookup from the lowercased term back to the full entry.

    Multi-word terms are supported — `\\b` handles word boundaries on both
    sides of the phrase.
    """
    if not glossary:
        return re.compile(r"^$"), {}  # never matches
    lookup: dict[str, dict] = {}
    sorted_terms: list[str] = []
    for entry in glossary:
        term = str(entry.get("term", "")).strip()
        explanation = str(entry.get("explanation", "")).strip()
        if not term or not explanation:
            continue
        key = term.lower()
        if key in lookup:
            continue  # dedupe
        lookup[key] = {"term": term, "explanation": explanation}
        sorted_terms.append(term)
    # Sort longest-first so multi-word matches win over substring matches
    # of one of their constituent words.
    sorted_terms.sort(key=len, reverse=True)
    pattern = r"\b(?:" + "|".join(re.escape(t) for t in sorted_terms) + r")\b"
    return re.compile(pattern, re.IGNORECASE), lookup


def match_tappable_terms(
    transcript_text: str,
    glossary: list[dict] | None,
) -> list[dict]:
    """Return the glossary entries that appear in *transcript_text*.

    Output shape matches what the old generator produced so the broadcast
    payload doesn't change:  [{"term": str, "explanation": str}, ...].

    Returns [] when the glossary hasn't been pre-computed yet — pupils
    just see no underlines until the worker catches up.
    """
    if not glossary or not transcript_text:
        return []
    cache_key = id(glossary)
    cached = _GLOSSARY_REGEX_CACHE.get(cache_key)
    if cached is None:
        cached = _compile_glossary(glossary)
        _GLOSSARY_REGEX_CACHE[cache_key] = cached
    pattern, lookup = cached

    seen: set[str] = set()
    matches: list[dict] = []
    for raw_match in pattern.findall(transcript_text):
        key = raw_match.lower()
        if key in seen:
            continue
        seen.add(key)
        entry = lookup.get(key)
        if entry:
            matches.append(entry)
    return matches


# ---------------------------------------------------------------------------
# Prompt-card matching (semantic)
# ---------------------------------------------------------------------------

# Cosine-similarity threshold above which a card is considered relevant.
# Tuned by feel for nomic-embed-text on lesson-domain text; lower it if too
# few cards surface, raise it if irrelevant cards leak through.
_SIMILARITY_THRESHOLD = 0.55

# Max cards per broadcast — pupils see a small row in the UI, three is plenty.
_MAX_CARDS_PER_BROADCAST = 3


def _cosine(a: list[float], b: list[float]) -> float:
    """Standard cosine similarity. Returns 0 on degenerate inputs."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


async def match_prompt_cards(
    transcript_text: str,
    prompt_card_library: list[dict] | None,
    recently_shown_ids: set[str],
) -> list[dict]:
    """Find the most relevant cards for *transcript_text*, excluding any
    whose id is in *recently_shown_ids*.

    Single embedding call against the transcript window, then in-memory
    cosine similarity against each card's stored trigger embedding.

    Output shape matches the old generator so the broadcast payload is
    backwards-compatible with the existing pupil app:
        [{"id": str, "question": str, "color": str}, ...]
    The "id" field is new but additive — older clients will ignore it.
    """
    if not prompt_card_library or not transcript_text.strip():
        return []

    try:
        window_embedding = await ollama_client.embed(transcript_text)
    except Exception:
        logger.exception("[matcher] transcript embedding failed — skipping cards")
        return []

    scored: list[tuple[float, dict]] = []
    for card in prompt_card_library:
        if not isinstance(card, dict):
            continue
        if card.get("id") in recently_shown_ids:
            continue
        embedding = card.get("trigger_embedding")
        if not isinstance(embedding, list):
            continue
        score = _cosine(window_embedding, embedding)
        if score >= _SIMILARITY_THRESHOLD:
            scored.append((score, card))

    scored.sort(key=lambda pair: pair[0], reverse=True)

    return [
        {
            "id": card["id"],
            "question": card["question"],
            "color": card.get("color", "blue"),
        }
        for _, card in scored[:_MAX_CARDS_PER_BROADCAST]
    ]
