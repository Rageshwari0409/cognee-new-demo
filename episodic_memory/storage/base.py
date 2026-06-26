"""
Abstract storage interface for the episodic memory pipeline.

All concrete storage implementations must fulfill this contract.
High-level pipeline components depend only on this interface, keeping
them decoupled from any specific database or persistence technology.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

from episodic_memory.models.engagement_snapshot import EngagementSnapshot
from episodic_memory.models.episode_arc import EpisodeArc
from episodic_memory.models.episode_record import EpisodeRecord
from episodic_memory.models.reflection import Reflection
from episodic_memory.models.session import Session


class EpisodeStore(ABC):
    """
    Defines the complete persistence contract for episodic memory data.

    Concrete implementations provide storage for Sessions, EpisodeRecords,
    EpisodeArcs, Reflections, and the EngagementSnapshot. All methods are
    async to support non-blocking I/O in the pipeline.
    """

    @abstractmethod
    async def save_session(self, session: Session) -> None:
        """
        Persists a session.

        Args:
            session: The session to store.
        """

    @abstractmethod
    async def get_session(self, session_id: str) -> Session:
        """
        Retrieves a session by ID.

        Args:
            session_id: Unique identifier of the session.

        Returns:
            The matching Session.

        Raises:
            SessionNotFoundError: If no session with that ID exists.
        """

    @abstractmethod
    async def list_users_with_unprocessed_sessions(self) -> list[str]:
        """
        Returns the distinct user IDs that have at least one unprocessed session.

        Used by the Batch 1 scheduler to discover which users need processing
        without requiring the caller to know the full user list.

        Returns:
            List of user ID strings, in no guaranteed order.
        """

    @abstractmethod
    async def list_all_user_ids(self) -> list[str]:
        """
        Returns the distinct user IDs that have any data in the store.

        Used by the Batch 2 scheduler to run weekly insights for every user
        that has episodic history, regardless of whether new sessions exist.

        Returns:
            List of user ID strings, in no guaranteed order.
        """

    @abstractmethod
    async def list_unprocessed_sessions(self, user_id: str) -> list[Session]:
        """
        Returns all sessions for the user that have not yet been processed by Batch 1.

        Args:
            user_id: The user whose unprocessed sessions to retrieve.

        Returns:
            List of Session objects with processed=False, ordered by occurred_on ascending.
        """

    @abstractmethod
    async def mark_sessions_processed(self, session_ids: list[str]) -> None:
        """
        Marks the given sessions as processed so they are excluded from future Batch 1 runs.

        Args:
            session_ids: IDs of sessions to mark as processed.
        """

    @abstractmethod
    async def save_record(self, record: EpisodeRecord) -> None:
        """
        Persists an EpisodeRecord.

        Args:
            record: The record to store.
        """

    @abstractmethod
    async def get_record(self, record_id: str) -> EpisodeRecord:
        """
        Retrieves an EpisodeRecord by ID.

        Args:
            record_id: Unique identifier of the record.

        Returns:
            The matching EpisodeRecord.

        Raises:
            RecordNotFoundError: If no record with that ID exists.
        """

    @abstractmethod
    async def list_records(
        self,
        user_id: str,
        active_only: bool = True,
        since: date | None = None,
    ) -> list[EpisodeRecord]:
        """
        Returns EpisodeRecords for a user, optionally filtered by active status and date.

        Args:
            user_id: The user whose records to retrieve.
            active_only: If True, excludes superseded records.
            since: If provided, only returns records with occurred_on >= since.

        Returns:
            List of EpisodeRecord objects ordered by occurred_on descending.
        """

    @abstractmethod
    async def supersede_record(self, old_id: str, new_id: str) -> None:
        """
        Archives the old record and links it to its replacement.

        Sets old record's active=False, superseded_by=new_id, superseded_at=now.
        Marks all Reflections that cite old_id with needs_reeval=True.

        Args:
            old_id: ID of the record being superseded.
            new_id: ID of the new record that replaces it.

        Raises:
            RecordNotFoundError: If old_id does not exist.
        """

    @abstractmethod
    async def update_record(self, record: EpisodeRecord) -> None:
        """
        Updates an existing record in-place (used for merge/extension, not supersession).

        Args:
            record: The updated record. Must already exist in storage.

        Raises:
            RecordNotFoundError: If no record with record.id exists.
        """

    @abstractmethod
    async def save_arc(self, arc: EpisodeArc) -> None:
        """
        Persists an EpisodeArc. Creates if new, overwrites if ID exists.

        Args:
            arc: The arc to store.
        """

    @abstractmethod
    async def get_arc(self, arc_id: str) -> EpisodeArc:
        """
        Retrieves an EpisodeArc by ID.

        Args:
            arc_id: Unique identifier of the arc.

        Returns:
            The matching EpisodeArc.

        Raises:
            ArcNotFoundError: If no arc with that ID exists.
        """

    @abstractmethod
    async def list_arcs(
        self,
        user_id: str,
        state: str | None = None,
    ) -> list[EpisodeArc]:
        """
        Returns EpisodeArcs for a user, optionally filtered by state.

        Args:
            user_id: The user whose arcs to retrieve.
            state: If provided, only returns arcs matching this state
                   (e.g. 'open', 'completed', 'abandoned').

        Returns:
            List of EpisodeArc objects ordered by opened_on descending.
        """

    @abstractmethod
    async def save_reflection(self, reflection: Reflection) -> None:
        """
        Persists a Reflection. Creates if new, overwrites if ID exists.

        Args:
            reflection: The reflection to store.
        """

    @abstractmethod
    async def get_reflection(self, reflection_id: str) -> Reflection:
        """
        Retrieves a Reflection by ID.

        Args:
            reflection_id: Unique identifier of the reflection.

        Returns:
            The matching Reflection.

        Raises:
            ReflectionNotFoundError: If no reflection with that ID exists.
        """

    @abstractmethod
    async def list_reflections(
        self,
        user_id: str,
        active_only: bool = True,
    ) -> list[Reflection]:
        """
        Returns Reflections for a user.

        Args:
            user_id: The user whose reflections to retrieve.
            active_only: If True, excludes deactivated reflections.

        Returns:
            List of Reflection objects ordered by last_confirmed descending.
        """

    @abstractmethod
    async def save_snapshot(self, snapshot: EngagementSnapshot) -> None:
        """
        Saves the EngagementSnapshot for a user, replacing any previous snapshot.

        Args:
            snapshot: The snapshot to store.
        """

    @abstractmethod
    async def get_snapshot(self, user_id: str) -> EngagementSnapshot | None:
        """
        Returns the most recent EngagementSnapshot for a user.

        Args:
            user_id: The user whose snapshot to retrieve.

        Returns:
            The EngagementSnapshot, or None if no snapshot exists yet.
        """
