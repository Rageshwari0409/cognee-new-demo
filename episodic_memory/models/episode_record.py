"""
EpisodeRecord model — the primary unit of episodic memory.

Each record captures a single meaningful fitness event synthesised from
one or more coaching sessions. Records are written by Batch 1 and read
by Batch 2, the memory API, and the coaching inference layer.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from episodic_memory.models.episode_types import EpisodeType


class EpisodeRecord(BaseModel):
    """
    A structured memory of a single meaningful fitness event.

    Captures not just what happened but the situation surrounding it,
    what the user intended, and what the AI should do in the next conversation
    as a result. Source quotes from the original conversations are stored verbatim
    so the record can be audited and its provenance verified.

    Records are never physically deleted. When superseded by a contradicting
    record, they are archived with active=False and a superseded_by reference.
    """

    id: str
    user_id: str
    episode_type: EpisodeType
    situation: str
    intent: str
    outcome: str
    significance: Literal["one_off", "notable", "turning_point"]
    coach_note: str
    occurred_on: date
    source_session_ids: list[str]
    source_quotes: list[str]
    challenge_subtype: Literal["injury", "fatigue", "motivation", "scheduling", "other"] | None = None
    arc_id: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    user_verified: bool = False
    active: bool = True
    superseded_by: str | None = None
    superseded_at: datetime | None = None
    pipeline_run_id: str | None = None
