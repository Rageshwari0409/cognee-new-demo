"""
Engagement snapshot computer.

Derives the EngagementSnapshot from recent EpisodeRecord counts using
rule-based logic. No LLM call is made — this runs deterministically
every time Batch 1 completes.
"""

from __future__ import annotations

from datetime import date, timedelta

from episodic_memory.models.engagement_snapshot import EngagementSnapshot
from episodic_memory.models.episode_record import EpisodeRecord
from episodic_memory.models.episode_types import DominantFocus, EpisodeType

def compute_snapshot(
    user_id: str,
    records: list[EpisodeRecord],
    window_days: int = 14,
    as_of: date | None = None,
) -> EngagementSnapshot:
    """
    Computes an EngagementSnapshot from recent episode records.

    Applies the DominantFocus rules in priority order — first match wins.
    The result is the complete coaching context signal for the current session.

    Args:
        user_id: The user the snapshot is for.
        records: All active EpisodeRecords for the user.
        window_days: How many past days to consider in the window.
        as_of: Reference date. Defaults to today.

    Returns:
        A fully populated EngagementSnapshot.
    """
    reference = as_of or date.today()
    cutoff = reference - timedelta(days=window_days)
    recent = [r for r in records if r.occurred_on >= cutoff and r.active]

    counts: dict[EpisodeType, int] = {t: 0 for t in EpisodeType}
    for record in recent:
        counts[record.episode_type] += 1

    dominant_focus = _determine_dominant_focus(recent, counts, reference, window_days)
    activity_level = _determine_activity_level(recent, reference)
    coach_signal = _build_coach_signal(dominant_focus, counts)

    return EngagementSnapshot(
        user_id=user_id,
        window_days=window_days,
        episode_type_counts=counts,
        dominant_focus=dominant_focus,
        activity_level=activity_level,
        coach_signal=coach_signal,
    )


def _determine_dominant_focus(
    recent: list[EpisodeRecord],
    counts: dict[EpisodeType, int],
    reference: date,
    window_days: int,
) -> DominantFocus:
    challenge_with_injury = [
        r for r in recent
        if r.episode_type == EpisodeType.CHALLENGE
        and r.challenge_subtype == "injury"
    ]
    if challenge_with_injury:
        return DominantFocus.INJURY_CONCERNED

    cutoff_7 = reference - timedelta(days=7)
    recent_workouts = [r for r in recent if r.episode_type == EpisodeType.WORKOUT and r.occurred_on >= cutoff_7]
    challenge_episodes = [r for r in recent if r.episode_type == EpisodeType.CHALLENGE]

    if not recent_workouts and challenge_episodes:
        return DominantFocus.LOW_ACTIVITY_CHALLENGED

    if not recent and window_days >= 14:
        return DominantFocus.DISENGAGED

    if not recent_workouts:
        return DominantFocus.LOW_ACTIVITY

    nutrition_count = counts[EpisodeType.NUTRITION]
    other_count = sum(v for k, v in counts.items() if k != EpisodeType.NUTRITION)
    if nutrition_count > other_count:
        return DominantFocus.MEAL_FOCUSED

    milestone_goal_count = counts[EpisodeType.MILESTONE] + counts[EpisodeType.GOAL]
    if milestone_goal_count >= 2:
        return DominantFocus.MOMENTUM_PHASE

    return DominantFocus.NORMAL


def _determine_activity_level(
    recent: list[EpisodeRecord],
    reference: date,
) -> str:
    cutoff_7 = reference - timedelta(days=7)
    recent_workouts_7 = [
        r for r in recent
        if r.episode_type == EpisodeType.WORKOUT and r.occurred_on >= cutoff_7
    ]

    if len(recent_workouts_7) >= 3:
        return "active"
    if len(recent_workouts_7) >= 1:
        return "reduced"
    return "inactive"


def _build_coach_signal(
    focus: DominantFocus,
    counts: dict[EpisodeType, int],
) -> str:
    challenge_count = counts[EpisodeType.CHALLENGE]
    nutrition_count = counts[EpisodeType.NUTRITION]
    milestone_goal_count = counts[EpisodeType.MILESTONE] + counts[EpisodeType.GOAL]

    signals = {
        DominantFocus.INJURY_CONCERNED: (
            f"ACTIVE INJURY SIGNAL: The user has reported {challenge_count} recent challenge episode(s) "
            "involving injury or pain. Open this conversation by checking in on their recovery status. "
            "Do not suggest new training targets or push intensity until they confirm the injury is resolved."
        ),
        DominantFocus.LOW_ACTIVITY_CHALLENGED: (
            "LOW ENGAGEMENT + CHALLENGES: The user has not logged workouts in over 7 days and has "
            "reported difficulties. Ask how they are doing generally before mentioning fitness. "
            "Keep the tone supportive and non-pressuring."
        ),
        DominantFocus.DISENGAGED: (
            "DISENGAGED: The user has had no recorded fitness episodes in the past 14 days. "
            "Do not open with training questions. Ask how they have been in general. "
            "Rebuild connection before returning to fitness content."
        ),
        DominantFocus.LOW_ACTIVITY: (
            "REDUCED ACTIVITY: The user has not logged workouts in over 7 days but has reported no "
            "challenges. Gently check what has been getting in the way and offer a low-barrier "
            "re-entry point — a short session, a walk, or a check-in on goals."
        ),
        DominantFocus.MEAL_FOCUSED: (
            f"NUTRITION FOCUS: The user has been primarily discussing nutrition ({nutrition_count} recent episodes) "
            "with little workout activity. Lead with diet and nutrition this session. "
            "Introduce training topics only if the user brings them up."
        ),
        DominantFocus.MOMENTUM_PHASE: (
            f"HIGH MOMENTUM: The user is in an active progress phase — {milestone_goal_count} recent milestone(s) "
            "or goal update(s). Match their energy. Acknowledge wins explicitly and offer a "
            "specific next target to keep the momentum going."
        ),
        DominantFocus.NORMAL: (
            "NORMAL ENGAGEMENT: The user's activity across training and nutrition is balanced. "
            "No special adjustment needed — proceed with the standard coaching approach."
        ),
    }
    return signals[focus]
