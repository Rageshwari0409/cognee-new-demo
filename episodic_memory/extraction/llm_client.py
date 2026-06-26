"""
Gemini LLM client with retry logic, timeout handling, and usage logging.

All calls to the Google Gemini API flow through this class. Callers never
import google.generativeai directly — they depend on this interface.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass

import google.generativeai as genai
from google.api_core.exceptions import (
    InternalServerError,
    ResourceExhausted,
    ServiceUnavailable,
)

from episodic_memory.config import Settings
from episodic_memory.exceptions import LLMError

logger = logging.getLogger(__name__)


@dataclass
class LLMResponse:
    """
    The result of a single LLM completion call.

    Attributes:
        content: The raw text returned by the model.
        prompt_tokens: Number of tokens in the input prompt.
        completion_tokens: Number of tokens in the model output.
        model: The model identifier that produced the response.
        latency_ms: Wall-clock time in milliseconds for the API call.
    """

    content: str
    prompt_tokens: int
    completion_tokens: int
    model: str
    latency_ms: float


class LLMClient:
    """
    Async Gemini API client with exponential backoff and observability.

    Wraps the google-generativeai SDK to provide consistent retry behaviour,
    latency logging, and error normalisation. Accepts a session_id for log
    correlation across a single pipeline run.
    """

    def __init__(self, settings: Settings) -> None:
        """
        Initialises the client from application settings.

        Configures the Gemini SDK with the API key and creates a reusable
        GenerativeModel instance with the configured generation parameters.

        Args:
            settings: The application settings instance supplying API key,
                      model name, and retry configuration.
        """
        genai.configure(api_key=settings.gemini_api_key)

        model_name = settings.llm_model
        if "/" in model_name and not model_name.startswith("models/") and not model_name.startswith("tunedModels/"):
            parts = model_name.split("/", 1)
            if parts[1].startswith("models/") or parts[1].startswith("tunedModels/"):
                model_name = parts[1]
            else:
                model_name = f"models/{parts[1]}"
        elif not model_name.startswith("models/") and not model_name.startswith("tunedModels/"):
            model_name = f"models/{model_name}"

        self._model = genai.GenerativeModel(
            model_name=model_name,
            generation_config=genai.GenerationConfig(
                max_output_tokens=settings.llm_max_tokens,
                temperature=settings.llm_temperature,
            ),
        )
        self._model_name = model_name
        self._max_retries = settings.llm_max_retries
        self._base_delay = settings.llm_retry_base_delay

    async def complete(self, prompt: str, session_id: str) -> LLMResponse:
        """
        Sends a prompt to Gemini and returns the response.

        Retries on transient API failures (rate limits, server errors) using
        exponential backoff with jitter. Logs model name, token usage, and
        latency at DEBUG level for observability.

        Args:
            prompt: The fully rendered prompt string to send.
            session_id: Unique identifier for the calling pipeline run.
                        Included in all log messages for traceability.

        Returns:
            An LLMResponse containing the text and usage metadata.

        Raises:
            LLMError: If all retry attempts are exhausted or a non-retryable
                      error is encountered.
        """
        last_error: Exception | None = None

        for attempt in range(1, self._max_retries + 1):
            try:
                return await self._call(prompt, session_id, attempt)
            except ResourceExhausted as exc:
                last_error = exc
                delay = self._backoff(attempt)
                logger.warning(
                    "[%s] Gemini rate limit hit on attempt %d/%d. Retrying in %.1fs.",
                    session_id, attempt, self._max_retries, delay,
                )
                await asyncio.sleep(delay)
            except (InternalServerError, ServiceUnavailable) as exc:
                last_error = exc
                delay = self._backoff(attempt)
                logger.warning(
                    "[%s] Gemini server error on attempt %d/%d. Retrying in %.1fs. Error: %s",
                    session_id, attempt, self._max_retries, delay, exc,
                )
                await asyncio.sleep(delay)
            except Exception as exc:
                raise LLMError(
                    f"[{session_id}] Non-retryable Gemini error: {exc}"
                ) from exc

        raise LLMError(
            f"[{session_id}] Gemini call failed after {self._max_retries} attempts. "
            f"Last error: {last_error}"
        ) from last_error

    async def _call(self, prompt: str, session_id: str, attempt: int) -> LLMResponse:
        start = time.perf_counter()

        response = await self._model.generate_content_async(prompt)

        latency_ms = (time.perf_counter() - start) * 1000

        content = response.text if response.parts else ""

        usage = response.usage_metadata
        prompt_tokens = usage.prompt_token_count if usage else 0
        completion_tokens = usage.candidates_token_count if usage else 0

        logger.debug(
            "[%s] attempt=%d model=%s prompt_tokens=%d completion_tokens=%d latency_ms=%.0f",
            session_id, attempt, self._model_name,
            prompt_tokens, completion_tokens, latency_ms,
        )

        if not content:
            raise LLMError(
                f"[{session_id}] Gemini returned an empty response on attempt {attempt}."
            )

        return LLMResponse(
            content=content,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            model=self._model_name,
            latency_ms=latency_ms,
        )

    def _backoff(self, attempt: int) -> float:
        jitter = random.uniform(0, self._base_delay)
        return self._base_delay * (2 ** (attempt - 1)) + jitter
