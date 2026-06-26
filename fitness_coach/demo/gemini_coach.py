#!/usr/bin/env python3
"""
demo/gemini_coach.py — CLI entry point for the Fitness & Diet Coach demo.

All logic lives in fitness_coach/coach.py.  This file is a thin wrapper that
parses command-line arguments and calls DietCoach.chat().

Usage:
    python demo/gemini_coach.py --mock "What should I eat for lunch?"
    python demo/gemini_coach.py "How much iron is in lentils?"
    python demo/gemini_coach.py --model gemini-2.0-flash "Snack ideas?"
    python demo/gemini_coach.py --mock --debug "Best post-workout meal?"
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# fitness_coach/demo/ → fitness_coach/ → project root (has fitness_coach as a package)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from fitness_coach import DietCoach  # noqa: E402

_SECTION_LABELS = {
    "mode":                "Mode",
    "files_always_loaded": "Always loaded",
    "fitness_files_used":  "Fitness data used",
    "mock_intent_matched": "Mock intent matched",
    "nutrients_detected":  "Nutrients detected",
    "dri_file_used":       "DRI file",
    "food_lookup":         "Food lookup",
}


def _print_context(ctx: dict) -> None:
    """Pretty-print the context_used dict in a readable block."""
    width = 72
    print("\n" + "─" * width)
    print("  CONTEXT USED")
    print("─" * width)
    for key, label in _SECTION_LABELS.items():
        value = ctx.get(key, "—")
        if isinstance(value, list):
            print(f"  {label}:")
            for item in value:
                print(f"      • {item}")
        else:
            print(f"  {label:24s}  {value}")
    print("─" * width + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Diet Coach — Gemini demo CLI")
    parser.add_argument("message", nargs="?", default="What should I eat for lunch?",
                        help="User message to send to the coach")
    parser.add_argument("--mock",  action="store_true",
                        help="Use offline mock responses (no API key needed)")
    parser.add_argument("--model", default="gemini-2.0-flash",
                        help="Gemini model ID (default: gemini-2.0-flash)")
    parser.add_argument("--debug", action="store_true",
                        help="Print which files and intents were used to build the reply")
    args = parser.parse_args()

    coach   = DietCoach(model=args.model, mock=args.mock)
    session = coach.reset_session()

    result = coach.chat(args.message, session)

    if args.debug:
        _print_context(result["context_used"])

    print(result["reply"])

    if result["sources"]:
        print("\nSources:", " | ".join(result["sources"]))

    if result["safety_class"]:
        print(f"\n[safety: {result['safety_class']}]", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
