from __future__ import annotations

"""Tuning parameters for the team practice subsystem.

Practice sessions are between-game activities (install, film, scrimmage, etc.).

This module is the single place to tune:
- intensity multipliers used by fatigue/injury coupling
- daily sharpness adjustments (in addition to readiness' base decay)
- daily scheme familiarity gain values (with diminishing returns)

All dates are in-game ISO (YYYY-MM-DD); never use the host OS clock.
"""

# ---------------------------------------------------------------------------
# Practice session types
# ---------------------------------------------------------------------------

PRACTICE_TYPES: set[str] = {
    "OFF_TACTICS",
    "DEF_TACTICS",
    "FILM",
    "SCRIMMAGE",
    "RECOVERY",
    "REST",
}

# ---------------------------------------------------------------------------
# Intensity multipliers
# ---------------------------------------------------------------------------

# One number per session type. This is intended to be *directly* consumed by:
# - fatigue recovery model (effective training load)
# - injury training-day hazard model (practice injury risk)
#
# Higher = harder practice (slower recovery, higher injury risk).
INTENSITY_MULT: dict[str, float] = {
    "REST": 0.60,
    "RECOVERY": 0.75,
    "FILM": 0.90,
    "OFF_TACTICS": 1.05,
    "DEF_TACTICS": 1.05,
    "SCRIMMAGE": 1.20,
}

# When a scrimmage session has non-participants, treat them as this type.
SCRIMMAGE_NON_PARTICIPANT_DEFAULT: str = "RECOVERY"

# Safety bounds for auto-filled scrimmage participant list.
SCRIMMAGE_MIN_PLAYERS: int = 8
SCRIMMAGE_MAX_PLAYERS: int = 15

# ---------------------------------------------------------------------------
# Sharpness (player match feel) daily adjustments
# ---------------------------------------------------------------------------

# Readiness already applies a base decay per day without game involvement.
# Practice adds an additional per-day delta on top of that.
#
# Example with readiness.SHARPNESS_DECAY_PER_DAY = 1.0:
#   REST: net -1.8/day
#   OFF_TACTICS: net -0.2/day
#   SCRIMMAGE(participant): net +1.0/day
#
# Non-participants in SCRIMMAGE get the delta of their non_participant_type.
SHARPNESS_DELTA: dict[str, float] = {
    "REST": -0.8,
    "RECOVERY": +0.6,
    "FILM": +0.3,
    "OFF_TACTICS": +0.8,
    "DEF_TACTICS": +0.8,
    "SCRIMMAGE": +2.0,
}

# ---------------------------------------------------------------------------
# Scheme familiarity (team) daily gains
# ---------------------------------------------------------------------------

# Familiarity gains are applied with diminishing returns:
#   fam' = fam + G*(1 - fam/100)
#
# These values should be smaller than per-game gains, since practice happens
# more frequently.
FAMILIARITY_GAIN: dict[str, float] = {
    "OFF_TACTICS": 2.0,
    "DEF_TACTICS": 2.0,
    "FILM": 1.2,
    "SCRIMMAGE": 0.8,
    # Explicit zeros for completeness (callers may assume missing => 0.0).
    "RECOVERY": 0.0,
    "REST": 0.0,
}
# ---------------------------------------------------------------------------
# AUTO AI heuristics (used by practice.ai + practice.service hint builder)
# ---------------------------------------------------------------------------

# If we know we're close to a game (days_to_next_game <= threshold), AUTO should
# prefer recovery/rest to avoid unrealistic fatigue spikes.
AI_RECOVERY_D2G_THRESHOLD: int = 1

# If familiarity for a main scheme is below this threshold, AUTO may select
# tactics install sessions to build scheme execution.
AI_LOW_FAMILIARITY_THRESHOLD: float = 55.0

# Players below this sharpness threshold are considered "out of rhythm".
AI_LOW_SHARPNESS_THRESHOLD: float = 45.0

# If enough players are out of rhythm and we have time, AUTO may schedule a scrimmage.
AI_LOW_SHARPNESS_COUNT_TRIGGER: int = 4

# Require at least this many days before the next game to consider SCRIMMAGE.
AI_SCRIMMAGE_MIN_D2G: int = 2
