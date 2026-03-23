from __future__ import annotations

"""Configuration for the player agency subsystem.

All numbers here are intended to be *tunable* without rewriting logic.

Important:
- These defaults are conservative to avoid "everyone is angry" syndrome.
- For commercial quality, treat these as initial values; then tune with
  playtest telemetry.
"""

from dataclasses import dataclass, field
from typing import Dict, Mapping, Optional

from config import CAP_BASE_SALARY_CAP

from .month_context import MonthContextConfig
from .team_transition import TransitionConfig


ROLE_BUCKETS: tuple[str, ...] = (
    "UNKNOWN",
    "FRANCHISE",
    "STAR",
    "STARTER",
    "ROTATION",
    "BENCH",
    "GARBAGE",
)


# ---------------------------------------------------------------------------
# Expectations / leverage
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExpectationsConfig:
    """How we compute role bucket / leverage / expected minutes."""

    expected_mpg_by_role: Mapping[str, float] = field(
        default_factory=lambda: {
            "FRANCHISE": 36.0,
            "STAR": 34.0,
            "STARTER": 30.0,
            "ROTATION": 24.0,
            "BENCH": 16.0,
            "GARBAGE": 6.0,
            "UNKNOWN": 12.0,
        }
    )


    # v2: expected role evidence (starts/closes) by role bucket (0..1)
    expected_starts_rate_by_role: Mapping[str, float] = field(
        default_factory=lambda: {
            "FRANCHISE": 0.95,
            "STAR": 0.90,
            "STARTER": 0.80,
            "ROTATION": 0.25,
            "BENCH": 0.05,
            "GARBAGE": 0.00,
            "UNKNOWN": 0.30,
        }
    )

    expected_closes_rate_by_role: Mapping[str, float] = field(
        default_factory=lambda: {
            "FRANCHISE": 0.80,
            "STAR": 0.65,
            "STARTER": 0.45,
            "ROTATION": 0.08,
            "BENCH": 0.02,
            "GARBAGE": 0.00,
            "UNKNOWN": 0.10,
        }
    )

    # How sensitive role-status pressure is to gaps in starts/closes
    role_status_softness: float = 0.35
    role_status_start_weight: float = 0.65
    role_status_close_weight: float = 0.35

    # Leverage = w_ovr * ovr_rank_score + w_salary * salary_score
    leverage_weight_ovr: float = 0.75
    leverage_weight_salary: float = 0.25

    # If a player takes ~20% of team payroll, salary_score ~ 1.0
    salary_share_star: float = 0.20


# ---------------------------------------------------------------------------
# Frustration update (monthly EMA)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FrustrationConfig:
    """Monthly update parameters for frustration and trust."""

    # minutes frustration
    minutes_base_gain: float = 0.55
    minutes_decay: float = 0.15

    # DNP frequency pressure (separate from MPG gap; uses games_played/games_possible)
    dnp_grace_rate: float = 0.20
    dnp_softness_rate: float = 0.40
    dnp_pressure_weight: float = 0.65

    # team frustration
    team_base_gain: float = 0.35
    team_decay: float = 0.12


    # v2 axes frustration (EMA)
    role_base_gain: float = 0.40
    role_decay: float = 0.12

    contract_base_gain: float = 0.25
    contract_decay: float = 0.10

    health_base_gain: float = 0.35
    health_decay: float = 0.18

    chemistry_base_gain: float = 0.20
    chemistry_decay: float = 0.10

    usage_base_gain: float = 0.15
    usage_decay: float = 0.12

    # v2 role pressure composition
    role_minutes_weight: float = 0.65
    role_status_weight: float = 0.35

    # v2 fatigue aggregation (matches fatigue.LT_WEIGHT by default)
    health_lt_weight: float = 0.65

    # v2 fatigue-to-pressure mapping (grace + softness)
    health_fatigue_grace: float = 0.35
    health_fatigue_softness: float = 0.40

    # v2 chemistry pressure mapping
    chemistry_team_grace: float = 0.45
    chemistry_team_softness: float = 0.35

    # Trust updates (simple v1)
    trust_decay: float = 0.05
    trust_recovery: float = 0.03
    trust_bad_frustration_threshold: float = 0.60

    # Expected tolerance window (in minutes) depends on mental traits
    tolerance_base_mpg: float = 4.0
    tolerance_coachability_bonus: float = 4.0
    tolerance_loyalty_bonus: float = 2.0
    tolerance_adaptability_bonus: float = 2.0
    tolerance_ego_penalty: float = 4.0
    tolerance_ambition_penalty: float = 2.0
    tolerance_min_mpg: float = 1.0
    tolerance_max_mpg: float = 12.0

    # Injury multipliers for minutes frustration accumulation
    injury_out_multiplier: float = 0.05
    injury_returning_multiplier: float = 0.40

    # Team win% target (below this contributes to "badness")
    team_good_win_pct: float = 0.55

    # Team direction mismatch (WIN_NOW vs REBUILD/etc)
    #
    # Added in v2 to make "direction" conflicts show up even when win% is not terrible.
    team_strategy_weight: float = 0.45
    team_strategy_age_weight: float = 0.20
    team_strategy_values: Dict[str, float] = field(
        default_factory=lambda: {
            "WIN_NOW": 1.00,
            "BALANCED": 0.65,
            "DEVELOP": 0.45,
            "REBUILD": 0.25,
        }
    )


# ---------------------------------------------------------------------------
# Event thresholds / cooldowns
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EventConfig:
    """When to emit events and how long to cool down."""

    # Sample gating (mid-month trade attribution safety)
    #
    # We primarily gate on `inputs.games_played` which, after month attribution, is
    # games played on the evaluated team for that month. This prevents implausible
    # "I just arrived and already demand a trade" after 1 cameo game.
    #
    # Note: DNP months have games_played=0 but should still be eligible for complaints
    # and requests; tick.py handles this via a DNP override.
    min_games_for_events: int = 2

    # v2: shared escalation deltas (frustration above threshold)
    axis_escalate_delta_2: float = 0.10
    axis_escalate_delta_3: float = 0.22

    # v2: role/status issue events
    role_issue_threshold: float = 0.58
    role_issue_softness: float = 0.16
    role_issue_min_leverage: float = 0.25
    cooldown_role_days: int = 35

    # v2: contract/security issue events
    contract_issue_threshold: float = 0.60
    contract_issue_softness: float = 0.18
    contract_issue_min_leverage: float = 0.45
    cooldown_contract_days: int = 60

    # v2: health/load issue events
    health_issue_threshold: float = 0.58
    health_issue_softness: float = 0.20
    cooldown_health_days: int = 35

    # v2: chemistry/locker-room issue events
    chemistry_issue_threshold: float = 0.62
    chemistry_issue_softness: float = 0.18
    chemistry_issue_min_leverage: float = 0.20
    cooldown_chemistry_days: int = 50

    # v2: team-level locker room meeting (service pass)
    locker_room_meeting_threshold: float = 0.68
    locker_room_meeting_softness: float = 0.14
    locker_room_meeting_min_players: int = 8
    locker_room_meeting_cooldown_days: int = 55

    # v2: locker room social contagion (pushes chemistry frustration upward)
    locker_room_contagion_threshold: float = 0.50
    locker_room_contagion_strength: float = 0.06

    # v2: team direction concern (pre-help-demand)
    team_issue_threshold: float = 0.56
    team_issue_softness: float = 0.20
    team_issue_min_leverage: float = 0.55
    cooldown_team_days: int = 45

    # Minutes complaint
    minutes_complaint_threshold: float = 0.60
    minutes_complaint_min_leverage: float = 0.35
    minutes_complaint_ego_override: float = 0.75
    minutes_complaint_softness: float = 0.12  # how "probabilistic" the trigger is
    cooldown_minutes_days: int = 28

    cooldown_help_days: int = 60

    help_need_rotation_top_n: int = 8
    help_need_tags_max: int = 3
    help_need_allowed_tags: tuple[str, ...] = (
        "DEFENSE",
        "SPACING",
        "RIM_PRESSURE",
        "PRIMARY_INITIATOR",
        "SHOT_CREATION",
    )

    # Trade request
    trade_request_softness: float = 0.10
    trade_request_threshold_base: float = 0.82
    trade_request_threshold_loyalty_bonus: float = 0.12
    trade_request_threshold_ambition_bonus: float = -0.08

    trade_request_public_escalate_delta: float = 0.08  # additional score needed to go public

    cooldown_trade_days: int = 90


# ---------------------------------------------------------------------------
# FM-style negotiation / credibility / stances
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SelfExpectationsConfig:
    """How player self expectations drift away from team expectations.

    These values drive the *persistent* self_expected_* fields stored in
    player_agency_state.
    """

    mpg_delta_scale: float = 0.30
    rate_delta_scale: float = 0.22

    drift_up: float = 0.18
    drift_down: float = 0.10

    down_sticky_ego: float = 0.55
    down_sticky_ambition: float = 0.35

    min_self_mpg_floor: float = 6.0
    max_self_mpg_ceiling: float = 40.0


@dataclass(frozen=True, slots=True)
class CredibilityConfig:
    """How promise credibility is computed from trust + history."""

    recent_window: int = 6

    broken_total_w: float = 0.10
    broken_type_w: float = 0.18

    fulfilled_total_w: float = 0.05
    fulfilled_type_w: float = 0.08

    recent_broken_w: float = 0.10
    recent_fulfilled_w: float = 0.06

    # If credibility is below this, even a "good" offer may counter instead of accept.
    min_accept_cred: float = 0.20


@dataclass(frozen=True, slots=True)
class NegotiationConfig:
    """Offer evaluation thresholds for ACCEPT/COUNTER/REJECT."""

    max_rounds_base: int = 2
    expire_months: int = 1

    # MINUTES
    minutes_tol_base: float = 2.0
    minutes_tol_max: float = 6.0
    minutes_insult_extra: float = 3.0
    minutes_cred_bump_max: float = 4.0

    # ROLE (rates)
    role_tol_base: float = 0.10
    role_tol_max: float = 0.30
    role_insult_extra: float = 0.18
    role_cred_bump_max: float = 0.20

    # HELP (need tag matching)
    help_accept_min_match: int = 2
    help_counter_min_match: int = 1
    help_reject_min_match: int = 0

    # EXTENSION_TALKS
    ext_due_by_years_left: Dict[int, int] = field(default_factory=lambda: {0: 1, 1: 2, 2: 3})
    ext_tol_months: int = 1


@dataclass(frozen=True, slots=True)
class StanceConfig:
    """How dynamic stances change and decay over time."""

    skepticism_gain_broken: float = 0.10
    resentment_gain_broken: float = 0.10
    hardball_gain_broken: float = 0.08

    skepticism_relief_fulfilled: float = 0.06
    resentment_relief_fulfilled: float = 0.05
    hardball_relief_fulfilled: float = 0.04

    skepticism_decay: float = 0.03
    resentment_decay: float = 0.02
    hardball_decay: float = 0.02

    decay_trust_bonus: float = 0.04
    decay_work_ethic_bonus: float = 0.03
    decay_coachability_bonus: float = 0.02


@dataclass(frozen=True, slots=True)
class TradeOfferGrievanceConfig:
    """Tuning for trade-offer grievance triggers (non-monthly, event-driven)."""

    # PUBLIC_OFFER: deterministic targeted grievance bump (lower than leak).
    public_targeted_delta_base: float = 0.055
    public_targeted_delta_mental_weight: float = 0.055
    public_targeted_delta_status_weight: float = 0.040
    public_targeted_delta_context_weight: float = 0.020
    public_targeted_delta_resilience_weight: float = 0.040
    public_targeted_delta_min: float = 0.025
    public_targeted_delta_max: float = 0.140

    # PRIVATE_OFFER_LEAKED targeted grievance: deterministic delta with signal-based modulation.
    leaked_targeted_delta_base: float = 0.12
    leaked_targeted_delta_mental_weight: float = 0.10
    leaked_targeted_delta_status_weight: float = 0.08
    leaked_targeted_delta_context_weight: float = 0.05
    leaked_targeted_delta_resilience_weight: float = 0.06
    leaked_targeted_delta_min: float = 0.08
    leaked_targeted_delta_max: float = 0.30

    # trade_request_level policy: 0=full bump, 1=dampened bump, 2=max(skip)
    trade_request_level_max: int = 2
    leaked_targeted_active_request_dampen: float = 0.45

    same_pos_base_prob: float = 0.18
    same_pos_delta_base: float = 0.03
    same_pos_delta_scale: float = 0.08
    same_pos_min_leverage: float = 0.28
    same_pos_max_ovr_gap: int = 3
    same_pos_max_role_tier_gap: int = 2


# ---------------------------------------------------------------------------
# Player option / ETO decisions
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OptionsConfig:
    """Decision logic settings for PLAYER option and ETO."""

    # Expected salary curve (sigmoid mapping)
    #
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
    #   compatibility (older call sites/tests).
    salary_cap: Optional[float] = None
  
    expected_salary_ovr_center: float = 75.0
    expected_salary_ovr_scale: float = 7.0
  
    # Cap-normalized defaults derived from the legacy 2025 base-cap tuning:
    #   midpoint=18M, span=16M at cap=CAP_BASE_SALARY_CAP
    expected_salary_midpoint_cap_pct: float = 18_000_000.0 / float(CAP_BASE_SALARY_CAP)
    expected_salary_span_cap_pct: float = 16_000_000.0 / float(CAP_BASE_SALARY_CAP)
    expected_salary_midpoint: float = 18_000_000.0
    expected_salary_span: float = 16_000_000.0

    # Hard edges for deterministic decisions
    hard_exercise_ratio: float = 1.10  # option >= 110% market => exercise
    hard_decline_ratio: float = 0.90  # option <= 90% market => decline

    # Probabilistic zone (between hard edges)
    ambiguous_value_center: float = 0.98  # slight bias towards exercising when close

    # Logit weights (tunable)
    w_value: float = 6.0
    w_ambition: float = 1.2
    w_loyalty: float = -0.8
    w_ego: float = 0.3
    w_age: float = -0.6
    w_injury_risk: float = -0.8


# ---------------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AgencyConfig:
    expectations: ExpectationsConfig = field(default_factory=ExpectationsConfig)
    frustration: FrustrationConfig = field(default_factory=FrustrationConfig)
    events: EventConfig = field(default_factory=EventConfig)

    # FM-style interaction tuning
    self_exp: SelfExpectationsConfig = field(default_factory=SelfExpectationsConfig)
    credibility: CredibilityConfig = field(default_factory=CredibilityConfig)
    negotiation: NegotiationConfig = field(default_factory=NegotiationConfig)
    stance: StanceConfig = field(default_factory=StanceConfig)

    options: OptionsConfig = field(default_factory=OptionsConfig)

    # Month attribution policy (mid-month trades)
    month_context: MonthContextConfig = field(default_factory=MonthContextConfig)

    # Team transition policy (evaluated team != current roster team)
    transition: TransitionConfig = field(default_factory=TransitionConfig)

    # Event-driven trade-offer grievance tuning
    trade_offer_grievance: TradeOfferGrievanceConfig = field(default_factory=TradeOfferGrievanceConfig)

    # Names for mental attributes in attrs_json
    mental_attr_keys: Mapping[str, str] = field(
        default_factory=lambda: {
            "work_ethic": "M_WorkEthic",
            "coachability": "M_Coachability",
            "ambition": "M_Ambition",
            "loyalty": "M_Loyalty",
            "ego": "M_Ego",
            "adaptability": "M_Adaptability",
        }
    )

    # Event type strings (UI + analytics)
    event_types: Dict[str, str] = field(
        default_factory=lambda: {
            "minutes_complaint": "MINUTES_COMPLAINT",
            "trade_request": "TRADE_REQUEST",
            "trade_request_public": "TRADE_REQUEST_PUBLIC",
            "trade_targeted_offer_public": "TRADE_TARGETED_OFFER_PUBLIC",
            "trade_targeted_offer_leaked": "TRADE_TARGETED_OFFER_LEAKED",
            "same_pos_recruit_attempt": "SAME_POS_RECRUIT_ATTEMPT",

            # v2 issue families (stage-specific)
            "role_private": "ROLE_PRIVATE",
            "role_agent": "ROLE_AGENT",
            "role_public": "ROLE_PUBLIC",

            "contract_private": "CONTRACT_PRIVATE",
            "contract_agent": "CONTRACT_AGENT",
            "contract_public": "CONTRACT_PUBLIC",

            "health_private": "HEALTH_PRIVATE",
            "health_agent": "HEALTH_AGENT",
            "health_public": "HEALTH_PUBLIC",

            "team_private": "TEAM_PRIVATE",
            "team_agent": "TEAM_AGENT",
            "team_public": "TEAM_PUBLIC",

            "chemistry_private": "CHEMISTRY_PRIVATE",
            "chemistry_agent": "CHEMISTRY_AGENT",
            "chemistry_public": "CHEMISTRY_PUBLIC",

            # v2 team pass
            "locker_room_meeting": "LOCKER_ROOM_MEETING",

            # v3 negotiation / promise reactions
            "promise_negotiation": "PROMISE_NEGOTIATION",
            "broken_promise_private": "BROKEN_PROMISE_PRIVATE",
            "broken_promise_agent": "BROKEN_PROMISE_AGENT",
            "broken_promise_public": "BROKEN_PROMISE_PUBLIC",
        }
    )


DEFAULT_CONFIG = AgencyConfig()
