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

ContractTag = Literal["OVERPAY", "FAIR", "VALUE"]


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

CONTRACT_TAG_BONUS: Final[Mapping[str, float]] = {
    "OVERPAY": -1.0,
    "FAIR": 0.0,
    "VALUE": 1.0,
}

SCORE_TOLERANCE: Final[float] = 0.5


@dataclass(frozen=True, slots=True)
class ScoreTarget:
    tier: str
    contract_tag: str
    required_score: float
    tolerance: float = SCORE_TOLERANCE


def normalize_tier(tier: str) -> str:
    t = str(tier).upper().strip()
    if t not in TIER_POINTS:
        raise ValueError(f"unknown tier: {tier!r}")
    return t


def normalize_contract_tag(tag: str) -> str:
    t = str(tag).upper().strip()
    if t not in CONTRACT_TAG_BONUS:
        raise ValueError(f"unknown contract tag: {tag!r}")
    return t


def target_required_score(tier: str, contract_tag: str) -> float:
    tier_u = normalize_tier(tier)
    tag_u = normalize_contract_tag(contract_tag)
    required = float(TIER_POINTS[tier_u]) + float(CONTRACT_TAG_BONUS[tag_u])
    return max(0.0, required)


def build_score_target(tier: str, contract_tag: str, *, tolerance: float = SCORE_TOLERANCE) -> ScoreTarget:
    tier_u = normalize_tier(tier)
    tag_u = normalize_contract_tag(contract_tag)
    required = target_required_score(tier_u, tag_u)
    tol = max(0.0, float(tolerance))
    return ScoreTarget(tier=tier_u, contract_tag=tag_u, required_score=required, tolerance=tol)


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
    "ContractTag",
    "TIER_POINTS",
    "PICK_POINTS",
    "CONTRACT_TAG_BONUS",
    "SCORE_TOLERANCE",
    "ScoreTarget",
    "normalize_tier",
    "normalize_contract_tag",
    "target_required_score",
    "build_score_target",
    "asset_points_for_pick",
    "is_score_satisfied",
]
