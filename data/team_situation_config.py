"""Configuration for team_situation evaluation.

This module centralizes tunable thresholds used by team need-tag generation so
behavior can be tuned without modifying core logic.
"""

from __future__ import annotations

from dataclasses import dataclass


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
