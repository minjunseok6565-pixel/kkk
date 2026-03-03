from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RetirementConfig:
    # Consideration stage (whether player seriously considers retirement)
    consider_bias: float = -3.2
    consider_w_age: float = 1.9
    consider_w_injury_burden: float = 1.5
    consider_w_teamless: float = 0.55
    consider_w_ambition: float = -0.85
    consider_w_work_ethic: float = -0.65
    consider_w_adaptability: float = -0.35

    # Final decision stage (if considered)
    decision_bias: float = -2.4
    decision_w_age: float = 2.2
    decision_w_injury_burden: float = 2.0
    decision_w_teamless: float = 0.75
    decision_w_loyalty: float = 0.15
    decision_w_ego: float = 0.20
    decision_w_ambition: float = -1.05
    decision_w_work_ethic: float = -0.85
    decision_w_adaptability: float = -0.65
    decision_w_coachability: float = -0.30

    # Interactions
    interaction_age_injury_burden: float = 0.70
    interaction_teamless_loyalty: float = 0.20
    interaction_ambition_adaptability: float = -0.45

    # Injury burden composition (0..1 each term)
    injury_current_status_w: float = 0.24
    injury_recent_missed_w: float = 0.22
    injury_three_year_missed_w: float = 0.12
    injury_severe_w: float = 0.16
    injury_reinjury_w: float = 0.12
    injury_perm_drop_w: float = 0.14

    # Guards / clipping
    youth_age_guard: int = 31
    youth_prob_cap: float = 0.06
    elite_ovr_guard: int = 90
    elite_ovr_z_penalty: float = 0.45
    hard_floor_prob: float = 0.002
    hard_ceiling_prob: float = 0.92


DEFAULT_RETIREMENT_CONFIG = RetirementConfig()
