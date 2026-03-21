"""Configuration for team_situation evaluation.

This module centralizes tunable thresholds used by team need-tag generation so
behavior can be tuned without modifying core logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True, slots=True)
class TeamSituationNeedTagConfig:
    """Thresholds and scaling constants for role-based need-tag generation."""

    # Position shortage gate for prefixing tags (G_/W_/B_)
    pos_shortage_rel_max: float = 0.25
    pos_shortage_abs_max: int = 3

    # Coverage counting threshold (player role score -> "can cover role")
    coverage_score_min: float = 55.0

    # Need-score shaping
    low_score_center: float = 0.62
    low_score_span: float = 0.35
    low_cov_center: float = 0.35
    low_cov_span: float = 0.35

    # Usage-coverage mismatch adjustment
    usage_cov_delta_deadzone: float = 0.08
    usage_cov_delta_span: float = 0.40
    usage_cov_positive_boost: float = 0.20
    usage_cov_negative_penalty: float = 0.15

    # Final noise floor
    min_emit_weight: float = 0.18

    # Soft gate around min_emit_weight (sigmoid temperature)
    soft_gate_tau: float = 0.04

    # Efficiency-aware need amplification (phase-specific)
    eff_bad_pct_center_off: float = 0.45
    eff_bad_pct_center_def: float = 0.45

    eff_add_scale_off: float = 0.10
    eff_add_scale_def: float = 0.10

    eff_mult_scale_off: float = 0.20
    eff_mult_scale_def: float = 0.20

    # min_emit_weight dynamic relaxation at poor efficiency
    eff_emit_relax_max_off: float = 0.05
    eff_emit_relax_max_def: float = 0.05

    # Early-season dampening (apply with stat_trust-like signal)
    eff_early_dampen_min: float = 0.50


TEAM_SITUATION_NEED_TAG_CONFIG = TeamSituationNeedTagConfig()


@dataclass(frozen=True, slots=True)
class TeamSituationTierModelConfig:
    """Tunable parameters for season-progress blended tier classification."""

    # overall strength score weights
    overall_weight_star_power: float = 0.35
    overall_weight_depth: float = 0.65

    # in-season performance score weights
    perf_weight_win_pct: float = 0.45
    perf_weight_net_rating_norm: float = 0.25
    perf_weight_point_diff_norm: float = 0.15
    perf_weight_trend_norm: float = 0.10
    perf_weight_bubble_bonus: float = 0.05

    # bubble bonus gates (used only when progress >= bubble_bonus_min_progress)
    bubble_bonus_min_progress: float = 0.55
    bubble_bonus_gb6_max: float = 3.0
    bubble_bonus_gb10_max: float = 2.0
    # Raw bonus magnitudes before perf_weight_bubble_bonus multiplier.
    bubble_bonus_gb6_raw: float = 0.70
    bubble_bonus_gb10_raw: float = 0.62

    # performance weight schedule by season progress (piecewise linear)
    # format: ((progress, perf_weight), ...)
    performance_weight_points: Tuple[Tuple[float, float], ...] = (
        (0.00, 0.15),
        (0.18, 0.15),
        (0.49, 0.40),
        (0.73, 0.60),
        (0.85, 0.80),
        (1.00, 0.80),
    )

    # rank quota (must sum to 30 teams)
    rank_quota_contender: int = 5
    rank_quota_playoff_buyer: int = 5
    rank_quota_fringe: int = 8
    rank_quota_rebuild: int = 7
    rank_quota_tank: int = 5

    # soft boundary around rank cut lines
    rank_boundary_width: float = 1.25
    rank_boundary_tau: float = 0.55
    rank_tie_breaker: str = "team_id"

    # reset special-case guard
    reset_min_progress: float = 0.35
    reset_min_perf_weight: float = 0.30
    reset_min_overall_score: float = 0.840
    reset_max_perf_score: float = 0.42

    # early-season protection (prevent premature demotion when roster is strong)
    early_protection_max_progress: float = 0.18
    early_protection_high_overall_floor: float = 0.68
    early_protection_mid_overall_floor: float = 0.58

    def __post_init__(self) -> None:
        total_quota = (
            int(self.rank_quota_contender)
            + int(self.rank_quota_playoff_buyer)
            + int(self.rank_quota_fringe)
            + int(self.rank_quota_rebuild)
            + int(self.rank_quota_tank)
        )
        if total_quota != 30:
            raise ValueError(
                "TeamSituationTierModelConfig: rank quotas must sum to 30 "
                f"(got {total_quota})"
            )
        if float(self.rank_boundary_width) <= 0.0:
            raise ValueError("TeamSituationTierModelConfig: rank_boundary_width must be > 0")
        if float(self.rank_boundary_tau) <= 0.0:
            raise ValueError("TeamSituationTierModelConfig: rank_boundary_tau must be > 0")
        if str(self.rank_tie_breaker).strip().lower() != "team_id":
            raise ValueError("TeamSituationTierModelConfig: rank_tie_breaker currently supports only 'team_id'")


TEAM_SITUATION_TIER_MODEL = TeamSituationTierModelConfig()
