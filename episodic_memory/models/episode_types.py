"""
Enumerations shared across all episodic memory models.

Defines episode types, arc types, and dominant focus categories
used to classify and interpret user fitness history.
"""

from enum import Enum


class EpisodeType(str, Enum):
    """Categories of fitness episodes that can be extracted from conversations."""

    WORKOUT = "WorkoutEpisode"
    NUTRITION = "NutritionEpisode"
    GOAL = "GoalEpisode"
    CHALLENGE = "ChallengeEpisode"
    MILESTONE = "MilestoneEpisode"
    CHECK_IN = "CheckInEpisode"


class ArcType(str, Enum):
    """Categories of multi-episode story arcs spanning days or weeks."""

    PROGRAM = "ProgramArc"
    INJURY = "InjuryArc"
    GOAL = "GoalArc"
    TRANSFORMATION = "TransformationArc"
    BEHAVIOR_CHANGE = "BehaviorChangeArc"


class DominantFocus(str, Enum):
    """
    The user's primary engagement theme derived from recent episode activity.

    Used to orient the coaching approach at the start of a session.
    Determined by rule-based logic applied to episode counts — no LLM call needed.
    """

    INJURY_CONCERNED = "injury_concerned"
    LOW_ACTIVITY_CHALLENGED = "low_activity_challenged"
    DISENGAGED = "disengaged"
    LOW_ACTIVITY = "low_activity"
    MEAL_FOCUSED = "meal_focused"
    MOMENTUM_PHASE = "momentum_phase"
    NORMAL = "normal"
