"""
Signal filter for incoming coaching sessions.

Discards sessions that are too short to contain meaningful fitness content
before they reach the LLM, or sessions that are explicitly off-topic.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from episodic_memory.models.session import Session

logger = logging.getLogger(__name__)


@dataclass
class FilterResult:
    """
    The outcome of running a session through the signal filter.

    Attributes:
        passed: True if the session should proceed to LLM extraction.
        reason: Human-readable explanation of the filter decision.
        session: The evaluated session.
    """

    passed: bool
    reason: str
    session: Session


def filter_sessions(sessions: list[Session], min_words: int) -> list[FilterResult]:
    """
    Applies a minimum word count and fitness relevance filter to a list of sessions.

    A session passes if it contains at least min_words words across all turns
    and contains fitness-related discussion.

    Args:
        sessions: The sessions to evaluate.
        min_words: Minimum total word count across all turns for a session to pass.

    Returns:
        A list of FilterResult objects — one per session — in input order.
    """
    results = []
    for session in sessions:
        result = _evaluate(session, min_words)
        if result.passed:
            logger.debug("Session %s passed filter: %s", session.id, result.reason)
        else:
            logger.debug("Session %s filtered out: %s", session.id, result.reason)
        results.append(result)
    return results


def _check_relevance(session: Session) -> tuple[bool, str]:
    """Helper to verify if a session is relevant to training/fitness/recovery."""
    text = " ".join(turn.content.lower() for turn in session.turns)

    # Check if user explicitly states there is no training/fitness content
    off_topic_phrases = [
        "nothing training related",
        "nothing fitness related",
        "nothing workout related",
        "no training today",
        "no workout today",
        "not training related",
    ]
    for phrase in off_topic_phrases:
        if phrase in text:
            return False, f"Session explicitly marked as off-topic ('{phrase}')."

    # Define a set of fitness/coaching keywords
    fitness_keywords = {
        "run", "jog", "workout", "training", "exercise", "stretch", "rehab", "physio",
        "shin", "pain", "sore", "injury", "fracture", "ache", "stiff", "protein",
        "supplement", "nutrition", "diet", "food", "oats", "seed", "lentil", "tofu",
        "yoghurt", "yogurt", "meal", "carb", "calorie", "weight", "lift", "squat",
        "shoulder", "lat", "pulldown", "yoga", "mobility", "flexor", "garmin", "strava",
        "watch", "gps", "km", "mile", "pace", "speed", "splits", "marathon", "distance",
        "sleep", "recovery", "rest", "physique", "athlete", "cardio", "gym", "surgeon",
        "surgery", "doctor", "blood test", "ferritin", "iron", "fatigue", "energy"
    }

    # Extract words
    words = set(re.findall(r'[a-z]+', text))
    matched = words.intersection(fitness_keywords)

    # If the only matched keyword is "training", check if it is negated
    if matched == {"training"}:
        if any(neg in text for neg in ["nothing training", "no training", "not training"]):
            return False, "Session only mentioned 'training' in a negated/off-topic context."

    if not matched:
        return False, "Session contains no fitness-related keywords."

    return True, ""


def _evaluate(session: Session, min_words: int) -> FilterResult:
    word_count = session.word_count()
    if word_count < min_words:
        return FilterResult(
            passed=False,
            reason=f"Word count {word_count} is below minimum {min_words}.",
            session=session,
        )

    # Check for fitness relevance
    is_relevant, relevance_reason = _check_relevance(session)
    if not is_relevant:
        return FilterResult(
            passed=False,
            reason=relevance_reason,
            session=session,
        )

    return FilterResult(
        passed=True,
        reason=f"Passed — word count {word_count}.",
        session=session,
    )
