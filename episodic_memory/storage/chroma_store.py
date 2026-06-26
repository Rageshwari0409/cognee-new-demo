"""
ChromaDB-backed implementation of the EpisodeStore interface.

All episodic memory data lives in a single ChromaDB persistent directory.
Five collections mirror the five entity types:

  episode_records     — semantic collection; documents are embedded with
                        Google gemini-embedding-001 for semantic search.
                        Full JSON is stored in metadata["data"] for hydration.

  sessions            — metadata-only collection (no embeddings).
                        Full JSON stored in metadata["data"].

  episode_arcs        — metadata-only collection.
  reflections         — metadata-only collection.
  engagement_snapshots— metadata-only collection (one document per user).

Structured retrieval (list, filter by date, active status, etc.) uses
Chroma's metadata `where` clauses.  Semantic search uses `collection.query()`
against the episode_records collection only.

Chroma is synchronous.  Every public method wraps its synchronous body in
asyncio.to_thread() so it does not block the event loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime
from typing import Any

try:
    import chromadb
    try:
        from chromadb.api.types import Documents, Embeddings, EmbeddingFunction
    except ImportError:
        from typing import Sequence
        Documents = Sequence[str]  # type: ignore
        Embeddings = Sequence[Sequence[float]]  # type: ignore
        EmbeddingFunction = object  # type: ignore
except ImportError as exc:
    raise ImportError(
        "chromadb is required. Install it with: pip install chromadb"
    ) from exc


class GoogleGenerativeAiEmbeddingFunction(EmbeddingFunction):
    """
    Custom embedding function that configures google-generativeai safely
    without triggering the ClientOptions ValueError.
    """

    def __init__(
        self,
        api_key: str,
        model_name: str = "models/gemini-embedding-001",
        task_type: str = "RETRIEVAL_DOCUMENT",
    ) -> None:
        try:
            import google.generativeai as genai
        except ImportError as exc:
            raise ImportError(
                "google-generativeai is required. Install it with: pip install google-generativeai"
            ) from exc

        self.api_key = api_key
        if "/" in model_name and not model_name.startswith("models/") and not model_name.startswith("tunedModels/"):
            parts = model_name.split("/", 1)
            if parts[1].startswith("models/") or parts[1].startswith("tunedModels/"):
                model_name = parts[1]
            else:
                model_name = f"models/{parts[1]}"
        elif not model_name.startswith("models/") and not model_name.startswith("tunedModels/"):
            model_name = f"models/{model_name}"
        self.model_name = model_name
        self.task_type = task_type

        # Configure the genai client directly without the headers key inside client_options,
        # avoiding the ValueError in google.api_core.client_options.from_dict.
        genai.configure(api_key=self.api_key)
        self._genai = genai

    def __call__(self, input: Documents) -> Embeddings:
        if not input:
            return []
        if not all(isinstance(item, str) for item in input):
            raise ValueError(
                "Google Generative AI only supports text documents"
            )

        try:
            response = self._genai.embed_content(
                model=self.model_name,
                content=list(input),
                task_type=self.task_type,
            )
            # Standard return is dictionary containing 'embedding'
            if isinstance(response, dict):
                embeds = response.get("embedding", [])
            else:
                embeds = getattr(response, "embedding", [])

            # Check if embeds is a single 1D list of numbers instead of 2D list of lists.
            # If so, wrap it in a list so it's a 2D list.
            if embeds and not isinstance(embeds[0], (list, tuple)):
                return [embeds]
            return embeds
        except Exception as exc:
            logger.error("Failed to generate embeddings: %s", exc)
            raise


from episodic_memory.exceptions import (
    ArcNotFoundError,
    RecordNotFoundError,
    ReflectionNotFoundError,
    SessionNotFoundError,
    StorageError,
)
from episodic_memory.models.engagement_snapshot import EngagementSnapshot
from episodic_memory.models.episode_arc import EpisodeArc
from episodic_memory.models.episode_record import EpisodeRecord
from episodic_memory.models.reflection import Reflection
from episodic_memory.models.session import Session
from episodic_memory.storage.base import EpisodeStore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Collection names
# ---------------------------------------------------------------------------

_COL_RECORDS = "episode_records"
_COL_SESSIONS = "sessions"
_COL_ARCS = "episode_arcs"
_COL_REFLECTIONS = "reflections"
_COL_SNAPSHOTS = "engagement_snapshots"


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def _date_to_int(d: date) -> int:
    """Encodes a date as YYYYMMDD integer for Chroma numeric range queries."""
    return int(d.strftime("%Y%m%d"))


def _int_to_date(n: int) -> date:
    s = str(n)
    return date(int(s[:4]), int(s[4:6]), int(s[6:8]))


# ---------------------------------------------------------------------------
# ChromaStore
# ---------------------------------------------------------------------------

class ChromaStore(EpisodeStore):
    """
    Persists all episodic memory data to a local ChromaDB directory.

    EpisodeRecords are stored with Google embeddings so they can be retrieved
    both by structured metadata filters (pipeline use) and by semantic
    similarity (user-facing search).

    All other entities are stored as metadata-only documents — Chroma acts
    as a key/value + metadata filter store for those collections.

    Call initialise() before use (it is a no-op here but kept for API
    compatibility with the rest of the codebase).
    """

    def __init__(
        self,
        persist_dir: str,
        api_key: str,
        embedding_model: str = "models/gemini-embedding-001",
    ) -> None:
        """
        Initialises the Chroma client and creates all collections.

        Args:
            persist_dir: Directory on disk where Chroma persists data.
            api_key: Google Generative AI API key for episode embedding.
            embedding_model: Gemini embedding model name.
        """
        self._client = chromadb.PersistentClient(path=persist_dir)
        self._embed_fn = GoogleGenerativeAiEmbeddingFunction(
            api_key=api_key,
            model_name=embedding_model,
        )

        # Semantic collection — episodes get Google embeddings
        self._records = self._client.get_or_create_collection(
            name=_COL_RECORDS,
            embedding_function=self._embed_fn,
            metadata={"hnsw:space": "cosine"},
        )

        # Metadata-only collections — no embedding function
        self._sessions = self._client.get_or_create_collection(
            name=_COL_SESSIONS,
            embedding_function=None,
        )
        self._arcs = self._client.get_or_create_collection(
            name=_COL_ARCS,
            embedding_function=None,
        )
        self._reflections = self._client.get_or_create_collection(
            name=_COL_REFLECTIONS,
            embedding_function=None,
        )
        self._snapshots = self._client.get_or_create_collection(
            name=_COL_SNAPSHOTS,
            embedding_function=None,
        )
        logger.info("ChromaStore ready at '%s'", persist_dir)

    async def initialise(self) -> None:
        """No-op — collections are created in __init__. Kept for API compatibility."""

    # -----------------------------------------------------------------------
    # Sessions
    # -----------------------------------------------------------------------

    async def save_session(self, session: Session) -> None:
        def _sync() -> None:
            self._sessions.upsert(
                ids=[session.id],
                embeddings=[[0.0]],
                metadatas=[{
                    "data": session.model_dump_json(),
                    "user_id": session.user_id,
                    "occurred_on_ts": _date_to_int(session.occurred_on),
                    "processed": 1 if session.processed else 0,
                    "processed_at": session.processed_at.isoformat() if session.processed_at else "",
                }],
            )
        try:
            await asyncio.to_thread(_sync)
        except Exception as exc:
            raise StorageError(f"Failed to save session {session.id}: {exc}") from exc

    async def get_session(self, session_id: str) -> Session:
        def _sync() -> Session:
            result = self._sessions.get(ids=[session_id], include=["metadatas"])
            if not result["ids"]:
                raise SessionNotFoundError(f"Session '{session_id}' not found.")
            return Session.model_validate_json(result["metadatas"][0]["data"])
        try:
            return await asyncio.to_thread(_sync)
        except SessionNotFoundError:
            raise
        except Exception as exc:
            raise StorageError(f"Failed to read session {session_id}: {exc}") from exc

    async def list_users_with_unprocessed_sessions(self) -> list[str]:
        def _sync() -> list[str]:
            result = self._sessions.get(
                where={"processed": {"$eq": 0}},
                include=["metadatas"],
            )
            seen: set[str] = set()
            out: list[str] = []
            for meta in result["metadatas"]:
                uid = meta["user_id"]
                if uid not in seen:
                    seen.add(uid)
                    out.append(uid)
            return out
        try:
            return await asyncio.to_thread(_sync)
        except Exception as exc:
            raise StorageError(f"Failed to list users with unprocessed sessions: {exc}") from exc

    async def list_all_user_ids(self) -> list[str]:
        def _sync() -> list[str]:
            result = self._records.get(include=["metadatas"])
            seen: set[str] = set()
            out: list[str] = []
            for meta in result["metadatas"]:
                uid = meta["user_id"]
                if uid not in seen:
                    seen.add(uid)
                    out.append(uid)
            return out
        try:
            return await asyncio.to_thread(_sync)
        except Exception as exc:
            raise StorageError(f"Failed to list all user IDs: {exc}") from exc

    async def list_unprocessed_sessions(self, user_id: str) -> list[Session]:
        def _sync() -> list[Session]:
            result = self._sessions.get(
                where={"$and": [
                    {"user_id": {"$eq": user_id}},
                    {"processed": {"$eq": 0}},
                ]},
                include=["metadatas"],
            )
            sessions = [Session.model_validate_json(m["data"]) for m in result["metadatas"]]
            return sorted(sessions, key=lambda s: s.occurred_on)
        try:
            return await asyncio.to_thread(_sync)
        except Exception as exc:
            raise StorageError(f"Failed to list unprocessed sessions for user {user_id}: {exc}") from exc

    async def mark_sessions_processed(self, session_ids: list[str]) -> None:
        if not session_ids:
            return

        def _sync() -> None:
            result = self._sessions.get(ids=session_ids, include=["metadatas"])
            if not result["ids"]:
                return
            now_iso = datetime.utcnow().isoformat()
            new_metadatas = []
            for meta in result["metadatas"]:
                session = Session.model_validate_json(meta["data"])
                session.processed = True
                session.processed_at = datetime.utcnow()
                meta = dict(meta)
                meta["processed"] = 1
                meta["processed_at"] = now_iso
                meta["data"] = session.model_dump_json()
                new_metadatas.append(meta)
            self._sessions.upsert(
                ids=result["ids"],
                embeddings=[[0.0]] * len(result["ids"]),
                metadatas=new_metadatas,
            )

        try:
            await asyncio.to_thread(_sync)
        except Exception as exc:
            raise StorageError(f"Failed to mark sessions processed: {exc}") from exc

    # -----------------------------------------------------------------------
    # EpisodeRecords
    # -----------------------------------------------------------------------

    async def save_record(self, record: EpisodeRecord) -> None:
        def _sync() -> None:
            self._records.upsert(
                ids=[record.id],
                documents=[_record_document_text(record)],
                metadatas=[_record_metadata(record)],
            )
        try:
            await asyncio.to_thread(_sync)
        except Exception as exc:
            raise StorageError(f"Failed to save record {record.id}: {exc}") from exc

    async def get_record(self, record_id: str) -> EpisodeRecord:
        def _sync() -> EpisodeRecord:
            result = self._records.get(ids=[record_id], include=["metadatas"])
            if not result["ids"]:
                raise RecordNotFoundError(f"EpisodeRecord '{record_id}' not found.")
            return EpisodeRecord.model_validate_json(result["metadatas"][0]["data"])
        try:
            return await asyncio.to_thread(_sync)
        except RecordNotFoundError:
            raise
        except Exception as exc:
            raise StorageError(f"Failed to read record {record_id}: {exc}") from exc

    async def list_records(
        self,
        user_id: str,
        active_only: bool = True,
        since: date | None = None,
    ) -> list[EpisodeRecord]:
        def _sync() -> list[EpisodeRecord]:
            filters: list[dict] = [{"user_id": {"$eq": user_id}}]
            if active_only:
                filters.append({"active": {"$eq": 1}})
            if since:
                filters.append({"occurred_on_ts": {"$gte": _date_to_int(since)}})
            where = {"$and": filters} if len(filters) > 1 else filters[0]

            result = self._records.get(where=where, include=["metadatas"])
            records = [EpisodeRecord.model_validate_json(m["data"]) for m in result["metadatas"]]
            return sorted(records, key=lambda r: r.occurred_on, reverse=True)

        try:
            return await asyncio.to_thread(_sync)
        except Exception as exc:
            raise StorageError(f"Failed to list records for user {user_id}: {exc}") from exc

    async def supersede_record(self, old_id: str, new_id: str) -> None:
        old_record = await self.get_record(old_id)
        old_record.active = False
        old_record.superseded_by = new_id
        old_record.superseded_at = datetime.utcnow()

        def _sync() -> None:
            # Update the superseded record
            self._records.upsert(
                ids=[old_id],
                documents=[_record_document_text(old_record)],
                metadatas=[_record_metadata(old_record)],
            )
            # Flag reflections that cite old_id for re-evaluation
            ref_result = self._reflections.get(
                where={"user_id": {"$eq": old_record.user_id}},
                include=["metadatas"],
            )
            updated_ids = []
            updated_metas = []
            for ref_id, meta in zip(ref_result["ids"], ref_result["metadatas"]):
                reflection = Reflection.model_validate_json(meta["data"])
                if old_id in reflection.supporting_episode_ids:
                    reflection.needs_reeval = True
                    meta = dict(meta)
                    meta["data"] = reflection.model_dump_json()
                    updated_ids.append(ref_id)
                    updated_metas.append(meta)
            if updated_ids:
                self._reflections.upsert(
                    ids=updated_ids,
                    embeddings=[[0.0]] * len(updated_ids),
                    metadatas=updated_metas,
                )

        try:
            await asyncio.to_thread(_sync)
        except RecordNotFoundError:
            raise
        except Exception as exc:
            raise StorageError(f"Failed to supersede record {old_id}: {exc}") from exc

        logger.info("Record %s superseded by %s", old_id, new_id)

    async def update_record(self, record: EpisodeRecord) -> None:
        await self.get_record(record.id)  # raises RecordNotFoundError if missing
        await self.save_record(record)

    # -----------------------------------------------------------------------
    # EpisodeArcs
    # -----------------------------------------------------------------------

    async def save_arc(self, arc: EpisodeArc) -> None:
        def _sync() -> None:
            self._arcs.upsert(
                ids=[arc.id],
                embeddings=[[0.0]],
                metadatas=[{
                    "data": arc.model_dump_json(),
                    "user_id": arc.user_id,
                    "state": arc.state,
                    "opened_on_ts": _date_to_int(arc.opened_on),
                    "arc_type": arc.arc_type.value,
                }],
            )
        try:
            await asyncio.to_thread(_sync)
        except Exception as exc:
            raise StorageError(f"Failed to save arc {arc.id}: {exc}") from exc

    async def get_arc(self, arc_id: str) -> EpisodeArc:
        def _sync() -> EpisodeArc:
            result = self._arcs.get(ids=[arc_id], include=["metadatas"])
            if not result["ids"]:
                raise ArcNotFoundError(f"EpisodeArc '{arc_id}' not found.")
            return EpisodeArc.model_validate_json(result["metadatas"][0]["data"])
        try:
            return await asyncio.to_thread(_sync)
        except ArcNotFoundError:
            raise
        except Exception as exc:
            raise StorageError(f"Failed to read arc {arc_id}: {exc}") from exc

    async def list_arcs(
        self,
        user_id: str,
        state: str | None = None,
    ) -> list[EpisodeArc]:
        def _sync() -> list[EpisodeArc]:
            filters: list[dict] = [{"user_id": {"$eq": user_id}}]
            if state:
                filters.append({"state": {"$eq": state}})
            where = {"$and": filters} if len(filters) > 1 else filters[0]

            result = self._arcs.get(where=where, include=["metadatas"])
            arcs = [EpisodeArc.model_validate_json(m["data"]) for m in result["metadatas"]]
            return sorted(arcs, key=lambda a: a.opened_on, reverse=True)

        try:
            return await asyncio.to_thread(_sync)
        except Exception as exc:
            raise StorageError(f"Failed to list arcs for user {user_id}: {exc}") from exc

    # -----------------------------------------------------------------------
    # Reflections
    # -----------------------------------------------------------------------

    async def save_reflection(self, reflection: Reflection) -> None:
        def _sync() -> None:
            self._reflections.upsert(
                ids=[reflection.id],
                embeddings=[[0.0]],
                metadatas=[{
                    "data": reflection.model_dump_json(),
                    "user_id": reflection.user_id,
                    "active": 1 if reflection.active else 0,
                    "last_confirmed_ts": _date_to_int(reflection.last_confirmed),
                    "pattern_type": reflection.pattern_type,
                    "confidence": reflection.confidence,
                }],
            )
        try:
            await asyncio.to_thread(_sync)
        except Exception as exc:
            raise StorageError(f"Failed to save reflection {reflection.id}: {exc}") from exc

    async def get_reflection(self, reflection_id: str) -> Reflection:
        def _sync() -> Reflection:
            result = self._reflections.get(ids=[reflection_id], include=["metadatas"])
            if not result["ids"]:
                raise ReflectionNotFoundError(f"Reflection '{reflection_id}' not found.")
            return Reflection.model_validate_json(result["metadatas"][0]["data"])
        try:
            return await asyncio.to_thread(_sync)
        except ReflectionNotFoundError:
            raise
        except Exception as exc:
            raise StorageError(f"Failed to read reflection {reflection_id}: {exc}") from exc

    async def list_reflections(
        self,
        user_id: str,
        active_only: bool = True,
    ) -> list[Reflection]:
        def _sync() -> list[Reflection]:
            filters: list[dict] = [{"user_id": {"$eq": user_id}}]
            if active_only:
                filters.append({"active": {"$eq": 1}})
            where = {"$and": filters} if len(filters) > 1 else filters[0]

            result = self._reflections.get(where=where, include=["metadatas"])
            reflections = [Reflection.model_validate_json(m["data"]) for m in result["metadatas"]]
            return sorted(reflections, key=lambda r: r.last_confirmed, reverse=True)

        try:
            return await asyncio.to_thread(_sync)
        except Exception as exc:
            raise StorageError(f"Failed to list reflections for user {user_id}: {exc}") from exc

    # -----------------------------------------------------------------------
    # EngagementSnapshot
    # -----------------------------------------------------------------------

    async def save_snapshot(self, snapshot: EngagementSnapshot) -> None:
        def _sync() -> None:
            self._snapshots.upsert(
                ids=[snapshot.user_id],
                embeddings=[[0.0]],
                metadatas=[{
                    "data": snapshot.model_dump_json(),
                    "user_id": snapshot.user_id,
                    "computed_at": snapshot.computed_at.isoformat(),
                }],
            )
        try:
            await asyncio.to_thread(_sync)
        except Exception as exc:
            raise StorageError(f"Failed to save snapshot for user {snapshot.user_id}: {exc}") from exc

    async def get_snapshot(self, user_id: str) -> EngagementSnapshot | None:
        def _sync() -> EngagementSnapshot | None:
            result = self._snapshots.get(ids=[user_id], include=["metadatas"])
            if not result["ids"]:
                return None
            return EngagementSnapshot.model_validate_json(result["metadatas"][0]["data"])
        try:
            return await asyncio.to_thread(_sync)
        except Exception as exc:
            raise StorageError(f"Failed to read snapshot for user {user_id}: {exc}") from exc

    # -----------------------------------------------------------------------
    # Semantic search — episode_records only
    # -----------------------------------------------------------------------

    async def search_episodes(
        self,
        user_id: str,
        query: str,
        top_k: int = 5,
        active_only: bool = True,
        episode_type: str | None = None,
        challenge_subtype: str | None = None,
        significance: str | None = None,
        arc_id: str | None = None,
    ) -> list[EpisodeSearchResult]:
        """
        Returns EpisodeRecords most semantically similar to the query.

        Combines vector similarity with metadata filters. All filters are AND-ed.

        Args:
            user_id: Scopes search to this user's records only.
            query: Natural language query, e.g. "shoulder pain during lifting".
            top_k: Maximum results.
            active_only: If True, excludes superseded/deactivated records.
            episode_type: e.g. "ChallengeEpisode", "WorkoutEpisode".
            challenge_subtype: "injury", "fatigue", "motivation", "scheduling".
            significance: "one_off", "notable", "turning_point".
            arc_id: Only records linked to this arc.

        Returns:
            List of EpisodeSearchResult ordered by similarity score descending.
        """
        def _sync() -> list[EpisodeSearchResult]:
            total = self._records.count()
            if total == 0:
                return []

            filters: list[dict[str, Any]] = [{"user_id": {"$eq": user_id}}]
            if active_only:
                filters.append({"active": {"$eq": 1}})
            if episode_type:
                filters.append({"episode_type": {"$eq": episode_type}})
            if challenge_subtype:
                filters.append({"challenge_subtype": {"$eq": challenge_subtype}})
            if significance:
                filters.append({"significance": {"$eq": significance}})
            if arc_id:
                filters.append({"arc_id": {"$eq": arc_id}})
            where = {"$and": filters} if len(filters) > 1 else filters[0]

            try:
                results = self._records.query(
                    query_texts=[query],
                    n_results=min(top_k, total),
                    where=where,
                    include=["documents", "metadatas", "distances"],
                )
            except Exception as exc:
                logger.warning("Chroma semantic query failed: %s", exc)
                return []

            hits: list[EpisodeSearchResult] = []
            ids = results.get("ids", [[]])[0]
            distances = results.get("distances", [[]])[0]
            documents = results.get("documents", [[]])[0]
            metadatas = results.get("metadatas", [[]])[0]

            for _id, dist, doc, meta in zip(ids, distances, documents, metadatas):
                score = round(1.0 - dist / 2.0, 4)
                record = EpisodeRecord.model_validate_json(meta["data"])
                hits.append(EpisodeSearchResult(record=record, score=score, matched_text=doc))

            return hits

        return await asyncio.to_thread(_sync)


# ---------------------------------------------------------------------------
# EpisodeSearchResult
# ---------------------------------------------------------------------------

class EpisodeSearchResult:
    """
    A single semantic search result with its similarity score.

    Attributes:
        record: The full hydrated EpisodeRecord.
        score: Cosine similarity in [0, 1]. 1.0 = perfect match.
        matched_text: The document text that was embedded and matched.
    """

    __slots__ = ("record", "score", "matched_text")

    def __init__(self, record: EpisodeRecord, score: float, matched_text: str) -> None:
        self.record = record
        self.score = score
        self.matched_text = matched_text

    def __repr__(self) -> str:
        return f"EpisodeSearchResult(score={self.score}, record_id={self.record.id})"


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _record_document_text(record: EpisodeRecord) -> str:
    """Builds the text embedded for semantic search."""
    subtype = f" [{record.challenge_subtype}]" if record.challenge_subtype else ""
    return (
        f"{record.episode_type.value}{subtype}\n"
        f"Situation: {record.situation}\n"
        f"Outcome: {record.outcome}"
    )


def _record_metadata(record: EpisodeRecord) -> dict:
    """Builds the metadata dict stored alongside each episode record."""
    return {
        "data": record.model_dump_json(),
        "user_id": record.user_id,
        "episode_type": record.episode_type.value,
        "challenge_subtype": record.challenge_subtype or "",
        "significance": record.significance,
        "occurred_on_ts": _date_to_int(record.occurred_on),
        "active": 1 if record.active else 0,
        "arc_id": record.arc_id or "",
    }
