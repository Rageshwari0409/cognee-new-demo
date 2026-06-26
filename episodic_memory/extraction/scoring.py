"""
Memory degradation scoring for retrieval-time ranking.

Scores are computed at retrieval time only. Stored records are never modified
by scoring. Higher scores indicate more relevant memories for the coaching prompt.
"""

from __future__ import annotations

from datetime import date

from episodic_memory.models.episode_arc import EpisodeArc
from episodic_memory.models.episode_record import EpisodeRecord
from episodic_memory.models.reflection import Reflection

_DECAY_RATES = {
    "one_off": 0.10,
    "notable": 0.04,
    "turning_point": 0.01,
}

_CONFIDENCE_WEIGHTS = {
    "low": 0.4,
    "medium": 0.7,
    "high": 1.0,
}

_ARC_DECAY_RATE = 0.15

_VERIFIED_FLOOR = 0.3

_RECENCY_DECAY_RATE = 0.2


def score_episode_record(record: EpisodeRecord, as_of: date | None = None) -> float:
    """
    Computes a retrieval-time relevance score for an EpisodeRecord.

    More significant records decay more slowly. User-verified records
    have a minimum floor so they are never ranked below unverified ones
    purely due to age.

    Args:
        record: The record to score.
        as_of: The reference date for age calculation. Defaults to today.

    Returns:
        A float in (0.0, 1.0] representing current relevance.
    """
    reference = as_of or date.today()
    days_old = max((reference - record.occurred_on).days, 0)
    decay = _DECAY_RATES.get(record.significance, 0.04)
    score = 1.0 / (1.0 + days_old * decay)

    if record.user_verified:
        score = max(score, _VERIFIED_FLOOR)

    return round(score, 4)


def score_episode_arc(arc: EpisodeArc, as_of: date | None = None) -> float:
    """
    Computes a retrieval-time relevance score for an EpisodeArc.

    Open arcs always score 1.0 — they are always injected into context.
    Concluded arcs decay slowly by weeks since conclusion. Abandoned arcs
    score 0.0 and are not injected into prompts.

    Args:
        arc: The arc to score.
        as_of: The reference date for age calculation. Defaults to today.

    Returns:
        A float in [0.0, 1.0].
    """
    if arc.state == "open":
        return 1.0

    if arc.state == "abandoned":
        return 0.0

    reference = as_of or date.today()
    concluded = arc.concluded_on or reference
    weeks_old = max((reference - concluded).days / 7, 0)
    return round(1.0 / (1.0 + weeks_old * _ARC_DECAY_RATE), 4)


def score_reflection(reflection: Reflection, as_of: date | None = None) -> float:
    """
    Computes a retrieval-time relevance score for a Reflection.

    Combines confidence weight with recency of last confirmation.
    Reflections with no recent supporting evidence decay faster.

    Args:
        reflection: The reflection to score.
        as_of: The reference date for age calculation. Defaults to today.

    Returns:
        A float in (0.0, 1.0].
    """
    reference = as_of or date.today()
    weeks_since_confirmed = max((reference - reflection.last_confirmed).days / 7, 0)
    recency = 1.0 / (1.0 + weeks_since_confirmed * _RECENCY_DECAY_RATE)
    weight = _CONFIDENCE_WEIGHTS.get(reflection.confidence, 0.7)
    return round(weight * recency, 4)


def apply_time_based_confidence_downgrade(
    reflection: Reflection, as_of: date | None = None
) -> Reflection:
    """
    Applies time-based confidence downgrade rules to a Reflection in-place.

    Downgrades confidence tier and deactivates if no new supporting episodes
    have been found within the defined windows. The returned reflection has
    updated confidence and active fields but is not persisted — callers must
    save it after calling this function.

    Args:
        reflection: The reflection to evaluate and potentially downgrade.
        as_of: The reference date. Defaults to today.

    Returns:
        The reflection with potentially downgraded confidence or active=False.
    """
    reference = as_of or date.today()
    days_since = (reference - reflection.last_confirmed).days

    if reflection.confidence == "high" and days_since >= 60:
        reflection.confidence = "medium"
    elif reflection.confidence == "medium" and days_since >= 90:
        reflection.confidence = "low"
    elif reflection.confidence == "low" and days_since >= 120:
        reflection.active = False

    return reflection
