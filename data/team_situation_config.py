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


TEAM_SITUATION_NEED_TAG_CONFIG = TeamSituationNeedTagConfig()
