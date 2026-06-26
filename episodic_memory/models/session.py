"""
Data models representing raw AI coaching conversations before episode extraction.

A Session is a sequence of ConversationTurns between the AI assistant and the
user on a given date. Sessions are the primary input to Batch 1. The AI
assistant's turns are stored with role="assistant" to match standard LLM
message format.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class ConversationTurn(BaseModel):
    """
    A single message exchanged between the AI assistant and the user.

    Args:
        role: Who sent the message — 'assistant' for the AI, 'user' for the human.
        content: The text content of the message.
        timestamp: When the message was sent. Optional for imported history.
    """

    role: Literal["assistant", "user"]
    content: str
    timestamp: datetime | None = None


class Session(BaseModel):
    """
    A coaching conversation session consisting of one or more turns.

    Tracks whether the session has been processed by Batch 1 so the
    pipeline can query only unprocessed sessions on each run.
    """

    id: str
    user_id: str
    turns: list[ConversationTurn]
    occurred_on: date
    processed: bool = False
    processed_at: datetime | None = None

    def user_text(self) -> str:
        """
        Returns all user-side messages concatenated as a single string.

        Useful for word-count filtering and source quote verification,
        where only user statements carry evidentiary weight.

        Returns:
            A newline-joined string of all user turn content.
        """
        return "\n".join(t.content for t in self.turns if t.role == "user")

    def word_count(self) -> int:
        """
        Returns the total word count across all turns in the session.

        Returns:
            Integer count of whitespace-delimited words in the full session.
        """
        full_text = " ".join(t.content for t in self.turns)
        return len(full_text.split())

    def as_formatted_text(self) -> str:
        """
        Formats the session as a readable assistant/user transcript.

        Used to construct the conversations block injected into LLM prompts.

        Returns:
            A multi-line string with each turn labeled by role.
        """
        lines = []
        for turn in self.turns:
            label = "Assistant" if turn.role == "assistant" else "User"
            lines.append(f"{label}: {turn.content}")
        return "\n".join(lines)
