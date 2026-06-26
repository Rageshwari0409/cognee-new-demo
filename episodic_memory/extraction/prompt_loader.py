"""
Prompt loader for the episodic memory pipeline.

Loads prompt templates from .txt files in the prompts directory and
performs variable substitution before returning the final prompt string.
All LLM-facing prompt text lives in those files — never in Python source.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from episodic_memory.exceptions import PromptNotFoundError

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"
_VARIABLE_PATTERN = re.compile(r"\{\{(\w+)\}\}")


class PromptLoader:
    """
    Loads and renders prompt templates from the prompts/ directory.

    Templates use {{variable_name}} placeholders. The render method
    substitutes all provided variables and raises if any placeholder
    in the template was not supplied.
    """

    def __init__(self, prompts_dir: Path | None = None) -> None:
        """
        Initialises the loader with a directory of .txt prompt files.

        Args:
            prompts_dir: Path to the prompts directory. Defaults to the
                         package-level prompts/ folder.
        """
        self._dir = prompts_dir or _PROMPTS_DIR
        self._cache: dict[str, str] = {}

    def render(self, prompt_name: str, variables: dict[str, str]) -> str:
        """
        Loads the named prompt template and substitutes all variables.

        Args:
            prompt_name: Filename without extension (e.g. 'batch1_episode_builder').
            variables: Mapping from placeholder name to substitution value.

        Returns:
            The rendered prompt string ready for the LLM.

        Raises:
            PromptNotFoundError: If the .txt file does not exist.
            ValueError: If the template contains placeholders not supplied in variables.
        """
        template = self._load(prompt_name)
        return self._substitute(template, variables, prompt_name)

    def _load(self, prompt_name: str) -> str:
        if prompt_name in self._cache:
            return self._cache[prompt_name]

        path = self._dir / f"{prompt_name}.txt"
        if not path.exists():
            raise PromptNotFoundError(
                f"Prompt '{prompt_name}' not found. Expected file: {path}"
            )

        text = path.read_text(encoding="utf-8")
        self._cache[prompt_name] = text
        logger.debug("Loaded prompt '%s' from %s", prompt_name, path)
        return text

    def _substitute(
        self, template: str, variables: dict[str, str], prompt_name: str
    ) -> str:
        required = set(_VARIABLE_PATTERN.findall(template))
        missing = required - variables.keys()
        if missing:
            raise ValueError(
                f"Prompt '{prompt_name}' requires variables {missing} "
                f"but they were not provided."
            )

        result = template
        for key, value in variables.items():
            result = result.replace(f"{{{{{key}}}}}", value)
        return result
