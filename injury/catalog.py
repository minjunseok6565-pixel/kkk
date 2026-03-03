from __future__ import annotations

"""Injury catalog (body parts + injury type templates).

This module is deliberately *data-driven*: the service layer uses the structures
here to roll concrete injuries (type, severity, duration, stat effects).

Key idea
--------
- First choose a body part (with positional + recurrence bias).
- Then choose an injury type template within that body part.
- Then roll severity/duration and effects.

All keys in temp/permanent effect profiles must match SSOT base rating keys used
in ``players.attrs_json`` (2K-style), e.g. "Speed", "Three-Point Shot".
"""

from dataclasses import dataclass
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple


# ---------------------------------------------------------------------------
# Canonical identifiers
# ---------------------------------------------------------------------------

# Body parts stored in DB as TEXT.
BODY_PARTS: Tuple[str, ...] = (
    "ANKLE",
    "KNEE",
    "HAMSTRING",
    "GROIN",
    "BACK",
    "SHOULDER",
    "WRIST_HAND",
    "HEAD",
)

# Context identifiers.
CONTEXT_GAME: str = "game"
CONTEXT_TRAINING: str = "training"


# ---------------------------------------------------------------------------
# Default tier ranges
# ---------------------------------------------------------------------------

# Severity tiers are 1..5 (5 can represent season-ending / long-term). Multi-year
# is handled by config + template overrides.
DEFAULT_DURATION_DAYS_BY_SEV: Dict[int, Tuple[int, int]] = {
    1: (1, 3),
    2: (7, 21),
    3: (21, 60),
    4: (60, 180),
    5: (180, 540),
}

DEFAULT_RETURNING_DAYS_BY_SEV: Dict[int, Tuple[int, int]] = {
    1: (3, 7),
    2: (7, 21),
    3: (14, 35),
    4: (21, 49),
    5: (35, 84),
}


# ---------------------------------------------------------------------------
# Template definition
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class InjuryTemplate:
    injury_type: str
    label: str
    body_part: str
    weight: float
    contexts: Tuple[str, ...]

    # Base severity probability distribution (before fatigue/age bumps).
    # Keys: 1..5, values sum to ~1.
    severity_probs: Mapping[int, float]

    # Duration (days) range per severity.
    duration_days: Mapping[int, Tuple[int, int]]

    # Returning debuff duration range per severity.
    returning_days: Mapping[int, Tuple[int, int]]

    # Temporary returning debuff profile: attr -> weight multiplier.
    # Final drop is computed by service using severity + age scaling.
    temp_attr_weights: Mapping[str, float]

    # Permanent drop profile: attr -> weight multiplier.
    perm_attr_weights: Mapping[str, float]


# ---------------------------------------------------------------------------
# Body part base weights + positional adjustments
# ---------------------------------------------------------------------------

# Base body-part weights (must sum to 1.0 for readability; service re-normalizes anyway).
BODY_PART_WEIGHTS_BASE: Dict[str, float] = {
    "ANKLE": 0.22,
    "KNEE": 0.18,
    "HAMSTRING": 0.13,
    "GROIN": 0.08,
    "BACK": 0.10,
    "SHOULDER": 0.08,
    "WRIST_HAND": 0.12,
    "HEAD": 0.09,
}

# Positional multipliers (light touch; realism without overfitting).
POS_MULTS_GUARD: Dict[str, float] = {
    "ANKLE": 1.12,
    "HAMSTRING": 1.10,
    "WRIST_HAND": 1.10,
    "KNEE": 0.95,
    "BACK": 0.95,
}

POS_MULTS_FORWARD: Dict[str, float] = {
    "ANKLE": 1.05,
    "KNEE": 1.05,
    "GROIN": 1.05,
}

POS_MULTS_CENTER: Dict[str, float] = {
    "KNEE": 1.15,
    "BACK": 1.12,
    "ANKLE": 1.05,
    "WRIST_HAND": 0.92,
    "HAMSTRING": 0.95,
}


def pos_group(pos: str) -> str:
    """Normalize a player's position to a coarse group: G / F / C."""
    s = str(pos or "").strip().upper()
    if not s:
        return "F"
    if s in {"PG", "SG", "G"}:
        return "G"
    if s in {"SF", "PF", "F"}:
        return "F"
    if s in {"C"}:
        return "C"
    # Hybrid strings like "G-F", "F-C".
    if "C" in s and "G" not in s:
        return "C"
    if "G" in s and "C" not in s:
        return "G"
    return "F"


def body_part_weights_for_pos(pos: str) -> Dict[str, float]:
    """Return base body-part weights adjusted for position group."""
    base = dict(BODY_PART_WEIGHTS_BASE)
    grp = pos_group(pos)
    mults: Mapping[str, float]
    if grp == "G":
        mults = POS_MULTS_GUARD
    elif grp == "C":
        mults = POS_MULTS_CENTER
    else:
        mults = POS_MULTS_FORWARD

    for k, m in mults.items():
        if k in base:
            base[k] = float(base[k]) * float(m)

    return base


# ---------------------------------------------------------------------------
# Injury templates
# ---------------------------------------------------------------------------


def _sev_probs(*pairs: Tuple[int, float]) -> Dict[int, float]:
    return {int(k): float(v) for k, v in pairs}


def _dur(overrides: Mapping[int, Tuple[int, int]] | None = None) -> Dict[int, Tuple[int, int]]:
    out = dict(DEFAULT_DURATION_DAYS_BY_SEV)
    if overrides:
        out.update({int(k): (int(v[0]), int(v[1])) for k, v in overrides.items()})
    return out


def _ret(overrides: Mapping[int, Tuple[int, int]] | None = None) -> Dict[int, Tuple[int, int]]:
    out = dict(DEFAULT_RETURNING_DAYS_BY_SEV)
    if overrides:
        out.update({int(k): (int(v[0]), int(v[1])) for k, v in overrides.items()})
    return out


# Temporary/permanent effect profiles per injury "family".
PROFILE_ANKLE_TEMP = {
    "Speed": 1.00,
    "Agility": 1.00,
    "Speed with Ball": 0.85,
    "Vertical": 0.65,
    "Stamina": 0.35,
}
PROFILE_ANKLE_PERM = {
    "Speed": 0.85,
    "Agility": 0.85,
    "Vertical": 0.70,
    "Overall Durability": 0.35,
}

PROFILE_KNEE_TEMP = {
    "Vertical": 1.00,
    "Speed": 0.80,
    "Agility": 0.75,
    "Driving Dunk": 0.55,
    "Defensive Consistency": 0.55,
    "Stamina": 0.45,
}
PROFILE_KNEE_PERM = {
    "Vertical": 1.00,
    "Speed": 0.75,
    "Agility": 0.70,
    "Stamina": 0.55,
    "Overall Durability": 0.45,
}

PROFILE_HAMSTRING_TEMP = {
    "Speed": 1.00,
    "Agility": 0.85,
    "Speed with Ball": 0.85,
    "Stamina": 0.40,
}
PROFILE_HAMSTRING_PERM = {
    "Speed": 0.85,
    "Agility": 0.75,
    "Overall Durability": 0.35,
}

PROFILE_GROIN_TEMP = {
    "Speed": 0.80,
    "Agility": 0.80,
    "Strength": 0.45,
    "Stamina": 0.45,
}
PROFILE_GROIN_PERM = {
    "Speed": 0.70,
    "Agility": 0.65,
    "Overall Durability": 0.30,
}

PROFILE_BACK_TEMP = {
    "Strength": 1.00,
    "Stamina": 0.80,
    "Post Control": 0.70,
    "Interior Defense": 0.60,
    "Hustle": 0.55,
}
PROFILE_BACK_PERM = {
    "Strength": 0.85,
    "Stamina": 0.75,
    "Post Control": 0.55,
    "Overall Durability": 0.40,
}

PROFILE_SHOULDER_TEMP = {
    "Strength": 0.85,
    "Pass Accuracy": 0.65,
    "Ball Handle": 0.55,
    "Hands": 0.55,
}
PROFILE_SHOULDER_PERM = {
    "Strength": 0.70,
    "Pass Accuracy": 0.45,
    "Hands": 0.40,
}

PROFILE_WRIST_TEMP = {
    "Three-Point Shot": 0.95,
    "Mid-Range Shot": 0.75,
    "Ball Handle": 0.60,
    "Pass Accuracy": 0.55,
    "Hands": 0.55,
}
PROFILE_WRIST_PERM = {
    "Three-Point Shot": 0.55,
    "Mid-Range Shot": 0.45,
    "Ball Handle": 0.35,
    "Hands": 0.35,
}

PROFILE_HEAD_TEMP = {
    "Shot IQ": 0.85,
    "Pass IQ": 0.85,
    "Offensive Consistency": 0.70,
    "Defensive Consistency": 0.70,
}
PROFILE_HEAD_PERM = {
    # Concussions generally shouldn't permanently tank ratings in a commercial sim.
}


TEMPLATES: Tuple[InjuryTemplate, ...] = (
    # ANKLE
    InjuryTemplate(
        injury_type="ANKLE_SPRAIN",
        label="Ankle sprain",
        body_part="ANKLE",
        weight=0.85,
        contexts=(CONTEXT_GAME, CONTEXT_TRAINING),
        severity_probs=_sev_probs((1, 0.52), (2, 0.33), (3, 0.13), (4, 0.02), (5, 0.00)),
        duration_days=_dur({1: (0, 3), 2: (4, 14), 3: (15, 45), 4: (60, 120)}),
        returning_days=_ret({1: (3, 7), 2: (7, 14), 3: (10, 21), 4: (14, 35)}),
        temp_attr_weights=PROFILE_ANKLE_TEMP,
        perm_attr_weights=PROFILE_ANKLE_PERM,
    ),
    InjuryTemplate(
        injury_type="ANKLE_FRACTURE",
        label="Ankle fracture",
        body_part="ANKLE",
        weight=0.15,
        contexts=(CONTEXT_GAME, CONTEXT_TRAINING),
        severity_probs=_sev_probs((1, 0.00), (2, 0.10), (3, 0.55), (4, 0.30), (5, 0.05)),
        duration_days=_dur({2: (21, 60), 3: (60, 150), 4: (150, 260), 5: (260, 420)}),
        returning_days=_ret({2: (14, 28), 3: (21, 42), 4: (28, 56), 5: (35, 84)}),
        temp_attr_weights=PROFILE_ANKLE_TEMP,
        perm_attr_weights=PROFILE_ANKLE_PERM,
    ),

    # KNEE
    InjuryTemplate(
        injury_type="KNEE_SPRAIN",
        label="Knee sprain/contusion",
        body_part="KNEE",
        weight=0.88,
        contexts=(CONTEXT_GAME, CONTEXT_TRAINING),
        severity_probs=_sev_probs((1, 0.40), (2, 0.42), (3, 0.15), (4, 0.03), (5, 0.00)),
        duration_days=_dur({1: (0, 3), 2: (4, 14), 3: (14, 35), 4: (60, 140)}),
        returning_days=_ret({1: (3, 7), 2: (7, 21), 3: (14, 28), 4: (21, 49)}),
        temp_attr_weights=PROFILE_KNEE_TEMP,
        perm_attr_weights=PROFILE_KNEE_PERM,
    ),
    InjuryTemplate(
        injury_type="ACL_TEAR",
        label="ACL tear",
        body_part="KNEE",
        weight=0.12,
        contexts=(CONTEXT_GAME, CONTEXT_TRAINING),
        severity_probs=_sev_probs((1, 0.00), (2, 0.00), (3, 0.10), (4, 0.82), (5, 0.08)),
        duration_days=_dur({3: (90, 180), 4: (260, 420), 5: (500, 900)}),
        returning_days=_ret({3: (28, 56), 4: (49, 84), 5: (84, 140)}),
        temp_attr_weights=PROFILE_KNEE_TEMP,
        perm_attr_weights=PROFILE_KNEE_PERM,
    ),

    # HAMSTRING
    InjuryTemplate(
        injury_type="HAMSTRING_STRAIN",
        label="Hamstring strain",
        body_part="HAMSTRING",
        weight=1.0,
        contexts=(CONTEXT_GAME, CONTEXT_TRAINING),
        severity_probs=_sev_probs((1, 0.28), (2, 0.40), (3, 0.28), (4, 0.04), (5, 0.00)),
        duration_days=_dur({1: (0, 3), 2: (5, 21), 3: (22, 60), 4: (60, 120)}),
        returning_days=_ret({1: (5, 10), 2: (10, 21), 3: (14, 28), 4: (21, 42)}),
        temp_attr_weights=PROFILE_HAMSTRING_TEMP,
        perm_attr_weights=PROFILE_HAMSTRING_PERM,
    ),

    # GROIN
    InjuryTemplate(
        injury_type="GROIN_STRAIN",
        label="Groin strain",
        body_part="GROIN",
        weight=1.0,
        contexts=(CONTEXT_GAME, CONTEXT_TRAINING),
        severity_probs=_sev_probs((1, 0.32), (2, 0.43), (3, 0.22), (4, 0.03), (5, 0.00)),
        duration_days=_dur({1: (0, 3), 2: (7, 21), 3: (21, 55), 4: (55, 120)}),
        returning_days=_ret({1: (5, 10), 2: (10, 21), 3: (14, 28), 4: (21, 42)}),
        temp_attr_weights=PROFILE_GROIN_TEMP,
        perm_attr_weights=PROFILE_GROIN_PERM,
    ),

    # BACK
    InjuryTemplate(
        injury_type="BACK_SPASM",
        label="Back spasm",
        body_part="BACK",
        weight=0.72,
        contexts=(CONTEXT_GAME, CONTEXT_TRAINING),
        severity_probs=_sev_probs((1, 0.35), (2, 0.44), (3, 0.18), (4, 0.03), (5, 0.00)),
        duration_days=_dur({1: (0, 3), 2: (4, 21), 3: (21, 60), 4: (60, 160)}),
        returning_days=_ret({1: (5, 10), 2: (10, 21), 3: (14, 35), 4: (21, 49)}),
        temp_attr_weights=PROFILE_BACK_TEMP,
        perm_attr_weights=PROFILE_BACK_PERM,
    ),
    InjuryTemplate(
        injury_type="HERNIATED_DISC",
        label="Herniated disc",
        body_part="BACK",
        weight=0.28,
        contexts=(CONTEXT_GAME, CONTEXT_TRAINING),
        severity_probs=_sev_probs((1, 0.00), (2, 0.15), (3, 0.45), (4, 0.35), (5, 0.05)),
        duration_days=_dur({2: (21, 60), 3: (60, 150), 4: (150, 260), 5: (260, 420)}),
        returning_days=_ret({2: (14, 28), 3: (21, 42), 4: (28, 56), 5: (35, 84)}),
        temp_attr_weights=PROFILE_BACK_TEMP,
        perm_attr_weights=PROFILE_BACK_PERM,
    ),

    # SHOULDER
    InjuryTemplate(
        injury_type="SHOULDER_STRAIN",
        label="Shoulder strain",
        body_part="SHOULDER",
        weight=0.78,
        contexts=(CONTEXT_GAME, CONTEXT_TRAINING),
        severity_probs=_sev_probs((1, 0.30), (2, 0.45), (3, 0.22), (4, 0.03), (5, 0.00)),
        duration_days=_dur({1: (0, 3), 2: (7, 21), 3: (21, 75), 4: (75, 160)}),
        returning_days=_ret({1: (5, 10), 2: (10, 21), 3: (14, 35), 4: (21, 49)}),
        temp_attr_weights=PROFILE_SHOULDER_TEMP,
        perm_attr_weights=PROFILE_SHOULDER_PERM,
    ),
    InjuryTemplate(
        injury_type="SHOULDER_DISLOCATION",
        label="Shoulder dislocation",
        body_part="SHOULDER",
        weight=0.22,
        contexts=(CONTEXT_GAME, CONTEXT_TRAINING),
        severity_probs=_sev_probs((1, 0.00), (2, 0.15), (3, 0.55), (4, 0.25), (5, 0.05)),
        duration_days=_dur({2: (21, 60), 3: (60, 150), 4: (150, 260), 5: (260, 420)}),
        returning_days=_ret({2: (14, 28), 3: (21, 42), 4: (28, 56), 5: (35, 84)}),
        temp_attr_weights=PROFILE_SHOULDER_TEMP,
        perm_attr_weights=PROFILE_SHOULDER_PERM,
    ),

    # WRIST/HAND
    InjuryTemplate(
        injury_type="WRIST_SPRAIN",
        label="Wrist sprain",
        body_part="WRIST_HAND",
        weight=0.80,
        contexts=(CONTEXT_GAME, CONTEXT_TRAINING),
        severity_probs=_sev_probs((1, 0.35), (2, 0.45), (3, 0.18), (4, 0.02), (5, 0.00)),
        duration_days=_dur({1: (0, 3), 2: (5, 21), 3: (21, 60), 4: (60, 120)}),
        returning_days=_ret({1: (5, 10), 2: (10, 21), 3: (14, 28), 4: (21, 42)}),
        temp_attr_weights=PROFILE_WRIST_TEMP,
        perm_attr_weights=PROFILE_WRIST_PERM,
    ),
    InjuryTemplate(
        injury_type="BROKEN_HAND",
        label="Broken hand",
        body_part="WRIST_HAND",
        weight=0.20,
        contexts=(CONTEXT_GAME, CONTEXT_TRAINING),
        severity_probs=_sev_probs((1, 0.00), (2, 0.12), (3, 0.58), (4, 0.25), (5, 0.05)),
        duration_days=_dur({2: (14, 45), 3: (45, 120), 4: (120, 220), 5: (220, 360)}),
        returning_days=_ret({2: (14, 28), 3: (21, 42), 4: (28, 56), 5: (35, 84)}),
        temp_attr_weights=PROFILE_WRIST_TEMP,
        perm_attr_weights=PROFILE_WRIST_PERM,
    ),

    # HEAD
    InjuryTemplate(
        injury_type="CONCUSSION",
        label="Concussion",
        body_part="HEAD",
        weight=1.0,
        contexts=(CONTEXT_GAME,),  # keep training concussions off for now
        severity_probs=_sev_probs((1, 0.55), (2, 0.35), (3, 0.10), (4, 0.00), (5, 0.00)),
        duration_days=_dur({1: (0, 3), 2: (3, 10), 3: (7, 21)}),
        returning_days=_ret({1: (3, 7), 2: (7, 14), 3: (10, 21)}),
        temp_attr_weights=PROFILE_HEAD_TEMP,
        perm_attr_weights=PROFILE_HEAD_PERM,
    ),
)


# Index templates by body part for fast selection.
_TEMPLATES_BY_PART: Dict[str, List[InjuryTemplate]] = {}
for t in TEMPLATES:
    _TEMPLATES_BY_PART.setdefault(str(t.body_part), []).append(t)


def templates_for_body_part(body_part: str, *, context: str) -> List[InjuryTemplate]:
    """Return templates for a body part that are allowed in the given context."""
    bp = str(body_part or "").strip().upper()
    ctx = str(context or "").strip().lower()
    out: List[InjuryTemplate] = []
    for t in _TEMPLATES_BY_PART.get(bp, []):
        if ctx in {c.lower() for c in t.contexts}:
            out.append(t)
    return out


def all_templates() -> Sequence[InjuryTemplate]:
    return TEMPLATES


def normalize_body_part(value: str) -> str:
    s = str(value or "").strip().upper()
    return s if s in BODY_PARTS else "ANKLE"


def normalize_context(value: str) -> str:
    s = str(value or "").strip().lower()
    if s in {CONTEXT_GAME, CONTEXT_TRAINING}:
        return s
    return CONTEXT_GAME

