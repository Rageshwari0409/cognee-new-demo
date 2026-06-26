"""
Domain-specific exceptions for the episodic memory pipeline.

All modules in this package raise these exceptions rather than generic
built-in exceptions so callers can handle failures precisely.
"""


class EpisodicMemoryError(Exception):
    """Base class for all episodic memory pipeline errors."""


class RecordNotFoundError(EpisodicMemoryError):
    """Raised when an EpisodeRecord cannot be found by its ID."""


class ArcNotFoundError(EpisodicMemoryError):
    """Raised when an EpisodeArc cannot be found by its ID."""


class ReflectionNotFoundError(EpisodicMemoryError):
    """Raised when a Reflection cannot be found by its ID."""


class SessionNotFoundError(EpisodicMemoryError):
    """Raised when a Session cannot be found by its ID."""


class StorageError(EpisodicMemoryError):
    """Raised when the storage backend encounters an unrecoverable error."""


class LLMError(EpisodicMemoryError):
    """Raised when the LLM call fails after all retries are exhausted."""


class LLMResponseParseError(EpisodicMemoryError):
    """Raised when the LLM returns a response that cannot be parsed into the expected schema."""


class PromptNotFoundError(EpisodicMemoryError):
    """Raised when a requested prompt file does not exist in the prompts directory."""


class SourceQuoteVerificationError(EpisodicMemoryError):
    """Raised when source quote verification fails at the pipeline level (not per-record)."""


class ConfigurationError(EpisodicMemoryError):
    """Raised when a required environment variable or configuration value is missing."""
