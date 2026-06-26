"""
Intermediate Pydantic models representing LLM output before storage.

These types are used only within the extraction layer to parse and validate
what the language model returns. They are converted to domain models before
being passed to the storage layer.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, field_validator

from episodic_memory.models.episode_types import ArcType, EpisodeType

_MIN_OUTCOME_CHARS = 30
_MIN_COACH_NOTE_CHARS = 40
_MIN_QUOTE_CHARS = 8
_MIN_OBSERVATION_CHARS = 40
_MIN_COACH_ACTION_CHARS = 40
_MIN_REFLECTION_EPISODES = 3


class EpisodeRecordInput(BaseModel):
    """
    The shape of a single episode as returned by the Batch 1 LLM call.

    Validated before source quote verification and before ID assignment.
    Pydantic validators enforce minimum field quality so vague or
    single-word LLM outputs are rejected at parse time.
    """

    episode_type: EpisodeType
    situation: str
    intent: str
    outcome: str
    significance: Literal["one_off", "notable", "turning_point"]
    coach_note: str
    occurred_on: date
    source_quotes: list[str]
    challenge_subtype: Literal["injury", "fatigue", "motivation", "scheduling", "other"] | None = None
    arc_id: str | None = None

    @field_validator("outcome")
    @classmethod
    def outcome_must_be_substantive(cls, v: str) -> str:
        if len(v.strip()) < _MIN_OUTCOME_CHARS:
            raise ValueError(
                f"outcome must be at least {_MIN_OUTCOME_CHARS} characters — "
                f"received {len(v.strip())} chars: {v!r}"
            )
        return v

    @field_validator("coach_note")
    @classmethod
    def coach_note_must_be_actionable(cls, v: str) -> str:
        if len(v.strip()) < _MIN_COACH_NOTE_CHARS:
            raise ValueError(
                f"coach_note must be at least {_MIN_COACH_NOTE_CHARS} characters — "
                f"received {len(v.strip())} chars: {v!r}"
            )
        return v

    @field_validator("source_quotes")
    @classmethod
    def quotes_must_be_present_and_non_trivial(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("source_quotes must contain at least one quote.")
        filtered = [q for q in v if len(q.strip()) >= _MIN_QUOTE_CHARS]
        if not filtered:
            raise ValueError(
                f"All source_quotes are too short (minimum {_MIN_QUOTE_CHARS} chars each). "
                f"Received: {v}"
            )
        return filtered


class ContradictionDecision(BaseModel):
    """
    The output of the contradiction_resolution prompt for a pair of records.

    Guides whether the old record should be superseded, the records merged,
    or the new information treated as an unrelated event.
    """

    decision: Literal["contradiction", "extension", "new_event"]
    reasoning: str
    action: Literal["supersede", "merge", "create"]


class ArcAction(BaseModel):
    """
    A single arc instruction returned by the Batch 2 arc detection LLM call.

    May describe advancing an existing arc, concluding it, creating a new one,
    or marking one as abandoned.
    """

    action: Literal["advance", "conclude", "create", "abandon"]
    arc_id: str | None = None
    arc_type: ArcType | None = None
    title: str | None = None
    updated_summary: str
    updated_coach_note: str
    state: Literal["open", "completed", "abandoned"]
    concluded_on: date | None = None
    opened_on: date | None = None
    source_episode_ids: list[str] | None = None


class ReflectionInput(BaseModel):
    """
    The shape of a single reflection as returned by the Batch 2 reflection LLM call.

    Validated before ID assignment and before storage. Validators ensure that
    only pattern-level insights (3+ episodes) with substantive observations are
    accepted — single-event summaries are rejected at parse time.
    """

    pattern_type: Literal[
        "adherence", "recovery", "motivation", "risk", "progress", "coaching_style"
    ]
    observation: str
    coach_action: str
    supporting_episode_ids: list[str]
    confidence: Literal["low", "medium", "high"]
    episode_count: int

    @field_validator("observation")
    @classmethod
    def observation_must_be_substantive(cls, v: str) -> str:
        if len(v.strip()) < _MIN_OBSERVATION_CHARS:
            raise ValueError(
                f"observation must be at least {_MIN_OBSERVATION_CHARS} characters. "
                f"Received {len(v.strip())} chars: {v!r}"
            )
        return v

    @field_validator("coach_action")
    @classmethod
    def coach_action_must_be_actionable(cls, v: str) -> str:
        if len(v.strip()) < _MIN_COACH_ACTION_CHARS:
            raise ValueError(
                f"coach_action must be at least {_MIN_COACH_ACTION_CHARS} characters. "
                f"Received {len(v.strip())} chars: {v!r}"
            )
        return v

    @field_validator("episode_count")
    @classmethod
    def episode_count_meets_pattern_threshold(cls, v: int) -> int:
        if v < _MIN_REFLECTION_EPISODES:
            raise ValueError(
                f"episode_count must be at least {_MIN_REFLECTION_EPISODES} — "
                f"a single or dual event is not a pattern. Received: {v}"
            )
        return v

    @field_validator("supporting_episode_ids")
    @classmethod
    def supporting_ids_must_be_present(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("supporting_episode_ids must contain at least one ID.")
        return v
