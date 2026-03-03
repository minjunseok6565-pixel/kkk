from __future__ import annotations

"""Tuning parameters for the between-game fatigue subsystem.

Philosophy
----------
This subsystem aims to be:
- Deterministic (no RNG) and safe to query at any time
- Cheap to compute per game (tens of players)
- Easy to tune by adjusting constants in one place

Model summary
-------------
Persisted state per player:
- ST: short-term fatigue (0..1)
- LT: long-term fatigue / wear (0..1)
- last_date: last date the state was finalized (YYYY-MM-DD)

At game start, we:
1) Apply rest recovery from last_date -> game_date
2) Compute Condition = 1 - ST - LT_WEIGHT*LT
3) Initialize match-engine energy from Condition (with a floor)
4) Optionally cap in-game recovery via energy_cap

After the game, we:
1) Compute a game load based on minutes/role/tempo/stamina + end-of-game energy
2) Increase ST and LT (LT is a small fraction of ST gain, plus age/training effects)
"""

# ---------------------------------------------------------------------------
# Between-game recovery model
# ---------------------------------------------------------------------------

# Back-to-back still has "overnight recovery". This is not a full rest day.
OVERNIGHT_REST_UNITS: float = 0.60

# Exponential decay rates for recovery:
#   ST' = ST * exp(-ST_REC_RATE * rest_units * R)
#   LT' = LT * exp(-LT_REC_RATE * rest_units * R)
ST_REC_RATE: float = 1.20
LT_REC_RATE: float = 0.08

# Condition is computed as: 1 - ST - LT_WEIGHT*LT
LT_WEIGHT: float = 0.65

# Match engine energy at tip-off is clamped to [START_ENERGY_MIN, 1.0]
START_ENERGY_MIN: float = 0.55

# In-game recovery cap: energy cannot exceed min(1.0, start_energy + CAP_BONUS)
# (Requires engine support; safe to keep configured even if not wired yet.)
CAP_BONUS: float = 0.15

# Clamp for combined recovery multiplier R to avoid extreme behavior.
RECOVERY_MULT_MIN: float = 0.60
RECOVERY_MULT_MAX: float = 1.40

# ---------------------------------------------------------------------------
# Recovery factors
# ---------------------------------------------------------------------------

# EnduranceFactor = lerp(ENDURANCE_FACTOR_MIN, ENDURANCE_FACTOR_MAX, ENDURANCE/100)
ENDURANCE_FACTOR_MIN: float = 0.90
ENDURANCE_FACTOR_MAX: float = 1.10

# AgeRecoveryFactor = 1 - AGE_REC_DROP_MAX * sigmoid((age-AGE_REC_START)/AGE_REC_K)
AGE_REC_START: float = 29.0
AGE_REC_K: float = 1.30
AGE_REC_DROP_MAX: float = 0.18

# TrainingRecoveryFactor = intensity_mult ** (-TRAIN_REC_POW)
TRAIN_REC_POW: float = 0.80

# ---------------------------------------------------------------------------
# Post-game load -> ST/LT gains
# ---------------------------------------------------------------------------

BASE_ST_GAIN: float = 0.15

# Minutes shaping:
#   m_factor = (minutes / MIN_REF) ** MIN_EXP
MIN_REF: float = 36.0
MIN_EXP: float = 1.10

# End-of-game penalty (adds a small ST gain when end_energy is low)
END_PEN_W: float = 0.03

# Tempo shaping:
#   tempo_factor = clamp((tempo / TEMPO_REF) ** TEMPO_EXP, TEMPO_LO, TEMPO_HI)
TEMPO_REF: float = 100.0
TEMPO_EXP: float = 0.50
TEMPO_LO: float = 0.90
TEMPO_HI: float = 1.10

# Stamina shaping (derived key: FAT_CAPACITY == Stamina 0..100):
#   stamina_mult = lerp(STAM_LO, STAM_HI, FAT_CAPACITY/100)
STAM_LO: float = 1.08
STAM_HI: float = 0.92

# Role multipliers (fatigue archetypes from matchengine_v3.sim_fatigue)
ROLE_MULT = {
    "handler": 1.10,
    "wing": 1.00,
    "big": 0.95,
}

# LT gain is a fraction of ST gain, with training + age multipliers.
LT_GAIN_RATIO: float = 0.07

# Training multiplier for LT gain:
#   lt_train_mult = intensity_mult ** LT_TRAIN_POW
LT_TRAIN_POW: float = 0.60

# Age multiplier for LT gain:
#   lt_age_mult = 1 + AGE_LT_BOOST * sigmoid((age-AGE_LT_START)/AGE_LT_K)
AGE_LT_START: float = 30.0
AGE_LT_K: float = 1.40
AGE_LT_BOOST: float = 0.35
