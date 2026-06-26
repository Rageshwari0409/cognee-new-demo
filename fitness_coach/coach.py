"""
fitness_coach/coach.py — Fitness & Diet Coach public API.

Self-contained module. Drop the entire ``fitness_coach/`` directory into any
project — no other files needed. All data (USDA, ISSN, NIH, safety) lives
under ``fitness_coach/data/``. User memory lives under ``fitness_coach/memory/``.

    data/system_prompt.txt      — LLM system instruction
    data/mock_responses.json    — offline intent → response templates
    data/safety/               — crisis hotlines + supportive responses
    data/reference/            — NIH DRI tables
    data/fitness/              — ISSN/ACSM workout nutrition data
    data/special/              — vegan, injury recovery, fat loss data
    memory/                    — user profile + memories

Streamlit usage::

    from fitness_coach import DietCoach
    import streamlit as st

    coach = DietCoach(api_key=st.secrets.get("GEMINI_API_KEY"))

    if "session" not in st.session_state:
        st.session_state.session = coach.reset_session()

    result = coach.chat(user_input, st.session_state.session)
    # session is mutated in-place — no sync step needed

FastAPI usage::

    from fitness_coach import DietCoach

    coach    = DietCoach(api_key=os.environ["GEMINI_API_KEY"])
    sessions = {}

    @app.post("/chat/{uid}")
    def chat(uid: str, body: ChatRequest):
        sessions.setdefault(uid, coach.reset_session())
        return coach.chat(body.message, sessions[uid])
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Root resolution — package is self-contained; data/ memory/ scripts/ are
# direct siblings of this file inside fitness_coach/
# ---------------------------------------------------------------------------

ROOT: Path = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# File I/O helpers — pure functions, safe to wrap with @st.cache_data
# ---------------------------------------------------------------------------

def _load_json(rel: str, default: Any = None) -> Any:
    """Load JSON relative to project root; return ``default`` on missing file."""
    path = ROOT / rel
    if not path.exists():
        return default if default is not None else {}
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def _load_text(rel: str, default: str = "") -> str:
    """Load plain text relative to project root; return ``default`` on missing file."""
    path = ROOT / rel
    return path.read_text(encoding="utf-8") if path.exists() else default


# ---------------------------------------------------------------------------
# Context assembly
#
# Bundles all data sources for one turn. ``session`` is a live reference so
# writes (e.g. coaching_paused) propagate back to st.session_state immediately.
# ---------------------------------------------------------------------------

def _build_ctx(
    session:          dict,
    profile_override: dict | None = None,
    memories_override: str | None = None,
) -> dict:
    """
    Assemble the full agent context for a single turn.

    All data files are read here. When ``profile_override`` or
    ``memories_override`` are supplied (e.g. from a parent app's user store),
    they replace the file-loaded values so the agent never needs to touch the
    ``memory/`` directory itself.

    Args:
        session:           Mutable dict owned by the caller.
        profile_override:  Full profile dict. When ``None``, loads from file.
        memories_override: Memories text. When ``None``, loads from file.

    Returns:
        Context dict with all data needed for one coaching turn.
    """
    prof = profile_override
    print(f"""Profile:
    {prof}
    """)
    prof_src = "CUSTOM_PROFILE_PASSED"
    if prof is None:
        prof = _load_json("memory/profile.json", {})
        prof_src = "memory/profile.json"

    mems = memories_override
    print(f"""Memories:
    {mems}
    """)   
    mems_src = "CUSTOM_MEMORIES_PASSED"
    if mems is None:
        mems = _load_text("memory/memories.md", "")
        mems_src = "memory/memories.md"

    return {
        "profile":          prof,
        "memories":         mems,
        "_profile_source":  prof_src,
        "_memories_source": mems_src,
        "session":          session,
        "dri":              _load_json("data/reference/dri_by_age_sex.json", {}),
        "supportive":       _load_text("data/safety/supportive_responses.md", ""),
        "crisis_res":       _load_json("data/safety/crisis_resources.json", {}),
        "system_prompt":    _load_text("data/system_prompt.txt", "You are a diet coach."),
        # Fitness data — loaded every turn; small files, negligible overhead
        "workout_nutrition": _load_json("data/fitness/workout_nutrition.json", {}),
        "protein_targets":   _load_json("data/fitness/protein_targets.json", {}),
        "hydration":         _load_json("data/fitness/hydration_electrolytes.json", {}),
        # Special population data
        "vegan_veg":         _load_json("data/special/vegan_vegetarian.json", {}),
        "injury_recovery":   _load_json("data/special/injury_recovery.json", {}),
        "fat_loss":          _load_json("data/special/fat_loss.json", {}),
        # New intent data
        "supplements":       _load_json("data/special/supplements.json", {}),
        "sleep_recovery":    _load_json("data/special/sleep_recovery.json", {}),
        "gut_health":        _load_json("data/special/gut_health.json", {}),
        "meal_prep":         _load_json("data/special/meal_prep.json", {}),
        "mock_responses":    _load_json("data/mock_responses.json", {}),
    }


# ---------------------------------------------------------------------------
# Safety classification — deterministic regex, runs before any LLM call
# ---------------------------------------------------------------------------

_CRISIS_PATTERNS: list[str] = [
    r"\b(kill myself|suicide|suicidal|want to die|end my life)\b",
    r"\b(hurt myself|self[- ]?harm|cut myself)\b",
    r"\b(don'?t deserve to eat|punish myself with food)\b",
]
_MEDICAL_PATTERNS: list[str] = [
    r"\b(chest pain|can'?t breathe|fainting|passed out|anaphylaxis)\b",
    r"\b(severe allergic reaction|blood in stool)\b",
]
_VIOLENCE_PATTERNS: list[str] = [
    r"\b(kill someone|hurt someone|kill him|kill her|kill them|murder|harm others|hurt others)\b",
    r"\b(want to shoot|want to stab|going to attack|assault)\b",
]


def _detect_safety(message: str) -> str | None:
    """
    Classify the message for safety before any coaching logic runs.

    Returns:
        ``"crisis"`` | ``"medical"`` | ``"violence"`` | ``None``.
    """
    lower = message.lower()
    for pattern in _CRISIS_PATTERNS:
        if re.search(pattern, lower):
            return "crisis"
    for pattern in _MEDICAL_PATTERNS:
        if re.search(pattern, lower):
            return "medical"
    for pattern in _VIOLENCE_PATTERNS:
        if re.search(pattern, lower):
            return "violence"
    return None


def _detect_crisis_theme(message: str) -> str:
    """
    Map the crisis message to the most relevant supportive response theme.

    Theme keys match headings in ``data/safety/supportive_responses.md``.

    Returns:
        One of: ``shame_food``, ``family_pressure``, ``guilt_eating``,
        ``self_worth``, ``hopelessness`` (default).
    """
    lower = message.lower()
    if any(w in lower for w in ["deserve", "punish", "bad person", "shouldn't eat"]):
        return "shame_food"
    if any(w in lower for w in ["family", "parents", "pressure", "forced"]):
        return "family_pressure"
    if any(w in lower for w in ["guilty", "guilt", "overate", "binge"]):
        return "guilt_eating"
    if any(w in lower for w in ["ugly", "fat", "body", "look"]):
        return "self_worth"
    return "hopelessness"


def _pick_supportive_line(text: str, theme: str) -> str:
    """
    Extract the first curated line for ``theme`` from ``supportive_responses.md``.

    Parses ``## theme`` headings and ``- line`` entries. Falls back to the first
    available line if the requested theme is absent.

    Args:
        text:  Full file contents of supportive_responses.md.
        theme: Theme key to look up.

    Returns:
        Single supportive sentence, or empty string.
    """
    current = ""
    for line in text.splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
        elif line.startswith("- ") and current == theme:
            return line[2:].strip()
    for line in text.splitlines():
        if line.startswith("- "):
            return line[2:].strip()
    return ""


def _pause_session(session: dict, reason: str) -> None:
    """
    Mark the session as paused. Writes event type only — never message content.

    Args:
        session: Caller-owned session dict.
        reason:  ``"crisis"`` or ``"medical"``.
    """
    session["coaching_paused"]   = True
    session["pause_reason"]      = reason
    session["last_safety_event"] = reason


def _crisis_reply(ctx: dict, message: str) -> str:
    """
    Build the crisis response from hotlines and curated supportive copy.

    All text comes from ``crisis_resources.json`` and ``supportive_responses.md``.
    No strings are hardcoded here. Pauses the session before returning.

    Args:
        ctx:     Full context dict.
        message: Original message (used for theme detection).

    Returns:
        Markdown-safe crisis response string.
    """
    country   = ctx["profile"].get("country", "IN")
    resources = [
        r for r in ctx["crisis_res"].get("resources", [])
        if r.get("country") == country
    ]
    emergency  = resources[0].get("emergency", "112") if resources else "112"
    theme      = _detect_crisis_theme(message)
    supportive = _pick_supportive_line(ctx["supportive"], theme)

    lines = [
        "I'm really glad you said something. What you're feeling is real, "
        "and you deserve support right now.",
        "",
        "I'm a diet coach — I'm not equipped to give you what you need in this moment, "
        "but there are people who are. Please reach out to any of these free services:",
        "",
    ]
    for r in resources:
        note = f" ({r['notes']})" if r.get("notes") else ""
        lines.append(f"- **{r['name']}** — {r['contact']}{note}")
    lines.append(f"- If you're in immediate danger, call **{emergency}**")
    if supportive:
        lines += ["", supportive]
    lines += ["", "I'm here whenever you're ready to talk food — no rush, no pressure."]

    _pause_session(ctx["session"], "crisis")
    return "\n".join(lines)


def _medical_reply(ctx: dict) -> str:
    """
    Build the medical escalation response using the country-specific emergency number.

    Args:
        ctx: Full context dict.

    Returns:
        Short response directing the user to seek medical care.
    """
    country   = ctx["profile"].get("country", "IN")
    emergency = "112"
    for r in ctx["crisis_res"].get("resources", []):
        if r.get("country") == country:
            emergency = r.get("emergency", emergency)
            break
    return (
        "What you're describing needs a real medical professional, not a diet coach. "
        f"Please contact your doctor or the nearest hospital — or call **{emergency}** "
        "if it feels serious right now.\n\n"
        "Once your care team has seen you, I'm happy to talk through any dietary support "
        "they recommend. Take care of yourself first."
    )


# ---------------------------------------------------------------------------
# Food lookup — importlib, no sys.path mutation (safe for Streamlit Cloud)
# ---------------------------------------------------------------------------

def _load_food_lookup_module() -> Any:
    """
    Load ``scripts/food_lookup.py`` via importlib from its absolute path.

    Avoids ``sys.path`` mutation which is unsafe in Streamlit Cloud's
    multi-worker environment.

    Returns:
        Loaded module, or ``None`` if unavailable.
    """
    path = ROOT / "scripts" / "food_lookup.py"
    if not path.exists():
        return None
    try:
        spec   = importlib.util.spec_from_file_location("food_lookup", path)
        module = importlib.util.module_from_spec(spec)   # type: ignore[arg-type]
        spec.loader.exec_module(module)                  # type: ignore[union-attr]
        return module
    except Exception:
        return None


def _food_lookup(term: str) -> dict | None:
    """
    Look up a specific food term in the local USDA cache.

    Args:
        term: Food name to look up, e.g. ``"lentils"``.

    Returns:
        USDA result dict or ``None`` on any failure.
    """
    module = _load_food_lookup_module()
    if module is None:
        return None
    try:
        return module.lookup(term)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# DRI (Dietary Reference Intakes) lookup
# ---------------------------------------------------------------------------

_DRI_MAP: dict[str, tuple[str, str]] = {
    "iron":      ("iron_mg_rda",     "mg"),
    "fiber":     ("fiber_g_ai",      "g"),
    "protein":   ("protein_g_rda",   "g"),
    "potassium": ("potassium_mg_ai", "mg"),
}


def _dri_line(nutrient: str, ctx: dict) -> str:
    """
    Return a plain-English DRI reference sentence for a given nutrient.

    Uses ``adults_19_59`` group. Defaults to female values (higher iron RDA)
    when sex is not stated — safer default for a vegetarian coaching context.

    Args:
        nutrient: Lowercase nutrient name (iron / fiber / protein / potassium).
        ctx:      Full context dict containing loaded DRI data.

    Returns:
        Citation sentence, e.g. ``"Daily iron target: about 18 mg (NIH ODS)."``
        Empty string for unsupported nutrients.
    """
    group   = ctx["dri"].get("adults_19_59", {})
    sex     = ctx["profile"].get("demographics", {}).get("sex", "prefer_not_to_say")
    sex_key = "female" if sex in ("female", "prefer_not_to_say") else "male"
    source  = "NIH ODS / National Academies DRI"

    if nutrient not in _DRI_MAP:
        return ""
    field, unit = _DRI_MAP[nutrient]
    value = group.get(field, {}).get(sex_key)
    return f"Daily {nutrient} target for most adults: about {value} {unit} ({source})." if value else ""


def _get_dri(message: str, ctx: dict) -> str:
    """Return the DRI line for the first nutrient keyword found in the message."""
    lower = message.lower()
    for nutrient in _DRI_MAP:
        if nutrient in lower:
            return _dri_line(nutrient, ctx)
    return ""


# ---------------------------------------------------------------------------
# Mock response engine — reads from data/mock_responses.json, no hardcoded text
# ---------------------------------------------------------------------------

def _render_template(template: str, food_data: dict | None, ctx: dict) -> str:
    """
    Fill placeholders in a mock response template.

    Supported placeholders:
        ``{iron_mg}``, ``{protein_g}``, ``{fiber_g}`` — from USDA food lookup result.
        ``{dri_iron}``, ``{dri_fiber}``, ``{dri_protein}`` — from NIH DRI tables.

    Args:
        template:  Raw template string from ``mock_responses.json``.
        food_data: USDA lookup result dict, or ``None``.
        ctx:       Full context dict.

    Returns:
        Rendered reply string with all placeholders substituted.
    """
    values: dict[str, str] = {}

    if food_data and food_data.get("ok"):
        n = food_data["food"].get("nutrients", {})
        values["iron_mg"]   = str(n.get("iron_mg",   ""))
        values["protein_g"] = str(n.get("protein_g", ""))
        values["fiber_g"]   = str(n.get("fiber_g",   ""))

    for nutrient in _DRI_MAP:
        values[f"dri_{nutrient}"] = _dri_line(nutrient, ctx)

    # Profile-based placeholders so mock default adapts to the user
    prof    = ctx.get("profile", {})
    cuisines = prof.get("preferences", {}).get("cuisines_liked", [])
    diet_type = ", ".join(prof.get("diet_constraints", {}).get("diet_type", [])) or "vegetarian"
    values["cuisines"]   = " and ".join(cuisines) if cuisines else "Indian and Mediterranean"
    values["diet_type"]  = diet_type
    values["goal"]       = prof.get("goals", {}).get("primary", "general health").replace("_", " ")
    values["max_prep"]   = str(prof.get("preferences", {}).get("max_prep_minutes", 20))

    try:
        return template.format_map(values)
    except KeyError:
        return template


def _mock_reply(message: str, ctx: dict) -> str:
    """
    Return an offline coaching response by matching message against intent rules
    defined in ``data/mock_responses.json``.

    No text is hardcoded in this function — all copy comes from the JSON file.
    To change wording, edit ``data/mock_responses.json`` directly.

    Intent matching rules per entry in ``intents``:
      - ``match_any``: reply if any keyword is found in the message.
      - ``match_all_secondary``: ALL of these must be present for this intent
        to trigger (used to distinguish "iron food" from just "iron").
      - ``food_lookup``: if set, look up this food term and pass to template.

    Args:
        message: User message text.
        ctx:     Full context dict containing loaded mock_responses.json.

    Returns:
        Rendered reply string.
    """
    responses = ctx["mock_responses"]

    # Coaching paused — check-in only
    if ctx["session"].get("coaching_paused"):
        return responses.get("paused", "How are you doing today?")

    lower = message.lower()

    for intent in responses.get("intents", []):
        primary   = intent.get("match_any", [])
        secondary = intent.get("match_all_secondary", [])

        primary_hit   = any(kw in lower for kw in primary)
        secondary_hit = all(kw in lower for kw in secondary) if secondary else True

        if not (primary_hit and secondary_hit):
            continue

        food_term = intent.get("food_lookup")
        food_data = _food_lookup(food_term) if food_term else None
        template  = intent.get("reply", "")
        return _render_template(template, food_data, ctx)

    # ── Fitness-aware fallback ────────────────────────────────────────────────
    # When a fitness data block was injected (sleep, gut, supplements, etc.)
    # but no mock intent matched the exact phrasing, return a domain-specific
    # stub rather than the generic food default, so the answer stays on-topic.
    lower_fitness_stubs: dict[str, str] = {
        "sleep":      "For overnight recovery, 30–40 g of slow-digesting protein 30–60 minutes before bed is the evidence-backed approach (Res et al 2012). Lactose-free Greek yogurt with a banana works well — casein protein from the yogurt, tryptophan from the banana. Avoid caffeine after 2 pm and keep your last big meal at least 2 hours before sleep.\n\nIs the issue falling asleep, staying asleep, or not feeling rested despite enough hours?",
        "supplement": "The supplements with the strongest evidence for your training type are creatine (3–5 g/day, Grade A — ISSN 2017) and caffeine (3–6 mg/kg before training). As a vegetarian your creatine baseline is lower, so you'd likely see a bigger benefit than an omnivore. Protein powder fills gaps when whole food isn't enough — a pea+rice blend gives a complete amino acid profile.\n\nWhat's the specific gap you're trying to fill?",
        "gut":        "GI distress during training is almost always a timing issue. The main culprits for vegetarians: dal and chickpeas eaten too close to training (high fibre + gas), and any dairy for lactose-intolerant athletes. Keep the pre-training meal to low-fibre, low-fat foods in the 90-minute window — banana, plain rice, tofu are all safe. Save the legumes for dinner.\n\nIs the discomfort happening before, during, or after the session?",
        "bulk":       "A lean bulk needs a 200–300 kcal surplus above maintenance — anything higher just adds fat faster. Protein stays at 1.6–2.2 g/kg/day; bump carbs to 5–7 g/kg on training days (ISSN 2017). For vegetarians the hardest part is hitting the surplus without eating endlessly — calorie-dense foods like avocado, seeds, olive oil, and full-fat lactose-free yogurt help.\n\nAre you currently tracking calories, or eating by feel?",
        "meal":       "A 30-minute Sunday prep covers 4 days of lunches and dinners. Start a pot of lentil dal (25 min unattended), cook a batch of rice or quinoa alongside, wash and chop vegetables while both simmer. That gives you 5 dal portions and a carb base — 3-minute assembly meals all week. Overnight oat jars take 2 minutes and cover every breakfast.\n\nHow many days are you trying to cover?",
    }
    fitness_block = _get_fitness_context(message, ctx)
    for stub_key, stub_reply in lower_fitness_stubs.items():
        if stub_key in fitness_block.lower():
            return stub_reply

    # Generic default
    default = responses.get("default", "")
    return _render_template(default, None, ctx)


# ---------------------------------------------------------------------------
# Gemini call — system prompt loaded from data/system_prompt.txt
# ---------------------------------------------------------------------------

def _explain_context(message: str, ctx: dict, food_data: dict | None, mode: str, matched_topics: list[str] | None = None) -> dict:
    """
    Build a human-readable summary of which data was used to answer this turn.

    Intended for ``--debug`` output and the ``context_used`` key in every result.
    No extra I/O — all information is derived from the already-built ``ctx`` and
    the same keyword tables used by the real pipeline, so there is no overhead.

    Args:
        message:   Raw user message.
        ctx:       Full context dict from ``_build_ctx``.
        food_data: Result from ``_food_lookup``, or ``None``.
        mode:      ``"mock"``, ``"gemini"``, or ``"safety"``.

    Returns:
        Dict with keys: mode, fitness_files_used, mock_intent_matched,
        nutrients_detected, food_lookup, files_always_loaded.
    """
    lower = message.lower()

    # ── Fitness sections that were injected ──────────────────────────────────
    fitness_sections: list[str] = []
    section_to_file: dict[str, str] = {
        "pre_workout":  "data/fitness/workout_nutrition.json  [pre_workout section]",
        "post_workout": "data/fitness/workout_nutrition.json  [post_workout section]",
        "protein":      "data/fitness/protein_targets.json    [by_goal section]",
        "hydration":    "data/fitness/hydration_electrolytes.json",
        "rest_day":     "data/fitness/workout_nutrition.json  [by_workout_type.rest_day]",
        "vegan_veg":      "data/special/vegan_vegetarian.json   (AND 2016 / NIH ODS)",
        "injury":         "data/special/injury_recovery.json    (ISSN 2017 / Tipton 2015)",
        "fat_loss":       "data/special/fat_loss.json           (ISSN 2017 / NIH NIDDK)",
        "bulking":        "data/fitness/workout_nutrition.json  [body_composition_phases.bulking]",
        "supplements":    "data/special/supplements.json        (ISSN evidence grades)",
        "sleep_recovery": "data/special/sleep_recovery.json     (ISSN 2017 / Halson 2014)",
        "gut_health":     "data/special/gut_health.json         (ISSN 2019 / ACSM 2016)",
        "meal_prep":      "data/special/meal_prep.json          (AND/USDA practical guidance)",
    }
    if matched_topics is not None:
        fitness_sections = list(matched_topics)
    else:
        for section, keywords in _FITNESS_KEYWORDS.items():
            if any(kw in lower for kw in keywords):
                fitness_sections.append(section)

    # Workout-type rows are always injected from the profile
    workout_types = ctx["profile"].get("fitness_profile", {}).get("workout_types", [])
    for wt in workout_types:
        key = f"workout_type:{wt}"
        if key not in fitness_sections:
            fitness_sections.append(key)
            section_to_file[key] = f"data/fitness/workout_nutrition.json  [by_workout_type.{wt}]"

    fitness_files_used = list(dict.fromkeys(
        section_to_file[s] for s in fitness_sections if s in section_to_file
    ))

    # ── Mock intent that matched ─────────────────────────────────────────────
    matched_intent: str | None = None
    if mode == "mock":
        for intent in ctx["mock_responses"].get("intents", []):
            primary   = intent.get("match_any", [])
            secondary = intent.get("match_all_secondary", [])
            if any(kw in lower for kw in primary):
                if all(kw in lower for kw in secondary) if secondary else True:
                    matched_intent = intent.get("id", "unknown")
                    break
        if matched_intent is None:
            matched_intent = "default"

    # ── DRI nutrients detected ───────────────────────────────────────────────
    nutrients_detected = [n for n in _DRI_MAP if n in lower]

    # ── Food lookup ──────────────────────────────────────────────────────────
    food_summary: str | None = None
    if food_data and food_data.get("ok"):
        food_summary = (
            f"{food_data['food'].get('name', '?')}  "
            f"(data/common_foods.json via scripts/food_lookup.py)"
        )

    # ── Memories preview — first 4 non-empty lines ───────────────────────────
    memories_text = ctx.get("memories", "")
    memories_lines = [l.strip() for l in memories_text.splitlines() if l.strip()]
    memories_preview = memories_lines[:4]
    if len(memories_lines) > 4:
        memories_preview.append(f"... (+{len(memories_lines) - 4} more lines)")

    # ── Profile summary for display ──────────────────────────────────────────
    profile_display = _build_profile_summary(ctx)

    profile_src  = ctx.get("_profile_source",  "memory/profile.json")
    memories_src = ctx.get("_memories_source", "memory/memories.md")

    return {
        "mode":                mode,
        "files_always_loaded": [
            f"profile  [{profile_src}]",
            f"memories [{memories_src}]",
            "session  [in-memory]",
            "data/mock_responses.json",
            "data/system_prompt.txt",
        ],
        "profile_source":      profile_src,
        "memories_source":     memories_src,
        "memories_in_prompt":  memories_preview,
        "profile_in_prompt":   profile_display,
        "fitness_files_used":  fitness_files_used or ["(none — no fitness keywords detected)"],
        "mock_intent_matched": matched_intent,
        "nutrients_detected":  nutrients_detected or ["(none)"],
        "dri_file_used":       "data/reference/dri_by_age_sex.json" if nutrients_detected else "(not loaded this turn)",
        "food_lookup":         food_summary or "(no specific food matched)",
    }


_FITNESS_KEYWORDS: dict[str, list[str]] = {
    "pre_workout":   ["before workout", "pre workout", "pre-workout", "before gym", "before training", "before my workout", "before evening workout", "before morning workout"],
    "post_workout":  ["after workout", "post workout", "post-workout", "after gym", "after training", "recovery", "after my workout", "after my evening workout", "after my morning workout", "after session"],
    "protein":       ["protein", "muscle", "strength", "hypertrophy", "bulk"],
    "hydration":     ["hydration", "electrolyte", "cramp", "dehydrated", "water intake"],
    "rest_day":      ["rest day", "off day", "not training", "no workout"],
    "vegan_veg":     ["vegan", "vegetarian", "plant based", "plant-based", "b12", "iron absorption",
                      "vitamin b12", "dairy free", "no meat", "no dairy"],
    "injury":        ["injury", "injured", "recovering", "sprain", "fracture", "tendon", "ligament",
                      "healing", "surgery", "immobilised", "immobilized", "broken", "torn"],
    "fat_loss":      ["lose weight", "weight loss", "fat loss", "lose fat", "cut calories",
                      "calorie deficit", "slim down", "reduce weight", "body fat"],
    "bulking":       ["bulk", "bulking", "gain muscle", "gain mass", "lean bulk", "eat more to grow",
                      "caloric surplus", "muscle building phase"],
    "supplements":   ["supplement", "creatine", "protein powder", "pre-workout", "beta alanine",
                      "caffeine supplement", "should i take", "vitamin d supplement", "magnesium supplement"],
    "sleep_recovery":["sleep", "can't sleep", "wake up sore", "overnight recovery", "before bed",
                      "pre sleep", "tired after training", "not recovering"],
    "gut_health":    ["bloated", "bloating", "stomach cramp", "nausea", "gi distress", "gut health",
                      "digestive", "runner's trots", "stomach issues", "bowel"],
    "meal_prep":     ["meal prep", "batch cook", "prep for the week", "sunday prep",
                      "cook ahead", "what to prep", "weekly meals"],
}


def _get_fitness_context(message: str, ctx: dict, matched_topics: list[str] | None = None) -> str:
    """
    Return a compact, focused fitness data block relevant to the user's message.

    Avoids injecting all three fitness files on every turn — only loads the
    section(s) most relevant to what was asked. This keeps prompt length short
    and latency low.

    Args:
        message: User message text.
        ctx:     Full context dict (must include workout_nutrition, protein_targets, hydration).
        matched_topics: Pre-classified list of relevant topics.

    Returns:
        Formatted string summarising the relevant fitness guidance, or empty string
        if the message has no fitness intent.
    """
    lower   = message.lower()
    parts: list[str] = []

    if matched_topics is None:
        matched_topics = []
        for topic, keywords in _FITNESS_KEYWORDS.items():
            if any(kw in lower for kw in keywords):
                matched_topics.append(topic)

    if "pre_workout" in matched_topics:
        pre = ctx["workout_nutrition"].get("pre_workout", {})
        parts.append(
            f"PRE-WORKOUT TIMING (ISSN/ACSM): "
            f"2-4 hr window — {pre.get('timing_windows', [{}])[0].get('carbs_g_per_kg', '1-4')} g/kg carbs, "
            f"20-40 g protein. 30-60 min window — lighter snack only. "
            f"Vegetarian note: {pre.get('vegetarian_notes', '')}"
        )

    if "post_workout" in matched_topics:
        post = ctx["workout_nutrition"].get("post_workout", {})
        parts.append(
            f"POST-WORKOUT (ISSN 2017): {post.get('protein_g_per_serving', '20-40')} g protein within "
            f"{post.get('timing', '2 hours')}. "
            f"Carbs: {post.get('carbs_g_per_kg', '0.8-1.2')} g/kg. "
            f"Leucine note: {post.get('leucine_note', '')}"
        )

    if "protein" in matched_topics:
        profile_goal = ctx["profile"].get("fitness_profile", {}).get("fitness_goal", "general_fitness_maintenance")
        goal_data    = ctx["protein_targets"].get("by_goal", {}).get(profile_goal, {})
        veg_note     = ctx["protein_targets"].get("vegetarian_plant_based_notes", {})
        if goal_data:
            parts.append(
                f"PROTEIN TARGET for '{profile_goal.replace('_', ' ')}' (ISSN 2017): "
                f"{goal_data.get('range_g_per_kg', '1.4-2.0')} g/kg/day. "
                f"Per meal: {goal_data.get('per_meal_g', '20-30')} g. "
                f"Vegetarian note: {veg_note.get('leucine_gap', '')}"
            )

    if "hydration" in matched_topics:
        h = ctx["hydration"].get("hydration_guidelines", {})
        e = ctx["hydration"].get("key_electrolytes", {})
        parts.append(
            f"HYDRATION (ACSM): Pre: {h.get('before_exercise', {}).get('amount', '500ml 2hr before')}. "
            f"During >60min: electrolytes. "
            f"Potassium RDA: {e.get('potassium', {}).get('rda_mg', 2600)} mg. "
            f"Magnesium RDA: {e.get('magnesium', {}).get('rda_mg', {}).get('female', 310)} mg (female)."
        )

    workout_types = ctx["profile"].get("fitness_profile", {}).get("workout_types", [])
    if workout_types:
        by_type = ctx["workout_nutrition"].get("by_workout_type", {})
        relevant = {wt: by_type[wt] for wt in workout_types if wt in by_type}
        if relevant:
            for wt, data in relevant.items():
                parts.append(
                    f"WORKOUT TYPE '{wt}' (ISSN/ACSM): "
                    f"Protein {data.get('protein_g_per_kg_per_day', '?')} g/kg/day. "
                    f"Carbs {data.get('carbs_g_per_kg_per_day', '?')} g/kg/day. "
                    f"Key nutrients: {', '.join(data.get('key_nutrients', []))}."
                )

    # ── Vegan / vegetarian special data ─────────────────────────────────────
    if "vegan_veg" in matched_topics:
        vv     = ctx["vegan_veg"]
        risk   = vv.get("nutrients_at_risk", {})
        checklist = vv.get("quick_reference_checklist", [])
        combos = vv.get("protein_completeness", {}).get("complementary_combos", [])
        parts.append(
            "VEGAN/VEGETARIAN NUTRITION (AND 2016 / NIH ODS):\n"
            f"  Nutrients at risk: {', '.join(risk.keys())}.\n"
            f"  Quick checklist: {'; '.join(checklist[:4])}.\n"
            f"  Complete protein combos: {'; '.join(combos[:3])}."
        )

    # ── Injury recovery data ─────────────────────────────────────────────────
    if "injury" in matched_topics:
        ir       = ctx["injury_recovery"]
        key_n    = ir.get("key_nutrients_for_recovery", {})
        acute    = ir.get("recovery_phases", {}).get("acute_inflammation", {})
        parts.append(
            "INJURY RECOVERY NUTRITION (ISSN 2017 / Tipton 2015 / NIH ODS):\n"
            f"  Key principle: {ir.get('key_principle', '')}.\n"
            f"  Acute phase: {'; '.join(acute.get('do', [])[:3])}.\n"
            f"  Protein target: {key_n.get('protein', {}).get('target_g_per_kg', '1.6-2.5')} g/kg. "
            f"  Vitamin C: {key_n.get('vitamin_c', {}).get('target_mg_per_day', '200-500')} mg/day for collagen. "
            f"  Zinc: {key_n.get('zinc', {}).get('target_mg_per_day', '8-11')} mg/day for wound healing."
        )

    # ── Fat loss data ────────────────────────────────────────────────────────
    if "fat_loss" in matched_topics:
        fl      = ctx["fat_loss"]
        kp      = fl.get("key_principles", {})
        sat     = fl.get("satiety_strategies", {})
        deficit = kp.get("caloric_deficit", {})
        protein = kp.get("protein_during_fat_loss", {})
        parts.append(
            "FAT LOSS NUTRITION (ISSN 2017 / NIH NIDDK):\n"
            f"  Deficit: {deficit.get('safe_range_kcal_per_day', '300-500')} kcal/day below maintenance. "
            f"  Risk of aggressive deficit: {deficit.get('aggressive_deficit_risk', '')}.\n"
            f"  Protein: {protein.get('target_g_per_kg', '1.8-2.7')} g/kg — {protein.get('why', '')}.\n"
            f"  Satiety: fill half plate with non-starchy veg; target {sat.get('fiber', {}).get('target_g_per_day', 25)} g fiber/day."
        )

    # ── Bulking / body composition phase ────────────────────────────────────
    if "bulking" in matched_topics:
        bcp   = ctx["workout_nutrition"].get("body_composition_phases", {})
        bulk  = bcp.get("bulking", {})
        parts.append(
            "BULKING / LEAN MASS GAIN (ISSN 2017):\n"
            f"  Surplus: {bulk.get('caloric_surplus_kcal', '200-300')} kcal/day above maintenance.\n"
            f"  Protein: {bulk.get('protein_g_per_kg', '1.6-2.2')} g/kg/day. "
            f"  Carbs (training days): {bulk.get('carbs_g_per_kg', '5-7')} g/kg.\n"
            f"  Vegetarian note: {bulk.get('vegetarian_note', '')}"
        )

    # ── Supplements ──────────────────────────────────────────────────────────
    if "supplements" in matched_topics:
        sups   = ctx["supplements"].get("supplements", {})
        rule   = ctx["supplements"].get("rule", "")
        ask    = ctx["supplements"].get("ask_before_recommending", [])
        # Summarise top Grade A supplements concisely
        grade_a = {k: v for k, v in sups.items() if v.get("evidence_grade") == "A"}
        summary = "; ".join(
            f"{k.replace('_', ' ')}: {v.get('dose', '?')}"
            for k, v in list(grade_a.items())[:3]
        )
        parts.append(
            f"SUPPLEMENTS (ISSN evidence-graded): {rule}\n"
            f"  Grade A: {summary}.\n"
            f"  Always ask first: {'; '.join(ask[:2])}."
        )

    # ── Sleep & overnight recovery ───────────────────────────────────────────
    if "sleep_recovery" in matched_topics:
        sr     = ctx["sleep_recovery"]
        pre    = sr.get("pre_sleep_nutrition", {})
        prot   = pre.get("protein_before_bed", {})
        avoid  = pre.get("avoid_before_bed", [])
        parts.append(
            "SLEEP & RECOVERY NUTRITION (ISSN 2017 / Halson 2014 / Res 2012):\n"
            f"  Pre-sleep protein: {prot.get('dose', '30-40g')} — {prot.get('why', '')}.\n"
            f"  Vegetarian options: {', '.join(prot.get('vegetarian_sources', [])[:2])}.\n"
            f"  Avoid before bed: {'; '.join(avoid[:3])}."
        )

    # ── Gut health & GI distress ─────────────────────────────────────────────
    if "gut_health" in matched_topics:
        gh     = ctx["gut_health"]
        causes = gh.get("gi_distress_during_exercise", {}).get("main_causes", [])
        timing = gh.get("timing_rules", {})
        parts.append(
            "GUT HEALTH / GI DISTRESS (ISSN 2019 / ACSM 2016):\n"
            f"  Main causes: {'; '.join(causes[:3])}.\n"
            f"  1 hr before training: {timing.get('1_hour_before', '')}.\n"
            f"  Avoid pre-training: {', '.join(timing.get('foods_to_avoid_pre_training', [])[:4])}."
        )

    # ── Meal prep ────────────────────────────────────────────────────────────
    if "meal_prep" in matched_topics:
        mp       = ctx["meal_prep"]
        plan     = mp.get("30_min_sunday_plan", {})
        staples  = mp.get("vegetarian_prep_staples", {}).get("protein_batch", [])
        top_prep = [s["item"] for s in staples[:3]]
        parts.append(
            "MEAL PREP GUIDE:\n"
            f"  Core principle: {mp.get('core_principles', [''])[0]}.\n"
            f"  Top batch protein options: {', '.join(top_prep)}.\n"
            f"  30-min Sunday plan: {plan.get('description', '')}."
        )

    return "\n".join(parts)


def _build_profile_summary(ctx: dict) -> str:
    """
    Build a one-line profile string from the context for use in system prompt and debug.

    Args:
        ctx: Full context dict (must contain ``profile`` key).

    Returns:
        Human-readable profile summary string.
    """
    p  = ctx["profile"]
    dc = p.get("diet_constraints", {})
    pr = p.get("preferences", {})
    gs = p.get("goals", {})
    fp = p.get("fitness_profile", {})

    fitness_summary = ""
    if fp:
        fitness_summary = (
            f" Workout types: {', '.join(fp.get('workout_types', [])) or 'none'}. "
            f"Trains {fp.get('workout_days_per_week', '?')}x/week, "
            f"typical workout time: {fp.get('typical_workout_time', 'unspecified')}. "
            f"Fitness goal: {fp.get('fitness_goal', 'general').replace('_', ' ')}. "
            f"Body weight: {fp.get('body_weight_kg') or 'not shared'} kg. "
            f"Height: {fp.get('height_cm') or 'not shared'} cm. "
            f"Workout level: {p.get('workout_level', 'unspecified')}. "
            f"Training location: {p.get('training_location', 'unspecified')}. "
            f"Workout duration limit: {p.get('workout_duration_limit', 'unspecified')}."
        )

    raw_answers = ""
    if p.get("raw_onboarding_answers"):
        raw_answers = "\nDetailed Onboarding: " + ", ".join(f"[{q}] {r}" for q, r in p["raw_onboarding_answers"].items())

    return (
        f"Life stage: {p.get('life_stage', 'unspecified')}. "
        f"Diet: {', '.join(dc.get('diet_type', [])) or 'none'}. "
        f"Allergies (HARD BLOCK): {', '.join(dc.get('allergies', [])) or 'none'}. "
        f"Intolerances: {', '.join(dc.get('intolerances', [])) or 'none'}. "
        f"Max prep: {pr.get('max_prep_minutes', 20)} min. "
        f"Cuisines liked: {', '.join(pr.get('cuisines_liked', []))}. "
        f"Goal: {gs.get('primary', 'general health')}. "
        f"Injuries/Health conditions: {p.get('injuries', 'none')}."
        f"{fitness_summary}"
        f"{raw_answers}"
    )


def _render_system_prompt(ctx: dict) -> str:
    """
    Fill ``{profile_summary}`` and ``{memories}`` placeholders in the system prompt template.

    ``data/system_prompt.txt`` carries explicit ``{profile_summary}`` and ``{memories}``
    markers so it is obvious in the file what user context gets injected. This function
    performs that substitution before the prompt is sent to Gemini.

    Args:
        ctx: Full context dict (must contain ``profile`` and ``memories`` keys).

    Returns:
        Rendered system prompt string ready for ``system_instruction``.
    """
    # When memory is disabled, use a generic coach prompt — no personalization.
    if ctx["profile"].get("_no_memory"):
        return (
            "You are a general fitness and diet coach. You do NOT know anything about "
            "the person you are talking to — no name, no history, no injuries, no prior "
            "conversations. Give helpful, evidence-based fitness and nutrition advice as "
            "if speaking to a complete stranger. Do not assume or fabricate any personal "
            "details. Under 180 words. End with one genuine question."
        )

    template = ctx["system_prompt"]
    profile_summary = _build_profile_summary(ctx)
    memories        = ctx["memories"]
    try:
        return template.format(
            profile_summary=profile_summary,
            memories=memories,
        )
    except KeyError:
        # Template missing placeholder — fall back to raw template (never blocks a reply)
        return template


def _build_prompt(message: str, ctx: dict, matched_topics: list[str] | None = None) -> str:
    """
    Assemble the per-turn user-turn prompt for Gemini.

    Profile and memories are already in the rendered system instruction
    (see ``_render_system_prompt``). This function only adds turn-specific
    dynamic data: food lookup, DRI targets, fitness context, and the message.

    Args:
        message: Current user message.
        ctx:     Full context dict.

    Returns:
        User-turn prompt string (``contents`` field sent to Gemini).
    """
    pause_note = (
        "NOTE: Coaching is paused due to a recent safety event. "
        "Only offer a gentle check-in — no meal plans.\n\n"
        if ctx["session"].get("coaching_paused") else ""
    )

    history_str = ""
    chat_history = ctx["session"].get("chat_history", [])
    if chat_history:
        turns = []
        # Exclude the current user message if it is at the end of the history to avoid duplicate input
        history_to_use = chat_history[:-1] if chat_history[-1].get("text") == message or chat_history[-1].get("content") == message else chat_history
        for msg in history_to_use[-5:]:  # Use last 5 turns for context window
            role = "User" if msg.get("role") == "user" else "Assistant"
            text_val = msg.get("text") or msg.get("content") or ""
            if text_val:
                turns.append(f"{role}: {text_val}")
        if turns:
            history_str = "CONVERSATION HISTORY:\n" + "\n".join(turns) + "\n\n"

    food          = _food_lookup(message)
    dri           = _get_dri(message, ctx)
    fitness_block = _get_fitness_context(message, ctx, matched_topics)

    return (
        f"{pause_note}"
        f"{history_str}"
        f"FOOD_LOOKUP (use these numbers only):\n"
        f"{json.dumps(food, indent=2) if food else 'null'}\n\n"
        f"DRI_REFERENCE (use for daily targets only):\n{dri or 'n/a'}\n\n"
        f"FITNESS_DATA (use for workout timing, protein targets, hydration — cite sources within):\n"
        f"{fitness_block or 'n/a'}\n\n"
        f"USER MESSAGE: {message}"
    )


def _call_gemini(prompt: str, system_prompt: str, api_key: str, model: str) -> str:
    """
    Send prompt to Gemini and return the text response.

    The system instruction is passed in as ``system_prompt`` (loaded from
    ``data/system_prompt.txt``) rather than hardcoded here.

    Args:
        prompt:        Assembled user-turn prompt from ``_build_prompt``.
        system_prompt: LLM instruction text loaded from file.
        api_key:       Gemini API key.
        model:         Gemini model ID.

    Returns:
        Stripped response text.

    Raises:
        RuntimeError: On import failure, bad key, or empty response.
                      ``DietCoach.chat`` catches this and falls back to mock.
    """
    try:
        from google import genai  # type: ignore
    except ImportError as exc:
        raise RuntimeError("Run: pip install google-genai") from exc

    client   = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config={
            "system_instruction": system_prompt,
            "temperature":        0.4,
            "max_output_tokens":  512,
        },
    )
    text = getattr(response, "text", None) or ""
    if not text.strip():
        raise RuntimeError("Empty response from Gemini")
    return text.strip()


# ---------------------------------------------------------------------------
# Citation extractor
# ---------------------------------------------------------------------------

_SOURCE_PATTERNS: list[str] = [
    r"USDA FoodData Central[^).\n]*",
    r"NIH ODS[^).\n]*",
    r"National Academies DRI[^).\n]*",
    r"according to USDA[^).\n]*",
    r"according to NIH[^).\n]*",
]


def _extract_sources(reply: str) -> list[str]:
    """
    Parse citation strings from a reply for display in the UI source panel.

    Args:
        reply: Full coaching reply string.

    Returns:
        Deduplicated list of citation strings found in the reply.
    """
    sources: list[str] = []
    for pattern in _SOURCE_PATTERNS:
        for match in re.findall(pattern, reply, re.IGNORECASE):
            cleaned = match.strip(" ,.")
            if cleaned and cleaned not in sources:
                sources.append(cleaned)
    return sources


# ---------------------------------------------------------------------------
# Skill-relevance gate
#
# Checked once at the start of every turn. If the message has no diet or
# fitness signal the agent declines immediately — no data files are loaded,
# no LLM call is made, no cost is incurred. Safety patterns always override
# this gate (handled earlier in chat()).
# ---------------------------------------------------------------------------

_SKILL_KEYWORDS: frozenset[str] = frozenset([
    # food & eating
    "eat", "food", "meal", "diet", "nutrition", "calorie", "snack",
    "breakfast", "lunch", "dinner", "recipe", "cook", "ingredient",
    "portion", "serving", "plate", "bowl",
    # macros & nutrients
    "protein", "carb", "carbohydrate", "fat", "fibre", "fiber",
    "vitamin", "mineral", "iron", "calcium", "zinc", "b12", "omega",
    "magnesium", "potassium", "sodium", "electrolyte",
    # specific foods
    "lentil", "dal", "chickpea", "tofu", "quinoa", "oat", "spinach",
    "banana", "rice", "egg", "yogurt", "cheese", "milk", "bread",
    "fruit", "vegetable", "salad", "curry", "roti", "dosa",
    # fitness & workout
    "workout", "exercise", "gym", "training", "train", "muscle",
    "strength", "endurance", "hiit", "run", "running", "cycling",
    "yoga", "lifting", "weight", "reps", "sets", "cardio",
    # goals & body composition
    "lose weight", "gain weight", "bulk", "cut", "fat loss",
    "body fat", "lean", "mass", "weight loss",
    # supplement & recovery
    "supplement", "creatine", "protein powder", "pre-workout",
    "post workout", "recovery", "hydration", "water intake", "sleep",
    # health conditions relevant to diet
    "allergy", "intolerance", "lactose", "gluten", "vegan",
    "vegetarian", "plant based", "injury", "bloat", "cramp",
    "energy", "tired", "fatigue", "digest",
])

def _classify_query_llm(message: str, api_key: str, model: str) -> dict:
    """
    Call Gemini to dynamically classify the user's query for:
    1. Scope check (is it related to fitness, workouts, sports science, diet, nutrition, injury recovery, or health?)
    2. Relevant topics to inject context for.
    """
    try:
        from google import genai
    except ImportError:
        return {"is_skill_query": False, "matched_topics": [], "matched_keyword": ""}

    prompt = f"""Analyze the following user query for an AI Workout Coach.

User Query: "{message}"

Determine:
1. Is this query related to fitness, workouts, gym, sports science, training, diet, nutrition, calorie/meal tracking, injury recovery, physical health, or supplements? Respond true or false.
2. Which of the following specific topics are relevant to the query? Select all that apply:
   - "pre_workout" (eating/drinking/preparing before training)
   - "post_workout" (eating/drinking/recovering after training)
   - "protein" (protein intake, muscle gain, hypertrophy)
   - "hydration" (water, electrolytes, cramps, dehydration)
   - "rest_day" (off days, rest days, non-training guidance)
   - "vegan_veg" (vegan, vegetarian, plant-based diets, B12, iron absorption)
   - "injury" (injury recovery, healing, joint pain, sprains, tendon/ligament care)
   - "fat_loss" (weight loss, calorie deficit, body fat reduction)
   - "bulking" (caloric surplus, muscle mass building phase)
   - "supplements" (creatine, protein powder, vitamins/minerals, caffeine)
   - "sleep_recovery" (sleep quality, overnight recovery, soreness, fatigue)
   - "gut_health" (bloating, GI distress, stomach cramps, digestion issues)
   - "meal_prep" (meal planning, batch cooking, prepping weekly meals)

Return the output strictly as a JSON object matching this schema (with no markdown formatting or extra text outside the JSON):
{{
  "is_skill_query": true,
  "matched_topics": ["pre_workout", "supplements"]
}}
"""
    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config={
                "response_mime_type": "application/json",
                "temperature": 0.1,
            }
        )
        text_parts = []
        try:
            if response.candidates and response.candidates[0].content.parts:
                for part in response.candidates[0].content.parts:
                    if hasattr(part, "text") and part.text:
                        text_parts.append(part.text)
        except Exception:
            pass
        text_content = "".join(text_parts) if text_parts else (getattr(response, "text", None) or "")
        if not text_content.strip():
            return {"is_skill_query": False, "matched_topics": [], "matched_keyword": ""}
            
        json_match = re.search(r"\{.*\}", text_content, re.DOTALL)
        if json_match:
            result = json.loads(json_match.group(0), strict=False)
        else:
            result = json.loads(text_content, strict=False)
            
        is_skill = bool(result.get("is_skill_query", False))
        topics = [t for t in result.get("matched_topics", []) if isinstance(t, str)]
        matched_kw = topics[0] if topics else ""
        return {
            "is_skill_query": is_skill,
            "matched_topics": topics,
            "matched_keyword": matched_kw
        }
    except Exception as e:
        print(f"Error in LLM classification: {e}")
        return {"is_skill_query": False, "matched_topics": [], "matched_keyword": ""}


def _is_skill_query(message: str) -> tuple[bool, str]:
    """
    Determine whether the message belongs to the diet/fitness coaching skill.

    Uses word-boundary regex matching so short keywords like ``"eat"`` or
    ``"fat"`` do not false-match inside unrelated words such as ``"weather"``
    or ``"platform"``.

    Args:
        message: Raw user input (will be lowercased internally).

    Returns:
        ``(True, matched_keyword)`` if at least one skill keyword is found.
        ``(False, "")`` otherwise.
    """
    lower = message.lower()
    for kw in _SKILL_KEYWORDS:
        # Use word-boundary anchors for short/ambiguous keywords to avoid
        # false positives like "eat" inside "weather" or "fat" inside "platform".
        pattern = rf"\b{re.escape(kw)}\b"
        if re.search(pattern, lower):
            return True, kw
    return False, ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class DietCoach:
    """
    Stateless diet coaching agent. One instance, many sessions.

    All text (responses, prompts) is loaded from data files at runtime —
    no strings are hardcoded in this class. To change wording:

    - Edit ``data/mock_responses.json`` for offline response copy.
    - Edit ``data/system_prompt.txt`` for the Gemini system instruction.
    - Edit ``data/safety/supportive_responses.md`` for crisis copy.

    Session state is a plain dict you own and pass to every ``chat()`` call.
    Pass ``st.session_state`` directly for Streamlit; the dict is mutated
    in-place so no extra sync step is needed.

    Attributes:
        api_key (str | None): Gemini API key. ``None`` forces mock mode.
        model   (str):        Gemini model ID. Default: ``"gemini-flash-lite-latest"``.
        mock    (bool):       ``True`` when forced offline or no key is set.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model:   str        = "gemini-flash-lite-latest",
        mock:    bool       = False,
    ) -> None:
        """
        Initialise the coach.

        Args:
            api_key: Gemini key. Falls back to ``GEMINI_API_KEY`` / ``GOOGLE_API_KEY``
                     env vars. Absent key auto-enables mock mode.
            model:   Gemini model ID. ``"gemini-flash-lite-latest"`` is fastest for demos.
            mock:    Force offline mode regardless of key presence (useful for tests).
        """
        self.api_key = (
            api_key
            or os.environ.get("GEMINI_API_KEY")
            or os.environ.get("GOOGLE_API_KEY")
        )
        self.model = model
        self.mock  = mock or not bool(self.api_key)

    def chat(
        self,
        message:           str,
        session:           dict,
        profile:           dict | None = None,
        memories:          str  | None = None,
    ) -> dict:
        """
        Process one user message and return a structured result.

        The agent first checks whether the message is within the coaching
        skill's scope (diet, nutrition, fitness). If not, it returns a
        non-skill result immediately — no data files are loaded, no LLM call
        is made. Safety patterns always override the skill gate.

        When ``profile`` or ``memories`` are supplied they replace the values
        from the local ``memory/`` files, letting a parent application manage
        its own user store without touching the package's files.

        Pipeline:
            1. Safety check — deterministic regex, no LLM, no data loaded.
            2. Skill gate — keyword check; non-skill → return early.
            3. Build full context (data files + overrides).
            4. Gemini call (system prompt from file) or mock fallback.
            5. Extract citations from reply.

        Args:
            message:  Raw user input.
            session:  Mutable session dict. Initialise with ``reset_session()``.
            profile:  Optional user profile dict. Overrides ``memory/profile.json``
                      when provided. Pass your app's own user object here.
            memories: Optional memories string. Overrides ``memory/memories.md``
                      when provided.

        Returns:
            dict:
                - ``reply``          (str)        — response text, markdown-safe.
                - ``is_skill_query`` (bool)        — ``False`` for out-of-scope messages.
                - ``safety_class``   (str|None)    — ``"crisis"``, ``"medical"``, or ``None``.
                - ``paused``         (bool)        — ``True`` when coaching is halted.
                - ``sources``        (list[str])   — citations found in the reply.
                - ``food_data``      (dict|None)   — raw USDA result if food was looked up.
                - ``context_used``   (dict)        — which files/intents drove this answer.
        """
        # ── 1. Safety check (always runs — no data needed) ──────────────────
        safety = _detect_safety(message)
        if safety in ("crisis", "medical", "violence"):
            ctx = _build_ctx(session, profile, memories)
            ctx_used = _explain_context(message, ctx, None, "safety")
            if safety == "violence":
                _pause_session(session, "violence")
                reply = (
                    "I cannot assist with messages involving violence, threats, or harm to others. "
                    "If you or someone else is in danger, please contact emergency services (112) immediately."
                )
            else:
                fn  = _crisis_reply if safety == "crisis" else _medical_reply
                reply = fn(ctx, message) if safety == "crisis" else fn(ctx)
            return self._make_result(reply, safety, session,
                                     context_used=ctx_used, is_skill=True)

        # ── 2. Skill gate — dynamic classification or keyword check ─────────
        if self.mock:
            skill, matched_kw = _is_skill_query(message)
            matched_topics = None
        else:
            classification = _classify_query_llm(message, self.api_key, self.model)
            skill = classification["is_skill_query"]
            matched_kw = classification["matched_keyword"]
            matched_topics = classification["matched_topics"]

        # Allow out-of-scope messages if it is a follow-up turn in an active session or a check-in
        is_follow_up = session.get("turn_count", 0) > 1 or session.get("coaching_paused", False)
        if not skill and not is_follow_up:
            return self._make_result(
                reply=(
                    "This question is outside my coaching scope — I focus on food, "
                    "nutrition, and fitness.\n\n"
                    "If you have a diet or workout question, I'm ready to help."
                ),
                safety=None,
                session=session,
                is_skill=False,
                context_used={"mode": "non-skill", "matched_keyword": ""},
            )

        # ── 3. Build full context (data files + optional overrides) ─────────
        ctx = _build_ctx(session, profile, memories)

        food_data = _food_lookup(message)
        mode_used = "mock" if self.mock else "gemini"

        # ── 4. Generate reply ────────────────────────────────────────────────
        try:
            if self.mock:
                print("Mock calling")
                reply = _mock_reply(message, ctx)
            else:
                print("Gemini calling")
                reply = _call_gemini(
                    _build_prompt(message, ctx, matched_topics),
                    _render_system_prompt(ctx),
                    self.api_key,
                    self.model,
                )
                print("Gemini Called")
        except Exception as _gemini_err:
            print("MASSIVE!!!!!!!!!!!!!!")
            import traceback
            traceback.print_exc()
            reply     = _mock_reply(message, ctx)
            err_str   = str(_gemini_err)[:160]
            if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                err_label = "429 quota exhausted — set mock=True or wait for quota reset"
            else:
                err_label = f"{type(_gemini_err).__name__}: {err_str}"
            mode_used = f"mock-fallback ({err_label})"

        ctx_used = _explain_context(message, ctx, food_data, mode_used, matched_topics)
        ctx_used["skill_keyword_matched"] = matched_kw
        return self._make_result(reply, safety=None, session=session,
                                 food_data=food_data, context_used=ctx_used,
                                 is_skill=True)

    def resume(self, session: dict) -> None:
        """
        Resume coaching after a safety-triggered pause.

        Call when the user confirms they are safe and ready to continue.

        Args:
            session: The same session dict passed to ``chat()``.
        """
        session["coaching_paused"]   = False
        session["pause_reason"]      = None
        session["last_safety_event"] = None

    def reset_session(self) -> dict:
        """
        Return a new session dict with safe defaults.

        Call at the start of every new conversation.

        Returns:
            dict with keys: coaching_paused, pause_reason,
            last_safety_event, turn_count.
        """
        return {
            "coaching_paused":   False,
            "pause_reason":      None,
            "last_safety_event": None,
            "turn_count":        0,
        }

    @staticmethod
    def _make_result(
        reply:        str,
        safety:       str | None,
        session:      dict,
        food_data:    dict | None = None,
        context_used: dict | None = None,
        is_skill:     bool        = True,
    ) -> dict:
        """
        Pack agent output into the standard result dict returned from ``chat()``.

        Args:
            reply:        Coaching reply string.
            safety:       Safety class or ``None``.
            session:      Current session dict (read for ``coaching_paused``).
            food_data:    Raw USDA lookup result, or ``None``.
            context_used: Debug dict of files/intents used.
            is_skill:     ``False`` when the message was out of coaching scope.

        Returns:
            Standardised result dict (see ``chat()`` for full schema).
        """
        return {
            "reply":          reply,
            "is_skill_query": is_skill,
            "safety_class":   safety,
            "paused":         bool(session.get("coaching_paused")),
            "sources":        _extract_sources(reply),
            "food_data":      food_data,
            "context_used":   context_used or {},
        }
