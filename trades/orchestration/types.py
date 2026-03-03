from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

try:
    # 딜 생성기 zip에서 제공되는 wrapper
    from ..generation.deal_generator import (  # type: ignore
        DealGeneratorConfig,
        DealGeneratorStats,
        DealProposal,
    )
except Exception:  # pragma: no cover
    DealGeneratorConfig = None  # type: ignore
    DealGeneratorStats = Any  # type: ignore
    DealProposal = Any  # type: ignore


def _default_generator_config() -> Any:
    # 딜 생성기 모듈이 존재하지 않는 상태에서도 import 자체는 가능하게.
    if DealGeneratorConfig is None:
        return None
    try:
        return DealGeneratorConfig()  # type: ignore[misc]
    except Exception:
        return None


@dataclass(frozen=True, slots=True)
class OrchestrationConfig:
    """
    리그 전체 트레이드 오케스트레이션 설정.

    원칙:
    - 오케스트레이션은 "리그 운영"만 담당한다.
    - GM 성향/knobs를 직접 사용하지 않는다.
    - 팀 행동 동기는 TeamSituation(프로젝트 내 평가 결과)만 사용한다.
    - 딜 타당성/가치/수락판정은 DealGenerator + validator/valuation SSOT가 담당한다.
    """

    enabled: bool = True

    # --- tick 실행 정책
    min_active_teams: int = 6
    max_active_teams: int = 14
    deadline_bonus_active_teams: int = 6

    # --- 딜 생성기 호출 파라미터
    per_team_max_results: int = 6
    generator_config: Any = field(default_factory=_default_generator_config)

    # --- (A-4) per-team max_results 동적 스케일링
    # 동적 스케일링이 꺼져있으면 per_team_max_results(고정값)를 그대로 사용한다.
    enable_dynamic_per_team_max_results: bool = True
    per_team_min_results: int = 3
    per_team_max_results_cap: int = 9
    per_team_results_activity_gamma: float = 0.70  # rank percentile 곡선(작을수록 상위팀에 더 몰림)
    per_team_results_day_exponent: float = 0.75    # day_mult 반영 강도
    per_team_results_team_jitter_strength: float = 0.08  # 팀별 미세 변동(±8%), stable hash 기반

    # --- (A-5) Market Day Rhythm (무거운 날/조용한 날)
    enable_market_day_rhythm: bool = True
    market_day_budget_strength: float = 0.80  # 활동 팀수(n)에 day_mult를 얼마나 반영할지

    # 확률(상한/하한은 코드에서 clamp)
    market_day_spike_prob_base: float = 0.08
    market_day_spike_prob_deadline_bonus: float = 0.12
    market_day_slump_prob_base: float = 0.10
    market_day_slump_prob_deadline_reduction: float = 0.08
    market_day_prob_cap: float = 0.35  # spike/slump 확률 상한

    # day_mult 범위
    market_day_normal_mult_lo: float = 0.90
    market_day_normal_mult_hi: float = 1.10
    market_day_spike_mult_lo: float = 1.15
    market_day_spike_mult_hi: float = 1.35
    market_day_slump_mult_lo: float = 0.75
    market_day_slump_mult_hi: float = 0.90

    # --- AI↔AI 체결 캡(동적 조절용)
    ai_ai_cap_min: int = 0
    ai_ai_cap_max: int = 2  # 데드라인 근접 시 peak cap
    ai_ai_cap_pressure_low: float = 0.15
    ai_ai_cap_pressure_high: float = 0.65

    # --- AI↔AI cap 확률 보정(현실감)
    # pressure_low 이하에서도 "가끔 1건"은 허용해서 시즌 초/중반 시장이 너무 정적이지 않게 만든다.
    ai_ai_cap_stochastic: bool = True
    ai_ai_cap_idle_trade_prob: float = 0.25  # pressure_low 이하 구간에서 cap_min 대신 1을 허용할 확률
    ai_ai_cap_stochastic_rounding: bool = True  # 선형 보간 구간에서 확률 라운딩 적용

    # --- 데드라인 러시 모드(거래량/리듬): cap↑ + 쿨다운↓를 동시에 완화
    # rush_scalar = lerp(0..1) based on deadline_pressure (clamp)
    rush_pressure_start: float = 0.65
    rush_pressure_full: float = 0.90
    ai_ai_cap_rush_max: int = 4  # rush에서만 도달 가능한 상한(기본 cap_max=2는 평시 peak)

    # --- 팀별 pressure tier (actor selection / 탐색량 분배)
    pressure_tier_high_threshold: float = 0.60
    pressure_tier_rush_threshold: float = 0.85
    pressure_tier_weight_multiplier_high: float = 1.15
    pressure_tier_weight_multiplier_rush: float = 1.35
    pressure_tier_max_results_bonus_high: int = 1
    pressure_tier_max_results_bonus_rush: int = 2

    # --- 유저 오퍼 상한(스팸 방지)
    max_user_offers_per_tick: int = 3
    max_active_user_sessions: int = 4  # ACTIVE 협상 세션이 너무 많으면 신규 오퍼 억제

    # --- 유저 오퍼 보호(agreement+asset lock)
    lock_user_offers: bool = True
    user_offer_valid_days: int = 2

    # --- UI cache refresh (AI↔AI 체결 직후 이적 선수 UI 반영)
    refresh_ui_cache_after_execute: bool = True
    
    # --- 팀 쿨다운
    # 정의: cooldown_days = "오늘 액션 이후, 다음날부터 N일 동안 막는다"
    # 저장 필드: expires_on(YYYY-MM-DD) = tick 시작 시점에 (today >= expires_on) 이면 삭제되는 날짜(배타적 종료)
    cooldown_days_after_executed_trade: int = 5
    # 데드라인 러시/팀별 pressure에 따라 executed_trade 쿨다운을 동적으로 내릴 때의 바닥값
    cooldown_executed_trade_min_days: int = 1
    cooldown_executed_trade_pressure_low: float = 0.15
    cooldown_executed_trade_pressure_high: float = 0.85
    cooldown_days_after_user_offer: int = 1

    # --- trade_market.events 보관량 제한
    max_market_events_kept: int = 200

    # --- executed trade projection idempotency guard (state bloat 방지)
    # trade_market["applied_exec_deal_ids"] 보관량 제한
    max_applied_exec_deal_ids_kept: int = 500

    # --- Market realism: rumors / threads (v1)
    # 상업용 안전성 원칙:
    # - 루머는 "고득점 + office gate 통과" 딜만 기록(버그성/극단 딜 노출 방지)
    # - 스팸 방지(틱당 상한, 페어 쿨다운)
    enable_market_rumors: bool = True
    rumor_min_score: float = 0.25
    max_rumors_per_tick: int = 3
    rumor_pair_cooldown_days: int = 1  # 팀 페어당 하루 1회(기본)

    # threads: "접촉 중" 상태를 며칠간 유지해서 다음 tick에서 다시 얽히게(진행중 체감)
    enable_threads: bool = True
    thread_ttl_days: int = 5
    max_threads_kept: int = 50  # state 폭발 방지(최근 last_at 기준 유지)

    # actor selection 영향(접촉 중인 팀이 더 자주 시장에 등장)
    thread_activity_weight_multiplier: float = 1.35
    thread_min_actors_per_tick: int = 1
    thread_extra_pool_size: int = 8

    # 접촉 중인 팀은 딜 탐색량을 소폭 늘려서(생성기 호출) "진행중" 강화
    thread_per_team_max_results_bonus: int = 2

    # --- Trade block (listing) market effects
    enable_trade_block: bool = True
    trade_block_actor_weight_multiplier: float = 1.20
    # Public trade-request teams should surface somewhat more often.
    trade_request_public_actor_weight_multiplier: float = 1.15
    # If a team has public trade request(s) but no active listing, give a smaller boost.
    trade_request_public_no_listing_weight_multiplier: float = 1.08
    # If a team has both listing and public trade request signal, apply a mild extra boost.
    trade_request_public_with_listing_weight_multiplier: float = 1.06
    # Guardrail: cap total actor weight multiplier after all boosts.
    actor_weight_multiplier_cap: float = 3.00
    trade_block_auto_list_days_public_offer: int = 10

    # --- Offer privacy / leaks
    default_offer_privacy: str = "PRIVATE"
    enable_private_offer_leaks: bool = True
    ai_private_leak_base_prob: float = 0.08
    ai_private_leak_pressure_bonus: float = 0.22
    ai_private_leak_prob_cap: float = 0.30
    private_leak_pair_cooldown_days: int = 7
    enable_ai_leak_publicize: bool = True
    enable_ai_leak_grievance: bool = True

    # user leak relationship penalty
    user_leak_trust_penalty: int = 35
    user_leak_promises_broken_inc: int = 1

    # --- RNG 재현성(프로세스/머신이 달라도 동일하게 나오도록 안정 해시 사용)
    seed_salt: str = "trade_orchestration_v2"

    # seed 구성 옵션(상업용 기본: 리그/실행별 변동성 부여)
    seed_with_league_fingerprint: bool = True
    seed_with_tick_nonce: bool = True

    # --- Commercial safety: human-controlled teams (fail-closed)
    persist_human_team_ids_in_state: bool = True
    human_team_ids_state_key: str = "human_controlled_team_ids"
    fail_closed_if_human_ids_missing: bool = True
    exclude_human_teams_from_initiators: bool = True

    # --- 유저에게 보내는 오퍼 quality gate (DealGenerator score만 사용)
    user_offer_min_score: float = 0.15

    # --- 유저 REJECT 기반 "탐색(PROBE)/로우볼(LOWBALL)" 오퍼
    # 목적: NBA 시장의 "떠보기/로우볼" 현실감을 주되, 스팸/불쾌감은 구조적으로 차단한다.
    #
    # 핵심 정의(DecisionPolicy 기반):
    # - net_surplus = "유저 관점 가치 잉여"(>0 이득, <0 손해)
    # - overpay_allowed = "유저가 감수할 수 있는 손해 한도"(>=0)
    # - REJECT 조건: net_surplus < -overpay_allowed
    # - exceed_overpay = (-overpay_allowed) - net_surplus  (REJECT면 항상 > 0)
    #
    # 분류:
    # - PROBE: 0 < exceed_overpay <= probe_exceed_max
    # - LOWBALL: probe_exceed_max < exceed_overpay <= lowball_exceed_max
    # - 그 외: suppress(모욕/스팸 방지)
    enable_user_reject_offers: bool = True
    skip_reject_offer_if_active_session_exists: bool = True

    # SERIOUS(uv!=REJECT) 오퍼를 이미 보냈으면, 같은 tick에서 PROBE/LOWBALL은 기본적으로 금지
    disable_reject_offers_if_any_serious_sent: bool = True

    # REJECT 오퍼에서도 최소한의 "딜 생성기 score" 하한을 둔다(완전 쓰레기 방지).
    user_reject_offer_min_score: float = 0.10

    # tick당 tone별 상한(스팸 방지)
    max_user_probe_offers_per_tick: int = 1
    max_user_lowball_offers_per_tick: int = 1

    # 같은 상대가 반복해서 유저를 찌르지 않게 pair 쿨다운(일 단위)
    user_pair_probe_cooldown_days: int = 4
    user_pair_lowball_cooldown_days: int = 10

    # exceed 윈도우 계산에서 outgoing_total scale의 바닥값(DecisionPolicy min_outgoing_scale과 맞춤)
    user_offer_min_outgoing_scale: float = 6.0

    # PROBE 허용치: probe_exceed_max = min(cap, max(abs_min, ratio * scale))
    probe_exceed_scale_ratio: float = 0.02
    probe_exceed_abs_min: float = 0.12
    probe_exceed_abs_cap: float = 1.2

    # LOWBALL 허용치: lowball_exceed_max = min(cap, max(abs_min, ratio * scale))
    lowball_exceed_scale_ratio: float = 0.06
    lowball_exceed_abs_min: float = 0.30
    lowball_exceed_abs_cap: float = 3.0

    # 확률(0~1): p = base + bonus * deadline_pressure (clamp01)
    probe_base_prob: float = 0.18
    probe_pressure_bonus: float = 0.22
    lowball_base_prob: float = 0.10
    lowball_pressure_bonus: float = 0.15

    # --- 현실감: 같은 날 한 팀이 여러 번 트레이드하는 것 방지(기본 True)
    prevent_multiple_trades_per_team_per_tick: bool = True

    # --- 데드라인 근처 팀당 '하루 2딜' 제한적 허용(연쇄 트레이드 체감)
    # 기본 방지 로직을 유지하되, pressure가 매우 높을 때만 확률적으로 2번째 딜을 허용한다.
    allow_second_trade_pressure_threshold: float = 0.80
    allow_second_trade_prob: float = 0.35
    max_trades_per_team_per_tick_rush: int = 2
    # 상업용 안전: human 팀은 자동으로 2딜 허용하지 않는다(유저 로스터 자동 변경 체감 방지)
    never_allow_multi_trade_for_humans: bool = True

    # --- "리그 오피스 게이트"(극단/버그성 체결 방지) — 오케스트레이션은 새로운 평가를 하지 않고,
    # 생성기 산출물(score, evaluation, decision)로만 안전 컷을 둔다.
    ai_ai_office_gate_min_score: float = 0.25
    ai_ai_office_gate_min_margin: float = -14.0
    ai_ai_office_gate_overpay_slack: float = 0.5


@dataclass(frozen=True, slots=True)
class ActorPlan:
    team_id: str
    activity_score: float
    max_results: int
    # Optional breakdown for telemetry/UI: why this team was selected as an active trade actor.
    # Kept optional to preserve compatibility with older policy modules.
    activity_breakdown: Optional[Dict[str, Any]] = None
    activity_tags: Optional[List[str]] = None

    # Team-specific deadline pressure (0..1) and derived tier.
    # Optional for backward compatibility (filled by actor_selection in newer orchestration).
    effective_pressure: Optional[float] = None
    pressure_tier: Optional[str] = None


@dataclass(frozen=True, slots=True)
class GeneratedBatch:
    initiator_team_id: str
    proposals: List[Any]  # List[DealProposal]
    stats: Optional[Any] = None  # Optional[DealGeneratorStats]


@dataclass(slots=True)
class CleanupReport:
    removed_cooldowns: int = 0
    removed_threads_expired: int = 0
    pruned_threads_limit: int = 0
    pruned_events: int = 0
    pruned_applied_exec_deal_ids: int = 0


@dataclass(slots=True)
class PromotionResult:
    executed_trade_events: List[Dict[str, Any]] = field(default_factory=list)
    user_offer_sessions: List[Dict[str, Any]] = field(default_factory=list)  # session meta
    skipped: int = 0
    vetoed: int = 0

    # market realism stats
    rumors_created: int = 0
    threads_touched: int = 0
    threads_opened: int = 0

    # UI cache refresh stats (best-effort)
    ui_cache_refreshed_players: int = 0
    ui_cache_refresh_failures: int = 0
    
    errors: List[Dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class TickReport:
    tick_date: str
    skipped: bool = False
    skip_reason: str = ""

    active_teams: List[str] = field(default_factory=list)
    ai_ai_trade_cap: int = 0

    cleanup: CleanupReport = field(default_factory=CleanupReport)
    batches: List[GeneratedBatch] = field(default_factory=list)
    promotion: PromotionResult = field(default_factory=PromotionResult)

    meta: Dict[str, Any] = field(default_factory=dict)
