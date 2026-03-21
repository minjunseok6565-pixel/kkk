from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple, List, Mapping

import math

from decision_context import DecisionContext

from .fit_engine import FitEngine, FitEngineConfig

from .env import ValuationEnv

from .types import (
    AssetKind,
    AssetSnapshot,
    PlayerSnapshot,
    PickSnapshot,
    SwapSnapshot,
    FixedAssetSnapshot,
    MarketValuation,
    TeamValuation,
    FitAssessment,
    ValuationStage,
    StepMode,
    ValueComponents,
    ValuationStep,
    snapshot_kind,
    snapshot_ref_id,
    pick_protection_signature,
)


# =============================================================================
# Module contract
# =============================================================================
"""
team_utility.py

Role
----
Transform MarketValuation (team-agnostic market price) into TeamValuation
(team-specific utility) using DecisionContext only.

Inputs:
- MarketValuation (from market_pricing.py)
- AssetSnapshot (player/pick/swap/fixed)
- DecisionContext (knobs + need_map)

Outputs:
- TeamValuation with:
  - market_value = MarketValuation.value
  - team_value = transformed ValueComponents(now,future)
  - team_steps = explainable adjustments (stage=TEAM)
  - fit assessment (players only, based on need_map matching)

Hard rules:
- Do NOT compute/modify market pricing primitives (OVR curve, pick curve, etc).
- Do NOT create/recompute team needs. Only consume DecisionContext.need_map.
- Do NOT validate feasibility (salary matching, apron rules, Stepien, locks, etc).
"""


# =============================================================================
# Helpers (pure)
# =============================================================================
def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return float(default)
        return float(x)
    except Exception:
        return float(default)


def _clamp(x: float, lo: float, hi: float) -> float:
    xf = _safe_float(x, lo)
    if xf < lo:
        return float(lo)
    if xf > hi:
        return float(hi)
    return float(xf)


def _vc(now: float = 0.0, future: float = 0.0) -> ValueComponents:
    return ValueComponents(float(now), float(future))


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _soft_count(x: float, k: float) -> float:
    xv = max(_safe_float(x, 0.0), 0.0)
    kv = max(_safe_float(k, 0.0), 0.0)
    if kv <= 0.0:
        return 0.0
    return 1.0 - math.exp(-kv * xv)


def _scale_components(v: ValueComponents, *, now_factor: float = 1.0, future_factor: float = 1.0) -> ValueComponents:
    return ValueComponents(v.now * float(now_factor), v.future * float(future_factor))


def _add_components(v: ValueComponents, delta: ValueComponents) -> ValueComponents:
    return ValueComponents(v.now + delta.now, v.future + delta.future)


def _normalize_0_1(value: float, *, lo: float, hi: float) -> float:
    if hi <= lo:
        return 0.0
    return _clamp((value - lo) / (hi - lo), 0.0, 1.0)


# =============================================================================
# Config (tunable, deterministic)
# =============================================================================
@dataclass(frozen=True, slots=True)
class TeamUtilityConfig:
    """
    knobs는 DecisionContext에서 오므로 여기서는 "해석/적용 방식"만 조절한다.
    """

    # --- Weighting (now/future split application)
    weight_now_floor: float = 0.10
    weight_now_cap: float = 2.50
    weight_future_floor: float = 0.10
    weight_future_cap: float = 2.50

    # --- Pick multiplier application
    # pick_multiplier는 future에만 적용하는 편이 깔끔하다(픽은 미래자산).
    pick_future_factor_floor: float = 0.50
    pick_future_factor_cap: float = 2.50

    # --- Youth multiplier application (players)
    youth_age_start: float = 20.0
    youth_age_end: float = 28.0
    youth_future_factor_floor: float = 0.70
    youth_future_factor_cap: float = 1.80

    # --- Star premium exponent application (players)
    # 시장가를 다시 만들지 않고, "팀이 스타를 얼마나 비싸게 치는지"만 반영.
    star_reference_total: float = 15.0
    star_factor_floor: float = 0.80
    star_factor_cap: float = 1.80

    # --- Fit / supply (SSOT delegated to FitEngine)
    fit: FitEngineConfig = field(default_factory=FitEngineConfig)

    # --- Risk discount (team preference)
    risk_factor_floor: float = 0.60
    risk_factor_cap: float = 1.00
    risk_age_start: float = 29.0
    risk_age_end: float = 36.0
    risk_contract_years_start: float = 2.0
    risk_contract_years_end: float = 5.0
    risk_base_weight_age_term: float = 1.00
    risk_injury_hist_weight: float = 0.85
    risk_injury_current_weight: float = 1.00
    risk_squash_center: float = 0.55
    risk_squash_scale: float = 0.18
    risk_scale_multiplier: float = 0.35

    # Injury history signals (soft-count)
    risk_injury_soft_k_recent: float = 0.28
    risk_injury_soft_k_critical: float = 0.65
    risk_injury_soft_k_repeat: float = 0.72
    risk_injury_soft_k_severity: float = 0.45
    risk_injury_hist_weight_recent: float = 0.22
    risk_injury_hist_weight_critical: float = 0.36
    risk_injury_hist_weight_repeat: float = 0.30
    risk_injury_hist_weight_severity: float = 0.12

    # Current injury signals
    risk_current_status_weight: float = 0.55
    risk_current_critical_part_bonus: float = 0.25
    risk_current_severity_scale: float = 0.20
    risk_current_days_weight: float = 0.35
    risk_current_days_t30: float = 30.0
    risk_current_days_t180: float = 180.0
    risk_current_days_s30: float = 10.0
    risk_current_days_s180: float = 24.0
    risk_current_out_multiplier: float = 1.00
    risk_current_returning_multiplier: float = 0.55
    risk_current_severity_max: float = 5.0

    # Healthy relief (risk reduction, never inversion)
    risk_health_relief_weight: float = 0.22
    risk_health_relief_availability_ref: float = 0.90
    risk_health_relief_availability_scale: float = 0.06
    risk_health_relief_low_critical_ref: float = 0.25
    risk_health_relief_low_repeat_ref: float = 0.25
    risk_health_relief_cap: float = 0.30

    # Critical body parts used for current injury risk uplift
    risk_critical_body_parts: Tuple[str, ...] = (
        "KNEE",
        "BACK",
        "HAMSTRING",
    )

    # --- Finance penalty (team preference)
    # IMPORTANT (cap-normalized salary scale)
    # --------------------------------------
    # Salary thresholds must scale with the league salary cap over time.
    # When `salary_cap` is provided (SSOT: trade_rules.salary_cap), we convert
    # cap-share ratios into dollar thresholds.
    #
    # - When `salary_cap` is provided: use cap-share ratios below.
    # - When missing: fall back to legacy absolute-dollar defaults for backward
    #   compatibility (older call sites/tests).
    salary_cap: Optional[float] = None
    # Player-level finance penalty is intentionally capped to a mild haircut
    # (at most 5%) so package-level finance effects can carry most of the signal.
    finance_factor_floor: float = 0.95
    finance_factor_cap: float = 1.00
    # Cap-normalized defaults derived from the legacy 2025 base-cap tuning:
    #   lo=8M, hi=40M at cap=154,647,000
    finance_salary_lo_cap_pct: float = 8_000_000.0 / 154_647_000.0
    finance_salary_hi_cap_pct: float = 40_000_000.0 / 154_647_000.0
    finance_salary_lo: float = 8_000_000.0
    finance_salary_hi: float = 40_000_000.0
    finance_term_weight: float = 0.35  # 긴 계약일수록 재정 부담 가중

    # NOTE: eps is used across fit/star/finance computations and is owned by FitEngineConfig

# =============================================================================
# Engine
# =============================================================================
@dataclass(slots=True)
class TeamUtilityAdjuster:
    """
    Team utility engine (pure).
    - MarketValuation을 DecisionContext로 조정해서 TeamValuation 생성.
    """
    config: TeamUtilityConfig = field(default_factory=TeamUtilityConfig)

    _fit_engine: FitEngine = field(init=False, repr=False)

    # team-specific cache: (team_id, asset_key, season_year_ctx) -> TeamValuation
    # NOTE: team utility can depend on season-year via cap-scaled finance thresholds.
    _cache: Dict[Tuple[str, str, int, str], TeamValuation] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        # Single Source of Truth for fit evaluation.
        self._fit_engine = FitEngine(config=self.config.fit)

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------
    def value_asset(
        self,
        market: MarketValuation,
        snap: AssetSnapshot,
        ctx: DecisionContext,
        env: ValuationEnv,
    ) -> TeamValuation:
        """
        단일 진입점.
        - ctx는 knobs/need_map의 공급자.
        - snap은 fit/risk/finance 신호 제공자(공급 측).

        NOTE
        ----
        Player 자산은 MarketValuation에서 "농구가치(basketball)"와 "계약가치(contract)"를 분리해 전달한다.
        팀 성향/리스크/재정/핏은 농구가치에만 적용하고,
        계약가치는 (가중치 적용 후) 그대로 더한다.
        """
        env_key = int(env.current_season_year)
        if env_key <= 0:
            raise ValueError("ValuationEnv.current_season_year must be a positive integer")
        kind = snapshot_kind(snap)

        prot_sig = ""
        if kind == AssetKind.PICK and isinstance(snap, PickSnapshot):
            prot_sig = pick_protection_signature(snap.protection)

        key = (str(ctx.team_id), str(market.asset_key), env_key, str(prot_sig))
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        ref_id = snapshot_ref_id(snap)

        team_steps: List[ValuationStep] = []

        # -----------------------------------------------------------------
        # Player path: apply team multipliers to basketball value only.
        # -----------------------------------------------------------------
        if kind == AssetKind.PLAYER and isinstance(snap, PlayerSnapshot):
            bball, contract, market_star_total = self._extract_market_breakdown(market)

            # 1) Apply now/future weights (DecisionContext.knobs) once, to both components.
            w_now, w_fut = self._compute_now_future_weights(ctx)
            team_steps.append(
                ValuationStep(
                    stage=ValuationStage.TEAM,
                    mode=StepMode.MUL,
                    code="WEIGHT_NOW_FUTURE",
                    label="팀 윈도우 기반 now/future 가중",
                    factor=None,
                    meta={"w_now": w_now, "w_future": w_fut},
                )
            )
            bball = _scale_components(bball, now_factor=w_now, future_factor=w_fut)
            contract = _scale_components(contract, now_factor=w_now, future_factor=w_fut)

            # 2) Team preference multipliers (basketball only)
            bball = self._apply_youth_preference(bball, snap, ctx, team_steps)
            bball = self._apply_star_preference(bball, market_total=market_star_total, ctx=ctx, steps=team_steps)

            # 3) Fit / needs matching (players only; need_map is consumed, never created)
            fit: Optional[FitAssessment] = None
            bball, fit = self._apply_fit(bball, snap, ctx, team_steps)

            # 4) Risk discount (basketball only)
            bball = self._apply_risk_discount(bball, snap, ctx, team_steps)

            # 5) Finance penalty (basketball only)
            bball = self._apply_finance_penalty(bball, snap, ctx, team_steps, env=env)

            value = _add_components(bball, contract)

            out = TeamValuation(
                asset_key=market.asset_key,
                kind=kind,
                ref_id=str(ref_id),
                market_value=market.value,
                team_value=value,
                market_steps=market.steps,
                team_steps=tuple(team_steps),
                fit=fit,
                meta={
                    "team_id": ctx.team_id,
                    "team_value_breakdown": {
                        "basketball": {"now": bball.now, "future": bball.future, "total": bball.total},
                        "contract": {"now": contract.now, "future": contract.future, "total": contract.total},
                    },
                },
            )
            self._cache[key] = out
            return out

        # -----------------------------------------------------------------
        # Non-player path: keep existing behavior.
        # -----------------------------------------------------------------
        value = market.value

        # 1) Apply now/future weights (DecisionContext.knobs)
        value = self._apply_now_future_weights(value, ctx, team_steps)

        # 2) Asset-type multipliers (DecisionContext.knobs)
        if kind in (AssetKind.PICK, AssetKind.SWAP):
            value = self._apply_pick_preference(value, ctx, team_steps)

        out = TeamValuation(
            asset_key=market.asset_key,
            kind=kind,
            ref_id=str(ref_id),
            market_value=market.value,
            team_value=value,
            market_steps=market.steps,
            team_steps=tuple(team_steps),
            fit=None,
            meta={"team_id": ctx.team_id},
        )
        self._cache[key] = out
        return out

    # -------------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------------
    def _compute_now_future_weights(self, ctx: DecisionContext) -> Tuple[float, float]:
        cfg = self.config
        w_now = _clamp(_safe_float(ctx.knobs.w_now, 1.0), cfg.weight_now_floor, cfg.weight_now_cap)
        w_fut = _clamp(_safe_float(ctx.knobs.w_future, 1.0), cfg.weight_future_floor, cfg.weight_future_cap)
        return float(w_now), float(w_fut)

    def _extract_market_breakdown(self, market: MarketValuation) -> Tuple[ValueComponents, ValueComponents, float]:
        """
        Extract (basketball_value, contract_value, market_star_total) from MarketValuation.meta.
        - basketball_value: value affected by team preferences
        - contract_value: added after weights, not affected by team multipliers
        - market_star_total: star preference reference (basketball total)

        Backward compatible: if breakdown is missing, treats entire market value as basketball.
        """
        if isinstance(market.meta, dict):
            bd = market.meta.get("value_breakdown")
            if isinstance(bd, dict):
                bb = bd.get("basketball") or {}
                cc = bd.get("contract") or {}
                bball = _vc(
                    now=_safe_float(bb.get("now"), market.value.now),
                    future=_safe_float(bb.get("future"), market.value.future),
                )
                contract = _vc(
                    now=_safe_float(cc.get("now"), 0.0),
                    future=_safe_float(cc.get("future"), 0.0),
                )
                market_star_total = _safe_float(bb.get("total"), bball.total)
                return bball, contract, market_star_total
        return market.value, ValueComponents.zero(), market.value.total
    
    # -------------------------------------------------------------------------
    # 1) Weights: now vs future
    # -------------------------------------------------------------------------
    def _apply_now_future_weights(
        self,
        v: ValueComponents,
        ctx: DecisionContext,
        steps: List[ValuationStep],
    ) -> ValueComponents:
        cfg = self.config
        w_now = _clamp(_safe_float(ctx.knobs.w_now, 1.0), cfg.weight_now_floor, cfg.weight_now_cap)
        w_fut = _clamp(_safe_float(ctx.knobs.w_future, 1.0), cfg.weight_future_floor, cfg.weight_future_cap)

        steps.append(
            ValuationStep(
                stage=ValuationStage.TEAM,
                mode=StepMode.MUL,
                code="WEIGHT_NOW_FUTURE",
                label="팀 윈도우 기반 now/future 가중",
                factor=None,  # now/future가 분리 적용이므로 factor는 None
                meta={"w_now": w_now, "w_future": w_fut},
            )
        )
        return _scale_components(v, now_factor=w_now, future_factor=w_fut)

    # -------------------------------------------------------------------------
    # 2-A) Picks: preference multiplier
    # -------------------------------------------------------------------------
    def _apply_pick_preference(
        self,
        v: ValueComponents,
        ctx: DecisionContext,
        steps: List[ValuationStep],
    ) -> ValueComponents:
        cfg = self.config
        m = _safe_float(ctx.knobs.pick_multiplier, 1.0)
        m = _clamp(m, cfg.pick_future_factor_floor, cfg.pick_future_factor_cap)

        steps.append(
            ValuationStep(
                stage=ValuationStage.TEAM,
                mode=StepMode.MUL,
                code="PICK_MULTIPLIER_FUTURE",
                label="픽/미래자산 선호 배율(미래값에 적용)",
                factor=m,
                meta={"pick_multiplier": m},
            )
        )
        return _scale_components(v, now_factor=1.0, future_factor=m)

    # -------------------------------------------------------------------------
    # 2-B) Youth: preference multiplier (players)
    # -------------------------------------------------------------------------
    def _apply_youth_preference(
        self,
        v: ValueComponents,
        snap: PlayerSnapshot,
        ctx: DecisionContext,
        steps: List[ValuationStep],
    ) -> ValueComponents:
        cfg = self.config
        base = _safe_float(ctx.knobs.youth_multiplier, 1.0)
        base = _clamp(base, cfg.youth_future_factor_floor, cfg.youth_future_factor_cap)

        age = _safe_float(snap.age, cfg.youth_age_end)
        # youth_score: younger -> 1.0, older -> 0.0
        youth_score = 1.0 - _normalize_0_1(age, lo=cfg.youth_age_start, hi=cfg.youth_age_end)
        factor = 1.0 + (base - 1.0) * youth_score

        steps.append(
            ValuationStep(
                stage=ValuationStage.TEAM,
                mode=StepMode.MUL,
                code="YOUTH_MULTIPLIER_FUTURE",
                label="유망/젊음 선호 배율(나이 기반, 미래값에 적용)",
                factor=factor,
                meta={"age": age, "youth_score": youth_score, "youth_multiplier": base},
            )
        )
        return _scale_components(v, now_factor=1.0, future_factor=factor)

    # -------------------------------------------------------------------------
    # 2-C) Star preference exponent (players)
    # -------------------------------------------------------------------------
    def _apply_star_preference(
        self,
        v: ValueComponents,
        *,
        market_total: float,
        ctx: DecisionContext,
        steps: List[ValuationStep],
    ) -> ValueComponents:
        """
        시장가를 다시 만들지 않고,
        팀이 "상위 자산을 더 비싸게/덜 비싸게" 치는 성향을 factor로 환산해 적용한다.

        NOTE
        ----
        Player의 경우 market_total은 "basketball" 부분의 total을 사용한다.
        (bad contract 음수 가치가 스타 판단을 망치지 않도록)
        """
        cfg = self.config
        exp = _safe_float(ctx.knobs.star_premium_exponent, 1.0)
        exp = _clamp(exp, 0.75, 1.60)

        ref = max(cfg.star_reference_total, cfg.fit.eps)
        x = max(float(market_total), cfg.fit.eps) / ref

        # exp=1 -> 1.0
        # exp>1 -> big assets get factor up
        # exp<1 -> big assets get factor down
        factor = x ** (exp - 1.0)
        factor = _clamp(factor, cfg.star_factor_floor, cfg.star_factor_cap)

        steps.append(
            ValuationStep(
                stage=ValuationStage.TEAM,
                mode=StepMode.MUL,
                code="STAR_PREMIUM_FACTOR",
                label="상위 자산(스타) 선호 배율",
                factor=factor,
                meta={"star_premium_exponent": exp, "market_total": float(market_total), "reference_total": ref},
            )
        )
        return v.scale(factor)

    # -------------------------------------------------------------------------
    # 3) Fit: need matching (players)
    # -------------------------------------------------------------------------
    def _apply_fit(
        self,
        v: ValueComponents,
        snap: PlayerSnapshot,
        ctx: DecisionContext,
        steps: List[ValuationStep],
    ) -> Tuple[ValueComponents, FitAssessment]:
        res = self._fit_engine.assess_player_fit(snap, ctx)
        for st in res.steps:
            steps.append(st)
        return v.scale(res.multiplier), res.fit

    # -------------------------------------------------------------------------
    # 4) Risk discount (players)
    # -------------------------------------------------------------------------
    def _apply_risk_discount(
        self,
        v: ValueComponents,
        snap: PlayerSnapshot,
        ctx: DecisionContext,
        steps: List[ValuationStep],
    ) -> ValueComponents:
        cfg = self.config
        scale = _safe_float(ctx.knobs.risk_discount_scale, 0.0)
        scale = _clamp(scale, 0.0, 2.0)

        age = _safe_float(snap.age, cfg.risk_age_start)
        age_risk = _normalize_0_1(age, lo=cfg.risk_age_start, hi=cfg.risk_age_end)

        years = 0.0
        if snap.contract is not None:
            years = _safe_float(snap.contract.years, 0.0)
        term_risk = _normalize_0_1(years, lo=cfg.risk_contract_years_start, hi=cfg.risk_contract_years_end)

        age_term_risk = _clamp(0.65 * age_risk + 0.35 * term_risk, 0.0, 1.0)

        injury_inputs = self._extract_injury_inputs(snap)
        injury_hist_risk, injury_hist_meta = self._injury_history_risk(injury_inputs)
        injury_current_risk, injury_current_meta = self._injury_current_risk(injury_inputs)
        health_relief, health_relief_meta = self._health_relief(injury_inputs)

        raw = (
            _safe_float(cfg.risk_base_weight_age_term, 1.0) * age_term_risk
            + _safe_float(cfg.risk_injury_hist_weight, 0.85) * injury_hist_risk
            + _safe_float(cfg.risk_injury_current_weight, 1.0) * injury_current_risk
            - _safe_float(cfg.risk_health_relief_weight, 0.22) * health_relief
        )
        center = _safe_float(cfg.risk_squash_center, 0.55)
        squash_scale = max(_safe_float(cfg.risk_squash_scale, 0.18), self.config.fit.eps)
        risk_score = _sigmoid((raw - center) / squash_scale)

        # scale이 클수록 더 할인
        factor = 1.0 - scale * _safe_float(cfg.risk_scale_multiplier, 0.35) * risk_score
        factor = _clamp(factor, cfg.risk_factor_floor, cfg.risk_factor_cap)

        steps.append(
            ValuationStep(
                stage=ValuationStage.TEAM,
                mode=StepMode.MUL,
                code="RISK_DISCOUNT",
                label="리스크 회피 할인(나이/계약+부상 기반)",
                factor=factor,
                meta={
                    "risk_discount_scale": scale,
                    "age": age,
                    "contract_years": years,
                    "age_risk": age_risk,
                    "term_risk": term_risk,
                    "age_term_risk": age_term_risk,
                    "injury_payload_present": bool(injury_inputs.get("injury_payload_present", False)),
                    "injury_fallback_used": bool(injury_inputs.get("fallback_used", False)),
                    "injury_hist_risk": injury_hist_risk,
                    "injury_current_risk": injury_current_risk,
                    "health_relief": health_relief,
                    "risk_raw": raw,
                    "risk_squash_center": center,
                    "risk_squash_scale": squash_scale,
                    "risk_score": risk_score,
                    **injury_hist_meta,
                    **injury_current_meta,
                    **health_relief_meta,
                },
            )
        )
        return v.scale(factor)

    def _extract_injury_inputs(self, snap: PlayerSnapshot) -> Dict[str, Any]:
        meta = snap.meta if isinstance(snap.meta, dict) else {}
        payload = meta.get("injury") if isinstance(meta.get("injury"), Mapping) else {}
        flags = payload.get("flags") if isinstance(payload.get("flags"), Mapping) else {}
        current = payload.get("current") if isinstance(payload.get("current"), Mapping) else {}
        history = payload.get("history") if isinstance(payload.get("history"), Mapping) else {}
        health = payload.get("health_credit_inputs") if isinstance(payload.get("health_credit_inputs"), Mapping) else {}

        return {
            "injury_payload_present": isinstance(payload, Mapping) and bool(payload),
            "fallback_used": bool(flags.get("fallback_used", False)),
            "current": dict(current),
            "history": dict(history),
            "health": dict(health),
        }

    def _injury_history_risk(self, inputs: Mapping[str, Any]) -> Tuple[float, Dict[str, Any]]:
        cfg = self.config
        history = inputs.get("history") if isinstance(inputs.get("history"), Mapping) else {}

        recent_180 = max(_safe_float(history.get("recent_count_180d"), 0.0), 0.0)
        critical_365 = max(_safe_float(history.get("critical_count_365d"), 0.0), 0.0)
        repeat_same = max(_safe_float(history.get("same_part_repeat_365d_max"), 0.0) - 1.0, 0.0)
        weighted_severity = max(_safe_float(history.get("weighted_severity_365d"), 0.0), 0.0)
        sev_signal = max(weighted_severity - 1.0, 0.0)

        fr = _soft_count(recent_180, cfg.risk_injury_soft_k_recent)
        fc = _soft_count(critical_365, cfg.risk_injury_soft_k_critical)
        fp = _soft_count(repeat_same, cfg.risk_injury_soft_k_repeat)
        fs = _soft_count(sev_signal, cfg.risk_injury_soft_k_severity)

        wr = _clamp(_safe_float(cfg.risk_injury_hist_weight_recent, 0.22), 0.0, 1.0)
        wc = _clamp(_safe_float(cfg.risk_injury_hist_weight_critical, 0.36), 0.0, 1.0)
        wp = _clamp(_safe_float(cfg.risk_injury_hist_weight_repeat, 0.30), 0.0, 1.0)
        ws = _clamp(_safe_float(cfg.risk_injury_hist_weight_severity, 0.12), 0.0, 1.0)
        wsum = max(wr + wc + wp + ws, self.config.fit.eps)

        risk = _clamp((wr * fr + wc * fc + wp * fp + ws * fs) / wsum, 0.0, 1.0)
        return risk, {
            "inj_hist_recent_180": recent_180,
            "inj_hist_critical_365": critical_365,
            "inj_hist_repeat_same": repeat_same,
            "inj_hist_weighted_severity_365d": weighted_severity,
            "inj_hist_fr": fr,
            "inj_hist_fc": fc,
            "inj_hist_fp": fp,
            "inj_hist_fs": fs,
        }

    def _injury_current_risk(self, inputs: Mapping[str, Any]) -> Tuple[float, Dict[str, Any]]:
        cfg = self.config
        current = inputs.get("current") if isinstance(inputs.get("current"), Mapping) else {}

        status = str(current.get("status") or "UNKNOWN").upper()
        is_out = bool(current.get("is_out", status == "OUT"))
        is_returning = bool(current.get("is_returning", status == "RETURNING"))
        is_active = is_out or is_returning

        severity = max(_safe_float(current.get("severity"), 0.0), 0.0)
        sev_norm = _clamp(severity / max(_safe_float(cfg.risk_current_severity_max, 5.0), self.config.fit.eps), 0.0, 1.0)

        body_part = str(current.get("body_part") or "").upper().strip()
        critical_parts = {str(x).upper().strip() for x in cfg.risk_critical_body_parts}
        is_critical_part = bool(body_part) and body_part in critical_parts

        days_to_return = max(_safe_float(current.get("days_to_return"), 0.0), 0.0)
        t30 = max(_safe_float(cfg.risk_current_days_t30, 30.0), 1.0)
        t180 = max(_safe_float(cfg.risk_current_days_t180, 180.0), t30 + 1.0)
        s30 = max(_safe_float(cfg.risk_current_days_s30, 10.0), self.config.fit.eps)
        s180 = max(_safe_float(cfg.risk_current_days_s180, 24.0), self.config.fit.eps)
        g30 = _sigmoid((days_to_return - t30) / s30)
        g180 = _sigmoid((days_to_return - t180) / s180)
        mid = _clamp((days_to_return - t30) / max(t180 - t30, self.config.fit.eps), 0.0, 1.0)
        day_gate = _clamp(0.45 * g30 * mid + 0.55 * g180, 0.0, 1.0)

        status_mult = 0.0
        if is_out:
            status_mult = _clamp(_safe_float(cfg.risk_current_out_multiplier, 1.0), 0.0, 1.5)
        elif is_returning:
            status_mult = _clamp(_safe_float(cfg.risk_current_returning_multiplier, 0.55), 0.0, 1.5)

        if not is_active:
            return 0.0, {
                "inj_cur_status": status,
                "inj_cur_is_out": is_out,
                "inj_cur_is_returning": is_returning,
                "inj_cur_body_part": body_part or None,
                "inj_cur_is_critical_part": bool(is_critical_part),
                "inj_cur_severity": severity,
                "inj_cur_sev_norm": sev_norm,
                "inj_cur_days_to_return": days_to_return,
                "inj_cur_day_gate": day_gate,
            }

        base = _clamp(_safe_float(cfg.risk_current_status_weight, 0.55), 0.0, 1.0)
        part_bonus = _safe_float(cfg.risk_current_critical_part_bonus, 0.25) if is_critical_part else 0.0
        sev_term = _clamp(_safe_float(cfg.risk_current_severity_scale, 0.20), 0.0, 1.0) * sev_norm
        day_term = _clamp(_safe_float(cfg.risk_current_days_weight, 0.35), 0.0, 1.0) * day_gate

        risk = _clamp(status_mult * (base + part_bonus + sev_term + day_term), 0.0, 1.0)
        return risk, {
            "inj_cur_status": status,
            "inj_cur_is_out": is_out,
            "inj_cur_is_returning": is_returning,
            "inj_cur_body_part": body_part or None,
            "inj_cur_is_critical_part": bool(is_critical_part),
            "inj_cur_severity": severity,
            "inj_cur_sev_norm": sev_norm,
            "inj_cur_days_to_return": days_to_return,
            "inj_cur_day_gate": day_gate,
            "inj_cur_status_mult": status_mult,
        }

    def _health_relief(self, inputs: Mapping[str, Any]) -> Tuple[float, Dict[str, Any]]:
        cfg = self.config
        history = inputs.get("history") if isinstance(inputs.get("history"), Mapping) else {}
        health = inputs.get("health") if isinstance(inputs.get("health"), Mapping) else {}

        availability = _clamp(_safe_float(health.get("availability_rate_365d"), 1.0), 0.0, 1.0)
        critical_365 = max(_safe_float(history.get("critical_count_365d"), 0.0), 0.0)
        repeat_same = max(_safe_float(history.get("same_part_repeat_365d_max"), 0.0) - 1.0, 0.0)

        avail_ref = _clamp(_safe_float(cfg.risk_health_relief_availability_ref, 0.90), 0.0, 1.0)
        avail_scale = max(_safe_float(cfg.risk_health_relief_availability_scale, 0.06), self.config.fit.eps)
        avail_gate = _sigmoid((availability - avail_ref) / avail_scale)

        low_critical_ref = max(_safe_float(cfg.risk_health_relief_low_critical_ref, 0.25), 0.0)
        low_repeat_ref = max(_safe_float(cfg.risk_health_relief_low_repeat_ref, 0.25), 0.0)
        critical_clean = _clamp(1.0 - (critical_365 / max(low_critical_ref, self.config.fit.eps)), 0.0, 1.0)
        repeat_clean = _clamp(1.0 - (repeat_same / max(low_repeat_ref, self.config.fit.eps)), 0.0, 1.0)
        clean_mix = (critical_clean + repeat_clean) * 0.5

        cap = _clamp(_safe_float(cfg.risk_health_relief_cap, 0.30), 0.0, 1.0)
        relief = _clamp(avail_gate * clean_mix, 0.0, cap)
        return relief, {
            "health_availability_rate": availability,
            "health_availability_gate": avail_gate,
            "health_critical_365": critical_365,
            "health_repeat_same": repeat_same,
            "health_clean_mix": clean_mix,
            "health_relief_cap": cap,
        }

    # -------------------------------------------------------------------------
    # 5) Finance penalty (players)
    # -------------------------------------------------------------------------
    def _apply_finance_penalty(
        self,
        v: ValueComponents,
        snap: PlayerSnapshot,
        ctx: DecisionContext,
        steps: List[ValuationStep],
        *,
        env: ValuationEnv,
    ) -> ValueComponents:
        cfg = self.config
        scale = _safe_float(ctx.knobs.finance_penalty_scale, 0.0)
        scale = _clamp(scale, 0.0, 2.0)

        salary = _safe_float(snap.salary_amount, 0.0)
        if salary <= cfg.fit.eps and snap.contract is not None and isinstance(snap.contract.salary_by_year, dict):
            # fallback: known salary proxy
            vals = [ _safe_float(x, 0.0) for x in snap.contract.salary_by_year.values() ]
            vals = [ x for x in vals if x > 0.0 ]
            if vals:
                salary = float(sorted(vals)[-1])

        # Salary burden is normalized using cap-scaled thresholds when available.
        salary_lo = float(cfg.finance_salary_lo)
        salary_hi = float(cfg.finance_salary_hi)
        salary_scale_source = "legacy_abs"
        lo_pct = None
        hi_pct = None

        cap = float(env.salary_cap())
        if cap <= cfg.fit.eps:
            raise ValueError("env.salary_cap() must be > 0 for finance penalty scaling")
        cap_source = "env"

        lo_pct = _safe_float(getattr(cfg, "finance_salary_lo_cap_pct", None), 0.0)
        hi_pct = _safe_float(getattr(cfg, "finance_salary_hi_cap_pct", None), 0.0)
        if lo_pct > cfg.fit.eps and hi_pct > cfg.fit.eps:
            salary_lo = float(cap * lo_pct)
            salary_hi = float(cap * hi_pct)
            salary_scale_source = "cap_pct"

        burden = _normalize_0_1(salary, lo=salary_lo, hi=salary_hi)
        salary_cap_pct = (salary / cap) if cap > cfg.fit.eps else None

        years = 0.0
        if snap.contract is not None:
            years = _safe_float(snap.contract.years, 0.0)
        term = _normalize_0_1(years, lo=1.0, hi=5.0)

        # 재정 부담 합성(0..1)
        finance_score = _clamp((1.0 - cfg.finance_term_weight) * burden + cfg.finance_term_weight * term, 0.0, 1.0)

        # scale이 클수록 더 할인
        factor = 1.0 - scale * 0.45 * finance_score
        factor = _clamp(factor, cfg.finance_factor_floor, cfg.finance_factor_cap)

        steps.append(
            ValuationStep(
                stage=ValuationStage.TEAM,
                mode=StepMode.MUL,
                code="FINANCE_PENALTY",
                label="재정 부담 페널티(연봉/기간 기반)",
                factor=factor,
                meta={
                    "finance_penalty_scale": scale,
                    "salary": salary,
                    "salary_cap": (cap if cap > cfg.fit.eps else None),
                    "salary_cap_source": cap_source,
                    "env_current_season_year": int(env.current_season_year),
                    "salary_cap_pct": salary_cap_pct,
                    "salary_lo": salary_lo,
                    "salary_hi": salary_hi,
                    "salary_scale_source": salary_scale_source,
                    "salary_lo_cap_pct": lo_pct,
                    "salary_hi_cap_pct": hi_pct,
                    "contract_years": years,
                    "salary_burden": burden,
                    "term_burden": term,
                    "finance_score": finance_score,
                },
            )
        )
        return v.scale(factor)
