from __future__ import annotations

"""Tuning parameters for the injury subsystem.

Design goals
------------
- Commercial safety: avoid "save is ruined" experiences by default.
- Deterministic: no reliance on OS time; callers provide in-game date.
- Tunable: most behavior can be adjusted by editing constants here.

Notes
-----
This system uses a hazard model:
    p = 1 - exp(-lambda * dt)
where lambda is a per-second (game) or per-day (training) hazard.
"""

# ---------------------------------------------------------------------------
# Global options / multipliers
# ---------------------------------------------------------------------------

# Global multiplier applied to all injury hazards.
# Intended to be exposed as a difficulty slider later.
GLOBAL_INJURY_MULT: float = 1.0

# Multi-year/career-threatening injuries are disabled by default (commercial).
ENABLE_MULTI_YEAR: bool = False

# If False, severity >= 4 events are heavily suppressed (but not necessarily impossible
# depending on templates). Keep True for "normal" NBA-like tuning.
ENABLE_SEVERE_INJURIES: bool = True

# ---------------------------------------------------------------------------
# Base hazard (frequency)
# ---------------------------------------------------------------------------

# Base injury hazard per second for a "median" player (I_InjuryFreq ~ 5) at fresh
# energy (1.0), young age, low wear.
#
# This is tuned so that a 36-minute game baseline is well under 1% before modifiers:
#   p36 ~= 1 - exp(-BASE * 2160)
BASE_GAME_HAZARD_PER_SEC: float = 3.2e-6

# Base training hazard per off-day for a median player.
# Training injuries should be noticeably rarer than game injuries.
BASE_TRAINING_HAZARD_PER_DAY: float = 2.0e-4

# InjuryFreq (1..10) -> multiplier via exponential mapping.
# freq_mult = exp(FREQ_EXP_K * (injury_freq - 5))
FREQ_EXP_K: float = 0.12

# ---------------------------------------------------------------------------
# Fatigue (energy) + durability shaping
# ---------------------------------------------------------------------------

# fatigue_mult = 1 + FATIGUE_K * dur_sens * (1-energy)^FATIGUE_POW
FATIGUE_K: float = 2.2
FATIGUE_POW: float = 1.8

# Durability (1..100) affects how strongly low energy increases injury risk.
# We compute dur_sens in [DUR_FATIGUE_MULT_MIN, DUR_FATIGUE_MULT_MAX], where
# higher durability -> lower sensitivity.
DUR_FATIGUE_MULT_MIN: float = 0.35
DUR_FATIGUE_MULT_MAX: float = 1.00

# Hard cap for fatigue multiplier to prevent extreme spikes.
FATIGUE_MULT_CAP: float = 3.0

# ---------------------------------------------------------------------------
# Age and wear
# ---------------------------------------------------------------------------

# Age multiplier (frequency):
#   age_mult = 1 + AGE_MAX_MULT * sigmoid((age-AGE_INFLECT)/AGE_SCALE)
AGE_INFLECT: float = 32.0
AGE_SCALE: float = 2.7
AGE_MAX_MULT: float = 0.45

# Wear multiplier from LT fatigue (0..1):
#   wear_mult = 1 + WEAR_LT_K * lt
WEAR_LT_K: float = 0.75
WEAR_MULT_CAP: float = 1.25

# Severity bump probabilities (applied after base severity sampling).
SEVERITY_BUMP_FAT_W: float = 0.08
SEVERITY_BUMP_FAT_POW: float = 1.7
SEVERITY_BUMP_AGE_W: float = 0.12
SEVERITY_BUMP_LT_W: float = 0.10

# Age curve for severity bump: sigmoid((age-SEV_AGE_INFLECT)/SEV_AGE_SCALE)
SEV_AGE_INFLECT: float = 33.0
SEV_AGE_SCALE: float = 1.8

# Training contexts are slightly less likely to bump severity.
TRAINING_SEVERITY_BUMP_MULT: float = 0.75

# ---------------------------------------------------------------------------
# Training intensity effect
# ---------------------------------------------------------------------------

# Training risk multiplier:
#   training_mult = intensity_mult ^ TRAINING_INTENSITY_EXP
TRAINING_INTENSITY_EXP: float = 1.35

# ---------------------------------------------------------------------------
# Re-injury / recurrence
# ---------------------------------------------------------------------------

# We model recurrence primarily as:
# - a bias toward previously injured body parts when selecting a new injury
# - a small additive risk bonus based on recent injury history
REINJURY_RECENCY_DAYS: int = 180
REINJURY_BONUS_PER_PRIOR: float = 0.08
REINJURY_BONUS_CAP: float = 0.35

# When selecting a body part for a new injury, previously injured body parts get
# extra weight. This is intentionally stronger than the global risk bonus, to
# create "nagging" injuries without exploding overall injury counts.
REINJURY_PART_BIAS_PER_COUNT: float = 0.28
REINJURY_PART_BIAS_CAP: float = 1.20

# ---------------------------------------------------------------------------
# Commercial safety caps (per-game)
# ---------------------------------------------------------------------------

MAX_INJURIES_PER_GAME_TOTAL: int = 2
MAX_INJURIES_PER_TEAM_PER_GAME: int = 1

# Severe means "season-altering" tier. We treat severity>=4 as severe.
SEVERE_THRESHOLD: int = 4
MAX_SEVERE_INJURY_PER_GAME: int = 1

# Soft safety to prevent rosters collapsing from training injuries.
MIN_AVAILABLE_PLAYERS_SOFT: int = 8

# ---------------------------------------------------------------------------
# Debuff / permanent effect tuning
# ---------------------------------------------------------------------------

# Temporary (returning) debuff scaling by severity:
# temp_base = -TEMP_DROP_PER_SEV * severity
TEMP_DROP_PER_SEV: int = 2

# Temporary debuff age multiplier:
#   age_temp_mult = 1 + TEMP_AGE_MAX_MULT * sigmoid((age-TEMP_AGE_INFLECT)/TEMP_AGE_SCALE)
TEMP_AGE_INFLECT: float = 33.0
TEMP_AGE_SCALE: float = 1.8
TEMP_AGE_MAX_MULT: float = 0.60

# Probability of permanent drops for severity>=3.
# p_perm = base_perm[severity] * (1 + PERM_AGE_MAX_MULT * sigmoid((age-PERM_AGE_INFLECT)/PERM_AGE_SCALE))
PERM_BASE_CHANCE_BY_SEV = {
    1: 0.00,
    2: 0.02,
    3: 0.06,
    4: 0.25,
    5: 0.45,
}
PERM_AGE_INFLECT: float = 33.0
PERM_AGE_SCALE: float = 1.7
PERM_AGE_MAX_MULT: float = 0.90

# Permanent drop magnitude base (per affected attribute) is sampled from a small range.
PERM_DROP_MIN: int = 1
PERM_DROP_MAX: int = 5

