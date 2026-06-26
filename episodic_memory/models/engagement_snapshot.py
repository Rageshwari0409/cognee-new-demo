"""
EngagementSnapshot model — the user's current coaching focus and activity state.

The snapshot is computed by pure rule-based logic applied to recent episode
counts. No LLM call is required. It is overwritten after every Batch 1 run
and always injected first into the coaching prompt.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from episodic_memory.models.episode_types import DominantFocus, EpisodeType


class EngagementSnapshot(BaseModel):
    """
    A lightweight summary of the user's recent engagement and primary focus.

    Provides the coach with an immediate signal about how to approach the
    current session before any episode content is considered. Replaces
    any previous snapshot for the same user on write.
    """

    user_id: str
    computed_at: datetime = Field(default_factory=datetime.utcnow)
    window_days: int
    episode_type_counts: dict[EpisodeType, int]
    dominant_focus: DominantFocus
    activity_level: Literal["active", "reduced", "inactive"]
    coach_signal: str
