"""
Batch 2 pipeline runner — Weekly Insight Generator.

Intended to be called once per week by a scheduler. Runs arc detection
first (so arc state is current), then reflection generation, for every
user that has episodic memory history.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from episodic_memory.config import settings
from episodic_memory.extraction.arc_detector import ArcDetectionResult, ArcDetector
from episodic_memory.extraction.llm_client import LLMClient
from episodic_memory.extraction.prompt_loader import PromptLoader
from episodic_memory.extraction.reflection_generator import ReflectionGenerator, ReflectionResult
from episodic_memory.storage.base import EpisodeStore
from episodic_memory.storage.chroma_store import ChromaStore

logger = logging.getLogger(__name__)


@dataclass
class BatchTwoResult:
    """
    Combined result of a Batch 2 run for a single user.

    Attributes:
        arc_result: Outcome of Step 1 — arc detection.
        reflection_result: Outcome of Step 2 — reflection generation.
    """

    arc_result: ArcDetectionResult
    reflection_result: ReflectionResult


@dataclass
class AllUsersBatchTwoResult:
    """
    Aggregated result of a Batch 2 run across all users.

    Attributes:
        users_processed: Number of users successfully processed.
        users_skipped: Number of users skipped due to errors.
        per_user: Mapping of user_id to their individual BatchTwoResult.
    """

    users_processed: int
    users_skipped: int
    per_user: dict[str, BatchTwoResult]


async def run_batch_two(
    user_id: str,
    store: EpisodeStore | None = None,
    processing_days: int | None = None,
) -> BatchTwoResult:
    """
    Executes a full Batch 2 run for a single user.

    Runs arc detection before reflection generation so reflection inputs
    include up-to-date arc context.

    Args:
        user_id: The user to process.
        store: Optional pre-configured store. Defaults to ChromaStore.
        processing_days: Optional processing window in days.

    Returns:
        A BatchTwoResult combining arc and reflection outcomes.
    """
    session_id = str(uuid.uuid4())
    logger.info("[%s] Starting Batch 2 for user %s.", session_id, user_id)

    store = store or await _default_store()
    result = await _run_for_user(user_id, store, session_id, processing_days)

    logger.info(
        "[%s] Batch 2 finished for user %s. Arcs: %s. Reflections: %s",
        session_id, user_id, result.arc_result, result.reflection_result,
    )
    return result


async def run_batch_two_all_users(
    store: EpisodeStore | None = None,
) -> AllUsersBatchTwoResult:
    """
    Executes Batch 2 for every user that has any episodic memory history.

    Unlike Batch 1, Batch 2 runs for all users regardless of whether new
    sessions exist — it advances arcs, generates reflections from accumulated
    history, and applies time-based confidence downgrades.

    A failure for one user is logged and skipped — other users are still processed.

    Args:
        store: Optional pre-configured store. Defaults to ChromaStore.

    Returns:
        An AllUsersBatchTwoResult summarising results across all users.
    """
    run_id = str(uuid.uuid4())
    logger.info("[%s] Starting Batch 2 for all users.", run_id)

    store = store or await _default_store()
    all_user_ids = await store.list_all_user_ids()

    if not all_user_ids:
        logger.info("[%s] No users found in store.", run_id)
        return AllUsersBatchTwoResult(users_processed=0, users_skipped=0, per_user={})

    logger.info("[%s] Running Batch 2 for %d user(s).", run_id, len(all_user_ids))

    per_user: dict[str, BatchTwoResult] = {}
    skipped = 0

    for user_id in all_user_ids:
        session_id = str(uuid.uuid4())
        try:
            result = await _run_for_user(user_id, store, session_id)
            per_user[user_id] = result
            logger.info(
                "[%s] Batch 2 complete for user %s: arcs_advanced=%d reflections_created=%d",
                run_id, user_id,
                result.arc_result.arcs_advanced,
                result.reflection_result.reflections_created,
            )
        except Exception as exc:
            logger.error(
                "[%s] Batch 2 failed for user %s — skipping. Error: %s",
                run_id, user_id, exc,
            )
            skipped += 1

    logger.info(
        "[%s] Batch 2 all-users run complete: processed=%d skipped=%d",
        run_id, len(per_user), skipped,
    )

    return AllUsersBatchTwoResult(
        users_processed=len(per_user),
        users_skipped=skipped,
        per_user=per_user,
    )


async def _run_for_user(
    user_id: str, store: EpisodeStore, session_id: str, processing_days: int | None = None
) -> BatchTwoResult:
    llm_client = LLMClient(settings)
    prompt_loader = PromptLoader()

    p_days = processing_days if processing_days is not None else settings.batch2_processing_days

    arc_detector = ArcDetector(
        store=store,
        llm_client=llm_client,
        prompt_loader=prompt_loader,
        processing_days=p_days,
    )
    arc_result = await arc_detector.run(user_id, session_id)

    reflection_generator = ReflectionGenerator(
        store=store,
        llm_client=llm_client,
        prompt_loader=prompt_loader,
        processing_days=p_days,
    )
    reflection_result = await reflection_generator.run(user_id, session_id)

    return BatchTwoResult(arc_result=arc_result, reflection_result=reflection_result)


async def _default_store() -> EpisodeStore:
    store = ChromaStore(
        persist_dir=settings.chroma_persist_dir,
        api_key=settings.gemini_api_key,
        embedding_model=settings.embedding_model,
    )
    await store.initialise()
    return store
