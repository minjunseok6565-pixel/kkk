from __future__ import annotations

"""Tuning parameters for the between-game readiness subsystem.

This module is the single place to tune readiness behavior.

Sharpness (player)
------------------
- Stored in 0..100.
- Decays with inactivity (time since last update).
- Gains from game minutes, with diminishing returns as sharpness approaches 100.

Scheme familiarity (team)
-------------------------
- Stored in 0..100 per (team, season, scheme).
- Decays toward a floor value with time when not used.
- Gains when the scheme is used in a game, with diminishing returns.

Engine coupling
---------------
- Sharpness and (optionally) familiarity can be converted into attribute deltas
  (attrs_mods_by_pid) via readiness.service.
- Familiarity can also be converted into multipliers for matchengine tactics knobs.

All timestamps must be in *in-game* ISO format; never use the host OS clock.
"""

# ---------------------------------------------------------------------------
# Player match sharpness
# ---------------------------------------------------------------------------

SHARPNESS_DEFAULT: float = 50.0

# Linear decay per day without game involvement.
SHARPNESS_DECAY_PER_DAY: float = 1.0

# Linear decay per day when player is OUT (injury inactive).
# This is used by practice/readiness callers to apply a faster decay without
# duplicating math SSOT (see readiness.formulas.decay_sharpness_linear).
SHARPNESS_DECAY_PER_DAY_OUT: float = 2.0

# Game gain shaping (minutes -> gain).
SHARPNESS_GAIN_MINUTES_REF: float = 36.0
SHARPNESS_GAIN_MINUTES_EXP: float = 0.70

# Gain is: (BASE + SCALE * (minutes/REF)^EXP) * (1 - sharpness/100)
SHARPNESS_GAIN_BASE: float = 1.5
SHARPNESS_GAIN_SCALE: float = 6.5
SHARPNESS_GAIN_MAX_PER_GAME: float = 8.0

# Attributes affected by sharpness. Values are max delta magnitude at extreme sharpness.
# We compute factor = clamp((sharpness-50)/50, -1, 1) and apply round(weight*factor).
SHARPNESS_ATTR_WEIGHTS: dict[str, float] = {
    "Offensive Consistency": 6.0,
    "Defensive Consistency": 6.0,
    "Hands": 3.0,
}

# ---------------------------------------------------------------------------
# Team scheme familiarity
# ---------------------------------------------------------------------------

# Starting familiarity for a scheme with no persisted row yet.
# NOTE: 50 is still the mathematical neutral point for scaling formulas, but we
# intentionally start unseen schemes lower to represent changeover risk.
FAMILIARITY_DEFAULT: float = 35.0

# Familiarity decays toward this floor (not all the way to 0).
FAMILIARITY_FLOOR: float = 20.0

# Exponential decay coefficient per day:
#   fam' = floor + (fam-floor) * exp(-K * days)
FAMILIARITY_DECAY_K: float = 0.03

# Game gain (used scheme):
#   fam' = fam + G * (1 - fam/100)
FAMILIARITY_GAIN_PER_GAME: float = 4.0

# Convert familiarity to matchengine tactics knob multipliers.
# fam_factor = clamp((fam-50)/50, -1, 1)
OFF_SCHEME_WEIGHT_SHARPNESS_W: float = 0.20
OFF_SCHEME_OUTCOME_STRENGTH_W: float = 0.15
DEF_SCHEME_WEIGHT_SHARPNESS_W: float = 0.20
DEF_SCHEME_OUTCOME_STRENGTH_W: float = 0.15

# Clamp tactic knob multipliers (keep conservative; also clamped again in service).
TACTICS_MULT_MIN: float = 0.70
TACTICS_MULT_MAX: float = 1.40

# Optional: convert familiarity to small team-wide attribute deltas (IQ/vision/rotation).
# This is intentionally subtle.
ENABLE_FAMILIARITY_ATTR_MODS: bool = True

FAMILIARITY_ATTR_WEIGHTS_OFFENSE: dict[str, float] = {
    "Pass IQ": 2.5,
    "Shot IQ": 2.5,
    "Pass Vision": 2.0,
}

FAMILIARITY_ATTR_WEIGHTS_DEFENSE: dict[str, float] = {
    "Help Defense IQ": 2.5,
    "Pass Perception": 2.0,
}
