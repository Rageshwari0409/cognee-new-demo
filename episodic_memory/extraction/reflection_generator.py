"""
Batch 2 Step 2 — Reflection Generator.

Identifies behavioral patterns across episode records and generates
or updates Reflections. Runs after arc detection so arc data is current.
Also applies time-based confidence downgrades to existing reflections.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import date, timedelta

from pydantic import ValidationError

from episodic_memory.extraction.llm_client import LLMClient
from episodic_memory.extraction.llm_types import ReflectionInput
from episodic_memory.extraction.prompt_loader import PromptLoader
from episodic_memory.extraction.scoring import apply_time_based_confidence_downgrade
from episodic_memory.models.episode_arc import EpisodeArc
from episodic_memory.models.episode_record import EpisodeRecord
from episodic_memory.models.reflection import Reflection
from episodic_memory.storage.base import EpisodeStore

logger = logging.getLogger(__name__)

_INACTIVITY_MILD_DAYS = 7
_INACTIVITY_SIGNIFICANT_DAYS = 14


@dataclass
class ReflectionResult:
    """
    Summary of a single reflection generation run.

    Attributes:
        reflections_created: New Reflection records written.
        reflections_updated: Existing reflections updated with new evidence.
        reflections_downgraded: Reflections whose confidence was reduced by time.
        reflections_deactivated: Reflections deactivated due to extended inactivity.
    """

    reflections_created: int
    reflections_updated: int
    reflections_downgraded: int
    reflections_deactivated: int


class ReflectionGenerator:
    """
    Orchestrates Batch 2 Step 2 — reflection generation and maintenance.

    Loads recent records, all arcs, and existing reflections. Flags any
    reflections that need re-evaluation due to superseded supporting episodes.
    Calls the LLM for pattern analysis. Applies time-based confidence downgrades.
    """

    def __init__(
        self,
        store: EpisodeStore,
        llm_client: LLMClient,
        prompt_loader: PromptLoader,
        processing_days: int,
    ) -> None:
        """
        Initialises the generator with required dependencies.

        Args:
            store: The episode store for reading records and writing reflections.
            llm_client: The LLM client for reflection generation calls.
            prompt_loader: Loader for the reflection generation prompt.
            processing_days: How many past days of records to include as evidence.
        """
        self._store = store
        self._llm = llm_client
        self._prompts = prompt_loader
        self._processing_days = processing_days

    async def run(self, user_id: str, session_id: str) -> ReflectionResult:
        """
        Executes reflection generation for a user.

        Args:
            user_id: The user to process.
            session_id: Unique identifier for this pipeline run, used in logging.

        Returns:
            A ReflectionResult summarising the actions taken.
        """
        since = date.today() - timedelta(days=self._processing_days)
        recent_records = await self._store.list_records(user_id, active_only=True, since=since)
        all_arcs = await self._store.list_arcs(user_id)
        existing_reflections = await self._store.list_reflections(user_id, active_only=False)

        downgraded, deactivated = await self._apply_degradation(
            existing_reflections, session_id
        )

        if not recent_records:
            logger.info("[%s] No recent records for reflection generation.", session_id)
            return ReflectionResult(0, 0, downgraded, deactivated)

        active_reflections = [r for r in existing_reflections if r.active]
        prompt = self._build_prompt(recent_records, all_arcs, active_reflections)
        llm_response = await self._llm.complete(prompt, session_id)
        inputs = self._parse_llm_output(llm_response.content, session_id)

        known_episode_ids = {r.id for r in recent_records}
        episode_summaries = {r.id: r.outcome for r in recent_records}
        inputs = self._filter_episode_ids(inputs, known_episode_ids, session_id)

        created = updated = 0
        for inp in inputs:
            summaries = [
                episode_summaries[ep_id]
                for ep_id in inp.supporting_episode_ids
                if ep_id in episode_summaries
            ]
            existing_match = self._find_matching_reflection(inp, active_reflections)
            if existing_match:
                existing_match.observation = inp.observation
                existing_match.coach_action = inp.coach_action
                existing_match.supporting_episode_ids = inp.supporting_episode_ids
                existing_match.supporting_episode_summaries = summaries
                existing_match.confidence = inp.confidence
                existing_match.episode_count = inp.episode_count
                existing_match.last_confirmed = date.today()
                existing_match.needs_reeval = False
                await self._store.save_reflection(existing_match)
                updated += 1
            else:
                new_reflection = Reflection(
                    id=str(uuid.uuid4()),
                    user_id=user_id,
                    pattern_type=inp.pattern_type,
                    observation=inp.observation,
                    coach_action=inp.coach_action,
                    supporting_episode_ids=inp.supporting_episode_ids,
                    supporting_episode_summaries=summaries,
                    confidence=inp.confidence,
                    first_observed=date.today(),
                    last_confirmed=date.today(),
                    episode_count=inp.episode_count,
                )
                await self._store.save_reflection(new_reflection)
                created += 1

        logger.info(
            "[%s] Reflection generation complete for user %s: "
            "created=%d updated=%d downgraded=%d deactivated=%d",
            session_id, user_id, created, updated, downgraded, deactivated,
        )

        return ReflectionResult(
            reflections_created=created,
            reflections_updated=updated,
            reflections_downgraded=downgraded,
            reflections_deactivated=deactivated,
        )

    async def _apply_degradation(
        self, reflections: list[Reflection], session_id: str
    ) -> tuple[int, int]:
        downgraded = deactivated = 0
        for reflection in reflections:
            if not reflection.active:
                continue
            original_confidence = reflection.confidence
            original_active = reflection.active
            updated = apply_time_based_confidence_downgrade(reflection)
            if not updated.active and original_active:
                await self._store.save_reflection(updated)
                deactivated += 1
                logger.info(
                    "[%s] Reflection %s deactivated — no support in 120 days.", session_id, reflection.id
                )
            elif updated.confidence != original_confidence:
                await self._store.save_reflection(updated)
                downgraded += 1
                logger.info(
                    "[%s] Reflection %s downgraded from %s to %s.",
                    session_id, reflection.id, original_confidence, updated.confidence,
                )
        return downgraded, deactivated

    def _build_prompt(
        self,
        recent_records: list[EpisodeRecord],
        all_arcs: list[EpisodeArc],
        existing_reflections: list[Reflection],
    ) -> str:
        records_block = self._format_records(recent_records) or "None"
        arcs_block = self._format_arcs(all_arcs) or "None"
        reflections_block = self._format_reflections(existing_reflections) or "None"

        return self._prompts.render(
            "batch2_reflection",
            {
                "recent_episode_records": records_block,
                "all_arcs": arcs_block,
                "existing_reflections": reflections_block,
            },
        )

    def _format_records(self, records: list[EpisodeRecord]) -> str:
        """
        Formats episode records as a chronological timeline with inactivity gap
        markers inserted between episodes when the silence exceeds the threshold.

        A trailing gap marker is appended when the most recent record is older
        than the mild threshold relative to today — capturing current inactivity.
        """
        sorted_records = sorted(records, key=lambda r: r.occurred_on)
        lines = []
        today = date.today()

        for i, r in enumerate(sorted_records):
            if i > 0:
                prev_date = sorted_records[i - 1].occurred_on
                gap = (r.occurred_on - prev_date).days
                if gap >= _INACTIVITY_MILD_DAYS:
                    label = "SIGNIFICANT INACTIVITY" if gap >= _INACTIVITY_SIGNIFICANT_DAYS else "INACTIVITY GAP"
                    lines.append(
                        f"  ── {label}: {gap} days ({prev_date} → {r.occurred_on}) "
                        f"— no episode records in this window ──\n"
                    )

            subtype = f" [{r.challenge_subtype}]" if r.challenge_subtype else ""
            lines.append(
                f"  {r.episode_type.value}{subtype} ID {r.id}, occurred_on: {r.occurred_on}\n"
                f"  outcome: {r.outcome}\n"
            )

        if sorted_records:
            days_since_last = (today - sorted_records[-1].occurred_on).days
            if days_since_last >= _INACTIVITY_MILD_DAYS:
                label = "SIGNIFICANT INACTIVITY" if days_since_last >= _INACTIVITY_SIGNIFICANT_DAYS else "INACTIVITY GAP"
                lines.append(
                    f"  ── {label}: {days_since_last} days ({sorted_records[-1].occurred_on} → today {today}) "
                    f"— no episode records since the last entry above ──\n"
                )

        return "\n".join(lines)

    def _format_arcs(self, arcs: list[EpisodeArc]) -> str:
        lines = []
        for a in arcs:
            lines.append(
                f"  {a.arc_type.value} ID {a.id} ({a.state}): {a.summary}"
            )
        return "\n".join(lines)

    def _format_reflections(self, reflections: list[Reflection]) -> str:
        lines = []
        for r in reflections:
            status = "active" if r.active else "inactive"
            lines.append(
                f"  ID {r.id} | {r.pattern_type} | {r.confidence} | {status}\n"
                f"  observation: {r.observation}\n"
            )
        return "\n".join(lines)

    def _parse_llm_output(self, content: str, session_id: str) -> list[ReflectionInput]:
        content = content.strip()
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        try:
            raw = json.loads(content)
        except json.JSONDecodeError as exc:
            logger.warning("[%s] Failed to parse reflection JSON: %s", session_id, exc)
            return []

        if not isinstance(raw, list):
            logger.warning("[%s] Reflection output is not an array.", session_id)
            return []

        results = []
        for item in raw:
            item.pop("id", None)
            try:
                results.append(ReflectionInput.model_validate(item))
            except ValidationError as exc:
                logger.warning("[%s] Dropping invalid reflection: %s", session_id, exc)

        return results

    def _filter_episode_ids(
        self,
        inputs: list[ReflectionInput],
        known_ids: set[str],
        session_id: str,
    ) -> list[ReflectionInput]:
        """
        Filters each reflection's supporting_episode_ids to only IDs that were
        actually provided in the prompt.

        The LLM sometimes fabricates episode IDs that do not exist in the
        provided data. Any hallucinated ID is removed. If filtering leaves a
        reflection with fewer than 3 supporting episodes, the reflection is
        dropped entirely — it is no longer grounded in real evidence.

        Returns the filtered list of ReflectionInputs.
        """
        filtered = []
        for inp in inputs:
            verified_ids = [eid for eid in inp.supporting_episode_ids if eid in known_ids]
            hallucinated = set(inp.supporting_episode_ids) - known_ids
            if hallucinated:
                logger.warning(
                    "[%s] Reflection contained %d hallucinated episode ID(s): %s",
                    session_id, len(hallucinated), hallucinated,
                )
            if len(verified_ids) < 3:
                logger.warning(
                    "[%s] Dropping reflection — only %d verified episode ID(s) remain "
                    "after removing hallucinated IDs. pattern_type: %s",
                    session_id, len(verified_ids), inp.pattern_type,
                )
                continue
            inp.supporting_episode_ids = verified_ids
            inp.episode_count = len(verified_ids)
            filtered.append(inp)
        return filtered

    def _find_matching_reflection(
        self, inp: ReflectionInput, existing: list[Reflection]
    ) -> Reflection | None:
        for reflection in existing:
            if reflection.pattern_type == inp.pattern_type:
                overlap = set(inp.supporting_episode_ids) & set(reflection.supporting_episode_ids)
                if overlap:
                    return reflection
        return None
