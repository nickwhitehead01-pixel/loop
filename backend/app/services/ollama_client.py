"""
Thin async wrapper around the Ollama HTTP API.

Keeps all raw HTTP calls in one place so the rest of the codebase
never imports httpx directly for LLM work.
"""
from __future__ import annotations

import json
from typing import AsyncIterator

import httpx

from app.core.config import settings

# Single shared client — reused across requests (connection pooling)
_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(base_url=settings.ollama_base_url, timeout=120)
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

