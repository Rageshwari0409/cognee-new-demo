"""
Memory API — user-facing operations for episodic memory data.

Provides view, edit, delete (deactivate), and reinstate operations for
EpisodeRecords, EpisodeArcs, and Reflections. All modifications are
non-destructive: deactivation sets active=False rather than deleting.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from episodic_memory.extraction.scoring import (
    score_episode_arc,
    score_episode_record,
    score_reflection,
)
from episodic_memory.models.engagement_snapshot import EngagementSnapshot
from episodic_memory.models.episode_arc import EpisodeArc
from episodic_memory.models.episode_record import EpisodeRecord
from episodic_memory.models.reflection import Reflection
from episodic_memory.storage.base import EpisodeStore

logger = logging.getLogger(__name__)


@dataclass
class ScoredRecord:
    """An EpisodeRecord paired with its current retrieval-time score."""

    record: EpisodeRecord
    score: float


@dataclass
class ScoredArc:
    """An EpisodeArc paired with its current retrieval-time score."""

    arc: EpisodeArc
    score: float


@dataclass
class ScoredReflection:
    """A Reflection paired with its current retrieval-time score."""

    reflection: Reflection
    score: float


@dataclass
class CoachingContext:
    """
    The full context package assembled for injection into a coaching prompt.

    Contains all episodic memory components ranked and ready for inclusion
    in the system prompt at inference time.
    """

    snapshot: EngagementSnapshot | None
    top_records: list[ScoredRecord]
    open_arcs: list[ScoredArc]
    top_reflections: list[ScoredReflection]


class MemoryAPI:
    """
    Provides read and write access to episodic memory for user-facing features
    and for the coaching inference layer.

    All write operations are non-destructive. Deactivated records and
    reflections are retained in storage and can be reinstated.
    """

    def __init__(self, store: EpisodeStore) -> None:
        """
        Initialises the API with a storage backend.

        Args:
            store: The episode store instance to read from and write to.
        """
        self._store = store

    async def get_records(
        self,
        user_id: str,
        active_only: bool = True,
        since: date | None = None,
        limit: int = 20,
    ) -> list[ScoredRecord]:
        """
        Returns EpisodeRecords for a user, ranked by retrieval-time score.

        Args:
            user_id: The user whose records to retrieve.
            active_only: Excludes superseded records when True.
            since: If provided, only returns records on or after this date.
            limit: Maximum number of records to return.

        Returns:
            List of ScoredRecord objects sorted by score descending.
        """
        records = await self._store.list_records(user_id, active_only=active_only, since=since)
        scored = [ScoredRecord(r, score_episode_record(r)) for r in records]
        scored.sort(key=lambda x: x.score, reverse=True)
        return scored[:limit]

    async def get_arcs(
        self,
        user_id: str,
        state: str | None = None,
    ) -> list[ScoredArc]:
        """
        Returns EpisodeArcs for a user, ranked by retrieval-time score.

        Open arcs always score 1.0 and appear first.

        Args:
            user_id: The user whose arcs to retrieve.
            state: Optional filter — 'open', 'completed', or 'abandoned'.

        Returns:
            List of ScoredArc objects sorted by score descending.
        """
        arcs = await self._store.list_arcs(user_id, state=state)
        scored = [ScoredArc(a, score_episode_arc(a)) for a in arcs]
        scored.sort(key=lambda x: x.score, reverse=True)
        return scored

    async def get_reflections(
        self,
        user_id: str,
        active_only: bool = True,
        limit: int = 10,
    ) -> list[ScoredReflection]:
        """
        Returns Reflections for a user, ranked by retrieval-time score.

        Args:
            user_id: The user whose reflections to retrieve.
            active_only: Excludes deactivated reflections when True.
            limit: Maximum number of reflections to return.

        Returns:
            List of ScoredReflection objects sorted by score descending.
        """
        reflections = await self._store.list_reflections(user_id, active_only=active_only)
        scored = [ScoredReflection(r, score_reflection(r)) for r in reflections]
        scored.sort(key=lambda x: x.score, reverse=True)
        return scored[:limit]

    async def get_coaching_context(
        self,
        user_id: str,
        top_records: int = 5,
        top_reflections: int = 3,
    ) -> CoachingContext:
        """
        Assembles the full episodic memory context package for coaching inference.

        Returns all open arcs, top-scored records, top-scored reflections, and
        the engagement snapshot — ready for injection into the coaching prompt.

        Args:
            user_id: The user to build context for.
            top_records: Maximum number of EpisodeRecords to include.
            top_reflections: Maximum number of Reflections to include.

        Returns:
            A CoachingContext with all components ranked and ready to inject.
        """
        snapshot = await self._store.get_snapshot(user_id)
        scored_records = await self.get_records(user_id, active_only=True, limit=top_records)
        scored_arcs = await self.get_arcs(user_id, state="open")
        scored_reflections = await self.get_reflections(user_id, active_only=True, limit=top_reflections)

        return CoachingContext(
            snapshot=snapshot,
            top_records=scored_records,
            open_arcs=scored_arcs,
            top_reflections=scored_reflections,
        )

    async def edit_record(self, record_id: str, coach_note: str | None = None) -> EpisodeRecord:
        """
        Updates the coach_note on an EpisodeRecord.

        This is the only field a user or coach can edit directly.
        All other fields are managed by the pipeline.

        Args:
            record_id: ID of the record to edit.
            coach_note: New coach note text. If None, the existing note is kept.

        Returns:
            The updated EpisodeRecord.

        Raises:
            RecordNotFoundError: If no record with that ID exists.
        """
        record = await self._store.get_record(record_id)
        if coach_note is not None:
            record.coach_note = coach_note
        record.user_verified = True
        await self._store.update_record(record)
        logger.info("Record %s edited and marked user_verified.", record_id)
        return record

    async def deactivate_record(self, record_id: str) -> EpisodeRecord:
        """
        Deactivates an EpisodeRecord so it is excluded from future prompts.

        The record is not deleted — it remains in storage and can be reinstated.
        Any Reflections citing this record are flagged for re-evaluation.

        Args:
            record_id: ID of the record to deactivate.

        Returns:
            The deactivated EpisodeRecord.

        Raises:
            RecordNotFoundError: If no record with that ID exists.
        """
        record = await self._store.get_record(record_id)
        record.active = False
        await self._store.update_record(record)
        logger.info("Record %s deactivated.", record_id)
        return record

    async def reinstate_record(self, record_id: str) -> EpisodeRecord:
        """
        Reinstates a previously deactivated EpisodeRecord.

        Args:
            record_id: ID of the record to reinstate.

        Returns:
            The reinstated EpisodeRecord with active=True.

        Raises:
            RecordNotFoundError: If no record with that ID exists.
        """
        record = await self._store.get_record(record_id)
        record.active = True
        record.superseded_by = None
        record.superseded_at = None
        await self._store.update_record(record)
        logger.info("Record %s reinstated.", record_id)
        return record

    async def deactivate_reflection(self, reflection_id: str) -> Reflection:
        """
        Deactivates a Reflection so it is excluded from coaching prompts.

        Args:
            reflection_id: ID of the reflection to deactivate.

        Returns:
            The deactivated Reflection.

        Raises:
            ReflectionNotFoundError: If no reflection with that ID exists.
        """
        reflection = await self._store.get_reflection(reflection_id)
        reflection.active = False
        await self._store.save_reflection(reflection)
        logger.info("Reflection %s deactivated.", reflection_id)
        return reflection

    async def reinstate_reflection(self, reflection_id: str) -> Reflection:
        """
        Reinstates a previously deactivated Reflection.

        Args:
            reflection_id: ID of the reflection to reinstate.

        Returns:
            The reinstated Reflection with active=True.

        Raises:
            ReflectionNotFoundError: If no reflection with that ID exists.
        """
        reflection = await self._store.get_reflection(reflection_id)
        reflection.active = True
        await self._store.save_reflection(reflection)
        logger.info("Reflection %s reinstated.", reflection_id)
        return reflection

    async def format_coaching_prompt_block(self, user_id: str) -> str:
        """
        Formats the full episodic memory context as a block for injection
        into the AI assistant's system prompt.

        The block is structured so that the AI reads the most time-sensitive
        signal first (current engagement state), then the ongoing story context
        (arcs), then the persistent behavioral rules (reflections), then the
        specific recent events (episode records).

        This block is for the AI's internal context only — the user does not see it.

        Args:
            user_id: The user to build the prompt block for.

        Returns:
            A formatted string ready to embed in the AI system prompt.
        """
        context = await self.get_coaching_context(user_id)
        lines = []

        lines.append("=== YOUR MEMORY OF THIS USER ===")
        lines.append(
            "The following is your episodic memory for this user. "
            "Use it to personalise your responses. "
            "The user does not see this block."
        )
        lines.append("")

        if context.snapshot:
            lines.append("[CURRENT SESSION SIGNAL — read this first]")
            lines.append(context.snapshot.coach_signal)
            lines.append("")

        if context.open_arcs:
            lines.append("[ONGOING STORIES — apply these throughout the conversation]")
            for sa in context.open_arcs:
                lines.append(f"Story: {sa.arc.title}")
                lines.append(f"  What happened: {sa.arc.summary}")
                lines.append(f"  Your instruction: {sa.arc.coach_note}")
            lines.append("")

        if context.top_reflections:
            lines.append("[BEHAVIORAL PATTERNS — apply these as standing rules]")
            for sr in context.top_reflections:
                lines.append(f"Pattern ({sr.reflection.confidence} confidence): {sr.reflection.observation}")
                lines.append(f"  Your rule: {sr.reflection.coach_action}")
            lines.append("")

        if context.top_records:
            lines.append("[RECENT EVENTS — use for continuity and follow-up]")
            for sr in context.top_records:
                r = sr.record
                lines.append(
                    f"- [{r.occurred_on}] {r.episode_type.value}: {r.outcome}"
                )
                lines.append(f"  Your note: {r.coach_note}")
            lines.append("")

        lines.append("=== END OF MEMORY BLOCK ===")

        return "\n".join(lines)
