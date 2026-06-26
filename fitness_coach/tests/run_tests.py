#!/usr/bin/env python3
"""
fitness_coach/tests/run_tests.py — self-contained offline test suite.

No API key needed. No external dependencies beyond the stdlib.
Run from anywhere inside the fitness_coach package:

    python tests/run_tests.py
    python tests/run_tests.py --verbose
    python tests/run_tests.py --group safety
    python tests/run_tests.py --group fitness_data

Groups: safety | food_lookup | reference | personalization |
        response_quality | data_files | gemini_prompt | fitness_data
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
import time
from pathlib import Path

# ── Path resolution — works wherever the file is launched from ───────────────
# This file is at fitness_coach/tests/run_tests.py.
# ROOT  = fitness_coach/           (has data/, memory/, skills/, ...)
# PKGP  = parent of fitness_coach/ (needed for `from fitness_coach import ...`)
ROOT = Path(__file__).resolve().parent.parent      # fitness_coach/
PKGP = ROOT.parent                                 # project root

for p in (str(ROOT), str(PKGP)):
    if p not in sys.path:
        sys.path.insert(0, p)

# ── Import internal helpers from the package ─────────────────────────────────
from fitness_coach.coach import (   # type: ignore[import]
    _build_ctx,
    _detect_safety,
    _crisis_reply,
    _medical_reply,
    _mock_reply,
    _food_lookup,
    _dri_line,
    _pick_supportive_line,
    _detect_crisis_theme,
    _build_prompt,
    _get_fitness_context,
)
from fitness_coach import DietCoach


# ─────────────────────────────────────────────────────────────────────────────
# Tiny test framework
# ─────────────────────────────────────────────────────────────────────────────

PASS = "\033[92m PASS\033[0m"
FAIL = "\033[91m FAIL\033[0m"
SKIP = "\033[93m SKIP\033[0m"
BOLD = "\033[1m"
RESET = "\033[0m"

results: list[dict] = []


def test(name: str, group: str = "general"):
    def decorator(fn):
        results.append({"name": name, "group": group, "fn": fn, "result": None, "detail": ""})
        return fn
    return decorator


def assert_contains(text: str, *fragments: str, label: str = "") -> None:
    for fragment in fragments:
        if fragment.lower() not in text.lower():
            tag = f"[{label}] " if label else ""
            raise AssertionError(f"{tag}Expected to find: '{fragment}'\nIn: {text[:300]}")


def assert_not_contains(text: str, *fragments: str, label: str = "") -> None:
    for fragment in fragments:
        if fragment.lower() in text.lower():
            tag = f"[{label}] " if label else ""
            raise AssertionError(f"{tag}Should NOT contain: '{fragment}'\nIn: {text[:300]}")


def assert_under_words(text: str, limit: int = 200, label: str = "") -> None:
    count = len(text.split())
    if count > limit:
        tag = f"[{label}] " if label else ""
        raise AssertionError(f"{tag}Response is {count} words, limit is {limit}")


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

def make_session(paused: bool = False) -> dict:
    return {
        "coaching_paused":   paused,
        "pause_reason":      None,
        "last_safety_event": None,
        "turn_count":        0,
    }


def make_ctx(country: str = "IN", paused: bool = False) -> dict:
    ctx = _build_ctx(make_session(paused=paused))
    ctx["profile"]["country"] = country
    return ctx


# ─────────────────────────────────────────────────────────────────────────────
# GROUP: safety
# ─────────────────────────────────────────────────────────────────────────────

@test("Crisis phrase is detected", group="safety")
def _():
    assert _detect_safety("I want to kill myself") == "crisis"
    assert _detect_safety("I've been thinking about suicide") == "crisis"
    assert _detect_safety("I don't deserve to eat") == "crisis"


@test("Medical phrase is detected", group="safety")
def _():
    assert _detect_safety("I have chest pain after eating") == "medical"
    assert _detect_safety("I passed out this morning") == "medical"


@test("Violence phrase is detected", group="safety")
def _():
    assert _detect_safety("I want to murder my coworker") == "violence"
    assert _detect_safety("I'm going to hurt others today") == "violence"
    assert _detect_safety("I want to kill him") == "violence"


@test("Violence pauses session and returns standard message", group="safety")
def _():
    coach   = DietCoach(mock=True)
    session = coach.reset_session()
    result  = coach.chat("I want to kill someone", session)
    assert result["safety_class"] == "violence"
    assert result["paused"] is True
    assert_contains(result["reply"], "violence", "emergency services")


@test("Normal message passes safety", group="safety")
def _():
    assert _detect_safety("What should I eat for lunch?") is None
    assert _detect_safety("How much iron is in lentils?") is None
    assert _detect_safety("I feel low energy in the afternoon") is None


@test("Crisis response includes India helplines", group="safety")
def _():
    ctx = make_ctx(country="IN")
    reply = _crisis_reply(ctx, "I want to end my life")
    assert_contains(reply, "iCALL",      label="iCALL")
    assert_contains(reply, "Vandrevala", label="Vandrevala")
    assert_contains(reply, "KIRAN",      label="KIRAN")
    assert_contains(reply, "112",        label="emergency number")


@test("Crisis response includes supportive line from file", group="safety")
def _():
    ctx = make_ctx()
    reply = _crisis_reply(ctx, "I want to hurt myself")
    assert len(reply) > 200, "Crisis reply seems too short"
    assert_not_contains(reply, "## ", label="no markdown headers")


@test("Crisis response does NOT give meal advice", group="safety")
def _():
    ctx = make_ctx()
    reply = _crisis_reply(ctx, "I want to kill myself")
    assert_not_contains(reply, "recipe", "meal", "eat", "food", "lunch", "breakfast")


@test("Crisis pauses session coaching flag", group="safety")
def _():
    session = make_session()
    ctx = _build_ctx(session)
    _crisis_reply(ctx, "I want to die")
    assert session["coaching_paused"] is True
    assert session["last_safety_event"] == "crisis"


@test("Medical response includes India emergency number", group="safety")
def _():
    ctx = make_ctx(country="IN")
    reply = _medical_reply(ctx)
    assert_contains(reply, "112")
    assert_not_contains(reply, "recipe", "meal", "eat")


@test("Paused session gives check-in, not meal plan", group="safety")
def _():
    ctx = make_ctx(paused=True)
    reply = _mock_reply("What should I eat?", ctx)
    assert_not_contains(reply, "chickpea", "recipe", "dal", "spinach")
    assert_contains(reply, "how are you")


@test("Theme detection: shame maps to shame_food", group="safety")
def _():
    assert _detect_crisis_theme("I don't deserve to eat, I'm so bad") == "shame_food"


@test("Theme detection: family maps to family_pressure", group="safety")
def _():
    assert _detect_crisis_theme("my parents always pressure me about food") == "family_pressure"


@test("Theme detection: guilt maps to guilt_eating", group="safety")
def _():
    assert _detect_crisis_theme("I feel so guilty that I overate") == "guilt_eating"


@test("Theme detection: default is hopelessness", group="safety")
def _():
    assert _detect_crisis_theme("I want to die") == "hopelessness"


@test("Supportive line is picked from file, not empty", group="safety")
def _():
    text = (ROOT / "data" / "safety" / "supportive_responses.md").read_text()
    line = _pick_supportive_line(text, "hopelessness")
    assert len(line) > 10, f"Supportive line too short or empty: '{line}'"
    line2 = _pick_supportive_line(text, "shame_food")
    assert len(line2) > 10


# ─────────────────────────────────────────────────────────────────────────────
# GROUP: food_lookup
# ─────────────────────────────────────────────────────────────────────────────

@test("Lentils lookup returns ok=True", group="food_lookup")
def _():
    result = _food_lookup("lentils")
    assert result is not None
    assert result.get("ok") is True


@test("Lentil nutrients have iron, protein, fiber", group="food_lookup")
def _():
    result = _food_lookup("lentils")
    n = result["food"]["nutrients"]
    assert "iron_mg"   in n, "iron_mg missing"
    assert "protein_g" in n, "protein_g missing"
    assert "fiber_g"   in n, "fiber_g missing"


@test("Chickpeas lookup returns ok=True", group="food_lookup")
def _():
    result = _food_lookup("chickpeas")
    assert result is not None and result.get("ok") is True


@test("Unknown food returns ok=False, not an exception", group="food_lookup")
def _():
    result = _food_lookup("zzz_nonexistent_food_xyz")
    assert result is not None
    assert result.get("ok") is False
    assert "fallback" in result


@test("USDA citation present in lookup result", group="food_lookup")
def _():
    result = _food_lookup("banana")
    assert result is not None
    assert_contains(result.get("citation", ""), "USDA", "FoodData Central")


@test("Lookup is fast (under 500 ms)", group="food_lookup")
def _():
    start = time.perf_counter()
    _food_lookup("lentils")
    elapsed = time.perf_counter() - start
    assert elapsed < 0.5, f"Lookup took {elapsed:.2f}s — too slow for demo"


@test("Fitness foods present: tofu, edamame, pumpkin seeds", group="food_lookup")
def _():
    for term in ("tofu", "edamame", "pumpkin seeds"):
        result = _food_lookup(term)
        assert result is not None and result.get("ok") is True, \
            f"'{term}' not found in common_foods.json"


# ─────────────────────────────────────────────────────────────────────────────
# GROUP: reference
# ─────────────────────────────────────────────────────────────────────────────

@test("DRI iron line returned for iron question", group="reference")
def _():
    ctx = make_ctx()
    line = _dri_line("iron", ctx)
    assert len(line) > 10
    assert_contains(line, "NIH", "mg")


@test("DRI fiber line returned", group="reference")
def _():
    line = _dri_line("fiber", make_ctx())
    assert_contains(line, "NIH", "g")


@test("DRI protein line returned", group="reference")
def _():
    line = _dri_line("protein", make_ctx())
    assert_contains(line, "NIH", "g")


@test("DRI returns empty string for unknown nutrient", group="reference")
def _():
    line = _dri_line("cobalt", make_ctx())
    assert line == "", f"Expected empty string, got: '{line}'"


@test("DRI file loads and has adults_19_59 group", group="reference")
def _():
    data = json.loads((ROOT / "data" / "reference" / "dri_by_age_sex.json").read_text())
    assert "adults_19_59" in data
    assert "iron_mg_rda" in data["adults_19_59"]


# ─────────────────────────────────────────────────────────────────────────────
# GROUP: personalization
# ─────────────────────────────────────────────────────────────────────────────

@test("Peanut allergy never appears in mock replies", group="personalization")
def _():
    ctx = make_ctx()
    ctx["profile"]["diet_constraints"]["allergies"] = ["peanuts"]
    for question in ("Give me a snack idea", "Suggest a trail mix",
                     "What's a good protein snack?"):
        reply = _mock_reply(question, ctx)
        assert_not_contains(reply, "peanut", "peanut butter", label=question)


@test("Lactose intolerance respected — no regular dairy in suggestions", group="personalization")
def _():
    ctx = make_ctx()
    for question in ("What should I eat for breakfast?", "Dinner ideas?"):
        reply = _mock_reply(question, ctx)
        assert_not_contains(reply, "regular milk", "cow's milk", "cheese",
                             "paneer", label=question)


@test("Iron question references vegetarian diet context", group="personalization")
def _():
    ctx = make_ctx()
    reply = _mock_reply("How much iron is in lentils as a food source?", ctx)
    assert_contains(reply, "vegetarian")


@test("Profile file has required fields", group="personalization")
def _():
    profile = json.loads((ROOT / "memory" / "profile.json").read_text())
    for field in ("diet_constraints", "preferences", "goals", "country",
                  "onboarding_complete", "fitness_profile"):
        assert field in profile, f"Missing field: {field}"
    dc = profile["diet_constraints"]
    assert "allergies" in dc and "intolerances" in dc and "diet_type" in dc
    fp = profile["fitness_profile"]
    assert "workout_types" in fp and "fitness_goal" in fp


@test("Memories file is non-empty and contains explicit memories", group="personalization")
def _():
    text = (ROOT / "memory" / "memories.md").read_text()
    assert "[explicit]" in text
    assert len(text.strip()) > 50


@test("Session file has required fields", group="personalization")
def _():
    session = json.loads((ROOT / "memory" / "session.json").read_text())
    assert "coaching_paused" in session
    assert "last_safety_event" in session


# ─────────────────────────────────────────────────────────────────────────────
# GROUP: response_quality
# ─────────────────────────────────────────────────────────────────────────────

@test("Replies do not start with hollow openers", group="response_quality")
def _():
    ctx = make_ctx()
    for question in ("What should I eat for lunch?", "How much iron is in lentils?",
                     "What's a good snack?"):
        reply = _mock_reply(question, ctx)
        for opener in ("great!", "sure!", "absolutely!", "of course!", "certainly!"):
            assert not reply.lower().startswith(opener), \
                f"Reply starts with hollow opener '{opener}': {reply[:60]}"


@test("Replies do not use rigid ## section headers", group="response_quality")
def _():
    ctx = make_ctx()
    for question in ("What for lunch?", "Iron in lentils?", "Snack?", "Breakfast?"):
        reply = _mock_reply(question, ctx)
        assert_not_contains(reply, "## Suggestion", "## Why this fits you",
                             "## Evidence", "## Question", label=question)


@test("Lentil reply includes USDA and NIH citations", group="response_quality")
def _():
    ctx = make_ctx()
    reply = _mock_reply("How much iron is in lentils as a food source?", ctx)
    assert_contains(reply, "USDA")
    assert_contains(reply, "NIH")


@test("Lentil reply ends with a question", group="response_quality")
def _():
    ctx = make_ctx()
    reply = _mock_reply("How much iron is in lentils as a food source?", ctx)
    assert "?" in reply, "Reply should contain a follow-up question"


@test("Replies stay under 200 words", group="response_quality")
def _():
    ctx = make_ctx()
    for question in ("What should I eat for lunch?", "How much iron is in lentils?",
                     "Give me a snack idea", "Breakfast ideas?"):
        reply = _mock_reply(question, ctx)
        assert_under_words(reply, limit=200, label=question)


@test("Breakfast reply references known preferences from memories", group="response_quality")
def _():
    ctx = make_ctx()
    reply = _mock_reply("What should I have for breakfast?", ctx)
    assert any(w in reply.lower() for w in ("oat", "fruit", "banana", "almond")), \
        f"Should reference known breakfast preferences. Got: {reply[:200]}"


# ─────────────────────────────────────────────────────────────────────────────
# GROUP: data_files
# ─────────────────────────────────────────────────────────────────────────────

@test("All required data files exist", group="data_files")
def _():
    required = [
        "data/common_foods.json",
        "data/system_prompt.txt",
        "data/mock_responses.json",
        "data/reference/dri_by_age_sex.json",
        "data/safety/crisis_resources.json",
        "data/safety/supportive_responses.md",
        "data/fitness/workout_nutrition.json",
        "data/fitness/protein_targets.json",
        "data/fitness/hydration_electrolytes.json",
        "data/special/vegan_vegetarian.json",
        "data/special/injury_recovery.json",
        "data/special/fat_loss.json",
        "memory/profile.json",
        "memory/memories.md",
        "memory/session.json",
        "skills/SKILL.md",
        "skills/safety.md",
    ]
    missing = [p for p in required if not (ROOT / p).exists()]
    assert not missing, f"Missing files: {missing}"


@test("common_foods.json has at least 15 foods including fitness foods", group="data_files")
def _():
    data = json.loads((ROOT / "data" / "common_foods.json").read_text())
    foods = data.get("foods", [])
    assert len(foods) >= 15, f"Expected >=15 foods, got {len(foods)}"
    names = {f["id"] for f in foods}
    for expected in ("tofu_firm", "edamame_cooked", "pumpkin_seeds"):
        assert expected in names, f"Fitness food '{expected}' missing from common_foods.json"


@test("crisis_resources.json has 3+ India entries", group="data_files")
def _():
    data = json.loads((ROOT / "data" / "safety" / "crisis_resources.json").read_text())
    india = [r for r in data.get("resources", []) if r["country"] == "IN"]
    assert len(india) >= 3, f"Expected 3+ India entries, got {len(india)}"


@test("supportive_responses.md has India-relevant themes", group="data_files")
def _():
    text = (ROOT / "data" / "safety" / "supportive_responses.md").read_text()
    for theme in ("family_pressure", "shame_food", "guilt_eating"):
        assert theme in text, f"Missing theme: {theme}"


@test("SKILL.md is under 200 lines", group="data_files")
def _():
    lines = (ROOT / "skills" / "SKILL.md").read_text().splitlines()
    assert len(lines) <= 200, f"SKILL.md is {len(lines)} lines — trim it"


@test("mock_responses.json has fitness intents", group="data_files")
def _():
    data = json.loads((ROOT / "data" / "mock_responses.json").read_text())
    ids = {i["id"] for i in data.get("intents", [])}
    for expected in ("pre_workout", "post_workout", "protein_needs", "hydration_cramps",
                     "vegan_b12", "injury_recovery", "fat_loss"):
        assert expected in ids, f"Missing intent: '{expected}'"


# ─────────────────────────────────────────────────────────────────────────────
# GROUP: fitness_data
# ─────────────────────────────────────────────────────────────────────────────

@test("workout_nutrition.json has pre/post and by_workout_type sections", group="fitness_data")
def _():
    data = json.loads((ROOT / "data" / "fitness" / "workout_nutrition.json").read_text())
    assert "pre_workout"      in data
    assert "post_workout"     in data
    assert "by_workout_type"  in data
    assert "strength_hypertrophy" in data["by_workout_type"]


@test("protein_targets.json has muscle_gain and fat_loss goals", group="fitness_data")
def _():
    data = json.loads((ROOT / "data" / "fitness" / "protein_targets.json").read_text())
    goals = data.get("by_goal", {})
    assert "muscle_gain_hypertrophy"    in goals
    assert "fat_loss_body_recomposition" in goals
    assert "general_fitness_maintenance" in goals


@test("hydration_electrolytes.json has potassium and magnesium RDAs", group="fitness_data")
def _():
    data = json.loads((ROOT / "data" / "fitness" / "hydration_electrolytes.json").read_text())
    elec = data.get("key_electrolytes", {})
    assert "potassium"  in elec
    assert "magnesium"  in elec
    assert "rda_mg" in elec["potassium"]


@test("vegan_vegetarian.json covers B12, iron, omega-3", group="fitness_data")
def _():
    data = json.loads((ROOT / "data" / "special" / "vegan_vegetarian.json").read_text())
    risk = data.get("nutrients_at_risk", {})
    for nutrient in ("vitamin_b12", "iron", "omega_3"):
        assert nutrient in risk, f"Missing nutrient: {nutrient}"


@test("injury_recovery.json has recovery phases and key nutrients", group="fitness_data")
def _():
    data = json.loads((ROOT / "data" / "special" / "injury_recovery.json").read_text())
    assert "recovery_phases"          in data
    assert "key_nutrients_for_recovery" in data
    assert "vitamin_c" in data["key_nutrients_for_recovery"]


@test("fat_loss.json has caloric_deficit and protein principles", group="fitness_data")
def _():
    data = json.loads((ROOT / "data" / "special" / "fat_loss.json").read_text())
    kp = data.get("key_principles", {})
    assert "caloric_deficit"         in kp
    assert "protein_during_fat_loss" in kp


@test("Fitness context injected for pre-workout query", group="fitness_data")
def _():
    ctx   = make_ctx()
    block = _get_fitness_context("what should I eat before workout", ctx)
    assert len(block) > 20, "Expected fitness context block, got empty"
    assert_contains(block, "pre_workout", "ISSN")


@test("Fitness context injected for injury query", group="fitness_data")
def _():
    ctx   = make_ctx()
    block = _get_fitness_context("I am recovering from a knee injury", ctx)
    assert_contains(block, "INJURY RECOVERY")


@test("Fitness context injected for fat loss query", group="fitness_data")
def _():
    ctx   = make_ctx()
    block = _get_fitness_context("I want to lose weight", ctx)
    assert_contains(block, "FAT LOSS")


@test("Fitness context injected for vegan query", group="fitness_data")
def _():
    ctx   = make_ctx()
    block = _get_fitness_context("Am I getting enough B12 as a vegan?", ctx)
    assert_contains(block, "VEGAN")


# ─────────────────────────────────────────────────────────────────────────────
# GROUP: gemini_prompt
# ─────────────────────────────────────────────────────────────────────────────

@test("Gemini prompt contains profile summary", group="gemini_prompt")
def _():
    ctx    = make_ctx()
    prompt = _build_prompt("What should I eat for lunch?", ctx)
    assert_contains(prompt, "USER PROFILE", "vegetarian", "peanut")


@test("Gemini prompt contains memories block", group="gemini_prompt")
def _():
    ctx    = make_ctx()
    prompt = _build_prompt("What for breakfast?", ctx)
    assert_contains(prompt, "THINGS YOU KNOW ABOUT THIS USER", "explicit")


@test("Gemini prompt injects food lookup for food questions", group="gemini_prompt")
def _():
    ctx    = make_ctx()
    prompt = _build_prompt("How much iron is in lentils?", ctx)
    assert_contains(prompt, "FOOD_LOOKUP")
    assert_not_contains(prompt, '"ok": false')


@test("Gemini prompt injects DRI for nutrient question", group="gemini_prompt")
def _():
    ctx    = make_ctx()
    prompt = _build_prompt("How much iron do I need daily?", ctx)
    assert_contains(prompt, "DRI_REFERENCE", "NIH")


@test("Gemini prompt injects FITNESS_DATA for workout query", group="gemini_prompt")
def _():
    ctx    = make_ctx()
    prompt = _build_prompt("What should I eat before my workout?", ctx)
    assert_contains(prompt, "FITNESS_DATA")


@test("Gemini prompt flags paused session", group="gemini_prompt")
def _():
    ctx = make_ctx(paused=True)
    prompt = _build_prompt("What should I eat?", ctx)
    assert_contains(prompt, "coaching is paused")


# ─────────────────────────────────────────────────────────────────────────────
# GROUP: public_api  — DietCoach.chat() contract
# ─────────────────────────────────────────────────────────────────────────────

@test("chat() returns all expected result keys", group="public_api")
def _():
    coach   = DietCoach(mock=True)
    session = coach.reset_session()
    result  = coach.chat("What should I eat for lunch?", session)
    for key in ("reply", "safety_class", "paused", "sources", "food_data", "context_used"):
        assert key in result, f"Missing key: {key}"


@test("chat() crisis query returns safety_class=crisis and paused=True", group="public_api")
def _():
    coach   = DietCoach(mock=True)
    session = coach.reset_session()
    result  = coach.chat("I want to kill myself", session)
    assert result["safety_class"] == "crisis"
    assert result["paused"] is True


@test("chat() medical query returns safety_class=medical", group="public_api")
def _():
    coach   = DietCoach(mock=True)
    session = coach.reset_session()
    result  = coach.chat("I have severe chest pain", session)
    assert result["safety_class"] == "medical"


@test("context_used has mode, fitness_files_used, mock_intent_matched", group="public_api")
def _():
    coach   = DietCoach(mock=True)
    session = coach.reset_session()
    result  = coach.chat("What should I eat before my workout?", session)
    ctx     = result["context_used"]
    assert "mode"                in ctx
    assert "fitness_files_used"  in ctx
    assert "mock_intent_matched" in ctx
    assert ctx["mode"] == "mock"


@test("resume() clears paused flag", group="public_api")
def _():
    coach   = DietCoach(mock=True)
    session = coach.reset_session()
    coach.chat("I want to kill myself", session)
    assert session["coaching_paused"] is True
    coach.resume(session)
    assert session["coaching_paused"] is False


# ─────────────────────────────────────────────────────────────────────────────
# Test runner
# ─────────────────────────────────────────────────────────────────────────────

def run_suite(filter_group: str | None, verbose: bool) -> int:
    groups_seen: list[str] = []

    for entry in results:
        if filter_group and entry["group"] != filter_group:
            entry["result"] = "skip"
            continue

        group = entry["group"]
        if group not in groups_seen:
            groups_seen.append(group)
            print(f"\n{BOLD}── {group.upper()} ──{RESET}")

        try:
            entry["fn"]()
            entry["result"] = "pass"
            print(f"{PASS}  {entry['name']}")
        except AssertionError as e:
            entry["result"] = "fail"
            entry["detail"] = str(e)
            print(f"{FAIL}  {entry['name']}")
            if verbose:
                for line in textwrap.wrap(str(e), 80):
                    print(f"        {line}")
        except Exception as e:
            entry["result"] = "fail"
            entry["detail"] = f"{type(e).__name__}: {e}"
            print(f"{FAIL}  {entry['name']}")
            if verbose:
                print(f"        {type(e).__name__}: {e}")

    passed  = sum(1 for e in results if e["result"] == "pass")
    failed  = sum(1 for e in results if e["result"] == "fail")
    skipped = sum(1 for e in results if e["result"] == "skip")

    print(f"\n{'─' * 50}")
    print(f"  {passed} passed  |  {failed} failed  |  {skipped} skipped")
    print(f"{'─' * 50}\n")

    if failed:
        print(f"{FAIL}  Some tests failed. Run with --verbose for details.")
    else:
        print(f"{PASS}  All tests passed.")

    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="fitness_coach test suite")
    parser.add_argument("--group",   default=None,  help="Run only one group")
    parser.add_argument("--verbose", action="store_true", help="Show failure details")
    args = parser.parse_args()
    return run_suite(args.group, args.verbose)


if __name__ == "__main__":
    raise SystemExit(main())
