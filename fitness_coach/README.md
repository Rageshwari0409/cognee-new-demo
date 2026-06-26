# fitness_coach

Self-contained Fitness & Diet Coaching package. Drop this folder into any Python project and import `DietCoach`.

## Quick start

```python
from fitness_coach import DietCoach

coach   = DietCoach(api_key="your-gemini-key")  # or set GEMINI_API_KEY env var
session = coach.reset_session()

result = coach.chat("What should I eat before my evening workout?", session)
print(result["reply"])
```

## Setup

```bash
# Install dependencies
pip install -r requirements.txt          # Streamlit + Gemini
# or
pip install -r requirements-demo.txt     # Gemini only (CLI demo)

# Add your API key
cp .env.example .env
# Edit .env and set GEMINI_API_KEY=your-key-here

# Run CLI demo (no API key needed in mock mode)
python demo/gemini_coach.py --mock "What should I eat for lunch?"
python demo/gemini_coach.py --mock --debug "How much protein do I need?"
```

## Package structure

```
fitness_coach/
├── __init__.py              Public API — exports DietCoach
├── coach.py                 All agent logic
│
├── data/
│   ├── common_foods.json    USDA FoodData Central (15 demo foods)
│   ├── system_prompt.txt    Gemini system instruction
│   ├── mock_responses.json  Offline intent templates
│   ├── fitness/             ISSN 2017 / ACSM 2016 workout nutrition
│   ├── reference/           NIH ODS daily intake targets (DRI)
│   ├── safety/              Crisis hotlines + supportive responses
│   └── special/             Vegan, injury recovery, fat loss data
│
├── scripts/
│   └── food_lookup.py       Offline USDA food lookup (stdlib only)
│
├── memory/                  User data — edit to personalise
│   ├── profile.json         Diet constraints, goals, fitness profile
│   ├── memories.md          Learned preferences across sessions
│   └── session.json         Per-session state defaults
│
├── skills/
│   ├── SKILL.md             Agent behaviour, intents, voice rules
│   └── safety.md            Crisis / medical / ED detection rules
│
├── rules/
│   └── diet-coach-demo.mdc  Cursor workspace rule
│
├── docs/
│   └── SPEC.md              Full architecture specification
│
└── tests/
    ├── run_tests.py          Offline automated test suite
    └── test_caller.ipynb     30+ query integration test notebook
```

## Security

| What | How it is protected |
|------|---------------------|
| Gemini API key | Never hardcoded. Read from `GEMINI_API_KEY` env var or `.env` file. `.gitignore` excludes `.env`. |
| User health data in `memory/` | Contains diet constraints, health notes, and PII-adjacent data. Do not commit real user files. See `.gitignore` comments to exclude `memory/` in production. |
| LLM conversation content | Not written to disk. Session state lives in-memory only (no file writes unless explicitly requested). |
| Safety events | Only the event *type* (`"crisis"` / `"medical"`) is stored in session — never the user's message content. |

## Personalising for a real user

Edit `memory/profile.json` to set diet constraints, allergies, and fitness goal:

```json
{
  "diet_constraints": { "allergies": ["peanuts"], "diet_type": ["vegetarian"] },
  "fitness_profile":  { "workout_types": ["strength"], "fitness_goal": "muscle_gain_hypertrophy" }
}
```

Edit `memory/memories.md` to add things the coach should remember:

```markdown
## 2026-06-17
- [explicit] Prefers quick meals under 20 minutes.
- [explicit] Low energy around 3 pm.
```

## Running tests

```bash
# Automated offline suite (no API key needed)
python tests/run_tests.py

# Filter one group
python tests/run_tests.py --group safety

# Full integration notebook (Gemini or mock)
jupyter notebook tests/test_caller.ipynb
```

## Data sources

| File | Source |
|------|--------|
| `data/common_foods.json` | USDA FoodData Central (Foundation Foods) |
| `data/reference/dri_by_age_sex.json` | NIH ODS / National Academies DRI |
| `data/fitness/workout_nutrition.json` | ISSN 2017 + ACSM 2016 |
| `data/fitness/protein_targets.json` | ISSN Position Stand: Protein and Exercise (2017) |
| `data/fitness/hydration_electrolytes.json` | ACSM Position Stand: Fluid Replacement (2007) |
| `data/special/vegan_vegetarian.json` | Academy of Nutrition and Dietetics (2016) + NIH ODS |
| `data/special/injury_recovery.json` | ISSN 2017 + Tipton (Biol Sport, 2015) + NIH ODS |
| `data/special/fat_loss.json` | ISSN Position Stand: Diets and Body Composition (2017) + NIH NIDDK |
| `data/safety/crisis_resources.json` | iCALL/TISS, Vandrevala Foundation, KIRAN (India); 988 Lifeline (US) |
