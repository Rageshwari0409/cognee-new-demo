"""
Central configuration module for the episodic memory pipeline.

All environment variables are read here and exposed as typed attributes.
Every other module in the package imports from this module rather than
reading environment variables directly.
"""

import os

from dotenv import load_dotenv

from episodic_memory.exceptions import ConfigurationError

load_dotenv()


def _require(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise ConfigurationError(
            f"Required environment variable '{key}' is not set. "
            f"See .env.example for all required variables."
        )
    return value


def _optional(key: str, default: str) -> str:
    return os.getenv(key, default)


class Settings:
    """
    Typed access to all environment-driven configuration values.

    Instantiate once at application startup and pass the instance
    to components that need it, rather than importing the module-level
    instance directly in library code.
    """

    @property
    def gemini_api_key(self) -> str:
        """Google Gemini API key for all LLM calls."""
        return _require("GEMINI_API_KEY")

    @property
    def llm_model(self) -> str:
        """Gemini model identifier used for all LLM completions."""
        return _optional("LLM_MODEL", "models/gemini-flash-lite-latest")

    @property
    def llm_max_tokens(self) -> int:
        """Maximum token budget for a single LLM completion."""
        return int(_optional("LLM_MAX_TOKENS", "4096"))

    @property
    def llm_temperature(self) -> float:
        """Sampling temperature — lower is more deterministic."""
        return float(_optional("LLM_TEMPERATURE", "0.2"))

    @property
    def session_min_words(self) -> int:
        """Minimum word count a session must have to pass the signal filter."""
        return int(_optional("SESSION_MIN_WORDS", "15"))

    @property
    def batch1_context_days(self) -> int:
        """Number of past days to load EpisodeRecords for Batch 1 context."""
        return int(_optional("BATCH1_CONTEXT_DAYS", "30"))

    @property
    def batch2_processing_days(self) -> int:
        """Number of past days to load EpisodeRecords for Batch 2 processing."""
        return int(_optional("BATCH2_PROCESSING_DAYS", "7"))

    @property
    def llm_max_retries(self) -> int:
        """Maximum number of retry attempts on transient LLM failures."""
        return int(_optional("LLM_MAX_RETRIES", "3"))

    @property
    def llm_retry_base_delay(self) -> float:
        """Base delay in seconds for exponential backoff between LLM retries."""
        return float(_optional("LLM_RETRY_BASE_DELAY", "1.0"))

    @property
    def chroma_persist_dir(self) -> str:
        """Directory where ChromaDB persists its vector index on disk."""
        return _optional("CHROMA_PERSIST_DIR", ".chroma")

    @property
    def embedding_model(self) -> str:
        """Gemini embedding model used for semantic indexing."""
        return _optional("EMBEDDING_MODEL", "models/gemini-embedding-001")


settings = Settings()
