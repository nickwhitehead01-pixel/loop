from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Database
    database_url: str = "postgresql+asyncpg://gemma_user:gemma_password@localhost:5432/gemma_education"

    # Ollama
    ollama_base_url: str = "http://localhost:11434"
    ollama_model_pupil: str = "gemma4:e4b"
    ollama_model_teacher: str = "gemma4:e4b"
    ollama_embed_model: str = "nomic-embed-text"

    # App
    debug: bool = False
    upload_dir: str = "/app/uploads"

    # Whisper (speech-to-text)
    whisper_model_size: str = "small"

    # Conversation memory window (last N messages loaded per session)
    memory_window: int = 10

    # Semantic cache — cosine similarity threshold (0–1, higher = stricter match)
    semantic_cache_threshold: float = 0.92


settings = Settings()

