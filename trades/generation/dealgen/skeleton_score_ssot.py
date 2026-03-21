from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal, Mapping

Tier = Literal[
    "MVP",
    "ALL_NBA",
    "ALL_STAR",
    "HIGH_STARTER",
    "STARTER",
    "HIGH_ROTATION",
    "ROTATION",
    "GARBAGE",
]

TIER_POINTS: Final[Mapping[str, float]] = {
    "MVP": 26.0,
    "ALL_NBA": 18.0,
    "ALL_STAR": 12.0,
    "HIGH_STARTER": 8.0,
    "STARTER": 4.0,
    "HIGH_ROTATION": 2.0,
    "ROTATION": 1.0,
    "GARBAGE": 0.0,
}

PICK_POINTS: Final[Mapping[str, float]] = {
    "FIRST": 4.0,
    "SECOND": 0.5,
}

SCORE_TOLERANCE: Final[float] = 0.5


@dataclass(frozen=True, slots=True)
class ScoreTarget:
    tier: str
    required_score: float
    tolerance: float = SCORE_TOLERANCE


def normalize_tier(tier: str) -> str:
    t = str(tier).upper().strip()
    if t not in TIER_POINTS:
        raise ValueError(f"unknown tier: {tier!r}")
    return t


def target_required_score(tier: str) -> float:
    tier_u = normalize_tier(tier)
    required = float(TIER_POINTS[tier_u])
    return max(0.0, required)


def build_score_target(tier: str, *, tolerance: float = SCORE_TOLERANCE) -> ScoreTarget:
    tier_u = normalize_tier(tier)
    required = target_required_score(tier_u)
    tol = max(0.0, float(tolerance))
    return ScoreTarget(tier=tier_u, required_score=required, tolerance=tol)


def asset_points_for_pick(round_no: int) -> float:
    try:
        r = int(round_no)
    except Exception:
        return 0.0
    if r == 1:
        return float(PICK_POINTS["FIRST"])
    if r == 2:
        return float(PICK_POINTS["SECOND"])
    return 0.0


def is_score_satisfied(offered: float, required: float, tolerance: float = SCORE_TOLERANCE) -> bool:
    offered_f = float(offered)
    required_f = float(required)
    tol = max(0.0, float(tolerance))
    return offered_f >= (required_f - tol)


__all__ = [
    "Tier",
    "TIER_POINTS",
    "PICK_POINTS",
    "SCORE_TOLERANCE",
    "ScoreTarget",
    "normalize_tier",
    "target_required_score",
    "build_score_target",
    "asset_points_for_pick",
    "is_score_satisfied",
]
