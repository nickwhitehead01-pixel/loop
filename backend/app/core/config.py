from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Absolute path to the backend/ directory, regardless of cwd at startup.
_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(_BACKEND_DIR / ".env"), env_file_encoding="utf-8")

    # Database — absolute so the path is valid no matter where uvicorn is launched from.
    database_url: str = f"sqlite+aiosqlite:///{_BACKEND_DIR / 'data' / 'gemma_edu.db'}"
    chroma_dir: str = str(_BACKEND_DIR / "data" / "chroma")

    # Ollama
    ollama_base_url: str = "http://localhost:11434"
    ollama_model_pupil: str = "gemma4:e2b"
    ollama_model_teacher: str = "gemma4:e2b"
    ollama_embed_model: str = "nomic-embed-text"
    # Tappable-term flagging is a "classify-and-explain" task that doesn't
    # need full pupil-agent reasoning. Using a smaller, faster model keeps
    # the underline experience real-time even on a single-GPU classroom hub.
    ollama_model_tappable: str = "gemma3:1b"

    # App
    debug: bool = False
    upload_dir: str = str(_BACKEND_DIR / "uploads")

    # Whisper (speech-to-text).
    # `base.en` is the English-only variant of the base model: ~30–40% faster
    # than the multilingual variant of the same size because it skips language
    # detection, and ~2–3x faster than `small`. Accuracy on a single-teacher
    # classroom voice is effectively indistinguishable. Override via env to
    # `tiny.en` for low-spec demo hardware or back to `small.en` if you need
    # the extra accuracy.
    whisper_model_size: str = "base.en"

    # Conversation memory window (last N messages loaded per session)
    memory_window: int = 10

    # Semantic cache — cosine similarity threshold (0–1, higher = stricter match)
    semantic_cache_threshold: float = 0.92

    # Transcript bucket — accumulate small VAD utterances before embedding/storing.
    # A chunk is committed to ChromaDB when EITHER threshold is reached first.
    transcript_bucket_min_words: int = 200   # ~matches lesson document chunk size
    transcript_bucket_max_seconds: int = 15  # safety flush for slow/quiet teachers


settings = Settings()

