from __future__ import annotations

"""Tuning parameters for the training/growth subsystem.

This module intentionally centralizes "numbers you will want to tune" without
having to touch the growth engine logic.

Notes
-----
- Values are chosen for a reasonable MVP feel, not for perfect realism.
- It is safe to tweak these between seasons (or even mid-save) but results will
  diverge from previous tuning, as expected.
"""

from typing import Dict

# ---------------------------------------------------------------------------
# Ratings bounds (2K-style)
# ---------------------------------------------------------------------------

MIN_ATTR: int = 25
MAX_ATTR: int = 99

# ---------------------------------------------------------------------------
# Training intensity multipliers
# ---------------------------------------------------------------------------

INTENSITY_MULTIPLIER: Dict[str, float] = {
    "LOW": 0.85,
    "MED": 1.00,
    "HIGH": 1.15,
}

# ---------------------------------------------------------------------------
# Category weight shaping (team focus + player plan bias)
# ---------------------------------------------------------------------------

TEAM_FOCUS_MULTIPLIER: float = 1.8
TEAM_NONFOCUS_MULTIPLIER: float = 0.90

PLAYER_PRIMARY_BONUS: float = 1.2
PLAYER_SECONDARY_BONUS: float = 0.6

# Blend team vs player intensity.
TEAM_INTENSITY_SHARE: float = 0.60
PLAYER_INTENSITY_SHARE: float = 0.40

# ---------------------------------------------------------------------------
# Age curves
# ---------------------------------------------------------------------------

# Growth curve:
#   raw = 1/(1+exp((age-peak_age)/AGE_GROWTH_K))
#   factor = AGE_GROWTH_FLOOR + (1-AGE_GROWTH_FLOOR)*raw
AGE_GROWTH_K: float = 1.25
AGE_GROWTH_FLOOR: float = 0.20

# Decline curve:
#   start = sigmoid((age-decline_start_age)/AGE_DECLINE_START_K)
#   late  = sigmoid((age-late_decline_age)/AGE_DECLINE_LATE_K)
#   factor = start * (1 + AGE_DECLINE_LATE_MULT * late)
AGE_DECLINE_START_K: float = 1.15
AGE_DECLINE_LATE_K: float = 1.10
AGE_DECLINE_LATE_MULT: float = 0.85

# ---------------------------------------------------------------------------
# Ceiling diminishing returns
# ---------------------------------------------------------------------------

# cap_factor = room / (room + CAP_ROOM_DAMP)
CAP_ROOM_DAMP: float = 8.0

# ---------------------------------------------------------------------------
# Minutes factor
# ---------------------------------------------------------------------------

# minutes_factor = MINUTES_FLOOR + MINUTES_SCALE*sqrt(m/(m+ref))
MINUTES_FLOOR: float = 0.55
MINUTES_SCALE: float = 0.55

OFFSEASON_MINUTES_REF: float = 1100.0
MONTHLY_MINUTES_REF: float = 240.0

# ---------------------------------------------------------------------------
# Tick magnitude
# ---------------------------------------------------------------------------

OFFSEASON_BASE_POS: float = 8.0
OFFSEASON_BASE_NEG: float = 3.2

MONTHLY_BASE_POS: float = 1.6
MONTHLY_BASE_NEG: float = 0.9

# ---------------------------------------------------------------------------
# Mental -> drive / stability
# ---------------------------------------------------------------------------

DRIVE_BASE: float = 0.75
DRIVE_SCALE: float = 0.55
DRIVE_W_WORK: float = 0.45
DRIVE_W_COACH: float = 0.25
DRIVE_W_AMB: float = 0.15
DRIVE_W_ADAPT: float = 0.15
DRIVE_MIN: float = 0.70
DRIVE_MAX: float = 1.35

STABILITY_BASE: float = 0.55
STABILITY_SCALE: float = 0.35
STABILITY_W_WORK: float = 0.45
STABILITY_W_COACH: float = 0.20
STABILITY_W_LOYAL: float = 0.20
STABILITY_W_ADAPT: float = 0.15
STABILITY_EGO_PENALTY: float = 0.25
STABILITY_MIN: float = 0.10
STABILITY_MAX: float = 0.90

# ---------------------------------------------------------------------------
# Randomness / variance
# ---------------------------------------------------------------------------

NOISE_SIGMA_BASE: float = 0.10
NOISE_SIGMA_SCALE: float = 0.28

NOISE_MULT_MIN: float = 0.55
NOISE_MULT_MAX: float = 1.45

# ---------------------------------------------------------------------------
# Decline maintenance and noise
# ---------------------------------------------------------------------------

MAINTENANCE_BASE: float = 0.78
MAINTENANCE_W_WORK: float = 0.35
MAINTENANCE_W_COACH: float = 0.10

DECLINE_MULT_BASE: float = 1.25
DECLINE_MULT_MAINTENANCE_SCALE: float = 0.25

DECLINE_NOISE_BASE: float = 0.90
DECLINE_NOISE_SCALE: float = 0.15

# Safety guard for ceiling enforcement loop.
CEILING_SHAVE_GUARD: int = 40

# Category weights for decline allocation.
# IMPORTANT: should cover all categories in training.mapping.ALL_CATEGORIES.
DECLINE_WEIGHTS: Dict[str, float] = {
    "PHYSICAL": 0.37,
    "DEFENSE": 0.23,
    "FINISHING": 0.12,
    "POST": 0.05,
    "REBOUNDING": 0.08,
    "PLAYMAKING": 0.08,
    "SHOOTING": 0.05,
    "IQ": 0.02,
}

# ---------------------------------------------------------------------------
# Growth profile tuning (ceiling + curve milestones)
# ---------------------------------------------------------------------------

POT_MIN: float = 0.40
POT_MAX: float = 1.00

# Age factor applied to headroom: age_f = clamp(AGE_FACTOR_BASE - AGE_FACTOR_SLOPE*(age-AGE_FACTOR_START_AGE))
AGE_FACTOR_BASE: float = 1.10
AGE_FACTOR_SLOPE: float = 0.06
AGE_FACTOR_START_AGE: int = 19
AGE_FACTOR_MIN: float = 0.25
AGE_FACTOR_MAX: float = 1.10

# ceiling headroom: (HEADROOM_BASE + HEADROOM_SCALE*pot) * age_f * noise
HEADROOM_BASE: float = 6.0
HEADROOM_SCALE: float = 14.0
HEADROOM_NOISE_BASE: float = 0.85
HEADROOM_NOISE_RANGE: float = 0.30

CEILING_MIN_ADD: float = 1.0
CEILING_MAX: float = 99.0

# Peak age
PEAK_BASE: float = 24.0
PEAK_POT_SCALE: float = 4.0
PEAK_SIGMA: float = 0.8
PEAK_MIN: float = 23.0
PEAK_MAX: float = 29.5

# Decline start age
DECLINE_START_BASE_ADD: float = 3.5
DECLINE_START_POT_PENALTY: float = 2.0
DECLINE_START_SIGMA: float = 0.7
DECLINE_START_MIN: float = 27.5
DECLINE_START_MAX: float = 34.0

# Late decline age
LATE_DECLINE_BASE_ADD: float = 4.5
LATE_DECLINE_SIGMA: float = 0.8
LATE_DECLINE_MIN_ABS: float = 31.0
LATE_DECLINE_MIN_DELTA: float = 2.0
LATE_DECLINE_MAX: float = 38.0
