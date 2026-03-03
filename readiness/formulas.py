from __future__ import annotations

"""Readiness math formulas (SSOT).

This module contains the *pure* math used by the readiness subsystem:
player sharpness and team scheme familiarity.

Why this exists
---------------
- The readiness service originally embedded these formulas as private helpers.
- Practice/day-step processing and CPU AI hint building need the exact same math.
- Keeping the formulas in one place prevents drift and removes duplication.

Design goals
------------
- **Pure functions**: no DB access, no repo knowledge.
- **Defensive**: tolerate bad inputs (None, strings, NaNs) without raising.
- **Deterministic**: avoid non-deterministic behavior (no random, no wall-clock).
- **Config-driven**: default behavior reads tuning from ``readiness.config``.

Callers
-------
- ``readiness.service`` should import and use these functions rather than keeping
  local duplicates.
- ``practice.service`` (day-step effects) and ``practice.service.resolve_practice_session``
  (AUTO hint generation) should also reuse these formulas.

NOTE: This module intentionally mirrors the legacy behavior currently implemented in
``readiness/service.py``. When refactoring, change callers first, then delete the
duplicated helpers from the service module.
"""

import datetime as _dt
import math
from typing import Any, Mapping, MutableMapping, Optional, Tuple

from . import config as r_cfg
from .types import TacticsMultipliers


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------


def clamp(x: Any, lo: float, hi: float) -> float:
    """Clamp ``x`` to the inclusive range [lo, hi]."""
    try:
        v = float(x)
    except Exception:
        v = 0.0
    if v < lo:
        return float(lo)
    if v > hi:
        return float(hi)
    return float(v)


def clamp01(x: Any) -> float:
    """Clamp to [0, 1]."""
    return clamp(x, 0.0, 1.0)


def clamp100(x: Any) -> float:
    """Clamp to [0, 100]."""
    return clamp(x, 0.0, 100.0)


def scaled_factor_01(value_0_100: Any) -> float:
    """Map 0..100 to -1..1 with 50 as neutral.

    This is used to convert readiness values into small signed multipliers or
    attribute deltas.
    """
    try:
        f = (float(value_0_100) - 50.0) / 50.0
    except Exception:
        f = 0.0
    return clamp(f, -1.0, 1.0)


def date10(value: Any) -> str:
    """Best-effort conversion to YYYY-MM-DD (first 10 chars)."""
    if value is None:
        return ""
    s = str(value).strip()
    if not s:
        return ""
    return s[:10]


def parse_date_iso(value: Any) -> Optional[_dt.date]:
    """Parse ISO date/datetime-like values into ``datetime.date``.

    Accepts:
    - ``YYYY-MM-DD``
    - longer strings where the first 10 chars form an ISO date
    - returns None on failure
    """
    if value is None:
        return None
    s = date10(value)
    if not s:
        return None
    try:
        return _dt.date.fromisoformat(s)
    except Exception:
        return None


def days_since(*, last_date_iso: Optional[str], on_date: _dt.date) -> int:
    """Return non-negative days elapsed since ``last_date_iso`` until ``on_date``."""
    last = parse_date_iso(last_date_iso)
    if last is None:
        return 0
    try:
        d = (on_date - last).days
    except Exception:
        d = 0
    return int(max(0, int(d)))


def apply_diminishing_gain(value_pre: Any, *, gain: Any) -> float:
    """Apply diminishing-returns gain toward 100.

    Formula:
        v' = v + g * (1 - v/100)

    This is used by both familiarity gain (per-game or per-practice) and can be
    useful for other readiness-like meters.
    """
    v0 = clamp100(value_pre)
    try:
        g = max(0.0, float(gain))
    except Exception:
        g = 0.0
    return clamp100(v0 + g * (1.0 - clamp01(v0 / 100.0)))


# ---------------------------------------------------------------------------
# Sharpness (player)
# ---------------------------------------------------------------------------


def decay_sharpness_linear(sharpness: Any, *, days: int, decay_per_day: Any | None = None) -> float:
    """Linear sharpness decay.

    Parameters
    ----------
    sharpness:
        Current sharpness (0..100).
    days:
        Number of days to decay (non-negative).
    decay_per_day:
        If provided, overrides ``readiness.config.SHARPNESS_DECAY_PER_DAY``.
        This is intended for OUT/rehab variants while keeping a single math SSOT.

    Returns
    -------
    float
        Decayed sharpness, clamped to 0..100.
    """
    try:
        d = int(days)
    except Exception:
        d = 0
    if d <= 0:
        return clamp100(sharpness)
    if decay_per_day is None:
        decay_per_day = getattr(r_cfg, "SHARPNESS_DECAY_PER_DAY", 1.0)
    try:
        dec = float(decay_per_day) * float(d)
    except Exception:
        dec = 0.0
    return clamp100(float(clamp100(sharpness)) - dec)


def gain_from_minutes(minutes: Any) -> float:
    """Compute the raw sharpness gain component from minutes (before caps/DR)."""
    try:
        m = max(0.0, float(minutes))
    except Exception:
        m = 0.0
    if m <= 0.0:
        return 0.0

    ref = float(getattr(r_cfg, "SHARPNESS_GAIN_MINUTES_REF", 36.0) or 36.0)
    if ref <= 0.0:
        ref = 36.0

    exp = float(getattr(r_cfg, "SHARPNESS_GAIN_MINUTES_EXP", 0.70) or 0.70)
    base = float(getattr(r_cfg, "SHARPNESS_GAIN_BASE", 1.5) or 1.5)
    scale = float(getattr(r_cfg, "SHARPNESS_GAIN_SCALE", 6.5) or 6.5)

    try:
        x = (m / ref) ** exp
    except Exception:
        x = 0.0
    return base + scale * float(x)


def apply_sharpness_gain(sharpness_pre: Any, *, minutes: Any) -> float:
    """Apply post-game sharpness gain with diminishing returns near 100.

    Mirrors legacy behavior in ``readiness.service._apply_sharpness_gain``:
    - Cap raw gain to ``SHARPNESS_GAIN_MAX_PER_GAME``.
    - Multiply by (1 - sharpness/100).
    """
    s = clamp100(sharpness_pre)
    try:
        mins = float(minutes)
    except Exception:
        mins = 0.0
    if mins <= 0.0:
        return s

    raw_gain = gain_from_minutes(mins)
    raw_cap = float(getattr(r_cfg, "SHARPNESS_GAIN_MAX_PER_GAME", 8.0) or 8.0)
    raw_gain = min(float(raw_gain), float(raw_cap))

    eff = float(raw_gain) * (1.0 - clamp01(s / 100.0))
    return clamp100(s + eff)


def sharpness_attr_mods(sharpness: Any, *, weights: Optional[Mapping[str, float]] = None) -> dict[str, float]:
    """Compute temporary attribute deltas from sharpness.

    Uses factor = clamp((sharpness-50)/50, -1, 1).
    Each weight is multiplied by factor and rounded to nearest int.
    """
    if weights is None:
        weights = getattr(r_cfg, "SHARPNESS_ATTR_WEIGHTS", None) or {}
    factor = scaled_factor_01(sharpness)

    out: dict[str, float] = {}
    for k, w in weights.items():
        try:
            delta = int(round(float(w) * float(factor)))
        except Exception:
            continue
        if delta != 0:
            out[str(k)] = float(delta)
    return out


# ---------------------------------------------------------------------------
# Scheme familiarity (team)
# ---------------------------------------------------------------------------


def decay_familiarity_exp(fam: Any, *, days: int) -> float:
    """Exponential familiarity decay toward a floor.

    fam' = floor + (fam-floor) * exp(-K * days)
    """
    f0 = clamp100(fam)
    try:
        d = int(days)
    except Exception:
        d = 0
    if d <= 0:
        return f0

    floor = clamp100(getattr(r_cfg, "FAMILIARITY_FLOOR", 20.0))
    k = max(0.0, float(getattr(r_cfg, "FAMILIARITY_DECAY_K", 0.03) or 0.03))
    return clamp100(floor + (f0 - floor) * math.exp(-k * float(d)))


def apply_familiarity_gain(fam_pre: Any, *, gain: Any | None = None) -> float:
    """Apply per-event familiarity gain with diminishing returns.

    Default gain comes from ``readiness.config.FAMILIARITY_GAIN_PER_GAME``.
    """
    if gain is None:
        gain = getattr(r_cfg, "FAMILIARITY_GAIN_PER_GAME", 4.0)
    return apply_diminishing_gain(fam_pre, gain=gain)


def tactics_mult_from_familiarity(*, off_fam: Any, def_fam: Any) -> TacticsMultipliers:
    """Convert familiarity values into conservative tactics knob multipliers."""
    off_fac = scaled_factor_01(off_fam)
    def_fac = scaled_factor_01(def_fam)

    def _mul(base: float, w: Any, fac: float) -> float:
        return clamp(
            base + float(w) * float(fac),
            float(getattr(r_cfg, "TACTICS_MULT_MIN", 0.70)),
            float(getattr(r_cfg, "TACTICS_MULT_MAX", 1.40)),
        )

    return TacticsMultipliers(
        scheme_weight_sharpness=_mul(1.0, getattr(r_cfg, "OFF_SCHEME_WEIGHT_SHARPNESS_W", 0.20), off_fac),
        scheme_outcome_strength=_mul(1.0, getattr(r_cfg, "OFF_SCHEME_OUTCOME_STRENGTH_W", 0.15), off_fac),
        def_scheme_weight_sharpness=_mul(1.0, getattr(r_cfg, "DEF_SCHEME_WEIGHT_SHARPNESS_W", 0.20), def_fac),
        def_scheme_outcome_strength=_mul(1.0, getattr(r_cfg, "DEF_SCHEME_OUTCOME_STRENGTH_W", 0.15), def_fac),
    )


def familiarity_attr_mods(
    *,
    off_fam: Any,
    def_fam: Any,
    off_weights: Optional[Mapping[str, float]] = None,
    def_weights: Optional[Mapping[str, float]] = None,
) -> Tuple[dict[str, float], dict[str, float]]:
    """Return (offense_mods, defense_mods) based on team familiarity.

    This is intentionally subtle and can be disabled via config.
    """
    if not bool(getattr(r_cfg, "ENABLE_FAMILIARITY_ATTR_MODS", False)):
        return ({}, {})

    if off_weights is None:
        off_weights = getattr(r_cfg, "FAMILIARITY_ATTR_WEIGHTS_OFFENSE", None) or {}
    if def_weights is None:
        def_weights = getattr(r_cfg, "FAMILIARITY_ATTR_WEIGHTS_DEFENSE", None) or {}

    off_fac = scaled_factor_01(off_fam)
    def_fac = scaled_factor_01(def_fam)

    off_out: dict[str, float] = {}
    for k, w in off_weights.items():
        try:
            delta = int(round(float(w) * float(off_fac)))
        except Exception:
            continue
        if delta != 0:
            off_out[str(k)] = float(delta)

    def_out: dict[str, float] = {}
    for k, w in def_weights.items():
        try:
            delta = int(round(float(w) * float(def_fac)))
        except Exception:
            continue
        if delta != 0:
            def_out[str(k)] = float(delta)

    return (off_out, def_out)
