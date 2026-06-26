"""
Batch 2 Step 1 — Arc Detector.

Processes this week's EpisodeRecords against all open arcs. Advances open
arcs, concludes finished ones, starts new arcs, and marks abandoned arcs.
Runs once per week as the first step of Batch 2.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from pydantic import ValidationError

from episodic_memory.extraction.llm_client import LLMClient
from episodic_memory.extraction.llm_types import ArcAction
from episodic_memory.extraction.prompt_loader import PromptLoader
from episodic_memory.models.episode_arc import EpisodeArc
from episodic_memory.models.episode_record import EpisodeRecord
from episodic_memory.storage.base import EpisodeStore

logger = logging.getLogger(__name__)

ABANDON_AFTER_WEEKS = 3


@dataclass
class ArcDetectionResult:
    """
    Summary of a single arc detection run.

    Attributes:
        arcs_advanced: Open arcs updated with new episode progress.
        arcs_concluded: Open arcs marked as completed.
        arcs_created: New arcs started from this week's episodes.
        arcs_abandoned: Open arcs marked abandoned due to inactivity.
    """

    arcs_advanced: int
    arcs_concluded: int
    arcs_created: int
    arcs_abandoned: int


class ArcDetector:
    """
    Orchestrates arc detection and advancement for Batch 2 Step 1.

    First marks any open arcs that have been idle for 3+ weeks as abandoned.
    Then calls the LLM to advance active arcs, conclude finished ones, and
    detect new arcs from this week's episodes.
    """

    def __init__(
        self,
        store: EpisodeStore,
        llm_client: LLMClient,
        prompt_loader: PromptLoader,
        processing_days: int,
    ) -> None:
        """
        Initialises the detector with required dependencies.

        Args:
            store: The episode store for reading records and writing arcs.
            llm_client: The LLM client for arc detection calls.
            prompt_loader: Loader for the arc detection prompt.
            processing_days: How many past days of records to consider as "this week".
        """
        self._store = store
        self._llm = llm_client
        self._prompts = prompt_loader
        self._processing_days = processing_days

    async def run(self, user_id: str, session_id: str) -> ArcDetectionResult:
        """
        Executes arc detection for a user.

        Args:
            user_id: The user to process.
            session_id: Unique identifier for this pipeline run, used in logging.

        Returns:
            An ArcDetectionResult summarising the actions taken.
        """
        since = date.today() - timedelta(days=self._processing_days)
        recent_records = await self._store.list_records(user_id, active_only=True, since=since)
        open_arcs = await self._store.list_arcs(user_id, state="open")

        abandoned_count = await self._mark_abandoned_arcs(open_arcs, session_id)
        active_open_arcs = [a for a in open_arcs if a.state == "open"]

        if not recent_records and not active_open_arcs:
            logger.info("[%s] No records or arcs to process for user %s.", session_id, user_id)
            return ArcDetectionResult(0, 0, 0, abandoned_count)

        prompt = self._build_prompt(recent_records, active_open_arcs)
        llm_response = await self._llm.complete(prompt, session_id)
        actions = self._parse_llm_output(llm_response.content, session_id)

        known_episode_ids = {r.id for r in recent_records}
        episode_summaries = {r.id: r.outcome for r in recent_records}
        actions = self._filter_episode_ids(actions, known_episode_ids, session_id)

        advanced = concluded = created = 0
        for action in actions:
            if action.action == "advance":
                await self._apply_advance(action, user_id, episode_summaries, session_id)
                advanced += 1
            elif action.action == "conclude":
                await self._apply_conclude(action, user_id, episode_summaries, session_id)
                concluded += 1
            elif action.action == "create":
                await self._apply_create(action, user_id, episode_summaries, session_id)
                created += 1
            elif action.action == "abandon":
                await self._apply_abandon(action, session_id)
                abandoned_count += 1

        for action in actions:
            if action.arc_id and action.source_episode_ids:
                for ep_id in action.source_episode_ids:
                    try:
                        record = await self._store.get_record(ep_id)
                        if record.arc_id is None:
                            record.arc_id = action.arc_id
                            await self._store.update_record(record)
                    except Exception:
                        pass

        logger.info(
            "[%s] Arc detection complete for user %s: advanced=%d concluded=%d created=%d abandoned=%d",
            session_id, user_id, advanced, concluded, created, abandoned_count,
        )

        return ArcDetectionResult(
            arcs_advanced=advanced,
            arcs_concluded=concluded,
            arcs_created=created,
            arcs_abandoned=abandoned_count,
        )

    async def _mark_abandoned_arcs(
        self, open_arcs: list[EpisodeArc], session_id: str
    ) -> int:
        abandoned = 0
        cutoff = date.today() - timedelta(weeks=ABANDON_AFTER_WEEKS)
        for arc in open_arcs:
            last_update_date = arc.last_updated.date()
            if last_update_date < cutoff:
                arc.state = "abandoned"
                arc.last_updated = datetime.utcnow()
                await self._store.save_arc(arc)
                abandoned += 1
                logger.info(
                    "[%s] Arc %s marked abandoned — no activity since %s.",
                    session_id, arc.id, last_update_date,
                )
        return abandoned

    def _build_prompt(
        self,
        recent_records: list[EpisodeRecord],
        open_arcs: list[EpisodeArc],
    ) -> str:
        records_block = self._format_records(recent_records) or "None"
        arcs_block = self._format_arcs(open_arcs) or "None"

        return self._prompts.render(
            "batch2_arc_detection",
            {
                "new_episode_records": records_block,
                "open_arcs": arcs_block,
            },
        )

    def _format_records(self, records: list[EpisodeRecord]) -> str:
        lines = []
        for r in records:
            lines.append(
                f"  {r.episode_type.value} ID {r.id}, occurred_on: {r.occurred_on}\n"
                f"  outcome: {r.outcome}\n"
                f"  source_quotes: {r.source_quotes}\n"
            )
        return "\n".join(lines)

    def _format_arcs(self, arcs: list[EpisodeArc]) -> str:
        lines = []
        for a in arcs:
            lines.append(
                f"  ID: {a.id}\n"
                f"  arc_type: {a.arc_type.value}\n"
                f"  state: {a.state}\n"
                f"  opened_on: {a.opened_on}\n"
                f"  title: {a.title}\n"
                f"  summary: {a.summary}\n"
            )
        return "\n".join(lines)

    def _filter_episode_ids(
        self,
        actions: list[ArcAction],
        known_ids: set[str],
        session_id: str,
    ) -> list[ArcAction]:
        """
        Removes hallucinated episode IDs from arc actions.

        The LLM may cite episode IDs that were not in the provided data.
        These are stripped from source_episode_ids. The action itself is
        kept (an arc can be advanced or concluded without source IDs), but
        a "create" action with zero verified source IDs is dropped — a new
        arc must be grounded in at least one real episode.
        """
        filtered = []
        for action in actions:
            if not action.source_episode_ids:
                filtered.append(action)
                continue

            verified_ids = [eid for eid in action.source_episode_ids if eid in known_ids]
            hallucinated = set(action.source_episode_ids) - known_ids
            if hallucinated:
                logger.warning(
                    "[%s] Arc action '%s' contained %d hallucinated episode ID(s): %s",
                    session_id, action.action, len(hallucinated), hallucinated,
                )
            if action.action == "create" and not verified_ids:
                logger.warning(
                    "[%s] Dropping arc 'create' action — no verified episode IDs remain. "
                    "title: %s",
                    session_id, action.title,
                )
                continue
            action.source_episode_ids = verified_ids
            filtered.append(action)
        return filtered

    def _parse_llm_output(self, content: str, session_id: str) -> list[ArcAction]:
        content = content.strip()
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        try:
            raw = json.loads(content)
        except json.JSONDecodeError as exc:
            logger.warning("[%s] Failed to parse arc detection JSON: %s", session_id, exc)
            return []

        if not isinstance(raw, list):
            logger.warning("[%s] Arc detection returned non-array output.", session_id)
            return []

        actions = []
        for item in raw:
            try:
                actions.append(ArcAction.model_validate(item))
            except ValidationError as exc:
                logger.warning("[%s] Dropping invalid arc action: %s", session_id, exc)

        return actions

    async def _apply_advance(
        self,
        action: ArcAction,
        user_id: str,
        episode_summaries: dict[str, str],
        session_id: str,
    ) -> None:
        if not action.arc_id:
            logger.warning("[%s] Advance action missing arc_id — skipping.", session_id)
            return
        try:
            arc = await self._store.get_arc(action.arc_id)
        except Exception as exc:
            logger.warning("[%s] Arc %s not found for advance: %s", session_id, action.arc_id, exc)
            return
        arc.summary = action.updated_summary
        arc.coach_note = action.updated_coach_note
        arc.last_updated = datetime.utcnow()
        new_ids = action.source_episode_ids or []
        for ep_id in new_ids:
            if ep_id not in arc.source_episode_ids:
                arc.source_episode_ids.append(ep_id)
        new_summaries = [
            episode_summaries[ep_id] for ep_id in new_ids if ep_id in episode_summaries
        ]
        for s in new_summaries:
            if s not in arc.source_episode_summaries:
                arc.source_episode_summaries.append(s)
        await self._store.save_arc(arc)

    async def _apply_conclude(
        self,
        action: ArcAction,
        user_id: str,
        episode_summaries: dict[str, str],
        session_id: str,
    ) -> None:
        if not action.arc_id:
            logger.warning("[%s] Conclude action missing arc_id — skipping.", session_id)
            return
        try:
            arc = await self._store.get_arc(action.arc_id)
        except Exception as exc:
            logger.warning("[%s] Arc %s not found for conclude: %s", session_id, action.arc_id, exc)
            return
        arc.state = "completed"
        arc.concluded_on = action.concluded_on or date.today()
        arc.summary = action.updated_summary
        arc.coach_note = action.updated_coach_note
        arc.last_updated = datetime.utcnow()
        new_ids = action.source_episode_ids or []
        for ep_id in new_ids:
            if ep_id not in arc.source_episode_ids:
                arc.source_episode_ids.append(ep_id)
        new_summaries = [
            episode_summaries[ep_id] for ep_id in new_ids if ep_id in episode_summaries
        ]
        for s in new_summaries:
            if s not in arc.source_episode_summaries:
                arc.source_episode_summaries.append(s)
        await self._store.save_arc(arc)
        logger.info("[%s] Arc %s concluded on %s.", session_id, arc.id, arc.concluded_on)

    async def _apply_create(
        self,
        action: ArcAction,
        user_id: str,
        episode_summaries: dict[str, str],
        session_id: str,
    ) -> None:
        if not action.arc_type or not action.title:
            logger.warning("[%s] Create action missing arc_type or title — skipping.", session_id)
            return
        source_ids = action.source_episode_ids or []
        summaries = [
            episode_summaries[ep_id] for ep_id in source_ids if ep_id in episode_summaries
        ]
        new_arc = EpisodeArc(
            id=str(uuid.uuid4()),
            user_id=user_id,
            arc_type=action.arc_type,
            title=action.title,
            summary=action.updated_summary,
            state="open",
            opened_on=action.opened_on or date.today(),
            source_episode_ids=source_ids,
            source_episode_summaries=summaries,
            coach_note=action.updated_coach_note,
            last_updated=datetime.utcnow(),
        )
        await self._store.save_arc(new_arc)
        logger.info("[%s] New arc created: %s (%s).", session_id, new_arc.id, new_arc.title)

    async def _apply_abandon(self, action: ArcAction, session_id: str) -> None:
        if not action.arc_id:
            logger.warning("[%s] Abandon action missing arc_id — skipping.", session_id)
            return
        try:
            arc = await self._store.get_arc(action.arc_id)
        except Exception as exc:
            logger.warning("[%s] Arc %s not found for abandon: %s", session_id, action.arc_id, exc)
            return
        arc.state = "abandoned"
        arc.last_updated = datetime.utcnow()
        await self._store.save_arc(arc)
        logger.info("[%s] Arc %s marked abandoned by LLM.", session_id, arc.id)
