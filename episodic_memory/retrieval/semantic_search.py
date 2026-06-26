"""
Semantic retrieval utility for episodic memories.

SemanticRetriever wraps ChromaStore to provide a clean async API for
finding episodic records by meaning rather than exact field values.

ChromaStore is the single source of truth for all memory data — episode
records are stored with Google embeddings so they can be retrieved both
by metadata filters (pipeline operations) and by semantic similarity
(user-facing coaching queries).

Typical usage
─────────────

    from episodic_memory.storage.chroma_store import ChromaStore
    from episodic_memory.retrieval.semantic_search import SemanticRetriever
    from episodic_memory.config import settings

    store = ChromaStore(
        persist_dir=settings.chroma_persist_dir,
        api_key=settings.gemini_api_key,
        embedding_model=settings.embedding_model,
    )

    retriever = SemanticRetriever(store=store)

    results = await retriever.search_episodes(
        user_id=user_id,
        query="shoulder pain during overhead work",
        top_k=5,
    )
    for hit in results:
        print(hit.score, hit.record.outcome)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from episodic_memory.models.episode_record import EpisodeRecord
from episodic_memory.storage.chroma_store import ChromaStore, EpisodeSearchResult

logger = logging.getLogger(__name__)


@dataclass
class SemanticSearchResult:
    """
    A single semantic search result, fully hydrated from ChromaDB.

    Attributes:
        record: The full EpisodeRecord.
        score: Cosine similarity score in [0, 1]. 1.0 = perfect match.
        matched_text: The document text that was embedded (for inspection).
    """

    record: EpisodeRecord
    score: float
    matched_text: str


class SemanticRetriever:
    """
    Async utility for semantic episode retrieval backed by ChromaStore.

    ChromaStore holds both the vector index and the full record data, so
    no secondary lookup is needed — results are hydrated directly from
    the Chroma metadata.
    """

    def __init__(self, store: ChromaStore) -> None:
        """
        Args:
            store: An initialised ChromaStore instance.
        """
        self._store = store

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
    ) -> list[SemanticSearchResult]:
        """
        Returns the top_k EpisodeRecords most semantically similar to the query.

        Delegates to ChromaStore.search_episodes() which queries the vector
        index and hydrates records from Chroma metadata in a single pass.
        Results are ordered by similarity score (highest first).

        Args:
            user_id: Required. Scopes search to this user's records only.
            query: Natural language query — e.g. "shin pain during marathon training"
                   or "protein intake on heavy training days".
            top_k: Maximum number of results.
            active_only: If True, superseded and deactivated records are excluded.
            episode_type: Filter to a specific type — "WorkoutEpisode",
                          "ChallengeEpisode", "NutritionEpisode", etc.
            challenge_subtype: Filter to a challenge subtype — "injury",
                               "fatigue", "motivation", "scheduling".
            significance: Filter to "one_off", "notable", or "turning_point".
            arc_id: Filter to records linked to a specific arc.

        Returns:
            List of SemanticSearchResult ordered by score descending.
            Returns [] if no records exist or no results match.

        Example queries:
            "knee pain and running"
            "motivation drop, not wanting to train"
            "protein intake vegetarian training"
            "bench press milestone, personal best"
            "recovery after surgery shoulder"
        """
        raw_hits: list[EpisodeSearchResult] = await self._store.search_episodes(
            user_id=user_id,
            query=query,
            top_k=top_k,
            active_only=active_only,
            episode_type=episode_type,
            challenge_subtype=challenge_subtype,
            significance=significance,
            arc_id=arc_id,
        )

        return [
            SemanticSearchResult(
                record=hit.record,
                score=hit.score,
                matched_text=hit.matched_text,
            )
            for hit in raw_hits
        ]


def make_retriever(store: ChromaStore) -> SemanticRetriever:
    """
    Convenience factory that creates a SemanticRetriever from a ChromaStore.

    Args:
        store: An already-initialised ChromaStore.

    Returns:
        A ready-to-use SemanticRetriever.

    Example:
        store = ChromaStore(
            persist_dir=settings.chroma_persist_dir,
            api_key=settings.gemini_api_key,
        )
        retriever = make_retriever(store)
    """
    return SemanticRetriever(store=store)
