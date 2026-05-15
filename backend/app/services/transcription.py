"""
Live speech-to-text using faster-whisper (local, offline).

Loads the model once at startup, then transcribes audio chunks on demand.
Uses the 'small' model (~1GB RAM) for best balance on 16GB machines.
"""
from __future__ import annotations

import asyncio
import io
import logging
from typing import NamedTuple

from faster_whisper import WhisperModel

from app.core.config import settings

logger = logging.getLogger(__name__)

_model: WhisperModel | None = None

# ---------------------------------------------------------------------------
# Junk-bucket filtering constants
# ---------------------------------------------------------------------------

# Whisper's own confidence that the audio contains no speech. Segments above
# this threshold are almost certainly background noise, brief coughs, or
# silence — drop them before joining into a bucket.
_NO_SPEECH_THRESHOLD = 0.6

# Minimum word count for a transcript bucket to be considered meaningful.
# A single word or two is almost always a false-positive from noise.
_MIN_WORD_COUNT = 4

# Known STT hallucinations Whisper produces on silence or background music.
_HALLUCINATION_PHRASES = frozenset([
    "thanks for watching",
    "thank you for watching",
    "subscribe",
    "subtitles by",
    "amara.org",
    "www.movieweb.com",
    "transcribed by",
    "please subscribe",
    "like and subscribe",
    "www.youtube.com",
])

# If any single token makes up more than this fraction of all tokens the
# text is almost certainly a Whisper looping hallucination (e.g. "the the
# the the the...").
_MAX_DOMINANT_TOKEN_RATIO = 0.5


class TranscriptionResult(NamedTuple):
    text: str
    language: str


def get_model() -> WhisperModel:
    """Lazy-load the Whisper model (CPU, int8 quantisation for speed)."""
    global _model
    if _model is None:
        logger.info("Loading faster-whisper model: %s (cpu/int8)", settings.whisper_model_size)
        _model = WhisperModel(
            settings.whisper_model_size,
            device="cpu",
            compute_type="int8",
        )
        logger.info("Whisper model loaded successfully")
    return _model


def _transcribe_sync(audio_bytes: bytes) -> TranscriptionResult:
    """
    Blocking transcription — runs the full faster-whisper pipeline synchronously.

    Must be called via asyncio.to_thread() to avoid blocking the event loop.
    The generator returned by model.transcribe() is consumed here, inside the
    thread, so the event loop is never occupied during CPU inference.

    Noise handling:
    - vad_filter=True: Silero VAD pre-screens audio and drops non-speech
      regions before Whisper even sees them — free speed-up and major
      noise reduction for rooms without a wearable mic.
    - Segments where Whisper itself is uncertain (no_speech_prob ≥ threshold)
      are dropped at the segment level, giving a second filtering pass.
    """
    model = get_model()
    segments, info = model.transcribe(
        io.BytesIO(audio_bytes),
        beam_size=1,          # fastest decoding
        best_of=1,
        temperature=0.0,
        language="en",
        condition_on_previous_text=False,  # prevents repetition-loop hallucinations when audio quality drops mid-lesson
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
        no_speech_threshold=0.45,
    )
    # Consume the lazy generator fully within this thread so CPU work never
    # spills back onto the asyncio event loop.
    # Drop any segment Whisper itself flags as likely non-speech.
    text_parts = []
    for seg in segments:
        if seg.no_speech_prob >= _NO_SPEECH_THRESHOLD:
            logger.debug(
                "Dropping segment (no_speech_prob=%.2f): %r",
                seg.no_speech_prob, seg.text[:60],
            )
            continue
        text_parts.append(seg.text.strip())
    text = " ".join(text_parts).strip()
    return TranscriptionResult(text=text, language=info.language or "en")


def is_valid_transcript(text: str) -> bool:
    """Return True only if *text* looks like genuine classroom speech.

    Applied as a cheap post-transcription filter before any DB writes or
    downstream work (broadcasts, slide-sync, prompt cards). Entirely
    rule-based — no LLM call — so it adds negligible latency.

    Filters out:
    - Buckets that are too short (noise bursts, single coughs)
    - Known Whisper hallucination phrases (produced on silence/music)
    - Repetition loops (Whisper sometimes repeats a word endlessly)
    """
    words = text.split()

    if len(words) < _MIN_WORD_COUNT:
        logger.debug("Dropping junk bucket (too short, %d words): %r", len(words), text)
        return False

    lower = text.lower()
    for phrase in _HALLUCINATION_PHRASES:
        if phrase in lower:
            logger.debug("Dropping junk bucket (hallucination %r): %r", phrase, text[:80])
            return False

    # Repetition loop: if any single lowercased word is > 50% of all tokens
    if words:
        from collections import Counter
        most_common_word, count = Counter(w.lower() for w in words).most_common(1)[0]
        if count / len(words) > _MAX_DOMINANT_TOKEN_RATIO:
            logger.debug(
                "Dropping junk bucket (repetition loop, %r x%d/%d): %r",
                most_common_word, count, len(words), text[:80],
            )
            return False

    return True


async def transcribe_chunk(audio_bytes: bytes) -> TranscriptionResult:
    """
    Transcribe a chunk of audio (WAV/WebM/raw PCM).

    Runs faster-whisper in a thread-pool worker via asyncio.to_thread so the
    CPU-bound inference never blocks the event loop. All network I/O (WebSocket
    broadcasts, DB writes) continues uninterrupted while transcription runs.

    Args:
        audio_bytes: Raw audio data (any format ffmpeg can decode).

    Returns:
        TranscriptionResult with text and detected language.
    """
    return await asyncio.to_thread(_transcribe_sync, audio_bytes)
