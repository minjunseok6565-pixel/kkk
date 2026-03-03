from __future__ import annotations

"""ratings_2k

Commercial-grade 2K-style ratings generator.

Why this exists
--------------
The simulation layer (see :mod:`derived_formulas`) treats the 2K-style *base* ratings
stored in DB ``players.attrs_json`` as the SSOT, and computes *derived* ratings at
runtime (see :mod:`sim.roster_adapter`).

To make rookies/college players seamlessly integratable into the same DB/player
pipeline as existing Excel-imported rosters, this module generates attrs_json in
the exact same shape:

  - 35 base rating keys required by :data:`derived_formulas.COL` (values are 0..100 ints)
  - plus "Potential" stored as a letter grade string ("C-".."A+"), matching Excel.
  - plus extended personality/mental keys (prefixed with "M_") for downstream systems
    (growth, contracts, agency, dissatisfaction, etc.)

Design goals
------------
* Deterministic when the caller seeds the RNG (for save/load stability).
* Tunable via small, well-labeled knobs (archetypes, caps, distributions).
* Self-validating: missing SSOT keys are caught before persisting to DB.
* Identity-preserving calibration: target OVR is matched without washing out archetypes.
"""

import logging
import math
import random
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Tuple

from derived_formulas import COL as _DERIVED_COL, compute_derived


logger = logging.getLogger(__name__)
_WARN_COUNTS: Dict[str, int] = {}


def _warn_limited(code: str, msg: str, *, limit: int = 5) -> None:
    n = _WARN_COUNTS.get(code, 0)
    if n < limit:
        logger.warning("%s %s", code, msg, exc_info=True)
    _WARN_COUNTS[code] = n + 1


# -----------------------------------------------------------------------------
# SSOT keys
# -----------------------------------------------------------------------------

REQUIRED_2K_KEYS: Tuple[str, ...] = tuple(_DERIVED_COL.values())
"""All 2K-style base rating keys required by :func:`derived_formulas.compute_derived`.

These must exist in attrs_json with exact spelling (including spaces/hyphens).
"""

POTENTIAL_KEY: str = "Potential"

# Extended keys (kept in attrs_json for "player agency" + growth systems).
# NOTE: These are NOT used by derived_formulas; they are kept as SSOT inputs for
# higher-level gameplay layers (contracts/agency/growth, etc).
MENTAL_KEYS: Tuple[str, ...] = (
    "M_WorkEthic",
    "M_Coachability",
    "M_Ambition",
    "M_Loyalty",
    "M_Ego",
    "M_Adaptability",
)
MENTAL_MIN: int = 25
MENTAL_MAX: int = 99

INJURY_KEY: str = "I_InjuryFreq"
INJURY_MIN: int = 1
INJURY_MAX: int = 10

# Canonical attrs_json keys that must be preserved when persisting/upgrading a player.
# (draft/apply.py uses this list to filter attrs_json before writing to DB)
REQUIRED_KEYS: Tuple[str, ...] = REQUIRED_2K_KEYS + (POTENTIAL_KEY,) + MENTAL_KEYS + (INJURY_KEY,)


# -----------------------------------------------------------------------------
# Potential grade handling (Excel-compatible)
# -----------------------------------------------------------------------------

POTENTIAL_GRADES: Tuple[str, ...] = (
    "A+", "A", "A-",
    "B+", "B", "B-",
    "C+", "C", "C-",
    "D+", "D",
    "F",
)

POTENTIAL_TO_SCALAR: Dict[str, float] = {
    "A+": 1.0,
    "A": 0.95,
    "A-": 0.90,
    "B+": 0.85,
    "B": 0.80,
    "B-": 0.75,
    "C+": 0.70,
    "C": 0.65,
    "C-": 0.60,
    "D+": 0.55,
    "D": 0.50,
    "F": 0.40,
}


def potential_grade_to_scalar(grade: Any) -> float:
    """Convert a Potential grade ("A+".."F") to a numeric scalar.

    This mirrors the parsing logic used elsewhere in the project (team_utils.py).
    """
    if isinstance(grade, str):
        return float(POTENTIAL_TO_SCALAR.get(grade.strip(), 0.60))
    try:
        return float(grade)
    except (TypeError, ValueError):
        return 0.60


def scalar_to_potential_grade(x: float) -> str:
    """Quantize a scalar in [0.40, 1.00] to the nearest grade string."""
    x = float(max(0.40, min(1.00, x)))
    best = "C-"
    best_d = 10.0
    for g, s in POTENTIAL_TO_SCALAR.items():
        d = abs(s - x)
        if d < best_d:
            best = g
            best_d = d
    return best


# -----------------------------------------------------------------------------
# Archetype templates
# -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ArchetypeTemplate:
    """A 2K-style archetype defined as rating deltas.

    - boosts/penalties use SSOT key names (the 2K column strings).
    - core_keys drives calibration so identity remains stable.
    """

    id: str
    label: str
    positions: Tuple[str, ...]
    boosts: Mapping[str, int]
    penalties: Mapping[str, int]
    core_keys: Tuple[str, ...]
    potential_tier_bias: int = 0  # -1, 0, +1
    body_predicate: Optional[Callable[[int, int], bool]] = None  # (height_in, weight_lb) -> bool

    def allows_body(self, height_in: int, weight_lb: int) -> bool:
        if self.body_predicate is None:
            return True
        try:
            return bool(self.body_predicate(int(height_in), int(weight_lb)))
        except Exception:
            _warn_limited("ARCH_BODY_PREDICATE_FAILED", f"arch={self.id}")
            return True


def _h_at_least(h: int) -> Callable[[int, int], bool]:
    return lambda height_in, _w: int(height_in) >= int(h)


def _h_between(lo: int, hi: int) -> Callable[[int, int], bool]:
    return lambda height_in, _w: int(lo) <= int(height_in) <= int(hi)


# Archetype deltas are deliberately conservative so that calibration can still
# meet a broad target_ovr range without extreme clamping artifacts.
ARCHETYPES: Dict[str, ArchetypeTemplate] = {
    # PG
    "PG_PRIMARY_CREATOR": ArchetypeTemplate(
        id="PG_PRIMARY_CREATOR",
        label="Primary Creator",
        positions=("PG",),
        boosts={
            "Ball Handle": 12,
            "Speed with Ball": 10,
            "Pass Vision": 10,
            "Pass Accuracy": 8,
            "Shot IQ": 6,
            "Mid-Range Shot": 6,
            "Layup": 6,
            "Agility": 6,
        },
        penalties={
            "Post Hook": -10,
            "Post Fade": -8,
            "Post Control": -8,
            "Standing Dunk": -6,
            "Interior Defense": -6,
            "Offensive Rebound": -10,
            "Defensive Rebound": -10,
        },
        core_keys=(
            "Ball Handle",
            "Speed with Ball",
            "Pass Vision",
            "Pass Accuracy",
            "Pass IQ",
            "Shot IQ",
            "Agility",
            "Speed",
        ),
        potential_tier_bias=1,
    ),
    "PG_3PT_PLAYMAKER": ArchetypeTemplate(
        id="PG_3PT_PLAYMAKER",
        label="3PT Playmaker",
        positions=("PG",),
        boosts={
            "Three-Point Shot": 12,
            "Free Throw": 6,
            "Shot IQ": 6,
            "Offensive Consistency": 6,
            "Pass Accuracy": 8,
            "Pass IQ": 8,
            "Pass Vision": 8,
            "Ball Handle": 6,
        },
        penalties={
            "Driving Dunk": -10,
            "Standing Dunk": -12,
            "Strength": -6,
            "Interior Defense": -6,
            "Block": -10,
            "Post Control": -8,
        },
        core_keys=(
            "Three-Point Shot",
            "Shot IQ",
            "Offensive Consistency",
            "Ball Handle",
            "Pass Accuracy",
            "Pass Vision",
            "Pass IQ",
        ),
        potential_tier_bias=0,
    ),
    "PG_DEFENSIVE_GUARD": ArchetypeTemplate(
        id="PG_DEFENSIVE_GUARD",
        label="Defensive Guard",
        positions=("PG",),
        boosts={
            "Perimeter Defense": 12,
            "Steal": 10,
            "Pass Perception": 10,
            "Defensive Consistency": 8,
            "Help Defense IQ": 8,
            "Agility": 8,
            "Hustle": 6,
            "Speed": 6,
        },
        penalties={
            "Three-Point Shot": -6,
            "Mid-Range Shot": -6,
            "Post Fade": -10,
            "Post Hook": -10,
            "Driving Dunk": -6,
            "Pass Vision": -4,
        },
        core_keys=(
            "Perimeter Defense",
            "Steal",
            "Pass Perception",
            "Help Defense IQ",
            "Defensive Consistency",
            "Agility",
            "Speed",
            "Hustle",
        ),
        potential_tier_bias=0,
    ),
    "PG_SLASHING_GUARD": ArchetypeTemplate(
        id="PG_SLASHING_GUARD",
        label="Slashing Guard",
        positions=("PG",),
        boosts={
            "Layup": 12,
            "Driving Dunk": 10,
            "Draw Foul": 10,
            "Speed": 8,
            "Agility": 8,
            "Speed with Ball": 6,
            "Ball Handle": 6,
            "Vertical": 6,
        },
        penalties={
            "Three-Point Shot": -10,
            "Free Throw": -4,
            "Post Control": -10,
            "Pass Vision": -6,
            "Pass IQ": -4,
            "Shot IQ": -4,
        },
        core_keys=(
            "Layup",
            "Driving Dunk",
            "Draw Foul",
            "Speed",
            "Agility",
            "Ball Handle",
            "Speed with Ball",
            "Vertical",
        ),
        potential_tier_bias=1,
    ),

    # SG
    "SG_SHOT_CREATOR": ArchetypeTemplate(
        id="SG_SHOT_CREATOR",
        label="Shot Creator",
        positions=("SG",),
        boosts={
            "Mid-Range Shot": 12,
            "Shot IQ": 8,
            "Offensive Consistency": 8,
            "Ball Handle": 6,
            "Agility": 6,
            "Free Throw": 6,
            "Hands": 6,
        },
        penalties={
            "Pass Vision": -6,
            "Pass IQ": -4,
            "Post Hook": -10,
            "Offensive Rebound": -8,
            "Defensive Rebound": -8,
            "Block": -8,
        },
        core_keys=(
            "Mid-Range Shot",
            "Shot IQ",
            "Offensive Consistency",
            "Ball Handle",
            "Agility",
            "Hands",
        ),
        potential_tier_bias=1,
    ),
    "SG_3_AND_D": ArchetypeTemplate(
        id="SG_3_AND_D",
        label="3 & D",
        positions=("SG",),
        boosts={
            "Three-Point Shot": 12,
            "Perimeter Defense": 12,
            "Defensive Consistency": 8,
            "Shot IQ": 6,
            "Steal": 6,
            "Pass Perception": 6,
            "Hustle": 6,
        },
        penalties={
            "Ball Handle": -8,
            "Speed with Ball": -6,
            "Pass Vision": -6,
            "Post Control": -10,
            "Driving Dunk": -4,
        },
        core_keys=(
            "Three-Point Shot",
            "Perimeter Defense",
            "Defensive Consistency",
            "Steal",
            "Pass Perception",
            "Shot IQ",
        ),
        potential_tier_bias=0,
    ),
    "SG_OFF_BALL_SHOOTER": ArchetypeTemplate(
        id="SG_OFF_BALL_SHOOTER",
        label="Off-ball Shooter",
        positions=("SG",),
        boosts={
            "Three-Point Shot": 14,
            "Shot IQ": 8,
            "Hands": 8,
            "Offensive Consistency": 8,
            "Free Throw": 6,
            "Mid-Range Shot": 4,
        },
        penalties={
            "Ball Handle": -10,
            "Pass Vision": -8,
            "Interior Defense": -6,
            "Block": -10,
            "Strength": -4,
        },
        core_keys=(
            "Three-Point Shot",
            "Shot IQ",
            "Offensive Consistency",
            "Hands",
            "Free Throw",
        ),
        potential_tier_bias=0,
    ),
    "SG_TWO_WAY": ArchetypeTemplate(
        id="SG_TWO_WAY",
        label="Two-way",
        positions=("SG",),
        boosts={
            "Perimeter Defense": 10,
            "Steal": 8,
            "Three-Point Shot": 6,
            "Ball Handle": 4,
            "Shot IQ": 6,
            "Defensive Consistency": 6,
            "Agility": 6,
        },
        penalties={
            "Post Control": -8,
            "Standing Dunk": -6,
            "Post Hook": -10,
            "Offensive Rebound": -6,
        },
        core_keys=(
            "Perimeter Defense",
            "Steal",
            "Defensive Consistency",
            "Agility",
            "Three-Point Shot",
        ),
        potential_tier_bias=1,
    ),

    # SF
    "SF_3_AND_D_WING": ArchetypeTemplate(
        id="SF_3_AND_D_WING",
        label="3 & D Wing",
        positions=("SF",),
        boosts={
            "Three-Point Shot": 10,
            "Perimeter Defense": 12,
            "Help Defense IQ": 8,
            "Pass Perception": 8,
            "Defensive Consistency": 8,
            "Hustle": 6,
            "Agility": 6,
        },
        penalties={
            "Ball Handle": -8,
            "Pass Vision": -6,
            "Post Hook": -8,
            "Post Fade": -8,
        },
        core_keys=(
            "Three-Point Shot",
            "Perimeter Defense",
            "Help Defense IQ",
            "Pass Perception",
            "Defensive Consistency",
            "Agility",
        ),
    ),
    "SF_TWO_WAY_WING": ArchetypeTemplate(
        id="SF_TWO_WAY_WING",
        label="Two-way Wing",
        positions=("SF",),
        boosts={
            "Perimeter Defense": 10,
            "Three-Point Shot": 6,
            "Layup": 6,
            "Shot IQ": 6,
            "Defensive Consistency": 6,
            "Strength": 4,
            "Speed": 4,
        },
        penalties={
            "Post Hook": -6,
            "Pass Vision": -4,
            "Free Throw": -4,
        },
        core_keys=(
            "Perimeter Defense",
            "Defensive Consistency",
            "Three-Point Shot",
            "Layup",
            "Speed",
            "Strength",
        ),
        potential_tier_bias=1,
    ),
    "SF_SCORING_WING": ArchetypeTemplate(
        id="SF_SCORING_WING",
        label="Scoring Wing",
        positions=("SF",),
        boosts={
            "Three-Point Shot": 8,
            "Mid-Range Shot": 8,
            "Layup": 6,
            "Shot IQ": 6,
            "Offensive Consistency": 6,
            "Ball Handle": 4,
        },
        penalties={
            "Perimeter Defense": -6,
            "Help Defense IQ": -6,
            "Defensive Consistency": -6,
            "Pass Perception": -4,
        },
        core_keys=(
            "Three-Point Shot",
            "Mid-Range Shot",
            "Layup",
            "Shot IQ",
            "Offensive Consistency",
        ),
        potential_tier_bias=0,
    ),
    "SF_POINT_FORWARD": ArchetypeTemplate(
        id="SF_POINT_FORWARD",
        label="Point Forward",
        positions=("SF",),
        boosts={
            "Pass Vision": 12,
            "Pass Accuracy": 8,
            "Pass IQ": 8,
            "Ball Handle": 6,
            "Shot IQ": 6,
            "Hands": 6,
        },
        penalties={
            "Block": -6,
            "Interior Defense": -6,
            "Post Hook": -6,
            "Offensive Rebound": -6,
        },
        core_keys=(
            "Pass Vision",
            "Pass Accuracy",
            "Pass IQ",
            "Ball Handle",
            "Hands",
            "Shot IQ",
        ),
        potential_tier_bias=1,
        body_predicate=_h_at_least(78),
    ),

    # PF
    "PF_STRETCH_4": ArchetypeTemplate(
        id="PF_STRETCH_4",
        label="Stretch 4",
        positions=("PF",),
        boosts={
            "Three-Point Shot": 12,
            "Mid-Range Shot": 6,
            "Shot IQ": 6,
            "Free Throw": 6,
            "Offensive Consistency": 6,
            "Pass IQ": 4,
        },
        penalties={
            "Offensive Rebound": -6,
            "Defensive Rebound": -6,
            "Block": -6,
            "Interior Defense": -6,
            "Post Control": -4,
        },
        core_keys=(
            "Three-Point Shot",
            "Shot IQ",
            "Offensive Consistency",
            "Mid-Range Shot",
        ),
        potential_tier_bias=0,
    ),
    "PF_MOBILE_DEFENDER": ArchetypeTemplate(
        id="PF_MOBILE_DEFENDER",
        label="Mobile Defender",
        positions=("PF",),
        boosts={
            "Help Defense IQ": 12,
            "Perimeter Defense": 8,
            "Interior Defense": 8,
            "Defensive Consistency": 8,
            "Agility": 6,
            "Hustle": 6,
            "Pass Perception": 6,
        },
        penalties={
            "Three-Point Shot": -6,
            "Ball Handle": -8,
            "Post Fade": -6,
            "Free Throw": -4,
        },
        core_keys=(
            "Help Defense IQ",
            "Interior Defense",
            "Perimeter Defense",
            "Defensive Consistency",
            "Agility",
            "Hustle",
        ),
        potential_tier_bias=0,
    ),
    "PF_GLASS_CLEANER": ArchetypeTemplate(
        id="PF_GLASS_CLEANER",
        label="Glass Cleaner",
        positions=("PF",),
        boosts={
            "Offensive Rebound": 12,
            "Defensive Rebound": 12,
            "Strength": 10,
            "Hustle": 8,
            "Hands": 6,
            "Vertical": 6,
            "Interior Defense": 6,
        },
        penalties={
            "Three-Point Shot": -12,
            "Ball Handle": -12,
            "Speed with Ball": -10,
            "Pass Vision": -8,
            "Mid-Range Shot": -6,
        },
        core_keys=(
            "Offensive Rebound",
            "Defensive Rebound",
            "Strength",
            "Hustle",
            "Interior Defense",
        ),
        potential_tier_bias=0,
        body_predicate=_h_at_least(80),
    ),
    "PF_POST_4": ArchetypeTemplate(
        id="PF_POST_4",
        label="Post 4",
        positions=("PF",),
        boosts={
            "Post Control": 12,
            "Post Fade": 8,
            "Post Hook": 8,
            "Close Shot": 8,
            "Strength": 8,
            "Hands": 6,
        },
        penalties={
            "Three-Point Shot": -10,
            "Speed": -6,
            "Agility": -6,
            "Perimeter Defense": -6,
        },
        core_keys=(
            "Post Control",
            "Post Fade",
            "Post Hook",
            "Close Shot",
            "Strength",
        ),
        potential_tier_bias=0,
        body_predicate=_h_at_least(80),
    ),

    # C
    "C_RIM_PROTECTOR": ArchetypeTemplate(
        id="C_RIM_PROTECTOR",
        label="Rim Protector",
        positions=("C",),
        boosts={
            "Block": 14,
            "Interior Defense": 12,
            "Help Defense IQ": 10,
            "Defensive Consistency": 8,
            "Vertical": 8,
            "Strength": 8,
            "Defensive Rebound": 6,
        },
        penalties={
            "Three-Point Shot": -14,
            "Ball Handle": -14,
            "Speed with Ball": -12,
            "Pass Vision": -8,
            "Mid-Range Shot": -8,
        },
        core_keys=(
            "Block",
            "Interior Defense",
            "Help Defense IQ",
            "Defensive Consistency",
            "Strength",
            "Vertical",
            "Defensive Rebound",
        ),
        potential_tier_bias=0,
        body_predicate=_h_at_least(82),
    ),
    "C_ROLL_MAN": ArchetypeTemplate(
        id="C_ROLL_MAN",
        label="Roll Man",
        positions=("C",),
        boosts={
            "Standing Dunk": 14,
            "Driving Dunk": 10,
            "Layup": 6,
            "Draw Foul": 8,
            "Hands": 8,
            "Strength": 8,
            "Vertical": 6,
            "Shot IQ": 4,
        },
        penalties={
            "Three-Point Shot": -12,
            "Mid-Range Shot": -8,
            "Ball Handle": -14,
            "Pass Vision": -8,
            "Perimeter Defense": -6,
        },
        core_keys=(
            "Standing Dunk",
            "Driving Dunk",
            "Hands",
            "Strength",
            "Vertical",
            "Layup",
            "Draw Foul",
        ),
        potential_tier_bias=0,
        body_predicate=_h_at_least(81),
    ),
    "C_STRETCH_5": ArchetypeTemplate(
        id="C_STRETCH_5",
        label="Stretch 5",
        positions=("C",),
        boosts={
            "Three-Point Shot": 12,
            "Mid-Range Shot": 8,
            "Free Throw": 6,
            "Shot IQ": 6,
            "Hands": 6,
            "Pass IQ": 4,
        },
        penalties={
            "Offensive Rebound": -6,
            "Defensive Rebound": -6,
            "Block": -6,
            "Strength": -6,
            "Post Control": -4,
        },
        core_keys=(
            "Three-Point Shot",
            "Mid-Range Shot",
            "Shot IQ",
            "Hands",
            "Free Throw",
        ),
        potential_tier_bias=0,
        body_predicate=_h_at_least(82),
    ),
    "C_POST_SCORER": ArchetypeTemplate(
        id="C_POST_SCORER",
        label="Post Scorer",
        positions=("C",),
        boosts={
            "Post Control": 14,
            "Post Fade": 10,
            "Post Hook": 10,
            "Close Shot": 8,
            "Strength": 10,
            "Hands": 6,
            "Shot IQ": 4,
        },
        penalties={
            "Three-Point Shot": -12,
            "Speed with Ball": -14,
            "Ball Handle": -12,
            "Perimeter Defense": -10,
            "Steal": -8,
        },
        core_keys=(
            "Post Control",
            "Post Fade",
            "Post Hook",
            "Close Shot",
            "Strength",
            "Hands",
        ),
        potential_tier_bias=0,
        body_predicate=_h_at_least(82),
    ),
}


def _validate_archetype_tables() -> None:
    required = set(REQUIRED_2K_KEYS)
    for k, a in ARCHETYPES.items():
        if a.id != k:
            raise ValueError(f"ARCHETYPES key mismatch: dict_key={k!r} template_id={a.id!r}")
        for m in (a.boosts, a.penalties):
            for key in m.keys():
                if key not in required:
                    raise ValueError(f"Archetype {a.id} references unknown 2K key: {key!r}")
        for key in a.core_keys:
            if key not in required:
                raise ValueError(f"Archetype {a.id} references unknown core key: {key!r}")


_validate_archetype_tables()


# -----------------------------------------------------------------------------
# Generation knobs (tuning-friendly)
# -----------------------------------------------------------------------------

# The generator treats base 2K ratings as living on the same 0..100 scale as OVR.
# For college-level players, OVR typically sits around 40..86 in current game tuning.
DEFAULT_OVR_RANGE: Tuple[int, int] = (40, 86)


# Height bucket caps to keep outputs realistic.
# (These are tuned for plausibility over strict realism; tweak alongside gameplay.)
HEIGHT_BUCKETS: Tuple[Tuple[int, int], ...] = (
    (0, 74),
    (75, 77),
    (78, 80),
    (81, 83),
    (84, 120),
)

SPEED_CAP_BY_HEIGHT: Tuple[int, ...] = (95, 92, 89, 86, 82)
AGILITY_CAP_BY_HEIGHT: Tuple[int, ...] = (95, 92, 89, 86, 82)
HANDLE_CAP_BY_HEIGHT: Tuple[int, ...] = (97, 95, 92, 89, 85)

STRENGTH_FLOOR_BY_HEIGHT: Tuple[int, ...] = (40, 45, 50, 55, 60)
BLOCK_FLOOR_BY_HEIGHT_FOR_BIG: int = 55  # big bodies shouldn't have tiny block ratings
INTERIOR_DEF_FLOOR_BY_HEIGHT_FOR_BIG: int = 55
REB_FLOOR_BY_HEIGHT_FOR_BIG: int = 55


# Base group offsets per position, applied around target_ovr.
POS_GROUP_OFFSETS: Dict[str, Dict[str, float]] = {
    "PG": {"shoot": 2, "finish": 2, "play": 8, "per_def": 2, "in_def": -8, "reb": -10, "post": -10},
    "SG": {"shoot": 4, "finish": 2, "play": 2, "per_def": 0, "in_def": -6, "reb": -8, "post": -8},
    "SF": {"shoot": 2, "finish": 2, "play": 0, "per_def": 2, "in_def": 0, "reb": -2, "post": -2},
    "PF": {"shoot": 0, "finish": 2, "play": -4, "per_def": -2, "in_def": 4, "reb": 4, "post": 2},
    "C": {"shoot": -4, "finish": 4, "play": -8, "per_def": -6, "in_def": 8, "reb": 8, "post": 6},
}


GROUP_TO_KEYS: Dict[str, Tuple[str, ...]] = {
    "shoot": (
        "Three-Point Shot",
        "Mid-Range Shot",
        "Free Throw",
        "Shot IQ",
        "Offensive Consistency",
        "Hands",
    ),
    "finish": (
        "Layup",
        "Driving Dunk",
        "Standing Dunk",
        "Draw Foul",
        "Close Shot",
        "Hands",
    ),
    "play": (
        "Ball Handle",
        "Speed with Ball",
        "Pass Accuracy",
        "Pass IQ",
        "Pass Vision",
        "Hands",
    ),
    "per_def": (
        "Perimeter Defense",
        "Steal",
        "Pass Perception",
        "Help Defense IQ",
        "Defensive Consistency",
        "Speed",
        "Agility",
        "Hustle",
    ),
    "in_def": (
        "Interior Defense",
        "Block",
        "Help Defense IQ",
        "Defensive Consistency",
        "Strength",
        "Vertical",
    ),
    "reb": (
        "Offensive Rebound",
        "Defensive Rebound",
        "Hands",
        "Hustle",
        "Strength",
        "Vertical",
    ),
    "post": (
        "Post Hook",
        "Post Fade",
        "Post Control",
        "Close Shot",
        "Strength",
        "Hands",
    ),
}


PHYSICAL_KEYS: Tuple[str, ...] = (
    "Speed",
    "Agility",
    "Strength",
    "Vertical",
    "Stamina",
    "Hustle",
    "Overall Durability",
)


def normalize_pos(pos: str) -> str:
    """Normalize position strings to one of PG/SG/SF/PF/C."""
    p = (pos or "").strip().upper()
    if p in {"PG", "SG", "SF", "PF", "C"}:
        return p
    # common aliases
    if p in {"G", "GUARD"}:
        return "SG"
    if p in {"F", "FORWARD"}:
        return "SF"
    if p in {"CEN", "CENTER"}:
        return "C"
    return "SF"


def _bucket_index(height_in: int) -> int:
    h = int(height_in)
    for i, (lo, hi) in enumerate(HEIGHT_BUCKETS):
        if lo <= h <= hi:
            return i
    return len(HEIGHT_BUCKETS) - 1


def _clamp_int(x: float, lo: int = 0, hi: int = 100) -> int:
    return int(max(lo, min(hi, int(round(float(x))))))


# -----------------------------------------------------------------------------
# Mental / personality attrs (extended SSOT keys)
# -----------------------------------------------------------------------------

def _generate_mental_attrs(
    rng: random.Random,
    *,
    pos: str,
    archetype_id: str,
    potential_grade: str,
    class_year: int,
) -> Dict[str, int]:
    """Generate personality/mental attrs (prefixed with "M_").

    Design:
      - Random with light correlations via three latent factors.
      - Small contextual nudges from position/archetype/class_year.
      - Values are clamped to [MENTAL_MIN, MENTAL_MAX].

    These keys are intentionally NOT part of derived_formulas.COL; they are SSOT
    inputs for higher-level gameplay logic (growth/agency/contract demands/etc).
    """
    p = normalize_pos(pos)
    aid = (archetype_id or "").upper()
    cy = int(max(1, min(4, class_year)))

    # Weak correlation with potential (kept small to avoid "mental == skill").
    pot = potential_grade_to_scalar(potential_grade)  # ~0.40..1.00
    pot_n = (float(pot) - 0.70) / 0.15  # mean-ish around C+/C
    pot_n = float(max(-2.0, min(2.0, pot_n)))

    # Latent factors (N(0,1)): drive / team-orientation / flexibility-learning.
    z_drive = rng.gauss(0.0, 1.0)
    z_team = rng.gauss(0.0, 1.0)
    z_flex = rng.gauss(0.0, 1.0)

    def noise(sig: float) -> float:
        return float(rng.gauss(0.0, float(sig)))

    # Base correlated draws (tuned so most values land ~45..65, extremes are rare).
    we = 56.0 + 8.0 * z_drive + 2.0 * z_team + 3.0 * z_flex + 4.0 * pot_n + noise(5.0)
    co = 54.0 - 2.0 * z_drive + 5.0 * z_team + 7.0 * z_flex + 1.0 * pot_n + noise(5.0)
    am = 54.0 + 9.0 * z_drive - 4.0 * z_team + 2.0 * z_flex + 4.0 * pot_n + noise(6.0)
    lo = 52.0 - 6.0 * z_drive + 7.0 * z_team + 2.0 * z_flex - 1.0 * pot_n + noise(6.0)
    eg = 50.0 + 7.0 * z_drive - 6.0 * z_team - 4.0 * z_flex + 3.0 * pot_n + noise(7.0)
    ad = 55.0 - 2.0 * z_drive + 2.0 * z_team + 8.0 * z_flex - 1.0 * pot_n + noise(5.0)

    # Position nudges (very small; avoids turning into meta/OVR proxy).
    if p in {"PG", "SG"}:
        am += 2.0
        eg += 2.0
    elif p == "SF":
        ad += 1.0
    elif p == "PF":
        we += 1.0
        co += 1.0
    else:  # C
        lo += 2.0
        co += 2.0

    # Class-year nudges (tiny "maturity" signal).
    if cy >= 4:
        co += 1.0
        lo += 1.0
        eg -= 1.0
    elif cy <= 1:
        eg += 1.0

    # Archetype nudges (string-based; cheap + stable).
    if ("PRIMARY_CREATOR" in aid) or ("SHOT_CREATOR" in aid) or ("SCORING_WING" in aid) or ("POINT_FORWARD" in aid):
        am += 2.0
        eg += 2.0
        co -= 1.0
    if ("3_AND_D" in aid) or ("DEFENSIVE" in aid) or ("RIM_PROTECTOR" in aid) or ("MOBILE_DEFENDER" in aid):
        we += 2.0
        co += 2.0
        ad += 1.0
        eg -= 1.0

    return {
        "M_WorkEthic": _clamp_int(we, MENTAL_MIN, MENTAL_MAX),
        "M_Coachability": _clamp_int(co, MENTAL_MIN, MENTAL_MAX),
        "M_Ambition": _clamp_int(am, MENTAL_MIN, MENTAL_MAX),
        "M_Loyalty": _clamp_int(lo, MENTAL_MIN, MENTAL_MAX),
        "M_Ego": _clamp_int(eg, MENTAL_MIN, MENTAL_MAX),
        "M_Adaptability": _clamp_int(ad, MENTAL_MIN, MENTAL_MAX),
    }


def _sigmoid(x: float) -> float:
    # numerically stable enough for this domain
    return 1.0 / (1.0 + math.exp(-x))


def _sample_target_ovr(
    rng: random.Random,
    *,
    class_strength: float,
    ovr_range: Tuple[int, int],
) -> int:
    """Sample a target OVR using a tail-controlled latent talent model."""
    lo, hi = int(ovr_range[0]), int(ovr_range[1])

    # Latent talent core
    t = rng.gauss(0.0, 1.0)

    elite_prob = _sigmoid(-2.0 + 0.9 * float(class_strength))
    bust_prob = _sigmoid(-1.0 - 0.8 * float(class_strength))

    u = rng.random()
    if u < elite_prob:
        t += rng.gauss(2.2 + 0.5 * max(0.0, float(class_strength)), 0.45)
    elif u < elite_prob + bust_prob:
        t += rng.gauss(-1.3, 0.45)

    ovr = 62.0 + 7.5 * t
    return _clamp_int(ovr, lo, hi)


def _sample_potential_grade(
    rng: random.Random,
    *,
    target_ovr: int,
    class_year: int,
    class_strength: float,
    tier_bias: int,
) -> str:
    """Sample an Excel-compatible Potential grade string."""
    cy = int(max(1, min(4, class_year)))
    o = float(target_ovr)
    cs = float(class_strength)

    # Score in roughly [-1, +1]
    score = 0.0
    score += 0.25 * ((o - 60.0) / 28.0)
    score += 0.25 * (1.0 - (cy - 1) / 3.0)  # freshmen upside
    score += 0.20 * (cs / 2.0)
    score += 0.08 * float(tier_bias)
    score += rng.gauss(0.0, 0.12)

    mean_scalar = 0.70 + 0.18 * score
    x = float(max(0.40, min(1.00, mean_scalar + rng.gauss(0.0, 0.06))))
    return scalar_to_potential_grade(x)


def _compute_physical_block(
    rng: random.Random,
    *,
    pos: str,
    height_in: int,
    weight_lb: int,
    target_ovr: int,
) -> Dict[str, int]:
    """Compute physical ratings with body-aware caps and floors."""
    p = normalize_pos(pos)
    h = int(height_in)
    w = int(weight_lb)
    o = float(target_ovr)

    b = _bucket_index(h)
    speed_cap = SPEED_CAP_BY_HEIGHT[b]
    agi_cap = AGILITY_CAP_BY_HEIGHT[b]

    # Speed/Agility: anti-correlated with size
    # (ovr influences athletic ceiling, but body caps win)
    base_speed = 88.0 - 1.35 * max(0, h - 74) - 0.06 * max(0, w - 190) + 0.30 * (o - 60.0)
    base_agi = 90.0 - 1.45 * max(0, h - 74) - 0.06 * max(0, w - 190) + 0.25 * (o - 60.0)

    if p in ("PG", "SG"):
        base_speed += 2.5
        base_agi += 2.5
    elif p == "C":
        base_speed -= 2.0
        base_agi -= 2.0

    speed = _clamp_int(base_speed + rng.gauss(0.0, 3.0), 20, speed_cap)
    agility = _clamp_int(base_agi + rng.gauss(0.0, 3.0), 20, agi_cap)

    # Strength: correlated with weight and (slightly) height
    strength_floor = STRENGTH_FLOOR_BY_HEIGHT[b]
    base_str = 42.0 + 0.28 * max(0, w - 175) + 0.55 * max(0, h - 72) + 0.10 * (o - 60.0)
    if p in ("PF", "C"):
        base_str += 3.0
    strength = _clamp_int(base_str + rng.gauss(0.0, 3.5), strength_floor, 99)

    # Vertical: correlated with athleticism, slightly penalized by extreme size
    base_vert = 70.0 + 0.35 * (speed - 75) - 0.20 * max(0, h - 81) - 0.10 * max(0, w - 240)
    if p in ("PG", "SG"):
        base_vert += 2.0
    vertical = _clamp_int(base_vert + rng.gauss(0.0, 4.0), 30, 99)

    # Endurance trio: stamina/hustle/durability
    base_sta = 70.0 + 0.15 * (o - 60.0)
    stamina = _clamp_int(base_sta + rng.gauss(0.0, 4.0), 40, 99)

    base_hustle = 68.0 + 0.10 * (o - 60.0)
    hustle = _clamp_int(base_hustle + rng.gauss(0.0, 5.0), 35, 99)

    base_dur = 72.0 + 0.10 * (o - 60.0)
    durability = _clamp_int(base_dur + rng.gauss(0.0, 4.0), 40, 99)

    return {
        "Speed": speed,
        "Agility": agility,
        "Strength": strength,
        "Vertical": vertical,
        "Stamina": stamina,
        "Hustle": hustle,
        "Overall Durability": durability,
    }


def _apply_deltas(attrs: MutableMapping[str, int], deltas: Mapping[str, int]) -> None:
    for k, dv in deltas.items():
        attrs[k] = _clamp_int(float(attrs.get(k, 50)) + float(dv), 0, 100)


def _apply_body_caps(attrs: MutableMapping[str, int], *, height_in: int) -> None:
    b = _bucket_index(int(height_in))
    attrs["Speed"] = _clamp_int(attrs.get("Speed", 50), 0, SPEED_CAP_BY_HEIGHT[b])
    attrs["Agility"] = _clamp_int(attrs.get("Agility", 50), 0, AGILITY_CAP_BY_HEIGHT[b])
    attrs["Ball Handle"] = _clamp_int(attrs.get("Ball Handle", 50), 0, HANDLE_CAP_BY_HEIGHT[b])
    # speed with ball can't exceed the tighter of speed/handle, plus a small cushion
    swb = float(attrs.get("Speed with Ball", 50))
    cap = min(float(attrs.get("Speed", 50)), float(attrs.get("Ball Handle", 50))) + 6.0
    attrs["Speed with Ball"] = _clamp_int(min(swb, cap), 0, 100)


def _apply_relationship_constraints(attrs: MutableMapping[str, int], *, pos: str, height_in: int) -> None:
    """Enforce soft realism constraints that improve gameplay believability."""
    p = normalize_pos(pos)
    h = int(height_in)

    # "Big" floors: prevent absurdly low interior tools on large bodies.
    if h >= 82:
        attrs["Block"] = max(int(attrs.get("Block", 50)), BLOCK_FLOOR_BY_HEIGHT_FOR_BIG)
        attrs["Interior Defense"] = max(int(attrs.get("Interior Defense", 50)), INTERIOR_DEF_FLOOR_BY_HEIGHT_FOR_BIG)
        attrs["Defensive Rebound"] = max(int(attrs.get("Defensive Rebound", 50)), REB_FLOOR_BY_HEIGHT_FOR_BIG)

    # Guards shouldn't have elite post packages by default.
    if p in ("PG", "SG"):
        attrs["Post Hook"] = min(int(attrs.get("Post Hook", 50)), 70)
        attrs["Post Control"] = min(int(attrs.get("Post Control", 50)), 75)

    # IQ/Consistency shouldn't be wildly separated.
    shot_iq = int(attrs.get("Shot IQ", 50))
    off_cons = int(attrs.get("Offensive Consistency", 50))
    def_cons = int(attrs.get("Defensive Consistency", 50))

    # Nudge consistency towards IQ a bit.
    attrs["Offensive Consistency"] = _clamp_int(0.75 * off_cons + 0.25 * shot_iq)
    attrs["Defensive Consistency"] = _clamp_int(0.85 * def_cons + 0.15 * int(attrs.get("Help Defense IQ", 50)))


def _build_base_ratings(
    rng: random.Random,
    *,
    pos: str,
    height_in: int,
    weight_lb: int,
    target_ovr: int,
    class_year: int,
) -> Dict[str, int]:
    """Create a full 35-key dict before archetype deltas & calibration."""
    p = normalize_pos(pos)
    o = float(target_ovr)
    cy = int(max(1, min(4, class_year)))

    # Start with neutral defaults.
    out: Dict[str, int] = {k: 50 for k in REQUIRED_2K_KEYS}

    # Physical first (overrides a lot of defaults).
    out.update(_compute_physical_block(rng, pos=p, height_in=height_in, weight_lb=weight_lb, target_ovr=target_ovr))

    # IQ/consistency: older players tend to be more stable.
    maturity = (cy - 1) / 3.0  # 0..1
    iq_base = 55.0 + 10.0 * maturity + 0.20 * (o - 60.0)
    out["Shot IQ"] = _clamp_int(iq_base + rng.gauss(0.0, 4.0), 35, 95)
    out["Pass IQ"] = _clamp_int(iq_base + rng.gauss(0.0, 4.0), 35, 95)
    out["Help Defense IQ"] = _clamp_int(iq_base + rng.gauss(0.0, 4.0), 35, 95)

    # Base group offsets around target_ovr.
    offs = POS_GROUP_OFFSETS.get(p, POS_GROUP_OFFSETS["SF"])
    for g, keys in GROUP_TO_KEYS.items():
        g_off = float(offs.get(g, 0.0))
        for k in keys:
            # physical keys already seeded; still allow mild position shaping later
            base = o + g_off + rng.gauss(0.0, 4.0)
            if k in PHYSICAL_KEYS:
                # keep physical mostly body-driven
                base = 0.70 * float(out.get(k, 50)) + 0.30 * base
            out[k] = _clamp_int(base, 25, 99)

    # Post package is sensitive; tie it to size for non-bigs.
    if height_in <= 77:
        out["Post Hook"] = min(out["Post Hook"], 65)
        out["Post Fade"] = min(out["Post Fade"], 70)
        out["Post Control"] = min(out["Post Control"], 72)

    # Guards typically have better ball skills.
    if p in ("PG", "SG"):
        out["Ball Handle"] = max(out["Ball Handle"], _clamp_int(o + 6 + rng.gauss(0.0, 3.0), 40, 99))
        out["Pass Accuracy"] = max(out["Pass Accuracy"], _clamp_int(o + 2 + rng.gauss(0.0, 3.0), 35, 99))
        out["Pass Vision"] = max(out["Pass Vision"], _clamp_int(o + 1 + rng.gauss(0.0, 3.0), 35, 99))

    # Bigs typically have stronger interior tools.
    if p in ("PF", "C"):
        out["Interior Defense"] = max(out["Interior Defense"], _clamp_int(o + 3 + rng.gauss(0.0, 3.0), 35, 99))
        out["Defensive Rebound"] = max(out["Defensive Rebound"], _clamp_int(o + 4 + rng.gauss(0.0, 3.0), 35, 99))
        out["Block"] = max(out["Block"], _clamp_int(o + 1 + rng.gauss(0.0, 4.0), 30, 99))

    _apply_body_caps(out, height_in=height_in)
    _apply_relationship_constraints(out, pos=p, height_in=height_in)
    return out


def compute_ovr_proxy(attrs: Mapping[str, Any], *, pos: str) -> float:
    """Compute an OVR-like scalar from derived metrics.

    This is used internally to calibrate generated base ratings to a target_ovr.
    The weights are designed to be sensible out of the box and tunable later.

    If you want data-driven calibration, you can fit new weights from the
    Excel-imported roster distribution and replace/override this function.
    """
    p = normalize_pos(pos)
    d = compute_derived(attrs)

    # Group aggregates (all are 0..100 floats)
    shooting = (d["SHOT_3_CS"] + d["SHOT_MID_CS"] + d["SHOT_MID_PU"] + d["SHOT_3_OD"] + d["SHOT_FT"] + d["SHOT_TOUCH"]) / 6.0
    finishing = (d["FIN_RIM"] + d["FIN_DUNK"] + d["FIN_CONTACT"] + d["DRIVE_CREATE"]) / 4.0
    playmaking = (d["HANDLE_SAFE"] + d["PASS_SAFE"] + d["PASS_CREATE"] + d["PNR_READ"] + d["SHORTROLL_PLAY"]) / 5.0
    defense = (d["DEF_POA"] + d["DEF_HELP"] + d["DEF_STEAL"] + d["DEF_RIM"] + d["DEF_POST"]) / 5.0
    reb = (d["REB_OR"] + d["REB_DR"]) / 2.0
    physical = (d["PHYSICAL"] + d["ENDURANCE"]) / 2.0

    # Position-aware weights
    if p == "PG":
        w = dict(shoot=0.22, fin=0.18, play=0.26, defn=0.22, reb=0.06, phy=0.06)
    elif p == "SG":
        w = dict(shoot=0.26, fin=0.18, play=0.18, defn=0.24, reb=0.06, phy=0.08)
    elif p == "SF":
        w = dict(shoot=0.24, fin=0.18, play=0.16, defn=0.26, reb=0.08, phy=0.08)
    elif p == "PF":
        w = dict(shoot=0.20, fin=0.20, play=0.10, defn=0.28, reb=0.14, phy=0.08)
    else:  # C
        w = dict(shoot=0.14, fin=0.22, play=0.08, defn=0.30, reb=0.18, phy=0.08)

    return (
        w["shoot"] * shooting
        + w["fin"] * finishing
        + w["play"] * playmaking
        + w["defn"] * defense
        + w["reb"] * reb
        + w["phy"] * physical
    )


def _calibrate_to_target_ovr(
    attrs: MutableMapping[str, int],
    *,
    pos: str,
    height_in: int,
    target_ovr: int,
    archetype: Optional[ArchetypeTemplate],
    max_iters: int = 5,
) -> None:
    """Adjust ratings so that derived-based proxy aligns with target_ovr.

    Calibration is identity-preserving:
      - If proxy is too low, we lift *core* archetype keys first.
      - If proxy is too high, we reduce *non-core* keys first.
    """
    p = normalize_pos(pos)
    core: Tuple[str, ...] = archetype.core_keys if archetype else ()
    core_set = set(core)

    # Adjustable pools
    skill_keys: List[str] = [k for k in REQUIRED_2K_KEYS if k not in PHYSICAL_KEYS]
    non_core_keys: List[str] = [k for k in skill_keys if k not in core_set]

    for _ in range(int(max_iters)):
        proxy = compute_ovr_proxy(attrs, pos=p)
        diff = float(target_ovr) - float(proxy)
        if abs(diff) <= 0.85:
            break

        # Step size; bigger early, smaller near convergence.
        step = max(-6.0, min(6.0, 0.65 * diff))

        if diff > 0:
            # Raise core first, then the rest.
            keys_primary = list(core) if core else skill_keys
            keys_secondary = non_core_keys
            for k in keys_primary:
                attrs[k] = _clamp_int(attrs.get(k, 50) + step, 0, 100)
            for k in keys_secondary:
                attrs[k] = _clamp_int(attrs.get(k, 50) + 0.35 * step, 0, 100)
        else:
            # Reduce non-core first to keep archetype identity.
            keys_primary = non_core_keys if non_core_keys else skill_keys
            keys_secondary = list(core) if core else ()
            for k in keys_primary:
                attrs[k] = _clamp_int(attrs.get(k, 50) + step, 0, 100)
            for k in keys_secondary:
                attrs[k] = _clamp_int(attrs.get(k, 50) + 0.25 * step, 0, 100)

        _apply_body_caps(attrs, height_in=height_in)
        _apply_relationship_constraints(attrs, pos=p, height_in=height_in)


def validate_attrs(attrs: Mapping[str, Any], *, strict: bool = True) -> List[str]:
    """Validate SSOT compliance and basic sanity.

    Returns a list of human-readable error strings. If strict=True and any
    error exists, a ValueError is raised.
    """
    errors: List[str] = []

    for k in REQUIRED_2K_KEYS:
        if k not in attrs:
            errors.append(f"missing_2k_key:{k}")
            continue
        v = attrs.get(k)
        if not isinstance(v, (int, float)):
            errors.append(f"bad_type:{k}:{type(v).__name__}")
            continue
        if float(v) < 0 or float(v) > 100:
            errors.append(f"out_of_range:{k}:{v}")

    pot = attrs.get(POTENTIAL_KEY)
    if not isinstance(pot, str) or pot.strip() not in POTENTIAL_GRADES:
        errors.append(f"bad_potential:{pot!r}")

    # Ensure compute_derived works (and catches key spelling mismatches early).
    try:
        _ = compute_derived(attrs)
    except Exception as exc:
        errors.append(f"compute_derived_failed:{type(exc).__name__}")

    if errors and strict:
        raise ValueError("Invalid 2K attrs: " + "; ".join(errors))
    return errors


@dataclass(frozen=True, slots=True)
class GeneratedRatings:
    """Return object for generation."""

    attrs: Dict[str, Any]
    target_ovr: int
    archetype_id: str
    potential_grade: str
    ovr_proxy: float


def sample_archetype_id(
    rng: random.Random,
    *,
    pos: str,
    height_in: int,
    weight_lb: int,
    class_strength: float,
) -> str:
    """Sample an archetype id for a given position/body.

    The distribution is intentionally simple and tunable. class_strength nudges
    rarer "skill-heavy" archetypes upward when the class is strong.
    """
    p = normalize_pos(pos)
    cs = float(class_strength)

    # Base weights by position.
    if p == "PG":
        weights = {
            "PG_PRIMARY_CREATOR": 0.26 + 0.04 * max(0.0, cs),
            "PG_3PT_PLAYMAKER": 0.24 + 0.03 * max(0.0, cs),
            "PG_DEFENSIVE_GUARD": 0.25,
            "PG_SLASHING_GUARD": 0.25,
        }
    elif p == "SG":
        weights = {
            "SG_SHOT_CREATOR": 0.26 + 0.02 * max(0.0, cs),
            "SG_3_AND_D": 0.28,
            "SG_OFF_BALL_SHOOTER": 0.20 + 0.02 * max(0.0, cs),
            "SG_TWO_WAY": 0.26,
        }
    elif p == "SF":
        weights = {
            "SF_3_AND_D_WING": 0.28,
            "SF_TWO_WAY_WING": 0.26,
            "SF_SCORING_WING": 0.26,
            "SF_POINT_FORWARD": 0.20 + 0.03 * max(0.0, cs),
        }
    elif p == "PF":
        weights = {
            "PF_STRETCH_4": 0.24 + 0.03 * max(0.0, cs),
            "PF_MOBILE_DEFENDER": 0.26,
            "PF_GLASS_CLEANER": 0.26,
            "PF_POST_4": 0.24,
        }
    else:  # C
        weights = {
            "C_RIM_PROTECTOR": 0.30,
            "C_ROLL_MAN": 0.28,
            "C_STRETCH_5": 0.20 + 0.03 * max(0.0, cs),
            "C_POST_SCORER": 0.22,
        }

    # Filter by body predicates.
    candidates: List[Tuple[str, float]] = []
    total = 0.0
    for aid, w in weights.items():
        a = ARCHETYPES.get(aid)
        if not a:
            continue
        if not a.allows_body(height_in, weight_lb):
            continue
        ww = float(max(0.0, w))
        if ww <= 0:
            continue
        candidates.append((aid, ww))
        total += ww

    if not candidates:
        # fallback: any archetype for this position
        candidates = [(aid, 1.0) for aid, a in ARCHETYPES.items() if p in a.positions]
        total = float(len(candidates))

    r = rng.random() * total
    acc = 0.0
    for aid, w in candidates:
        acc += w
        if r <= acc:
            return aid
    return candidates[-1][0]


def generate_2k_ratings(
    rng: random.Random,
    *,
    pos: str,
    height_in: int,
    weight_lb: int,
    age: int,
    class_year: int,
    class_strength: float,
    target_ovr: Optional[int] = None,
    archetype_id: Optional[str] = None,
    ovr_range: Tuple[int, int] = DEFAULT_OVR_RANGE,
) -> GeneratedRatings:
    """Generate a 2K-style rating dict (attrs_json-compatible).

    Outputs:
      - attrs dict containing 35 SSOT base ratings + "Potential" + extended "M_*" keys
      - chosen archetype_id
      - target_ovr (caller should store in players/college_players table column)
      - ovr_proxy (derived-based, useful for tuning & tests)

    Notes:
      - age is currently used only as a soft signal (potential distribution);
        gameplay realism is dominated by class_year and body.
      - caller is responsible for persisting the returned attrs dict as JSON.
    """
    p = normalize_pos(pos)
    h = int(height_in)
    w = int(weight_lb)
    cy = int(max(1, min(4, class_year)))

    if target_ovr is None:
        target_ovr = _sample_target_ovr(rng, class_strength=float(class_strength), ovr_range=ovr_range)
    else:
        target_ovr = _clamp_int(target_ovr, int(ovr_range[0]), int(ovr_range[1]))

    if archetype_id is None:
        archetype_id = sample_archetype_id(
            rng,
            pos=p,
            height_in=h,
            weight_lb=w,
            class_strength=float(class_strength),
        )

    archetype = ARCHETYPES.get(str(archetype_id))
    if archetype is None:
        _warn_limited("ARCHETYPE_UNKNOWN", f"archetype_id={archetype_id!r}; falling back")
        archetype_id = sample_archetype_id(
            rng,
            pos=p,
            height_in=h,
            weight_lb=w,
            class_strength=float(class_strength),
        )
        archetype = ARCHETYPES.get(str(archetype_id))

    # Base build
    attrs_i: Dict[str, int] = _build_base_ratings(
        rng,
        pos=p,
        height_in=h,
        weight_lb=w,
        target_ovr=int(target_ovr),
        class_year=cy,
    )

    # Apply archetype deltas
    if archetype is not None:
        _apply_deltas(attrs_i, archetype.boosts)
        _apply_deltas(attrs_i, archetype.penalties)

    _apply_body_caps(attrs_i, height_in=h)
    _apply_relationship_constraints(attrs_i, pos=p, height_in=h)

    # Calibrate to target_ovr
    _calibrate_to_target_ovr(
        attrs_i,
        pos=p,
        height_in=h,
        target_ovr=int(target_ovr),
        archetype=archetype,
    )

    # Potential grade
    tier_bias = archetype.potential_tier_bias if archetype is not None else 0
    pot_grade = _sample_potential_grade(
        rng,
        target_ovr=int(target_ovr),
        class_year=cy,
        class_strength=float(class_strength),
        tier_bias=int(tier_bias),
    )

    # Final attrs dict (typed as Any to match JSON dump pathways)
    attrs: Dict[str, Any] = {k: int(attrs_i.get(k, 50)) for k in REQUIRED_2K_KEYS}
    attrs[POTENTIAL_KEY] = str(pot_grade)
    # Injury frequency (1..10). Higher => more injury-prone.
    # Intentionally independent from other stats for now.
    attrs[INJURY_KEY] = int(rng.randint(INJURY_MIN, INJURY_MAX))
    attrs.update(
        _generate_mental_attrs(
            rng,
            pos=p,
            archetype_id=str(archetype_id),
            potential_grade=str(pot_grade),
            class_year=cy,
        )
    )

    # Validate SSOT compliance
    validate_attrs(attrs, strict=True)

    proxy = float(compute_ovr_proxy(attrs, pos=p))
    return GeneratedRatings(
        attrs=attrs,
        target_ovr=int(target_ovr),
        archetype_id=str(archetype_id),
        potential_grade=str(pot_grade),
        ovr_proxy=proxy,
    )
