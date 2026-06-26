"""
EpisodeArc model — a higher-level story spanning multiple EpisodeRecords.

An arc represents a chapter in the user's fitness journey: a training
program, an injury recovery, a goal pursuit, or a behavior change. Arcs
are detected and maintained by Batch 2 and always injected into the
coaching prompt so the coach has current story context.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from episodic_memory.models.episode_types import ArcType


class EpisodeArc(BaseModel):
    """
    A multi-episode narrative representing an ongoing chapter in the user's journey.

    Open arcs are always injected into the coaching context without decay.
    Concluded arcs decay slowly over weeks. Abandoned arcs are retained
    for historical reference but not injected into prompts.
    """

    id: str
    user_id: str
    arc_type: ArcType
    title: str
    summary: str
    state: Literal["open", "completed", "abandoned"]
    opened_on: date
    concluded_on: date | None = None
    source_episode_ids: list[str]
    source_episode_summaries: list[str] = Field(default_factory=list)
    coach_note: str
    last_updated: datetime = Field(default_factory=datetime.utcnow)
