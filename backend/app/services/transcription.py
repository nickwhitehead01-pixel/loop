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
    """
    model = get_model()
    segments, info = model.transcribe(
        io.BytesIO(audio_bytes),
        beam_size=1,          # fastest decoding
        best_of=1,
        temperature=0.0,
        language="en",
        condition_on_previous_text=False,
        vad_filter=False,
        no_speech_threshold=0.45,
    )
    # Consume the lazy generator fully within this thread so CPU work never
    # spills back onto the asyncio event loop.
    text = " ".join(seg.text.strip() for seg in segments).strip()
    return TranscriptionResult(text=text, language=info.language or "en")


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
