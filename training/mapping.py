from __future__ import annotations

"""Mappings from training categories to SSOT rating keys.

We keep this mapping deliberately simple for the first iteration:
  - It provides enough control for training focus.
  - It aligns with `derived_formulas.COL` (2K-style base keys).

Later, you can refine this into more granular focuses (e.g. 3PT vs Mid vs Rim
finishing) without changing DB schema (plans are JSON).
"""

from typing import Dict, List


CATEGORY_KEYS: Dict[str, List[str]] = {
    "SHOOTING": [
        "Close Shot",
        "Mid-Range Shot",
        "Three-Point Shot",
        "Free Throw",
        "Shot IQ",
        "Offensive Consistency",
    ],
    "FINISHING": [
        "Layup",
        "Driving Dunk",
        "Standing Dunk",
        "Draw Foul",
        "Hands",
        "Close Shot",
    ],
    "PLAYMAKING": [
        "Pass Accuracy",
        "Pass Vision",
        "Pass IQ",
        "Ball Handle",
        "Speed with Ball",
        "Hands",
    ],
    "DEFENSE": [
        "Perimeter Defense",
        "Interior Defense",
        "Steal",
        "Block",
        "Help Defense IQ",
        "Pass Perception",
        "Defensive Consistency",
        "Hustle",
    ],
    "REBOUNDING": [
        "Offensive Rebound",
        "Defensive Rebound",
        "Vertical",
        "Strength",
        "Hustle",
        "Hands",
    ],
    "PHYSICAL": [
        "Speed",
        "Agility",
        "Strength",
        "Vertical",
        "Stamina",
        "Overall Durability",
        "Hustle",
    ],
    "IQ": [
        "Shot IQ",
        "Pass IQ",
        "Help Defense IQ",
        "Offensive Consistency",
        "Defensive Consistency",
        "Pass Vision",
    ],
    "POST": [
        "Post Control",
        "Post Fade",
        "Post Hook",
        "Close Shot",
        "Strength",
        "Hands",
    ],
}


ALL_CATEGORIES: List[str] = [
    "SHOOTING",
    "FINISHING",
    "PLAYMAKING",
    "DEFENSE",
    "REBOUNDING",
    "PHYSICAL",
    "IQ",
    "POST",
]
