from __future__ import annotations

import math
import random
from typing import Any, Dict, Mapping

from ratings_2k import potential_grade_to_scalar, compute_ovr_proxy

from .types import stable_seed
from . import config as cfg


def _clamp(x: float, lo: float, hi: float) -> float:
    return float(max(lo, min(hi, x)))


def build_growth_profile(
    *,
    player_id: str,
    attrs: Mapping[str, Any],
    pos: str,
    age: int,
) -> Dict[str, Any]:
    """Create a deterministic per-player career curve profile.

    - ceiling_proxy: max OVR-proxy the player can realistically reach
    - peak_age: age where growth starts slowing sharply
    - decline_start_age: age where decline starts
    - late_decline_age: age where decline accelerates

    Determinism: profile is a pure function of player_id + current snapshot,
    so a save reload yields the same curve.
    """

    pid = str(player_id)
    rng = random.Random(stable_seed("growth_profile", pid))

    pot = potential_grade_to_scalar(attrs.get("Potential"))  # 0.40..1.00
    pot = _clamp(pot, float(cfg.POT_MIN), float(cfg.POT_MAX))

    try:
        cur_proxy = float(compute_ovr_proxy(attrs, pos=str(pos)))
    except Exception:
        cur_proxy = 60.0

    # Younger players tend to have more runway.
    # Age factor: 19 -> 1.00, 27 -> ~0.55, 31 -> ~0.35
    age_f = _clamp(
        float(cfg.AGE_FACTOR_BASE) - float(cfg.AGE_FACTOR_SLOPE) * float(max(0, age - int(cfg.AGE_FACTOR_START_AGE))),
        float(cfg.AGE_FACTOR_MIN),
        float(cfg.AGE_FACTOR_MAX),
    )

    # Headroom: 6..18ish depending on potential.
    base_headroom = float(cfg.HEADROOM_BASE) + float(cfg.HEADROOM_SCALE) * pot
    noise = float(cfg.HEADROOM_NOISE_BASE) + float(cfg.HEADROOM_NOISE_RANGE) * rng.random()
    headroom = base_headroom * age_f * noise

    ceiling = _clamp(cur_proxy + headroom, cur_proxy + float(cfg.CEILING_MIN_ADD), float(cfg.CEILING_MAX))

    # Peak age: higher potential tends to peak slightly later.
    peak = float(cfg.PEAK_BASE) + float(cfg.PEAK_POT_SCALE) * pot + rng.gauss(0.0, float(cfg.PEAK_SIGMA))
    peak = _clamp(peak, float(cfg.PEAK_MIN), float(cfg.PEAK_MAX))

    # Decline starts after peak.
    decline_start = (
        peak
        + float(cfg.DECLINE_START_BASE_ADD)
        + (1.0 - pot) * float(cfg.DECLINE_START_POT_PENALTY)
        + rng.gauss(0.0, float(cfg.DECLINE_START_SIGMA))
    )
    decline_start = _clamp(decline_start, float(cfg.DECLINE_START_MIN), float(cfg.DECLINE_START_MAX))

    late_decline = decline_start + float(cfg.LATE_DECLINE_BASE_ADD) + rng.gauss(0.0, float(cfg.LATE_DECLINE_SIGMA))
    late_decline = _clamp(
        late_decline,
        max(float(cfg.LATE_DECLINE_MIN_ABS), decline_start + float(cfg.LATE_DECLINE_MIN_DELTA)),
        float(cfg.LATE_DECLINE_MAX),
    )

    return {
        "player_id": pid,
        "ceiling_proxy": float(ceiling),
        "peak_age": float(peak),
        "decline_start_age": float(decline_start),
        "late_decline_age": float(late_decline),
    }


def ensure_profile(
    *,
    existing: Dict[str, Any] | None,
    player_id: str,
    attrs: Mapping[str, Any],
    pos: str,
    age: int,
) -> Dict[str, Any]:
    if existing:
        return dict(existing)
    return build_growth_profile(player_id=player_id, attrs=attrs, pos=pos, age=age)
