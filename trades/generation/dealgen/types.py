from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from ...errors import (
    TradeError,
    DEAL_INVALIDATED,
    ROSTER_LIMIT,
    ASSET_LOCKED,
    PLAYER_NOT_OWNED,
    PICK_NOT_OWNED,
    SWAP_NOT_OWNED,
    SWAP_NOT_FOUND,
    SWAP_INVALID,
    TRADE_DEADLINE_PASSED,
    TRADE_DEADLINE_INVALID,
    DUPLICATE_ASSET,
)
from ...models import Deal
from ...valuation.types import DealDecision, TeamDealEvaluation


# Public DTOs
# =============================================================================


@dataclass(frozen=True, slots=True)
class DealGeneratorConfig:
    """DealGenerator 탐색/수리/복잡도 예산.

    - 이 값들은 "base"이며, generate_for_team() 호출 시 팀 posture/urgency/deadline_pressure에 따라
      동적으로 스케일링된 DealGeneratorBudget이 실제로 사용된다.
    - "상업용" 목표: 어떤 팀/어떤 시즌에서도 틱당 계산량이 폭주하지 않도록 상한을 둔다.
    """

    # --- hard upper bounds (absolute safety)
    max_targets_hard: int = 28
    max_attempts_per_target_hard: int = 80
    max_validations_hard: int = 900
    max_evaluations_hard: int = 450

    # --- base budgets (scaled)
    base_max_targets: int = 14
    base_beam_width: int = 12
    base_max_attempts_per_target: int = 45
    base_max_validations: int = 360
    base_max_evaluations: int = 180
    base_max_repairs: int = 2

    # --- "young + pick" heuristic
    # asset_catalog에 YOUNG 버킷이 없으므로 generator-side에서 정의한다.
    # 기존(v1): age-only(<= 24.5)
    # 변경: age + team control(remaining_years) 기반 (fallback으로 age-only 완화)
    young_age_max: float = 24.5
    young_min_control_years: float = 2.0

    # --- young split (prospect vs throw-in)  [v2 parity]
    # Throw-in: cheap young bodies
    young_throwin_max_market: float = 22.0
    # Prospect pool: top fraction among young controllable (by market.total desc)
    young_prospect_top_frac: float = 0.35
    young_prospect_max_candidates: int = 6
    young_throwin_max_candidates: int = 6

    # --- deal shape constraints (generator-side)
    skeleton_overhaul_enabled: bool = True
    skeleton_modifiers_enabled: bool = True
    modifier_max_variants_per_candidate: int = 3
    modifier_protection_enabled: bool = True
    modifier_swap_substitute_enabled: bool = True
    modifier_protection_default_ladder: Tuple[str, ...] = ("prot_light", "prot_mid", "prot_heavy")
    skeleton_gate_strictness: float = 0.35
    skeleton_false_negative_bias: float = 0.75
    max_assets_per_side: int = 9
    max_players_moved_total: int = 7
    max_players_per_side: int = 4
    max_picks_per_side: int = 4
    max_seconds_per_side: int = 4

    # --- target tier routing (phase-4)
    skeleton_route_role: Tuple[str, ...] = (
        "compat.picks_only",
        "compat.young_plus_pick",
        "compat.p4p_salary",
        "compat.consolidate_2_for_1",
        "player_swap.role_swap_small_delta",
        "player_swap.fit_swap_2_for_2",
        "player_swap.one_for_two_depth",
        "player_swap.bench_bundle_for_role",
        "player_swap.change_of_scenery_young",
        "timeline.veteran_for_young",
        "salary_cleanup.rental_expiring_plus_second",
        "salary_cleanup.pure_absorb_for_asset",
        "salary_cleanup.partial_dump_for_expiring",
        "salary_cleanup.bad_money_swap",
        "pick_engineering.first_split",
        "pick_engineering.second_ladder_to_protected_first",
        "pick_engineering.swap_purchase",
        "pick_engineering.swap_substitute_for_first",
    )
    skeleton_route_starter: Tuple[str, ...] = (
        "compat.picks_only",
        "compat.young_plus_pick",
        "compat.p4p_salary",
        "compat.consolidate_2_for_1",
        "player_swap.role_swap_small_delta",
        "player_swap.fit_swap_2_for_2",
        "player_swap.one_for_two_depth",
        "player_swap.starter_for_two_rotation",
        "player_swap.three_for_one_upgrade",
        "player_swap.bench_bundle_for_role",
        "player_swap.change_of_scenery_young",
        "timeline.veteran_for_young",
        "timeline.veteran_for_young_plus_protected_first",
        "salary_cleanup.rental_expiring_plus_second",
        "salary_cleanup.pure_absorb_for_asset",
        "salary_cleanup.partial_dump_for_expiring",
        "salary_cleanup.bad_money_swap",
        "pick_engineering.first_split",
        "pick_engineering.second_ladder_to_protected_first",
        "pick_engineering.swap_purchase",
        "pick_engineering.swap_substitute_for_first",
    )
    skeleton_route_high_starter: Tuple[str, ...] = (
        "compat.picks_only",
        "compat.young_plus_pick",
        "compat.p4p_salary",
        "compat.consolidate_2_for_1",
        "player_swap.role_swap_small_delta",
        "player_swap.fit_swap_2_for_2",
        "player_swap.one_for_two_depth",
        "player_swap.starter_for_two_rotation",
        "player_swap.three_for_one_upgrade",
        "player_swap.star_lateral_plus_delta",
        "timeline.veteran_for_young",
        "timeline.veteran_for_young_plus_protected_first",
        "timeline.bluechip_plus_first_plus_swap",
        "salary_cleanup.rental_expiring_plus_second",
        "salary_cleanup.pure_absorb_for_asset",
        "salary_cleanup.partial_dump_for_expiring",
        "salary_cleanup.bad_money_swap",
        "pick_engineering.first_split",
        "pick_engineering.second_ladder_to_protected_first",
        "pick_engineering.swap_purchase",
        "pick_engineering.swap_substitute_for_first",
    )
    skeleton_route_pick_only: Tuple[str, ...] = (
        "compat.picks_only",
        "pick_engineering.first_split",
        "pick_engineering.second_ladder_to_protected_first",
        "pick_engineering.swap_purchase",
        "pick_engineering.swap_substitute_for_first",
        "salary_cleanup.pure_absorb_for_asset",
    )

    # --- sweetener loop
    sweetener_enabled: bool = True
    sweetener_max_additions: int = 2
    sweetener_max_deficit: float = 10.0  # "조금 부족"(margin deficit)만 수리
    # Sweetener activation window (v2 absorption: scale by receiver's outgoing_total)
    # corridor = clamp(floor, ratio * max(outgoing_total, 6.0), cap)
    # effective corridor = min(corridor, sweetener_max_deficit)  # 기존 상한은 안전장치로 존중
    sweetener_close_corridor_ratio: float = 0.12
    sweetener_close_floor: float = 0.6
    sweetener_close_cap: float = 8.0
    sweetener_min_improvement: float = 0.25  # score 또는 margin 개선이 거의 없으면 중단
    sweetener_try_buckets: Tuple[str, ...] = (
        "SECOND",
        "SWAP",
        "FIRST_SAFE",
        "SECOND",  # allow 2nd second-rounder
        "FIRST_SENSITIVE",
    )
    # sweetener 후보를 token(bucket)별로 몇 개까지 trial 할지(베스트-오브-N).
    # 예산이 빡빡하면 sweetener.py에서 자동으로 더 줄인다.
    sweetener_candidate_width: int = 3
    
    # --- fit swap counter (DecisionReason: FIT_FAILS)
    # FIT_FAILS(=상대가 받는 incoming 플레이어들의 team-fit 불만)일 때,
    # outgoing 플레이어 1명을 "더 맞는 선수"로 교체해보는 카운터를 시도한다.
    fit_swap_enabled: bool = True
    # base(=sweetener 이전) 딜 하나당 fit-swap 시도 상한
    fit_swap_max_trials_per_base: int = 1
    # replacement 후보 풀/시도 제한
    fit_swap_candidate_pool: int = 18
    fit_swap_try_top_n: int = 6
    # 얼마나 fit이 좋아져야 교체 후보로 인정할지
    fit_swap_min_fit_improvement: float = 0.02
    # 샐러리 급격히 달라져 수리 비용이 폭증하는 것을 방지(단위: $M)
    fit_swap_max_salary_diff_m: float = 10.0
    # fit-swap 카운터에서 허용하는 최대 repair 횟수(최소 수리)
    fit_swap_max_repairs: int = 1

    # --- fit swap horizon-aware scoring (v2 absorption)
    # receiver(=FIT_FAILS 낸 팀)의 타임라인/포스처 성향에 따라
    # replacement 후보 랭킹 primary_score를 youth/fit/market_norm 가중합으로 계산한다.
    # primary_score = w_youth*youth + w_fit*fit + w_market*market_norm
    #
    # - REBUILD: youth/years 우선, market은 약하게 감점
    # - WIN_NOW: fit + market(즉시전력) 우선
    # - NEUTRAL: 균형
    #
    # NOTE: fit_swap.py에서만 사용하며, False면 기존(v1)처럼 fit 중심으로 랭킹한다.
    fit_swap_use_horizon_weights: bool = True

    # market normalization: market_norm = market_total / divisor
    fit_swap_market_norm_divisor: float = 50.0

    # youth score shaping:
    # youth = max(0, age_anchor - age) / age_span  +  min(years_cap, remaining_years) / years_span
    fit_swap_youth_age_anchor: float = 30.0
    fit_swap_youth_age_span: float = 10.0
    fit_swap_youth_years_cap: float = 4.0
    fit_swap_youth_years_span: float = 4.0

    # weights are (w_youth, w_fit, w_market)
    fit_swap_weights_rebuild: Tuple[float, float, float] = (0.55, 0.40, -0.05)
    fit_swap_weights_win_now: Tuple[float, float, float] = (0.05, 0.70, 0.25)
    fit_swap_weights_neutral: Tuple[float, float, float] = (0.20, 0.60, 0.20)

    # --- target diversity / spam prevention (v2 absorption)
    # 동일 타깃(같은 선수)이 결과 상단에 반복 노출되는 것을 억제하기 위한 soft penalty.
    # v2는 core 단계에서 target_seen 카운트 기반으로 score를 감점한다.
    target_repeat_penalty: float = 0.15

    # --- market signal priority boosts (sell target ordering)
    public_request_priority_boost: float = 0.55
    public_request_priority_boost_cap: float = 1.25

    # --- listing interest priority boosts (buy target ordering)
    buy_target_listing_interest_enabled: bool = True
    buy_target_listing_interest_boost_base: float = 0.25
    buy_target_listing_interest_priority_scale: float = 0.35
    buy_target_listing_interest_recency_half_life_days: float = 7.0
    buy_target_listing_interest_need_weight_scale: float = 0.25
    buy_target_listing_interest_cap: float = 0.85

    # --- buy retrieval: tiered market scan (stage 1 foundation)
    buy_target_listed_min_quota: int = 6
    buy_target_listed_max_share: float = 0.75
    buy_target_non_listed_base_quota: int = 8
    buy_target_non_listed_deadline_bonus_max: int = 12

    buy_target_max_teams_scanned_base: int = 8
    buy_target_max_teams_scanned_deadline_bonus: int = 18
    buy_target_max_players_scanned_base: int = 120
    buy_target_max_players_scanned_deadline_bonus: int = 220

    buy_target_expand_tier2_enabled: bool = True
    buy_target_expand_tier2_budget_share: float = 0.35
    buy_target_retrieval_iteration_cap: int = 400

    buy_target_need_weight_scale: float = 0.55
    buy_target_need_mismatch_floor: float = -0.20
    buy_target_market_weight: float = 0.30
    buy_target_fit_weight: float = 0.45
    buy_target_salary_penalty_weight: float = 0.20
    buy_target_salary_penalty_cap: float = 0.35

    # --- proactive listing controls (AI)
    ai_proactive_listing_enabled: bool = True
    ai_proactive_listing_team_daily_cap: int = 2
    ai_proactive_listing_team_active_cap: int = 4
    ai_proactive_listing_player_cooldown_days: int = 7
    ai_proactive_listing_ttl_days_sell: int = 12
    ai_proactive_listing_ttl_days_soft_sell: int = 7
    ai_proactive_listing_ttl_days_default: int = 5
    ai_proactive_listing_priority_base: float = 0.45
    ai_proactive_listing_priority_span: float = 0.35

    # proactive listing cadence (listing only; proposal generation cadence is unchanged)
    ai_proactive_listing_cadence: str = "WEEKLY"  # DAILY | WEEKLY
    ai_proactive_listing_anchor_weekday: int = 0  # 0=Mon .. 6=Sun

    # proactive listing threshold gating
    ai_proactive_listing_threshold_enabled: bool = True
    ai_proactive_listing_threshold_default: float = 0.55
    ai_proactive_listing_bucket_thresholds: Dict[str, Dict[str, float]] = field(default_factory=lambda: {
        "AGGRESSIVE_BUY": {
            "SURPLUS_LOW_FIT": 0.30,
            "SURPLUS_REDUNDANT": 0.35,
            "CONSOLIDATE": 0.55,
            "FILLER_CHEAP": 0.65,
            "FILLER_BAD_CONTRACT": 0.80,
            "VETERAN_SALE": 0.90,
        },
        "SOFT_BUY": {
            "SURPLUS_LOW_FIT": 0.38,
            "SURPLUS_REDUNDANT": 0.42,
            "CONSOLIDATE": 0.60,
            "FILLER_CHEAP": 0.68,
            "FILLER_BAD_CONTRACT": 0.82,
            "VETERAN_SALE": 0.92,
        },
        "STAND_PAT": {
            "SURPLUS_LOW_FIT": 0.50,
            "SURPLUS_REDUNDANT": 0.55,
            "CONSOLIDATE": 0.70,
            "FILLER_CHEAP": 0.72,
            "FILLER_BAD_CONTRACT": 0.86,
            "VETERAN_SALE": 0.95,
        },
        "SOFT_SELL": {
            "SURPLUS_LOW_FIT": 0.40,
            "SURPLUS_REDUNDANT": 0.45,
            "CONSOLIDATE": 0.85,
            "FILLER_CHEAP": 0.62,
            "FILLER_BAD_CONTRACT": 0.70,
            "VETERAN_SALE": 0.45,
        },
        "SELL": {
            "SURPLUS_LOW_FIT": 0.32,
            "SURPLUS_REDUNDANT": 0.38,
            "CONSOLIDATE": 0.90,
            "FILLER_CHEAP": 0.58,
            "FILLER_BAD_CONTRACT": 0.62,
            "VETERAN_SALE": 0.35,
        },
    })

    # threshold modifiers
    ai_proactive_listing_threshold_horizon_win_now_delta: float = -0.03
    ai_proactive_listing_threshold_horizon_rebuild_delta: float = -0.05
    ai_proactive_listing_threshold_urgency_cut: float = 0.75
    ai_proactive_listing_threshold_urgency_delta: float = -0.03
    ai_proactive_listing_threshold_cooldown_active_delta: float = 0.05
    ai_proactive_listing_threshold_min: float = 0.10
    ai_proactive_listing_threshold_max: float = 0.95

    # --- opponent diversity / spam prevention
    opponent_repeat_penalty: float = 0.25
    opponent_multi_repeat_penalty: float = 0.18

    # Final output hard cap: 같은 상대팀(파트너) 반복 상한. 0이면 비활성(기존 동작 유지).
    # - BUY 모드: seller_id 기준
    # - SELL 모드: buyer_id 기준
    max_partner_repeats: int = 0

    # --- scoring
    score_sigmoid_scale: float = 8.0
    penalty_per_asset: float = 0.15
    penalty_per_player: float = 0.10

    # deficit(손해) 패널티: 양쪽 모두에 적용 (buyer는 더 강하게)
    penalty_overpay_weight: float = 1.00

    penalty_opponent_overpay_weight: float = 0.85

    # REJECT를 강하게 벌점(유저 체감상 "말도 안 되는 오퍼" 상위 노출 방지)
    reject_penalty_base: float = 0.35
    reject_penalty_scale: float = 0.06

    # discard gate: 평가 결과가 너무 나쁘면 후보에서 제거
    discard_if_overpay_below: float = -18.0  # buyer margin이 이보다 더 나쁘면 후보 폐기
    discard_if_any_margin_below: float = -22.0  # 어느 한쪽이 이보다 나쁘면 폐기
    discard_if_reject_margin_below: float = -14.0  # REJECT인 팀 margin이 이보다 나쁘면 폐기
    discard_if_both_margins_below: float = -10.0

    # --- RNG determinism
    deterministic_seed_salt: str = "deal_generator_v2"

    # --- catalog behavior
    # allow_locked_by_deal_id가 주어진 경우, catalog를 1회 재빌드하여 locked asset을 풀어줄지
    rebuild_catalog_when_allow_locked: bool = True

    # --- soft guard (invalid 폭발 방지)
    # 딜 적용 후 추정 payroll_after가 second_apron 이상이면 one-for-one 형태만 남긴다(soft).
    # (SSOT: SalaryMatchingRule은 payroll_after 기반으로 apron status를 판정한다)
    soft_guard_second_apron_by_constraints: bool = True


@dataclass(frozen=True, slots=True)
class DealGeneratorBudget:
    """팀 posture/urgency 기반으로 스케일된 실제 예산."""

    max_targets: int
    beam_width: int
    max_attempts_per_target: int
    max_validations: int
    max_evaluations: int
    max_repairs: int


@dataclass(frozen=True, slots=True)
class DealProposal:
    deal: Deal
    buyer_id: str
    seller_id: str
    buyer_decision: DealDecision
    seller_decision: DealDecision
    buyer_eval: TeamDealEvaluation
    seller_eval: TeamDealEvaluation
    score: float
    tags: Tuple[str, ...] = tuple()


@dataclass(slots=True)
class DealGeneratorStats:
    """운영/튜닝용 통계(외부 로그/텔레메트리로 보내기 좋음)."""

    mode: str = "BUY"
    targets_considered: int = 0
    skeletons_built: int = 0
    candidates_attempted: int = 0

    validations: int = 0
    evaluations: int = 0
    repairs: int = 0

    sweetener_attempts: int = 0
    sweeteners_added: int = 0

    # sweetener telemetry (v2-style)
    sweetener_trials: int = 0
    sweetener_commits: int = 0
    sweetener_rollbacks: int = 0

    # fit swap counter telemetry
    fit_swap_triggers: int = 0
    fit_swap_candidates_tried: int = 0
    fit_swap_success: int = 0

    # hard-cap monitoring
    budget_validation_cap_hits: int = 0
    budget_evaluation_cap_hits: int = 0
    hard_validation_cap_hits: int = 0
    hard_evaluation_cap_hits: int = 0

    # skeleton observability (phase-5)
    unique_skeleton_count: int = 0
    modifier_candidates: int = 0
    modifier_applied_candidates: int = 0
    modifier_success_rate: float = 0.0

    skeleton_id_counts: Dict[str, int] = field(default_factory=dict)
    skeleton_domain_counts: Dict[str, int] = field(default_factory=dict)
    target_tier_counts: Dict[str, int] = field(default_factory=dict)
    arch_compat_counts: Dict[str, int] = field(default_factory=dict)
    modifier_trace_counts: Dict[str, int] = field(default_factory=dict)

    # failure kind -> count
    failures_by_kind: Dict[str, int] = field(default_factory=dict)

    def bump_failure(self, kind: str) -> None:
        self.failures_by_kind[kind] = int(self.failures_by_kind.get(kind, 0)) + 1

    def bump_counter(self, bucket: Dict[str, int], key: str) -> None:
        k = str(key or "")
        if not k:
            return
        bucket[k] = int(bucket.get(k, 0)) + 1


# =============================================================================
# Internal DTOs
# =============================================================================


@dataclass(frozen=True, slots=True)
class TargetCandidate:
    """BUY 모드에서 buyer가 원하는 incoming target 후보."""

    player_id: str
    from_team: str
    need_tag: str
    tag_strength: float
    market_total: float
    salary_m: float
    remaining_years: float
    age: Optional[float]


@dataclass(frozen=True, slots=True)
class SellAssetCandidate:
    """SELL 모드에서 initiator(=seller)가 시장에 내놓을 outgoing 후보."""

    player_id: str
    market_total: float
    salary_m: float
    remaining_years: float
    is_expiring: bool
    top_tags: Tuple[str, ...]


@dataclass(slots=True)
class DealCandidate:
    """탐색 중인 후보 딜(스켈레톤/수리 과정에서 mutate 가능)."""

    deal: Deal
    buyer_id: str
    seller_id: str

    # for debug/tagging
    focal_player_id: str
    archetype: str

    # v3 skeleton metadata (phase-1 compatibility introduction)
    skeleton_id: str = ""
    skeleton_domain: str = ""
    target_tier: str = ""
    compat_archetype: str = ""
    modifier_trace: List[str] = field(default_factory=list)

    tags: List[str] = field(default_factory=list)
    repairs_used: int = 0


# =============================================================================
# TradeError parsing (SSOT: TradeError.code + TradeError.details)
# =============================================================================


class RuleFailureKind(str, Enum):
    DEADLINE = "deadline"
    SALARY_MATCHING = "salary_matching"
    ROSTER_LIMIT = "roster_limit"
    ASSET_LOCK = "asset_lock"
    PLAYER_ELIGIBILITY = "player_eligibility"
    RETURN_TO_TRADING_TEAM = "return_to_trading_team_same_season"
    PICK_RULES = "pick_rules"
    OWNERSHIP = "ownership"
    DUPLICATE_ASSET = "duplicate_asset"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class RuleFailure:
    kind: RuleFailureKind
    code: str
    message: str
    rule_id: Optional[str] = None
    team_id: Optional[str] = None
    to_team: Optional[str] = None
    reason: Optional[str] = None
    method: Optional[str] = None
    status: Optional[str] = None
    player_id: Optional[str] = None
    pick_id: Optional[str] = None
    swap_id: Optional[str] = None
    asset_key: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)


def parse_trade_error(err: TradeError) -> RuleFailure:
    """TradeError -> RuleFailure.

    - 대부분의 rules는 TradeError.details에 {"rule": rule_id, ...}를 넣는다.
    - 일부는 code로만 구분한다(예: ROSTER_LIMIT, ASSET_LOCKED, DUPLICATE_ASSET, OWNERSHIP 계열).
    """

    details: Dict[str, Any] = {}
    if isinstance(getattr(err, "details", None), dict):
        details = dict(err.details)  # shallow copy

    # --- code-first
    if err.code in (TRADE_DEADLINE_PASSED, TRADE_DEADLINE_INVALID):
        return RuleFailure(
            kind=RuleFailureKind.DEADLINE,
            code=err.code,
            message=err.message,
            rule_id="deadline",
            details=details,
        )
    if err.code == ROSTER_LIMIT:
        return RuleFailure(
            kind=RuleFailureKind.ROSTER_LIMIT,
            code=err.code,
            message=err.message,
            rule_id="roster_limit",
            team_id=str(details.get("team_id") or "") or None,
            details=details,
        )
    if err.code == ASSET_LOCKED:
        return RuleFailure(
            kind=RuleFailureKind.ASSET_LOCK,
            code=err.code,
            message=err.message,
            rule_id="asset_lock",
            asset_key=str(details.get("asset_key") or "") or None,
            details=details,
        )
    if err.code in (PLAYER_NOT_OWNED, PICK_NOT_OWNED, SWAP_NOT_OWNED, SWAP_NOT_FOUND, SWAP_INVALID):
        player_id = str(details.get("player_id") or "") or None
        pick_id = str(details.get("pick_id") or "") or None
        swap_id = str(details.get("swap_id") or "") or None

        # (C) ownership 실패가 반복될 때 예산 낭비를 줄이기 위해 asset_key를 채운다.
        ak: Optional[str] = None
        if err.code == PLAYER_NOT_OWNED and player_id:
            ak = f"player:{player_id}"
        elif err.code == PICK_NOT_OWNED and pick_id:
            ak = f"pick:{pick_id}"
        elif err.code in (SWAP_NOT_OWNED, SWAP_NOT_FOUND, SWAP_INVALID) and swap_id:
            ak = f"swap:{swap_id}"

        return RuleFailure(
            kind=RuleFailureKind.OWNERSHIP,
            code=err.code,
            message=err.message,
            rule_id="ownership",
            team_id=str(details.get("team_id") or "") or None,
            player_id=player_id,
            pick_id=pick_id,
            swap_id=swap_id,
            asset_key=ak,
            details=details,
        )
    if err.code == DUPLICATE_ASSET:
        return RuleFailure(
            kind=RuleFailureKind.DUPLICATE_ASSET,
            code=err.code,
            message=err.message,
            rule_id="duplicate_asset",
            asset_key=str(details.get("asset_key") or "") or None,
            details=details,
        )

    # --- details["rule"]
    rule_id = details.get("rule") if isinstance(details.get("rule"), str) else None
    if err.code == DEAL_INVALIDATED and rule_id == "salary_matching":
        method = str(details.get("method") or "")
        return RuleFailure(
            kind=RuleFailureKind.SALARY_MATCHING,
            code=err.code,
            message=err.message,
            rule_id=rule_id,
            team_id=str(details.get("team_id") or "") or None,
            method=method or None,
            status=str(details.get("status") or "") or None,
            details=details,
        )

    if err.code == DEAL_INVALIDATED and rule_id == "player_eligibility":
        return RuleFailure(
            kind=RuleFailureKind.PLAYER_ELIGIBILITY,
            code=err.code,
            message=err.message,
            rule_id=rule_id,
            team_id=str(details.get("team_id") or "") or None,
            reason=str(details.get("reason") or "") or None,
            player_id=str(details.get("player_id") or "") or None,
            details=details,
        )

    if err.code == DEAL_INVALIDATED and rule_id == "return_to_trading_team_same_season":
        to_team = str(details.get("to_team") or "") or None
        to_team = str(to_team).upper() if to_team else None
        return RuleFailure(
            kind=RuleFailureKind.RETURN_TO_TRADING_TEAM,
            code=err.code,
            message=err.message,
            rule_id=rule_id,
            team_id=str(details.get("from_team") or "") or None,
            to_team=to_team,
            reason=str(details.get("reason") or "") or None,
            player_id=str(details.get("player_id") or "") or None,
            details=details,
        )

    if err.code == DEAL_INVALIDATED and rule_id == "pick_rules":
        return RuleFailure(
            kind=RuleFailureKind.PICK_RULES,
            code=err.code,
            message=err.message,
            rule_id=rule_id,
            team_id=str(details.get("team_id") or "") or None,
            reason=str(details.get("reason") or "") or None,
            pick_id=str(details.get("pick_id") or "") or None,
            details=details,
        )

    return RuleFailure(
        kind=RuleFailureKind.OTHER,
        code=str(getattr(err, "code", "")) or "UNKNOWN",
        message=str(getattr(err, "message", "")) or str(err),
        rule_id=rule_id,
        details=details,
    )
