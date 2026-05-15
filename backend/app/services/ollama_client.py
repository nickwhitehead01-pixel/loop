"""
Thin async wrapper around the Ollama HTTP API.

Keeps all raw HTTP calls in one place so the rest of the codebase
never imports httpx directly for LLM work.
"""
from __future__ import annotations

import json
import logging
from typing import AsyncIterator

import httpx

logger = logging.getLogger(__name__)

from app.core.config import settings

# Single shared client — reused across requests (connection pooling)
_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        # No read timeout — LLM inference on local hardware can be slow.
        # Short connect/pool timeouts so we still fail fast if Ollama is down.
        _client = httpx.AsyncClient(
            base_url=settings.ollama_base_url,
            timeout=httpx.Timeout(connect=10.0, read=None, write=None, pool=10.0),
        )
    return _client


async def close_client() -> None:
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
        _client = None


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

async def health_check() -> bool:
    """Return True if Ollama is reachable and responding."""
    try:
        r = await get_client().get("/api/tags", timeout=5)
        return r.status_code == 200
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------

async def embed(text: str) -> list[float]:
    """Embed *text* using nomic-embed-text and return a 768-dim float vector."""
    r = await get_client().post(
        "/api/embed",
        json={"model": settings.ollama_embed_model, "input": text},
    )
    r.raise_for_status()
    # /api/embed returns {"embeddings": [[...]]}
    return r.json()["embeddings"][0]


async def embed_batch(texts: list[str]) -> list[list[float]]:
    """
    Embed a list of texts in a single HTTP call to Ollama.
    Returns a list of 768-dim float vectors in the same order as *texts*.
    Much faster than calling embed() N times when ingesting document chunks.
    """
    r = await get_client().post(
        "/api/embed",
        json={"model": settings.ollama_embed_model, "input": texts},
    )
    r.raise_for_status()
    return r.json()["embeddings"]


# ---------------------------------------------------------------------------
# Streaming generation (raw Ollama API)
# Used by teacher_rag — pupil_graph uses ChatOllama from langchain-ollama
# ---------------------------------------------------------------------------

async def generate_stream(
    messages: list[dict[str, str]],
    model: str,
    system: str | None = None,
    format: str | None = None,
) -> AsyncIterator[str]:
    """
    Call /api/chat in streaming mode and yield token strings.

    Args:
        messages: list of {"role": "user"|"assistant", "content": "..."}
        model:    ollama model tag, e.g. "gemma4:e2b"
        system:   optional system prompt (prepended automatically)
        format:   optional output format, e.g. "json" to force valid JSON
    """
    payload: dict = {"model": model, "messages": messages, "stream": True}
    if system:
        payload["messages"] = [{"role": "system", "content": system}] + messages
    if format:
        payload["format"] = format

    async with get_client().stream("POST", "/api/chat", json=payload) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            token = data.get("message", {}).get("content", "")
            if token:
                yield token
            if data.get("done"):
                break


async def generate_full(
    messages: list[dict[str, str]],
    model: str,
    system: str | None = None,
    format: str | None = None,
) -> str:
    """Non-streaming version — collects all tokens and returns the full string."""
    tokens = []
    async for token in generate_stream(messages, model, system, format=format):
        tokens.append(token)
    return "".join(tokens)


async def warmup_model(model: str) -> None:
    """Load *model* into Ollama memory and pin it for the server's lifetime.

    Uses /api/embed for embedding models and /api/generate for LLM models,
    with keep_alive=-1 so Ollama never evicts after the default 5-min timeout.
    Swallows all exceptions so a slow or absent Ollama never aborts startup.
    """
    try:
        # Try embed endpoint first (works for embedding models; LLMs return 400)
        r = await get_client().post(
            "/api/embed",
            json={"model": model, "input": "", "keep_alive": -1},
            timeout=120.0,
        )
        if r.status_code == 400:
            # Not an embedding model — fall back to generate endpoint
            r = await get_client().post(
                "/api/generate",
                json={"model": model, "prompt": "", "keep_alive": -1},
                timeout=120.0,
            )
        r.raise_for_status()
        logger.info("Ollama model warm-up complete: %s (pinned in memory)", model)
    except Exception:
        logger.warning(
            "Ollama model warm-up failed for %s — first request will pay cold-start latency",
            model,
            exc_info=True,
        )


# ---------------------------------------------------------------------------
# Vision — image description via gemma4 multimodal
# ---------------------------------------------------------------------------

async def describe_image(image_b64: str) -> str:
    """
    Use gemma4's vision capability to produce a rich text description of a
    base64-encoded image extracted from an uploaded lesson document.

    Per Ollama/Gemma4 best practices, image content is placed before the text
    prompt in the message payload for optimal multimodal performance.

    Returns the description string, or an empty string if the model returns nothing.
    """
    payload = {
        "model": settings.ollama_model_teacher,
        "messages": [
            {
                "role": "user",
                # Image is declared in "images" list; Gemma4 processes it before the text.
                "content": (
                    "Describe this image in detail for educational indexing purposes. "
                    "Include all visible text, diagram labels, chart values, equations, "
                    "figures, and key visual concepts. Be thorough and specific."
                ),
                "images": [image_b64],
            }
        ],
        "stream": False,
    }
    r = await get_client().post("/api/chat", json=payload, timeout=120)
    r.raise_for_status()
    return r.json().get("message", {}).get("content", "").strip()

