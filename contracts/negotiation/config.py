from __future__ import annotations

"""Tunable configuration for contract negotiations.

All numbers here are intended to be tuned via playtests/telemetry.

Design goals
-----------
- Avoid 'everyone is angry' syndrome: conservative defaults.
- Ensure star/high-leverage players can credibly push back.
- Ensure low-leverage players still have preferences but limited leverage.
"""

from dataclasses import dataclass
from typing import Optional

from config import CAP_BASE_SALARY_CAP


@dataclass(frozen=True, slots=True)
class ContractNegotiationConfig:
    # ---------------------------------------------------------------------
    # Session controls
    # ---------------------------------------------------------------------
    session_valid_days_default: int = 14

    # ---------------------------------------------------------------------
    # Offer validation (hard bounds)
    # ---------------------------------------------------------------------
    min_years_allowed: int = 1
    max_years_allowed: int = 5

    # ---------------------------------------------------------------------
    # Money representation
    # ---------------------------------------------------------------------
    salary_rounding: int = 10_000  # round AAV to nearest $10k for clean UI

    # ---------------------------------------------------------------------
    # Market reference curve
    # ---------------------------------------------------------------------
    # If agency.options is available, we default to using that curve for consistency
    # with option decisions. If not, we fall back to this local curve.
    use_agency_options_curve: bool = True

    expected_salary_ovr_center: float = 75.0
    expected_salary_ovr_scale: float = 7.0

    # IMPORTANT (cap-normalized salary scale)
    # --------------------------------------
    # Salary scales drift over long simulations if expressed as fixed dollar amounts
    # while the league salary cap grows season-to-season.
    #
    # Therefore, we express the curve in *shares of cap* (pct of cap) and convert
    # to dollars using the SSOT cap value (trade_rules.salary_cap) when available.
    #
    # - When `salary_cap` is provided: use cap-share ratios below.
    # - When missing: fall back to legacy absolute-dollar defaults for backward
    #   compatibility.
    salary_cap: Optional[float] = None

    # Cap-normalized defaults derived from the legacy 2025 base-cap tuning:
    #   midpoint=18M, span=16M at cap=CAP_BASE_SALARY_CAP
    expected_salary_midpoint_cap_pct: float = 18_000_000.0 / float(CAP_BASE_SALARY_CAP)
    expected_salary_span_cap_pct: float = 16_000_000.0 / float(CAP_BASE_SALARY_CAP)
    
    expected_salary_midpoint: float = 18_000_000.0
    expected_salary_span: float = 16_000_000.0

    # ---------------------------------------------------------------------
    # Ask / floor construction
    # ---------------------------------------------------------------------
    ask_mult_min: float = 0.85
    ask_mult_max: float = 1.45

    floor_mult_min: float = 0.78
    floor_mult_max: float = 1.35

    # Base gap between ask and floor in multiplier space.
    base_floor_gap: float = 0.08

    # Team performance reference (below => 'misery' pressure)
    good_team_win_pct: float = 0.55

    # Ask premium weights (soft modifiers)
    w_ambition_ask: float = 0.14
    w_ego_ask: float = 0.10
    w_leverage_ask: float = 0.12
    w_frustration_ask: float = 0.06
    w_bad_team_ask: float = 0.10

    # Discount weight when loyalty+trust are high (team-friendly)
    w_loyalty_trust_discount_ask: float = 0.10

    # Floor rigidity weights (how close floor is to ask)
    w_floor_gap_ego: float = 0.06
    w_floor_gap_ambition: float = 0.04
    w_floor_gap_coachability: float = -0.06
    w_floor_gap_loyalty: float = -0.04
    w_floor_gap_trust: float = -0.03

    # ---------------------------------------------------------------------
    # Years preference
    # ---------------------------------------------------------------------
    years_mismatch_penalty: float = 0.03  # premium required per-year mismatch when preference is strong

    # Contract option value in negotiation (player perspective)
    # TEAM option: player-unfriendly -> requires higher money
    # PLAYER option: player-friendly -> can accept slightly lower money
    team_option_penalty_per_year: float = 0.03
    player_option_bonus_per_year: float = 0.03
    option_value_cap: float = 0.10

    # ---------------------------------------------------------------------
    # Counter behavior
    # ---------------------------------------------------------------------
    # If offer is within this margin below (adjusted) floor, prefer COUNTER over REJECT.
    counter_near_margin: float = 0.08

    # Counter step (min increase over offer when countering on money)
    min_counter_step_abs: float = 250_000.0
    min_counter_step_pct_of_market: float = 0.015

    # Ask decay schedule for counters over rounds:
    # target = floor + (ask-floor) * max(0, 1 - progress*(ask_decay_base + ask_decay_by_concession*concession_rate))
    ask_decay_base: float = 0.85
    ask_decay_by_concession: float = 0.55

    # Concession rate base + modifiers (0..1)
    concession_base: float = 0.22
    concession_min: float = 0.10
    concession_max: float = 0.38

    concession_w_coachability: float = 0.12
    concession_w_adaptability: float = 0.08
    concession_w_trust: float = 0.06
    concession_w_ego: float = -0.10
    concession_w_ambition: float = -0.06

    # ---------------------------------------------------------------------
    # Insult / walkout
    # ---------------------------------------------------------------------
    insult_ratio_base: float = 0.92
    insult_ratio_min: float = 0.86
    insult_ratio_max: float = 0.97

    insult_w_ego: float = 0.06
    insult_w_leverage: float = 0.03

    lowball_strikes_to_walk: int = 2
    walk_if_round_exceeded: bool = True

    # ---------------------------------------------------------------------
    # Patience / max rounds
    # ---------------------------------------------------------------------
    patience_base: float = 0.50
    patience_w_coachability: float = 0.12
    patience_w_loyalty: float = 0.10
    patience_w_adaptability: float = 0.08
    patience_w_trust: float = 0.08
    patience_w_ego: float = -0.10
    patience_w_ambition: float = -0.06

    rounds_min: int = 2
    rounds_max: int = 6

    # ---------------------------------------------------------------------
    # Non-monetary gates (optional; best used after agency response system exists)
    # ---------------------------------------------------------------------
    enable_non_monetary_gate: bool = False

    require_minutes_promise_threshold: float = 0.72
    require_help_promise_threshold: float = 0.62


DEFAULT_CONTRACT_NEGOTIATION_CONFIG = ContractNegotiationConfig()
