#!/usr/bin/env python3
"""Offline food lookup for demo — stdlib only, always exits 0."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def load_foods() -> list[dict]:
    path = project_root() / "data" / "common_foods.json"
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    return data.get("foods", [])


def normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def score_food(query: str, food: dict) -> int:
    q = normalize(query)
    name = normalize(food.get("name", ""))
    fid = normalize(food.get("id", "").replace("_", " "))
    if not q:
        return 0
    if q in name or name in q:
        return 100
    q_tokens = set(q.split())
    name_tokens = set(name.split()) | set(fid.split())
    overlap = len(q_tokens & name_tokens)
    return overlap * 10


def lookup(query: str) -> dict:
    foods = load_foods()
    if not foods:
        return {
            "ok": False,
            "query": query,
            "message": "Demo food cache unavailable.",
            "fallback": True,
        }

    ranked = sorted(foods, key=lambda f: score_food(query, f), reverse=True)
    best = ranked[0]
    if score_food(query, best) < 10:
        return {
            "ok": False,
            "query": query,
            "message": "Not in demo cache. Use general USDA guidance without specific numbers.",
            "suggestions": [f["name"] for f in ranked[:5]],
            "fallback": True,
        }

    return {
        "ok": True,
        "query": query,
        "food": best,
        "citation": f"USDA FoodData Central — {best['name']} (FDC ID: {best.get('fdc_id', 'n/a')})",
        "fallback": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Demo food nutrient lookup")
    parser.add_argument("--query", required=True, help="Food search query")
    args = parser.parse_args()
    result = lookup(args.query)
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
