"""
Batch 1 pipeline runner — Episode Builder.

Intended to be called every 2-4 hours by a scheduler (cron, APScheduler,
Celery beat, or equivalent). Processes every user that has unprocessed sessions.

Semantic memories come from a separate system. The caller must supply a
loader function that accepts a user_id and returns that user's current
semantic memories. This keeps the episodic pipeline decoupled from whatever
system manages semantic memories.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from episodic_memory.config import settings
from episodic_memory.extraction.episode_builder import BatchOneResult, EpisodeBuilder
from episodic_memory.extraction.llm_client import LLMClient
from episodic_memory.extraction.prompt_loader import PromptLoader
from episodic_memory.storage.base import EpisodeStore
from episodic_memory.storage.chroma_store import ChromaStore

logger = logging.getLogger(__name__)

SemanticMemoriesLoader = Callable[[str], Awaitable[list[str]]]


@dataclass
class AllUsersBatchOneResult:
    """
    Aggregated result of a Batch 1 run across all pending users.

    Attributes:
        users_processed: Number of users who had sessions to process.
        users_skipped: Number of users with no pending sessions.
        per_user: Mapping of user_id to their individual BatchOneResult.
    """

    users_processed: int
    users_skipped: int
    per_user: dict[str, BatchOneResult]


async def run_batch_one(
    user_id: str,
    semantic_memories: list[str],
    store: EpisodeStore | None = None,
    min_words: int | None = None,
) -> BatchOneResult:
    """
    Executes a Batch 1 run for a single user.

    Use this when integrating the pipeline into a system that already knows
    which user to process and can supply that user's semantic memories directly.

    Args:
        user_id: The user to process.
        semantic_memories: The user's current semantic memories from the
                           external semantic memory system.
        store: Optional pre-configured store. Defaults to ChromaStore.
        min_words: Optional word count filter override.

    Returns:
        A BatchOneResult summarising what was written.
    """
    session_id = str(uuid.uuid4())
    logger.info("[%s] Starting Batch 1 for user %s.", session_id, user_id)

    store = store or await _default_store()
    builder = _make_builder(store, min_words)

    result = await builder.run(user_id, semantic_memories, session_id)
    logger.info("[%s] Batch 1 finished for user %s: %s", session_id, user_id, result)
    return result


async def run_batch_one_all_users(
    semantic_memories_loader: SemanticMemoriesLoader,
    store: EpisodeStore | None = None,
) -> AllUsersBatchOneResult:
    """
    Executes Batch 1 for every user that has at least one unprocessed session.

    Discovers pending users from the store, fetches each user's semantic
    memories via the provided loader, and processes them one at a time.
    A failure for one user is logged and skipped — other users are still processed.

    This is the function to call from a scheduler. The caller provides a
    semantic_memories_loader that bridges to whatever system manages
    semantic memories in the larger application.

    Args:
        semantic_memories_loader: An async callable that accepts a user_id
            and returns that user's current semantic memories as a list of strings.
            Example:
                async def load(user_id: str) -> list[str]:
                    return await semantic_memory_service.get(user_id)
        store: Optional pre-configured store. Defaults to ChromaStore.

    Returns:
        An AllUsersBatchOneResult summarising results across all users.
    """
    run_id = str(uuid.uuid4())
    logger.info("[%s] Starting Batch 1 for all pending users.", run_id)

    store = store or await _default_store()
    pending_users = await store.list_users_with_unprocessed_sessions()

    if not pending_users:
        logger.info("[%s] No users with unprocessed sessions.", run_id)
        return AllUsersBatchOneResult(users_processed=0, users_skipped=0, per_user={})

    logger.info("[%s] Found %d user(s) with unprocessed sessions.", run_id, len(pending_users))

    builder = _make_builder(store)
    per_user: dict[str, BatchOneResult] = {}
    skipped = 0

    for user_id in pending_users:
        session_id = str(uuid.uuid4())
        try:
            semantic_memories = await semantic_memories_loader(user_id)
            result = await builder.run(user_id, semantic_memories, session_id)
            per_user[user_id] = result
            logger.info(
                "[%s] Batch 1 complete for user %s: created=%d",
                run_id, user_id, result.records_created,
            )
        except Exception as exc:
            logger.error(
                "[%s] Batch 1 failed for user %s — skipping. Error: %s",
                run_id, user_id, exc,
            )
            skipped += 1

    logger.info(
        "[%s] Batch 1 all-users run complete: processed=%d skipped=%d",
        run_id, len(per_user), skipped,
    )

    return AllUsersBatchOneResult(
        users_processed=len(per_user),
        users_skipped=skipped,
        per_user=per_user,
    )


def _make_builder(store: EpisodeStore, min_words: int | None = None) -> EpisodeBuilder:
    mw = min_words if min_words is not None else settings.session_min_words
    return EpisodeBuilder(
        store=store,
        llm_client=LLMClient(settings),
        prompt_loader=PromptLoader(),
        min_words=mw,
        context_days=settings.batch1_context_days,
    )


async def _default_store() -> EpisodeStore:
    store = ChromaStore(
        persist_dir=settings.chroma_persist_dir,
        api_key=settings.gemini_api_key,
        embedding_model=settings.embedding_model,
    )
    await store.initialise()
    return store
