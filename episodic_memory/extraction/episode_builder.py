"""
Batch 1 — Episode Builder.

Synthesises EpisodeRecords from multiple recent coaching sessions.
Runs every 2-4 hours. Processes sessions together in a single LLM call
so that weak individual signals combine into coherent episodes.
"""

from __future__ import annotations

import difflib
import json
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from pydantic import ValidationError

from episodic_memory.extraction.llm_client import LLMClient
from episodic_memory.extraction.llm_types import ContradictionDecision, EpisodeRecordInput
from episodic_memory.extraction.prompt_loader import PromptLoader
from episodic_memory.extraction.session_filter import FilterResult, filter_sessions
from episodic_memory.extraction.snapshot_computer import compute_snapshot
from episodic_memory.models.episode_record import EpisodeRecord
from episodic_memory.models.session import Session
from episodic_memory.storage.base import EpisodeStore

logger = logging.getLogger(__name__)


def _normalize_text(text: str) -> str:
    """Lowercase, strip punctuation, and collapse whitespace for text comparison."""
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


@dataclass
class BatchOneResult:
    """
    Summary of a single Batch 1 run.

    Attributes:
        sessions_received: Total sessions passed to the builder.
        sessions_filtered: Sessions discarded by the signal filter.
        sessions_processed: Sessions that passed filtering and were sent to the LLM.
        records_created: New EpisodeRecords written to storage.
        records_superseded: Existing records archived due to contradictions.
        records_merged: Existing records updated with new detail.
        records_dropped_no_quotes: LLM-returned records dropped because
            source quote verification failed.
        records_dropped_invalid_date: LLM-returned records dropped because
            occurred_on fell outside the session date window.
    """

    sessions_received: int
    sessions_filtered: int
    sessions_processed: int
    records_created: int
    records_superseded: int
    records_merged: int
    records_dropped_no_quotes: int
    records_dropped_invalid_date: int = 0


class EpisodeBuilder:
    """
    Orchestrates Batch 1 — multi-session episode synthesis.

    Loads unprocessed sessions, runs the signal filter, assembles context,
    calls the LLM, verifies source quotes, handles contradictions, writes
    records, and computes the engagement snapshot.
    """

    def __init__(
        self,
        store: EpisodeStore,
        llm_client: LLMClient,
        prompt_loader: PromptLoader,
        min_words: int,
        context_days: int,
    ) -> None:
        """
        Initialises the builder with all required dependencies.

        Args:
            store: The episode store for reading context and writing results.
            llm_client: The LLM client for extraction calls.
            prompt_loader: Loader for the episode builder and contradiction prompts.
            min_words: Minimum word count for a session to pass the filter.
            context_days: How many past days of records to load as context.
        """
        self._store = store
        self._llm = llm_client
        self._prompts = prompt_loader
        self._min_words = min_words
        self._context_days = context_days

    def _save_record_to_chroma_db(self, user_id: str, record: EpisodeRecord) -> None:
        try:
            import database_chroma_new as database
            outcome = record.outcome
            situation = record.situation
            intent = record.intent
            coach_note = record.coach_note
            episode_type = record.episode_type.value if hasattr(record.episode_type, "value") else str(record.episode_type)
            
            description = f"Type: {episode_type} | Situation: {situation} | Intent: {intent} | Outcome: {outcome} | Coach Note: {coach_note}"
            
            database.save_memory(
                username=user_id,
                tag="episodic",
                query="",
                response=description,
                subtag="implicit"
            )
            logger.info("Successfully propagated EpisodeRecord %s to main ChromaDB memories.", record.id)
        except Exception as e:
            logger.error("Failed to propagate EpisodeRecord to main ChromaDB: %s", e)

    async def run(
        self,
        user_id: str,
        semantic_memories: list[str],
        session_id: str,
    ) -> BatchOneResult:
        """
        Executes a full Batch 1 run for a user.

        Args:
            user_id: The user to process.
            semantic_memories: Current semantic memories injected as context.
                               These come from the separate semantic memory system.
            session_id: Unique identifier for this pipeline run, used in logging.

        Returns:
            A BatchOneResult summarising what was processed and written.
        """
        sessions = await self._store.list_unprocessed_sessions(user_id)
        if not sessions:
            logger.info("[%s] No unprocessed sessions for user %s.", session_id, user_id)
            return BatchOneResult(0, 0, 0, 0, 0, 0, 0)

        filter_results = filter_sessions(sessions, self._min_words)
        passing = [r for r in filter_results if r.passed]
        filtered_count = len(filter_results) - len(passing)

        if not passing:
            logger.info("[%s] All sessions filtered for user %s.", session_id, user_id)
            await self._store.mark_sessions_processed([s.id for s in sessions])
            return BatchOneResult(len(sessions), filtered_count, 0, 0, 0, 0, 0)

        logger.info(
            "[%s] %d/%d sessions passed filter for user %s.",
            session_id, len(passing), len(sessions), user_id,
        )

        since = date.today() - timedelta(days=self._context_days)
        recent_records = await self._store.list_records(user_id, active_only=True, since=since)

        prompt = self._build_prompt(
            [r.session for r in passing],
            semantic_memories,
            recent_records,
        )

        llm_response = await self._llm.complete(prompt, session_id)
        draft_records = self._parse_llm_output(llm_response.content, session_id)

        passing_sessions_text = {
            r.session.id: r.session for r in passing
        }
        passing_sessions_list = [r.session for r in passing]

        if len(draft_records) > len(passing) * 4:
            logger.warning(
                "[%s] LLM returned %d episodes for %d sessions — unusually high. "
                "Verify output quality.",
                session_id, len(draft_records), len(passing),
            )

        date_validated, date_dropped = self._validate_date_range(
            draft_records, passing_sessions_list, session_id
        )
        verified, dropped_count = self._verify_source_quotes(date_validated, passing_sessions_text)

        records_created = 0
        records_superseded = 0
        records_merged = 0

        passing_session_ids = [r.session.id for r in passing]
        for draft, supersede_id in verified:
            # Fallback: if LLM didn't identify a supersede ID, check for a date/type match as a candidate
            if not supersede_id:
                for existing in recent_records:
                    if (
                        existing.active
                        and existing.episode_type == draft.episode_type
                        and abs((existing.occurred_on - draft.occurred_on).days) <= 1
                    ):
                        supersede_id = existing.id
                        break

            if supersede_id:
                decision = await self._check_contradiction(
                    draft, await self._store.get_record(supersede_id), session_id
                )
                if decision.action == "supersede":
                    new_record = self._to_episode_record(
                        draft, user_id, passing_session_ids, session_id
                    )
                    await self._store.save_record(new_record)
                    self._save_record_to_chroma_db(user_id, new_record)
                    await self._store.supersede_record(supersede_id, new_record.id)
                    records_superseded += 1
                    records_created += 1
                    logger.info(
                        "[%s] Record %s superseded by %s.", session_id, supersede_id, new_record.id
                    )
                elif decision.action == "merge":
                    existing = await self._store.get_record(supersede_id)
                    existing.outcome = f"{existing.outcome} | {draft.outcome}"
                    existing.source_quotes.extend(draft.source_quotes)
                    await self._store.update_record(existing)
                    self._save_record_to_chroma_db(user_id, existing)
                    records_merged += 1
                else:
                    new_record = self._to_episode_record(
                        draft, user_id, passing_session_ids, session_id
                    )
                    await self._store.save_record(new_record)
                    self._save_record_to_chroma_db(user_id, new_record)
                    records_created += 1
            else:
                new_record = self._to_episode_record(
                    draft, user_id, passing_session_ids, session_id
                )
                await self._store.save_record(new_record)
                self._save_record_to_chroma_db(user_id, new_record)
                records_created += 1

        await self._store.mark_sessions_processed([s.id for s in sessions])

        updated_records = await self._store.list_records(user_id, active_only=True)
        snapshot = compute_snapshot(user_id, updated_records, window_days=14)
        await self._store.save_snapshot(snapshot)

        logger.info(
            "[%s] Batch 1 complete for user %s: created=%d superseded=%d merged=%d dropped=%d",
            session_id, user_id, records_created, records_superseded, records_merged, dropped_count,
        )

        return BatchOneResult(
            sessions_received=len(sessions),
            sessions_filtered=filtered_count,
            sessions_processed=len(passing),
            records_created=records_created,
            records_superseded=records_superseded,
            records_merged=records_merged,
            records_dropped_no_quotes=dropped_count,
            records_dropped_invalid_date=date_dropped,
        )

    def _build_prompt(
        self,
        sessions: list[Session],
        semantic_memories: list[str],
        recent_records: list[EpisodeRecord],
    ) -> str:
        conversations_block = self._format_conversations(sessions)
        semantic_block = "\n".join(f"- {m}" for m in semantic_memories) or "None"
        records_block = self._format_records(recent_records) or "None"

        return self._prompts.render(
            "batch1_episode_builder",
            {
                "semantic_memories": semantic_block,
                "recent_episode_records": records_block,
                "conversations": conversations_block,
            },
        )

    def _format_conversations(self, sessions: list[Session]) -> str:
        parts = []
        for session in sessions:
            parts.append(f"[Session {session.occurred_on}]")
            parts.append(session.as_formatted_text())
            parts.append("")
        return "\n".join(parts)

    def _format_records(self, records: list[EpisodeRecord]) -> str:
        lines = []
        for r in records:
            lines.append(
                f"  - ID {r.id} | {r.episode_type.value} on {r.occurred_on}: {r.outcome}"
            )
        return "\n".join(lines)

    def _parse_llm_output(
        self, content: str, session_id: str
    ) -> list[tuple[EpisodeRecordInput, str | None]]:
        """
        Parses the LLM JSON output into validated EpisodeRecordInput objects.

        Returns a list of (draft, supersede_record_id) tuples. Records that
        fail Pydantic validation are dropped and logged.
        """
        content = content.strip()
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        try:
            raw = json.loads(content)
        except json.JSONDecodeError as exc:
            logger.warning("[%s] Failed to parse LLM JSON output: %s", session_id, exc)
            return []

        if not isinstance(raw, list):
            logger.warning("[%s] LLM returned non-array output.", session_id)
            return []

        results = []
        for item in raw:
            supersede_id = item.pop("supersede_record_id", None)
            try:
                draft = EpisodeRecordInput.model_validate(item)
                results.append((draft, supersede_id))
            except ValidationError as exc:
                logger.warning("[%s] Dropping invalid record from LLM: %s", session_id, exc)

        return results

    def _validate_date_range(
        self,
        drafts: list[tuple[EpisodeRecordInput, str | None]],
        sessions: list[Session],
        session_id: str,
    ) -> tuple[list[tuple[EpisodeRecordInput, str | None]], int]:
        """
        Drops episodes whose occurred_on falls outside the session date window.

        Allows a 1-day buffer on either side to handle timezone edge cases and
        sessions that span midnight. Episodes claiming dates outside this window
        are likely hallucinated — the LLM had no conversations from those dates.

        Returns a tuple of (valid_drafts, dropped_count).
        """
        if not sessions:
            return drafts, 0

        session_dates = [s.occurred_on for s in sessions]
        min_date = min(session_dates) - timedelta(days=1)
        max_date = max(session_dates) + timedelta(days=1)

        valid = []
        dropped = 0
        for draft, supersede_id in drafts:
            if min_date <= draft.occurred_on <= max_date:
                valid.append((draft, supersede_id))
            else:
                logger.warning(
                    "[%s] Dropping episode — occurred_on %s is outside session "
                    "window [%s, %s]. Episode type: %s, outcome snippet: %.80s",
                    session_id, draft.occurred_on, min_date, max_date,
                    draft.episode_type, draft.outcome,
                )
                dropped += 1

        return valid, dropped

    def _verify_source_quotes(
        self,
        drafts: list[tuple[EpisodeRecordInput, str | None]],
        session_texts: dict[str, Session],
    ) -> tuple[list[tuple[EpisodeRecordInput, str | None]], int]:
        """
        Verifies that each draft's source quotes are grounded in individual user messages.

        Checks each quote against the list of individual user messages (one per turn),
        not against the whole document concatenated. This prevents a fitness keyword
        from one message anchoring a hallucinated quote from an entirely different part
        of the conversation.

        A record passes if at least one quote survives. Records where all quotes fail
        are dropped as hallucinated.

        Returns a tuple of (verified_drafts, dropped_count).
        """
        user_messages: list[str] = []
        for session in session_texts.values():
            user_messages.extend(t.content for t in session.turns if t.role == "user")

        verified = []
        dropped = 0
        for draft, supersede_id in drafts:
            passing_quotes = [
                q for q in draft.source_quotes
                if self._quote_is_grounded(q, user_messages)
            ]
            if passing_quotes:
                draft.source_quotes = passing_quotes
                verified.append((draft, supersede_id))
            else:
                logger.warning(
                    "Dropping record — no source quotes verified in user messages. "
                    "Episode type: %s, unverified quotes: %s",
                    draft.episode_type, draft.source_quotes,
                )
                dropped += 1

        return verified, dropped

    def _quote_is_grounded(self, quote: str, user_messages: list[str]) -> bool:
        """
        Returns True if the quote is grounded in at least one user message.

        Checks against individual user messages rather than concatenated text,
        so a fitness word in one message cannot anchor a hallucinated quote
        from an entirely different part of the conversation.

        Two checks in order:
        1. Normalized substring: after stripping punctuation and lowercasing,
           if the quote appears as a direct substring of any user message, accept.
        2. Difflib sentence similarity: compute the SequenceMatcher ratio between
           the normalized quote and each normalized user message. If any message
           scores >= 0.50, the quote is close enough to be a valid paraphrase.
        """
        norm_quote = _normalize_text(quote)
        if not norm_quote:
            return False

        for message in user_messages:
            norm_message = _normalize_text(message)
            if not norm_message:
                continue

            if len(norm_quote) >= 10 and norm_quote in norm_message:
                return True

            ratio = difflib.SequenceMatcher(None, norm_quote, norm_message, autojunk=False).ratio()
            if ratio >= 0.50:
                return True

        return False

    async def _check_contradiction(
        self,
        draft: EpisodeRecordInput,
        existing: EpisodeRecord,
        session_id: str,
    ) -> ContradictionDecision:
        """
        Calls the contradiction resolution prompt to decide how to handle a conflict.

        Returns a ContradictionDecision with the action to take.
        """
        prompt = self._prompts.render(
            "contradiction_resolution",
            {
                "existing_episode_type": existing.episode_type.value,
                "existing_occurred_on": str(existing.occurred_on),
                "existing_outcome": existing.outcome,
                "existing_source_quotes": json.dumps(existing.source_quotes),
                "new_episode_type": draft.episode_type.value,
                "new_occurred_on": str(draft.occurred_on),
                "new_outcome": draft.outcome,
                "new_source_quotes": json.dumps(draft.source_quotes),
            },
        )

        response = await self._llm.complete(prompt, session_id)
        content = response.content.strip()
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        try:
            return ContradictionDecision.model_validate_json(content)
        except (ValidationError, json.JSONDecodeError) as exc:
            logger.warning(
                "[%s] Failed to parse contradiction decision: %s. Defaulting to create.",
                session_id, exc,
            )
            return ContradictionDecision(
                decision="new_event",
                reasoning="Parse failure — defaulting to safe create action.",
                action="create",
            )

    def _to_episode_record(
        self,
        draft: EpisodeRecordInput,
        user_id: str,
        source_session_ids: list[str],
        pipeline_run_id: str,
    ) -> EpisodeRecord:
        return EpisodeRecord(
            id=str(uuid.uuid4()),
            user_id=user_id,
            episode_type=draft.episode_type,
            situation=draft.situation,
            intent=draft.intent,
            outcome=draft.outcome,
            significance=draft.significance,
            coach_note=draft.coach_note,
            occurred_on=draft.occurred_on,
            source_session_ids=source_session_ids,
            source_quotes=draft.source_quotes,
            challenge_subtype=draft.challenge_subtype,
            arc_id=draft.arc_id,
            created_at=datetime.utcnow(),
            pipeline_run_id=pipeline_run_id,
        )
