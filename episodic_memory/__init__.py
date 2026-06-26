"""
Episodic memory pipeline for fitness coaching applications.

This package extracts, stores, and retrieves meaningful fitness episodes
from user conversations. It operates in two scheduled batches:

- Batch 1 (every 2-4 hours): synthesises EpisodeRecords from recent sessions
- Batch 2 (weekly): detects EpisodeArcs and generates behavioral Reflections

The package is designed for integration into a larger coaching platform.
Semantic memories and user profiles are managed by separate systems and
injected into this pipeline as read-only context.
"""

from episodic_memory.models.episode_record import EpisodeRecord
from episodic_memory.models.episode_arc import EpisodeArc
from episodic_memory.models.reflection import Reflection
from episodic_memory.models.engagement_snapshot import EngagementSnapshot
from episodic_memory.models.episode_types import EpisodeType, ArcType, DominantFocus
from episodic_memory.models.session import Session, ConversationTurn

__all__ = [
    "EpisodeRecord",
    "EpisodeArc",
    "Reflection",
    "EngagementSnapshot",
    "EpisodeType",
    "ArcType",
    "DominantFocus",
    "Session",
    "ConversationTurn",
]
