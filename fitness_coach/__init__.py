"""
fitness_coach — self-contained Fitness & Diet Coaching package.

Drop this entire directory into any project and import:

    from fitness_coach import DietCoach

    coach   = DietCoach(api_key="...")   # or set GEMINI_API_KEY env var
    session = coach.reset_session()
    result  = coach.chat("What should I eat before my workout?", session)
    print(result["reply"])

All data files (USDA, ISSN, NIH, safety) live inside this package under data/.
User memory lives under memory/ and can be edited without touching code.
"""

from fitness_coach.coach import DietCoach

__all__ = ["DietCoach"]
