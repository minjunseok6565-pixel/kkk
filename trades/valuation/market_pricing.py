from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Tuple, Iterable, List

import math

# SSOT helpers
from contracts.terms import player_contract_terms

from .env import ValuationEnv

from .types import (
    AssetKind,
    AssetSnapshot,
    PlayerSnapshot,
    PickSnapshot,
    SwapSnapshot,
    FixedAssetSnapshot,
    PickExpectation,
    ValuationStage,
    StepMode,
    ValueComponents,
    ValuationStep,
    MarketValuation,
    snapshot_kind,
    snapshot_ref_id,
    pick_protection_signature,
)


# =============================================================================
# Module contract
# =============================================================================
"""
market_pricing.py

Role
----
League-wide asset pricing layer ("market price", team-agnostic).

Inputs:
- AssetSnapshot (player/pick/swap/fixed)
- Optional PickExpectation (expected pick position / uncertainty)

Outputs:
- MarketValuation with:
  - ValueComponents(now, future)
  - ValuationStep logs describing how the price was built

Hard rules:
- Do NOT use DecisionContext / needs / GM traits / team situation.
- Do NOT validate feasibility (salary matching, apron, Stepien, etc).
  Those must already be handled by trades/validator + trades/rules.

Design target:
- Deterministic, explainable, tunable with config.
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


def _safe_int(x: Any, default: int = 0) -> int:
    try:
        if x is None:
            return int(default)
        return int(x)
    except Exception:
        return int(default)


def _clamp(x: float, lo: float, hi: float) -> float:
    xf = _safe_float(x, lo)
    if xf < lo:
        return float(lo)
    if xf > hi:
        return float(hi)
    return float(xf)


def _sigmoid(x: float) -> float:
    # stable sigmoid
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _softplus(x: float) -> float:
    # stable softplus
    if x > 30:
        return x
    return math.log1p(math.exp(x))


def _vc(now: float = 0.0, future: float = 0.0) -> ValueComponents:
    return ValueComponents(float(now), float(future))


def _soft_count(x: float, k: float) -> float:
    xv = max(_safe_float(x, 0.0), 0.0)
    kv = max(_safe_float(k, 0.0), 0.0)
    if kv <= 0.0:
        return 0.0
    return 1.0 - math.exp(-kv * xv)


def _normalize_option_type(value: Any) -> str:
    v = str(value or "").strip().upper()
    if v in {"TEAM", "PLAYER", "ETO"}:
        return v
    return "PLAYER"


# =============================================================================
# Config: curves & weights (tunable, deterministic)
# =============================================================================
@dataclass(frozen=True, slots=True)
class MarketPricingConfig:
    """
    모든 숫자는 '튜닝 가능'하도록 config로 모은다.
    - 이 config는 market stage에서만 쓰인다.
    """

    # --- Player base pricing
    player_ovr_center: float = 75.0
    player_ovr_scale: float = 6.0
    player_ovr_now_max: float = 25.0  # OVR 기반 now 상한(화폐 단위)
    player_ovr_now_min: float = 0.0

    # OVR 증가가 상위 구간에서 더 비싸지도록(비선형)
    player_star_softplus_scale: float = 0.55
    player_star_softplus_shift: float = 86.0

    # --- Age / horizon split (market-level expectation)
    age_peak: float = 27.0
    age_now_decay_per_year: float = 0.06
    age_future_growth_per_year_under_peak: float = 0.07
    age_future_decay_per_year_over_peak: float = 0.05
    age_future_floor: float = 0.20
    age_future_cap: float = 1.40

    # --- Contract efficiency (market-level)
    # expected salary as function of ovr -> compare vs actual to compute contract factor
    # IMPORTANT (cap-normalized salary scale)
    # --------------------------------------
    # Salary-related thresholds should scale with the league salary cap over time.
    # We therefore express the expected-salary curve in *shares of cap* (pct of cap)
    # and convert to dollars using the SSOT cap value (trade_rules.salary_cap) when
    # available.
    #
    # - When `salary_cap` is provided: use cap-share ratios below.
    # - When missing: fall back to legacy absolute-dollar defaults for backward
    #   compatibility (older call sites/tests).
    salary_cap: Optional[float] = None

    expected_salary_ovr_center: float = 75.0
    expected_salary_ovr_scale: float = 7.0

    # Cap-normalized defaults derived from the legacy 2025 base-cap tuning:
    #   midpoint=18M, span=16M at cap=154,647,000
    expected_salary_midpoint_cap_pct: float = 18_000_000.0 / 154_647_000.0
    expected_salary_span_cap_pct: float = 16_000_000.0 / 154_647_000.0
    expected_salary_span: float = 16_000_000.0

    # Fair salary curve (cap%-based).
    # - Designed to cover max salary ranges (superstars can be underpaid even at max).
    fair_salary_pct_lo: float = 0.02
    fair_salary_pct_hi: float = 0.47
    fair_salary_ovr_center: float = 86.0
    fair_salary_ovr_scale: float = 7.0

    # Contract surplus (fair - actual) is converted into value units and ADDED to player value.
    # This allows truly bad contracts to become negative assets.
    contract_surplus_value_per_cap_pct: float = 0.90  # 1% of cap surplus ~= 0.9 value units (tunable)
    contract_year_discount_rate: float = 0.12
    contract_value_abs_cap: float = 40.0
    contract_fallback_years_cap: int = 6

    # Contract option value (added on top of contract surplus delta).
    # TEAM option: always + (team-favorable control)
    # PLAYER/ETO option: always - (team-side uncertainty), with salary mismatch shaping magnitude.
    option_value_base_units: float = 0.30
    option_pending_probability: float = 0.75
    option_mismatch_alpha: float = 0.45
    option_mismatch_beta_cap_share: float = 0.05
    option_mismatch_min_mult: float = 0.65
    option_mismatch_max_mult: float = 1.45
    option_value_abs_cap: float = 2.0

    contract_efficiency_factor_floor: float = 0.70
    contract_efficiency_factor_cap: float = 1.35
    contract_years_penalty_per_year: float = 0.03  # 긴 계약은 market에서 살짝 할인

    # --- Injury-aware market adjustments (players)
    # A) current injury discount (soft gates around 30/180 days)
    inj_current_t30_days: float = 30.0
    inj_current_t180_days: float = 180.0
    inj_current_s30_days: float = 10.0
    inj_current_s180_days: float = 24.0
    inj_current_weight_30: float = 0.08
    inj_current_weight_180: float = 0.16
    inj_current_returning_multiplier: float = 0.50
    inj_current_factor_floor: float = 0.82

    # B) injury history discount (soft-count saturating signals)
    inj_hist_recent_kr: float = 0.28
    inj_hist_critical_kc: float = 0.65
    inj_hist_repeat_kp: float = 0.72
    inj_hist_severity_ks: float = 0.45
    inj_hist_weight_recent: float = 0.26
    inj_hist_weight_critical: float = 0.36
    inj_hist_weight_repeat: float = 0.28
    inj_hist_weight_severity: float = 0.10
    inj_hist_penalty_cap: float = 0.22
    inj_hist_factor_floor: float = 0.78

    # C) health credit (counterbalance; intentionally smaller than discounts)
    health_credit_availability_ref: float = 0.90
    health_credit_availability_scale: float = 0.06
    health_credit_base_scale: float = 0.020
    health_credit_no_critical_bonus: float = 1.20
    health_credit_cap: float = 0.06

    # --- Pick base pricing
    pick_round1_base_future: float = 14.0
    pick_round2_base_future: float = 3.5

    # pick number -> value curve
    pick_num_best: int = 1
    pick_num_worst: int = 30
    pick_num_curve_power: float = 1.65  # 상위픽 프리미엄(비선형)

    # year discount (멀수록 가치 감소)
    pick_year_discount_rate: float = 0.10  # 1년당 할인

    # --- Protection expectation (TOP_N)
    protection_logit_k: float = 0.85  # convey probability sharpness
    protection_logit_bias: float = 0.0

    # --- Swap optionality (market-level)
    swap_exercise_scale: float = 0.65
    swap_gap_to_prob_scale: float = 0.35  # pick gap -> exercise prob

    # --- Fixed assets
    fixed_default_timing: str = "future"  # "now" or "future"

    # --- General
    eps: float = 1e-9


# =============================================================================
# Main engine
# =============================================================================
@dataclass(slots=True)
class MarketPricer:
    """
    market pricing 엔진.
    - pure logic: provider/DB 없음
    - caching 지원: deal evaluator가 같은 에셋을 여러 번 요청해도 안정/고속
    """
    config: MarketPricingConfig = field(default_factory=MarketPricingConfig)

    _cache_player: Dict[Tuple[str, int], MarketValuation] = field(default_factory=dict, init=False)
    _cache_pick: Dict[Tuple[str, int, str], MarketValuation] = field(default_factory=dict, init=False)
    _cache_swap: Dict[Tuple[str, int], MarketValuation] = field(default_factory=dict, init=False)
    _cache_fixed: Dict[Tuple[str, int], MarketValuation] = field(default_factory=dict, init=False)

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------
    def price_snapshot(
        self,
        snap: AssetSnapshot,
        *,
        asset_key: str,
        env: ValuationEnv,
        pick_expectation: Optional[PickExpectation] = None,
        resolved_pick_a: Optional[PickSnapshot] = None,
        resolved_pick_b: Optional[PickSnapshot] = None,
        resolved_pick_a_expectation: Optional[PickExpectation] = None,
        resolved_pick_b_expectation: Optional[PickExpectation] = None,
    ) -> MarketValuation:
        """
        deal_evaluator에서 호출하는 단일 진입점.

        - pick: pick_expectation을 주입할 수 있음
        - swap: swap의 pick_id_a/b를 resolve한 PickSnapshot을 같이 주입할 수 있음
          (swap pricing은 pick snapshot + 기대순번/연도할인 기대치가 필요)
        """
        kind = snapshot_kind(snap)
        ref_id = snapshot_ref_id(snap)

        # Cache key includes runtime season-year context.
        env_key = int(env.current_season_year)
        if env_key <= 0:
            raise ValueError("ValuationEnv.current_season_year must be a positive integer")
        cache_key = (str(ref_id), int(env_key))

        if kind == AssetKind.PLAYER:
            cached = self._cache_player.get(cache_key)
            if cached is not None:
                return cached
            out = self._price_player(snap, asset_key=asset_key, env=env)
            self._cache_player[cache_key] = out
            return out

        if kind == AssetKind.PICK:
            prot_sig = ""
            if isinstance(snap, PickSnapshot):
                prot_sig = pick_protection_signature(snap.protection)

            pick_cache_key = (str(ref_id), int(env_key), str(prot_sig))

            cached = self._cache_pick.get(pick_cache_key)
            if cached is not None:
                return cached
            out = self._price_pick(snap, asset_key=asset_key, expectation=pick_expectation, env=env)
            self._cache_pick[pick_cache_key] = out
            return out

        if kind == AssetKind.SWAP:
            cached = self._cache_swap.get(cache_key)
            if cached is not None:
                return cached
            out = self._price_swap(
                snap,
                asset_key=asset_key,
                pick_a=resolved_pick_a,
                pick_b=resolved_pick_b,
                pick_a_expectation=resolved_pick_a_expectation,
                pick_b_expectation=resolved_pick_b_expectation,
                env=env,
            )
            self._cache_swap[cache_key] = out
            return out

        cached = self._cache_fixed.get(cache_key)
        if cached is not None:
            return cached
        out = self._price_fixed(snap, asset_key=asset_key)
        self._cache_fixed[cache_key] = out
        return out

    # -------------------------------------------------------------------------
    # Player pricing
    # -------------------------------------------------------------------------
    def _price_player(
        self,
        snap: PlayerSnapshot,
        *,
        asset_key: str,
        env: ValuationEnv,
    ) -> MarketValuation:
        cfg = self.config
        steps: List[ValuationStep] = []

        ovr = _safe_float(snap.ovr, 70.0)
        age = _safe_float(snap.age, 27.0)

        # -----------------------------------------------------------------
        # Basketball value (pure talent/age/market scarcity).
        # -----------------------------------------------------------------
        # 1) OVR -> now base (sigmoid-like)
        now_base = self._ovr_to_now_value(ovr)
        steps.append(
            ValuationStep(
                stage=ValuationStage.MARKET,
                mode=StepMode.ADD,
                code="OVR_NOW_BASE",
                label="OVR 기반 현재가치",
                delta=_vc(now=now_base, future=0.0),
                meta={"ovr": ovr},
            )
        )

        # 2) Star premium (softplus, high-OVR nonlinearity)
        star_bonus = self._ovr_star_bonus(ovr)
        if abs(star_bonus) > cfg.eps:
            steps.append(
                ValuationStep(
                    stage=ValuationStage.MARKET,
                    mode=StepMode.ADD,
                    code="OVR_STAR_BONUS",
                    label="상위 OVR 프리미엄",
                    delta=_vc(now=star_bonus, future=0.0),
                    meta={"ovr": ovr},
                )
            )

        base_now_raw = now_base + star_bonus

        # 3) Age -> now decay (post-peak decline in current impact)
        now_decay = self._age_to_now_decay_factor(age)
        if abs(now_decay - 1.0) > cfg.eps:
            steps.append(
                ValuationStep(
                    stage=ValuationStage.MARKET,
                    mode=StepMode.MUL,
                    code="AGE_NOW_DECAY",
                    label="나이 기반 현재가치 감쇠",
                    factor=now_decay,
                    delta=_vc(0.0, 0.0),
                    meta={"age": age},
                )
            )
        base_now = base_now_raw * now_decay

        # 4) Age -> future multiplier (market-level expected horizon)
        age_future_factor = self._age_to_future_factor(age)
        steps.append(
            ValuationStep(
                stage=ValuationStage.MARKET,
                mode=StepMode.MUL,
                code="AGE_FUTURE_FACTOR",
                label="나이 기반 미래가치 배율",
                factor=age_future_factor,
                delta=_vc(0.0, 0.0),
                meta={"age": age},
            )
        )

        # We create a future component from current base using the factor.
        future_from_age = base_now * (age_future_factor - 1.0)
        steps.append(
            ValuationStep(
                stage=ValuationStage.MARKET,
                mode=StepMode.ADD,
                code="AGE_FUTURE_COMPONENT",
                label="나이 기반 미래가치 구성",
                delta=_vc(now=0.0, future=future_from_age),
                meta={"age": age, "factor": age_future_factor},
            )
        )

        basketball_value = _vc(now=base_now, future=future_from_age)

        # 4) Position scarcity (market-level) -> applies to basketball value only.
        pos_factor, pos_meta = self._position_scarcity_factor(snap)
        if abs(pos_factor - 1.0) > cfg.eps:
            steps.append(
                ValuationStep(
                    stage=ValuationStage.MARKET,
                    mode=StepMode.MUL,
                    code="POSITION_SCARCITY",
                    label="포지션 희소성 배율",
                    factor=pos_factor,
                    meta=pos_meta,
                )
            )
            basketball_value = basketball_value.scale(pos_factor)

        # -----------------------------------------------------------------
        # Injury-aware market adjustments on basketball component.
        # - CURRENT injury discount (soft-gated around 30/180 days): MUL
        # - HISTORY injury discount (recent/critical/repeat/severity): MUL
        # - HEALTH credit bonus (availability upside): ADD
        # -----------------------------------------------------------------
        injury_payload, injury_payload_meta = self._safe_injury_payload(snap)

        current_factor, current_meta = self._inj_current_penalty_factor(injury_payload)
        steps.append(
            ValuationStep(
                stage=ValuationStage.MARKET,
                mode=StepMode.MUL,
                code="INJURY_CURRENT_DISCOUNT",
                label="현재 부상 할인(소프트 게이트)",
                factor=current_factor,
                meta={**injury_payload_meta, **current_meta},
            )
        )
        basketball_value = basketball_value.scale(current_factor)

        history_factor, history_meta = self._inj_history_penalty_factor(injury_payload)
        steps.append(
            ValuationStep(
                stage=ValuationStage.MARKET,
                mode=StepMode.MUL,
                code="INJURY_HISTORY_DISCOUNT",
                label="부상 이력 할인(최근/치명/반복)",
                factor=history_factor,
                meta={**injury_payload_meta, **history_meta},
            )
        )
        basketball_value = basketball_value.scale(history_factor)

        health_credit_delta, health_meta = self._health_credit_bonus(injury_payload, basketball_value)
        if abs(health_credit_delta.now) + abs(health_credit_delta.future) > cfg.eps:
            steps.append(
                ValuationStep(
                    stage=ValuationStage.MARKET,
                    mode=StepMode.ADD,
                    code="HEALTH_CREDIT",
                    label="건강 보너스(가용성 반대급부)",
                    delta=health_credit_delta,
                    meta={**injury_payload_meta, **health_meta},
                )
            )
            basketball_value = basketball_value + health_credit_delta

        # -----------------------------------------------------------------
        # Contract value (fair salary - actual salary), ADDED as a separate component.
        # This allows bad contracts to become negative assets and fixes superstar max bias.
        # -----------------------------------------------------------------
        contract_delta, contract_meta = self._contract_value_delta(snap, env=env)
        if abs(contract_delta.now) + abs(contract_delta.future) > cfg.eps:
            steps.append(
                ValuationStep(
                    stage=ValuationStage.MARKET,
                    mode=StepMode.ADD,
                    code="CONTRACT_SURPLUS_DELTA",
                    label="계약 가치(시장가-실제연봉) 델타",
                    delta=contract_delta,
                    meta=contract_meta,
                )
            )

        value = basketball_value + contract_delta

        meta = {
            "name": snap.name,
            "pos": snap.pos,
            "team_id": snap.team_id,
            "value_breakdown": {
                "basketball": {
                    "now": basketball_value.now,
                    "future": basketball_value.future,
                    "total": basketball_value.total,
                },
                "injury": {
                    "current_factor": float(current_factor),
                    "history_factor": float(history_factor),
                    "health_credit_now": float(health_credit_delta.now),
                    "health_credit_future": float(health_credit_delta.future),
                    "health_credit_total": float(health_credit_delta.total),
                },
                "contract": {
                    "now": contract_delta.now,
                    "future": contract_delta.future,
                    "total": contract_delta.total,
                },
            },
        }

        return MarketValuation(
            asset_key=asset_key,
            kind=AssetKind.PLAYER,
            ref_id=str(snap.player_id),
            value=value,
            steps=tuple(steps),
            meta=meta,
        )

    # -------------------------------------------------------------------------
    # Contract helpers (market-level, pure)
    # -------------------------------------------------------------------------
    def _fair_salary_pct_from_ovr(self, ovr: float) -> float:
        cfg = self.config
        x = (ovr - cfg.fair_salary_ovr_center) / max(cfg.fair_salary_ovr_scale, cfg.eps)
        s = _sigmoid(x)
        return cfg.fair_salary_pct_lo + (cfg.fair_salary_pct_hi - cfg.fair_salary_pct_lo) * s

    def _remaining_salary_schedule(self, snap: PlayerSnapshot, *, current_season_year: int) -> List[Tuple[int, float]]:
        cfg = self.config
        cur = int(current_season_year)

        # 1) salary_by_year (best signal)
        if snap.contract is not None and isinstance(snap.contract.salary_by_year, dict) and snap.contract.salary_by_year:
            out: List[Tuple[int, float]] = []
            for y, v in snap.contract.salary_by_year.items():
                yy = int(y)
                sal = _safe_float(v, 0.0)
                if yy < cur:
                    continue
                if sal <= cfg.eps:
                    continue
                out.append((yy, float(sal)))
            out.sort(key=lambda t: t[0])
            if out:
                return out

        # 2) fallback: salary_amount + remaining years
        salary_now = _safe_float(snap.salary_amount, 0.0)
        years = 0
        if snap.contract is not None:
            if isinstance(snap.contract.meta, dict) and snap.contract.meta.get("remaining_years") is not None:
                try:
                    years = int(float(snap.contract.meta.get("remaining_years")))
                except Exception:
                    years = 0
            if years <= 0:
                years = _safe_int(snap.contract.years, 0)

        years = max(0, years)
        years = min(years, int(cfg.contract_fallback_years_cap))
        if salary_now <= cfg.eps or years <= 0:
            return []

        return [(cur + i, float(salary_now)) for i in range(years)]

    def _clamp_abs_mass(self, v: ValueComponents, abs_cap: float) -> ValueComponents:
        m = abs(float(v.now)) + abs(float(v.future))
        if abs_cap <= 0 or m <= abs_cap:
            return v
        f = abs_cap / max(m, self.config.eps)
        return ValueComponents(v.now * f, v.future * f)

    def _contract_value_delta(
        self,
        snap: PlayerSnapshot,
        *,
        env: ValuationEnv,
    ) -> Tuple[ValueComponents, Dict[str, Any]]:
        cfg = self.config
        ovr = _safe_float(snap.ovr, 70.0)

        # SSOT: current season context comes from env, NOT snapshot.meta.

        cur = int(env.current_season_year)
        if cur <= 0:
            raise ValueError("ValuationEnv.current_season_year must be a positive integer")

        terms = player_contract_terms(snap, current_season_year=cur)
        sched = list(terms.schedule)
        if not sched:
            return ValueComponents.zero(), {"reason": "no_salary_schedule", "cur": int(cur), "terms_meta": terms.meta}

        fair_pct = self._fair_salary_pct_from_ovr(ovr)

        now_units = 0.0
        fut_units = 0.0
        rows: List[Dict[str, Any]] = []

        for (year, actual) in sched:
            cap_y = float(env.cap_model.salary_cap_for_season(int(year)))
            fair_y = cap_y * fair_pct
            surplus = float(fair_y) - float(actual)
            surplus_frac = surplus / max(cap_y, cfg.eps)  # cap fraction

            years_ahead = max(int(year) - int(cur), 0)
            disc = (1.0 - cfg.contract_year_discount_rate) ** years_ahead
            disc = _clamp(disc, 0.35, 1.00)

            # Convert cap%% surplus into internal value units.
            units = (surplus_frac * 100.0) * cfg.contract_surplus_value_per_cap_pct * disc

            if int(year) == int(cur):
                now_units += units
            else:
                fut_units += units

            rows.append(
                {
                    "year": int(year),
                    "cap": cap_y,
                    "fair_pct": fair_pct,
                    "fair_salary": fair_y,
                    "actual_salary": float(actual),
                    "surplus": surplus,
                    "surplus_cap_pct": surplus_frac * 100.0,
                    "disc": disc,
                    "units": units,
                }
            )

        base_delta = ValueComponents(now_units, fut_units)
        option_delta, option_rows = self._contract_option_value_delta(
            snap,
            env=env,
            fair_pct=float(fair_pct),
        )
        delta = base_delta + option_delta
        delta = self._clamp_abs_mass(delta, cfg.contract_value_abs_cap)

        meta = {
            "cur": int(cur),
            "ovr": ovr,
            "fair_salary_pct": fair_pct,
            "terms_meta": terms.meta,
            "rows": rows,
            "base_delta": {"now": base_delta.now, "future": base_delta.future, "total": base_delta.total},
            "option_rows": option_rows,
            "option_delta": {"now": option_delta.now, "future": option_delta.future, "total": option_delta.total},
            "delta": {"now": delta.now, "future": delta.future, "total": delta.total},
        }
        return delta, meta

    def _contract_option_value_delta(
        self,
        snap: PlayerSnapshot,
        *,
        env: ValuationEnv,
        fair_pct: float,
    ) -> Tuple[ValueComponents, List[Dict[str, Any]]]:
        cfg = self.config
        c = snap.contract
        if c is None or not c.options:
            return ValueComponents.zero(), []

        if cfg.option_value_base_units <= cfg.eps:
            return ValueComponents.zero(), []

        cur = int(env.current_season_year)
        now_units = 0.0
        fut_units = 0.0
        rows: List[Dict[str, Any]] = []

        for opt in c.options:
            year = int(_safe_int(getattr(opt, "season_year", 0), 0))
            if year < cur:
                continue

            raw_status = str(getattr(opt, "status", "") or "").strip().upper()
            if raw_status == "DECLINED":
                p_active = 0.0
            elif raw_status == "EXERCISED":
                p_active = 1.0
            else:
                p_active = float(_clamp(cfg.option_pending_probability, 0.0, 1.0))
            if p_active <= cfg.eps:
                continue

            opt_type = _normalize_option_type(getattr(opt, "type", ""))
            sign = 1.0 if opt_type == "TEAM" else -1.0

            cap_y = float(env.cap_model.salary_cap_for_season(int(year)))
            if cap_y <= cfg.eps:
                continue
            salary_y = float(_safe_float(c.salary_by_year.get(int(year)), 0.0))
            fair_y = cap_y * float(fair_pct)
            mismatch_cap_share = (salary_y - fair_y) / max(cap_y, cfg.eps)
            beta = max(float(cfg.option_mismatch_beta_cap_share), cfg.eps)
            gm = 1.0 + float(cfg.option_mismatch_alpha) * math.tanh(float(mismatch_cap_share) / beta)
            gm = _clamp(gm, float(cfg.option_mismatch_min_mult), float(cfg.option_mismatch_max_mult))

            years_ahead = max(int(year) - int(cur), 0)
            disc = (1.0 - cfg.contract_year_discount_rate) ** years_ahead
            disc = _clamp(disc, 0.35, 1.00)

            units = sign * float(cfg.option_value_base_units) * float(gm) * float(p_active) * float(disc)
            if int(year) == int(cur):
                now_units += units
            else:
                fut_units += units

            rows.append(
                {
                    "year": int(year),
                    "type": str(opt_type),
                    "status": str(raw_status or "PENDING"),
                    "cap": cap_y,
                    "salary": salary_y,
                    "fair_salary": fair_y,
                    "mismatch_cap_share": mismatch_cap_share,
                    "mismatch_mult": gm,
                    "p_active": p_active,
                    "disc": disc,
                    "units": units,
                }
            )

        out = self._clamp_abs_mass(ValueComponents(now_units, fut_units), float(cfg.option_value_abs_cap))
        return out, rows


    def _ovr_to_now_value(self, ovr: float) -> float:
        cfg = self.config
        # Sigmoid mapping to [min, max]
        x = (ovr - cfg.player_ovr_center) / max(cfg.player_ovr_scale, cfg.eps)
        s = _sigmoid(x)
        return cfg.player_ovr_now_min + (cfg.player_ovr_now_max - cfg.player_ovr_now_min) * s

    def _ovr_star_bonus(self, ovr: float) -> float:
        cfg = self.config
        # Softplus above a shift; scaled
        x = (ovr - cfg.player_star_softplus_shift) * cfg.player_star_softplus_scale
        return _softplus(x) * 0.9  # bonus magnitude is tunable; keep deterministic

    def _age_to_now_decay_factor(self, age: float) -> float:
        cfg = self.config
        if age <= cfg.age_peak:
            return 1.0
        diff = age - cfg.age_peak
        factor = 1.0 - diff * cfg.age_now_decay_per_year
        return _clamp(factor, 0.35, 1.0)

    def _age_to_future_factor(self, age: float) -> float:
        cfg = self.config
        # below peak -> growth, above peak -> decay, both clamped
        if age <= cfg.age_peak:
            diff = cfg.age_peak - age
            factor = 1.0 + diff * cfg.age_future_growth_per_year_under_peak
        else:
            diff = age - cfg.age_peak
            factor = 1.0 - diff * cfg.age_future_decay_per_year_over_peak
        return _clamp(factor, cfg.age_future_floor, cfg.age_future_cap)

    def _resolve_expected_salary_scale(self) -> Tuple[float, float, Dict[str, Any]]:
        """Resolve expected-salary curve scale.

        Returns (midpoint_dollars, span_dollars, meta).

        - If config.salary_cap is provided (> eps), we use cap-share ratios.
        - Otherwise we fall back to legacy absolute-dollar defaults.
        """
        cfg = self.config

        salary_cap = _safe_float(getattr(cfg, "salary_cap", None), 0.0)
        if salary_cap > cfg.eps:
            mid_pct = _safe_float(getattr(cfg, "expected_salary_midpoint_cap_pct", None), 0.0)
            span_pct = _safe_float(getattr(cfg, "expected_salary_span_cap_pct", None), 0.0)
            midpoint = salary_cap * mid_pct
            span = salary_cap * span_pct
            return float(midpoint), float(span), {
                "source": "cap_pct",
                "salary_cap": float(salary_cap),
                "mid_pct": float(mid_pct),
                "span_pct": float(span_pct),
            }

        # Backward-compat / defensive: expected_salary_midpoint was removed from the
        # new contract-value design, but legacy helpers may still call this.
        midpoint_abs = _safe_float(getattr(cfg, "expected_salary_midpoint", None), 18_000_000.0)
        return float(midpoint_abs), float(cfg.expected_salary_span), {
            "source": "legacy_abs",
            "salary_cap": None,
        }

    def _expected_salary_from_ovr(self, ovr: float) -> Tuple[float, Dict[str, Any]]:
        cfg = self.config

        # Sigmoid mapping to [midpoint - span, midpoint + span]
        x = (ovr - cfg.expected_salary_ovr_center) / max(cfg.expected_salary_ovr_scale, cfg.eps)
        s = _sigmoid(x)

        midpoint, span, scale_meta = self._resolve_expected_salary_scale()
        lo = midpoint - span
        hi = midpoint + span

        # Defensive: keep ordering and non-negative lower bound.
        if hi < lo:
            lo, hi = hi, lo
        if lo < 0.0:
            lo = 0.0

        expected = lo + (hi - lo) * s

        meta: Dict[str, Any] = dict(scale_meta)
        meta.update(
            {
                "ovr": float(ovr),
                "x": float(x),
                "sigmoid": float(s),
                "lo": float(lo),
                "hi": float(hi),
                "midpoint": float(midpoint),
                "span": float(span),
            }
        )
        return float(expected), meta

    def _position_scarcity_factor(self, snap: PlayerSnapshot) -> Tuple[float, Dict[str, Any]]:
        """
        포지션 희소성은 '시장' 레벨에서만 아주 가볍게 반영.
        - 팀 니즈/상황과는 무관하게, 리그 전체에서 구하기 어려운 타입이 비싸질 수 있다.
        - 여기서는 과도한 영향을 피하도록 배율 폭을 억제한다.
        """
        pos = (snap.pos or "").upper()
        # 간단한 예시: C 희소성 살짝, WING( SF/SG ) 살짝
        if "C" in pos and "PF" not in pos:
            return 1.04, {"pos": pos, "bucket": "center"}
        if "SF" in pos and "SG" in pos:
            return 1.02, {"pos": pos, "bucket": "wing_combo"}
        return 1.0, {"pos": pos, "bucket": "neutral"}

    def _safe_injury_payload(self, snap: PlayerSnapshot) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        meta = snap.meta if isinstance(snap.meta, dict) else {}
        payload = meta.get("injury") if isinstance(meta, dict) else None
        if not isinstance(payload, dict):
            return {}, {"injury_payload_present": False, "fallback_used": True}
        flags = payload.get("flags") if isinstance(payload.get("flags"), dict) else {}
        return payload, {
            "injury_payload_present": True,
            "fallback_used": bool(flags.get("fallback_used", False)),
        }

    def _inj_current_penalty_factor(self, injury_payload: Mapping[str, Any]) -> Tuple[float, Dict[str, Any]]:
        cfg = self.config
        cur = injury_payload.get("current") if isinstance(injury_payload.get("current"), Mapping) else {}

        status = str(cur.get("status") or "UNKNOWN").upper()
        is_out = bool(cur.get("is_out", status == "OUT"))
        is_returning = bool(cur.get("is_returning", status == "RETURNING"))
        days_to_return = max(_safe_float(cur.get("days_to_return"), 0.0), 0.0)
        body_part = cur.get("body_part")
        severity = _safe_int(cur.get("severity"), 0)

        if not (is_out or is_returning):
            return 1.0, {
                "status": status,
                "is_out": bool(is_out),
                "is_returning": bool(is_returning),
                "days_to_return": float(days_to_return),
                "body_part": body_part,
                "severity": int(severity),
                "current_penalty": 0.0,
                "current_factor": 1.0,
            }

        t30 = max(_safe_float(cfg.inj_current_t30_days, 30.0), 1.0)
        t180 = max(_safe_float(cfg.inj_current_t180_days, 180.0), t30 + 1.0)
        s30 = max(_safe_float(cfg.inj_current_s30_days, 10.0), cfg.eps)
        s180 = max(_safe_float(cfg.inj_current_s180_days, 24.0), cfg.eps)
        w30 = _clamp(_safe_float(cfg.inj_current_weight_30, 0.08), 0.0, 1.0)
        w180 = _clamp(_safe_float(cfg.inj_current_weight_180, 0.16), 0.0, 1.0)

        g30 = _sigmoid((days_to_return - t30) / s30)
        g180 = _sigmoid((days_to_return - t180) / s180)
        mid = _clamp((days_to_return - t30) / max(t180 - t30, cfg.eps), 0.0, 1.0)

        penalty = (w30 * g30 * mid) + (w180 * g180)
        status_mult = 1.0 if is_out else _clamp(_safe_float(cfg.inj_current_returning_multiplier, 0.50), 0.0, 1.0)
        penalty = _clamp(penalty * status_mult, 0.0, 0.95)
        factor = _clamp(1.0 - penalty, _clamp(cfg.inj_current_factor_floor, 0.0, 1.0), 1.0)
        return factor, {
            "status": status,
            "is_out": bool(is_out),
            "is_returning": bool(is_returning),
            "days_to_return": float(days_to_return),
            "body_part": body_part,
            "severity": int(severity),
            "g30": float(g30),
            "g180": float(g180),
            "mid": float(mid),
            "w30": float(w30),
            "w180": float(w180),
            "status_mult": float(status_mult),
            "current_penalty": float(penalty),
            "current_factor": float(factor),
        }

    def _inj_history_penalty_factor(self, injury_payload: Mapping[str, Any]) -> Tuple[float, Dict[str, Any]]:
        cfg = self.config
        hist = injury_payload.get("history") if isinstance(injury_payload.get("history"), Mapping) else {}

        recent_180 = max(_safe_float(hist.get("recent_count_180d"), 0.0), 0.0)
        critical_365 = max(_safe_float(hist.get("critical_count_365d"), 0.0), 0.0)
        repeat_same = max(_safe_float(hist.get("same_part_repeat_365d_max"), 0.0) - 1.0, 0.0)
        weighted_severity = max(_safe_float(hist.get("weighted_severity_365d"), 0.0), 0.0)
        sev_signal = max(weighted_severity - 1.0, 0.0)

        fr = _soft_count(recent_180, cfg.inj_hist_recent_kr)
        fc = _soft_count(critical_365, cfg.inj_hist_critical_kc)
        fp = _soft_count(repeat_same, cfg.inj_hist_repeat_kp)
        fs = _soft_count(sev_signal, cfg.inj_hist_severity_ks)

        ar = _clamp(_safe_float(cfg.inj_hist_weight_recent, 0.26), 0.0, 1.0)
        ac = _clamp(_safe_float(cfg.inj_hist_weight_critical, 0.36), 0.0, 1.0)
        ap = _clamp(_safe_float(cfg.inj_hist_weight_repeat, 0.28), 0.0, 1.0)
        asv = _clamp(_safe_float(cfg.inj_hist_weight_severity, 0.10), 0.0, 1.0)
        weight_sum = max(ar + ac + ap + asv, cfg.eps)

        mix = (ar * fr + ac * fc + ap * fp + asv * fs) / weight_sum
        penalty_cap = _clamp(_safe_float(cfg.inj_hist_penalty_cap, 0.22), 0.0, 0.95)
        penalty = _clamp(penalty_cap * mix, 0.0, 0.95)
        factor = _clamp(1.0 - penalty, _clamp(cfg.inj_hist_factor_floor, 0.0, 1.0), 1.0)

        return factor, {
            "recent_180": float(recent_180),
            "critical_365": float(critical_365),
            "repeat_same": float(repeat_same),
            "weighted_severity_365d": float(weighted_severity),
            "fr": float(fr),
            "fc": float(fc),
            "fp": float(fp),
            "fs": float(fs),
            "history_penalty": float(penalty),
            "history_factor": float(factor),
        }

    def _health_credit_bonus(
        self,
        injury_payload: Mapping[str, Any],
        basketball_value: ValueComponents,
    ) -> Tuple[ValueComponents, Dict[str, Any]]:
        cfg = self.config
        hist = injury_payload.get("history") if isinstance(injury_payload.get("history"), Mapping) else {}
        health = (
            injury_payload.get("health_credit_inputs")
            if isinstance(injury_payload.get("health_credit_inputs"), Mapping)
            else {}
        )

        availability_rate = _clamp(_safe_float(health.get("availability_rate_365d"), 1.0), 0.0, 1.0)
        critical_365 = max(_safe_float(hist.get("critical_count_365d"), 0.0), 0.0)

        ref = _clamp(_safe_float(cfg.health_credit_availability_ref, 0.90), 0.0, 1.0)
        scale = max(_safe_float(cfg.health_credit_availability_scale, 0.06), cfg.eps)
        base_scale = max(_safe_float(cfg.health_credit_base_scale, 0.020), 0.0)
        no_critical_bonus = max(_safe_float(cfg.health_credit_no_critical_bonus, 1.20), 0.0)
        cap = _clamp(_safe_float(cfg.health_credit_cap, 0.06), 0.0, 0.30)

        gate = _sigmoid((availability_rate - ref) / scale)
        mult = no_critical_bonus if critical_365 <= 0.0 else 1.0
        bonus_pct = _clamp(base_scale * gate * mult, 0.0, cap)

        base_now = max(_safe_float(basketball_value.now, 0.0), 0.0)
        base_future = max(_safe_float(basketball_value.future, 0.0), 0.0)
        bonus = ValueComponents(base_now * bonus_pct, base_future * bonus_pct)

        return bonus, {
            "availability_rate": float(availability_rate),
            "availability_ref": float(ref),
            "availability_gate": float(gate),
            "critical_365": float(critical_365),
            "health_credit_pct": float(bonus_pct),
            "health_credit_cap": float(cap),
            "health_credit": float(bonus.total),
        }

    # -------------------------------------------------------------------------
    # Pick pricing
    # -------------------------------------------------------------------------
    def _price_pick(
        self,
        snap: PickSnapshot,
        *,
        asset_key: str,
        expectation: Optional[PickExpectation],
        env: ValuationEnv,
    ) -> MarketValuation:
        cfg = self.config
        steps: List[ValuationStep] = []

        year = int(snap.year)
        rnd = int(snap.round)

        # 1) round base
        base = cfg.pick_round1_base_future if rnd == 1 else cfg.pick_round2_base_future
        steps.append(
            ValuationStep(
                stage=ValuationStage.MARKET,
                mode=StepMode.ADD,
                code="PICK_ROUND_BASE",
                label="라운드 기반 기본가치",
                delta=_vc(now=0.0, future=base),
                meta={"round": rnd},
            )
        )

        # 2) expected pick number curve (if known)
        exp_num = None
        if expectation is not None:
            exp_num = expectation.expected_pick_number

        # fallback: use mid pick when unknown
        if exp_num is None:
            exp_num = 16.0

        curve_bonus = self._pick_number_bonus(float(exp_num), rnd=rnd)
        if abs(curve_bonus) > cfg.eps:
            steps.append(
                ValuationStep(
                    stage=ValuationStage.MARKET,
                    mode=StepMode.ADD,
                    code="PICK_EXPECTED_NUM_BONUS",
                    label="예상 순번 기반 프리미엄",
                    delta=_vc(now=0.0, future=curve_bonus),
                    meta={"expected_pick_number": float(exp_num), "round": rnd},
                )
            )

        value = _vc(now=0.0, future=base + curve_bonus)

        # 3) year discount (SSOT: env.current_season_year)
        cur_sy_i = int(env.current_season_year)
        if cur_sy_i <= 0:
            raise ValueError("ValuationEnv.current_season_year must be a positive integer")

        years_ahead = max(year - cur_sy_i, 0)
        disc = (1.0 - cfg.pick_year_discount_rate) ** years_ahead
        disc = _clamp(disc, 0.35, 1.0)
        steps.append(
            ValuationStep(
                stage=ValuationStage.MARKET,
                mode=StepMode.MUL,
                code="PICK_YEAR_DISCOUNT",
                label="연도 할인(먼 미래일수록 감소)",
                factor=disc,
                meta={"current_season_year": cur_sy_i, "pick_year": year, "years_ahead": years_ahead},
            )
        )
        value = value.scale(disc)

        # 4) protection expectation (TOP_N)
        prot = snap.protection
        if isinstance(prot, dict) and (prot.get("type") or prot.get("rule")):
            value, prot_steps = self._apply_pick_protection(value, exp_num=float(exp_num), protection=prot)
            steps.extend(prot_steps)

        return MarketValuation(
            asset_key=asset_key,
            kind=AssetKind.PICK,
            ref_id=str(snap.pick_id),
            value=value,
            steps=tuple(steps),
            meta={
                "year": year,
                "round": rnd,
                "original_team": snap.original_team,
                "owner_team": snap.owner_team,
                "expected_pick_number": float(exp_num) if exp_num is not None else None,
            },
        )

    def _pick_number_bonus(self, expected_pick_number: float, *, rnd: int) -> float:
        """
        expected pick number가 상위일수록(숫자가 작을수록) 더 비싸지는 비선형 프리미엄.
        """
        cfg = self.config
        n = _clamp(expected_pick_number, float(cfg.pick_num_best), float(cfg.pick_num_worst))
        # normalize: best -> 1.0, worst -> 0.0
        t = (float(cfg.pick_num_worst) - n) / max(float(cfg.pick_num_worst - cfg.pick_num_best), cfg.eps)
        # power curve
        p = t ** cfg.pick_num_curve_power
        # round2는 프리미엄 폭을 낮춘다
        scale = 10.0 if rnd == 1 else 2.0
        return scale * p

    def _apply_pick_protection(
        self,
        value: ValueComponents,
        *,
        exp_num: float,
        protection: Mapping[str, Any],
    ) -> Tuple[ValueComponents, List[ValuationStep]]:
        """
        TOP_N 보호 픽의 시장가 기대값:
        E[value] = p_convey * unprotected_value + (1 - p_convey) * compensation_value

        convey probability는 expected_pick_number와 보호 기준 n의 차이를
        logistic으로 근사한다.
        """
        cfg = self.config
        steps: List[ValuationStep] = []

        prot_type = str(protection.get("type") or protection.get("rule") or "").upper()
        if prot_type != "TOP_N":
            # unknown protection: do not modify, but leave a log
            steps.append(
                ValuationStep(
                    stage=ValuationStage.MARKET,
                    mode=StepMode.ADD,
                    code="PICK_PROTECTION_UNSUPPORTED",
                    label="보호 규칙 미지원(가격 변경 없음)",
                    meta={"type": prot_type},
                )
            )
            return value, steps

        n = _safe_int(protection.get("n"), 0)
        comp = protection.get("compensation") or {}
        comp_value = _safe_float(comp.get("value"), 0.0)
        comp_label = str(comp.get("label") or "Protection compensation")

        # probability that pick conveys (not protected)
        gap = exp_num - float(n)  # positive => likely conveys
        p = _sigmoid(cfg.protection_logit_k * gap + cfg.protection_logit_bias)
        p = _clamp(p, 0.05, 0.95)

        steps.append(
            ValuationStep(
                stage=ValuationStage.MARKET,
                mode=StepMode.MUL,
                code="PICK_CONVEY_PROB",
                label="보호 픽 convey 확률(근사)",
                factor=p,
                meta={"expected_pick_number": exp_num, "top_n": n, "gap": gap},
            )
        )

        # expected value blend (all future for picks)
        unprot = value.future
        blended_future = p * unprot + (1.0 - p) * comp_value

        steps.append(
            ValuationStep(
                stage=ValuationStage.MARKET,
                mode=StepMode.ADD,
                code="PICK_PROTECTION_EXPECTATION",
                label="보호 기대값 블렌딩",
                delta=_vc(now=0.0, future=(blended_future - unprot)),
                meta={"comp_label": comp_label, "comp_value": comp_value},
            )
        )

        return _vc(now=value.now, future=blended_future), steps

    # -------------------------------------------------------------------------
    # Swap pricing
    # -------------------------------------------------------------------------
    def _price_swap(
        self,
        snap: SwapSnapshot,
        *,
        asset_key: str,
        pick_a: Optional[PickSnapshot],
        pick_b: Optional[PickSnapshot],
        pick_a_expectation: Optional[PickExpectation],
        pick_b_expectation: Optional[PickExpectation],
        env: ValuationEnv,
    ) -> MarketValuation:
        cfg = self.config
        steps: List[ValuationStep] = []

        # swap pricing needs underlying pick snapshots; if missing, return neutral with log
        if pick_a is None or pick_b is None:
            steps.append(
                ValuationStep(
                    stage=ValuationStage.MARKET,
                    mode=StepMode.ADD,
                    code="SWAP_MISSING_PICKS",
                    label="스왑 대상 픽 스냅샷 없음(가격 중립)",
                    delta=_vc(0.0, 0.0),
                    meta={"pick_id_a": snap.pick_id_a, "pick_id_b": snap.pick_id_b},
                )
            )
            return MarketValuation(
                asset_key=asset_key,
                kind=AssetKind.SWAP,
                ref_id=str(snap.swap_id),
                value=_vc(0.0, 0.0),
                steps=tuple(steps),
                meta={"active": snap.active, "owner_team": snap.owner_team},
            )

        # Swap market value:
        # value ~= exercise_prob * (V(best) - V(worst)) * swap_exercise_scale
        #
        # IMPORTANT:
        # - Do NOT rely on PickSnapshot.meta for expected pick number.
        # - Reuse pick pricing (_price_pick) so year discount / protection / curves stay consistent.

        mv_a = self._price_pick(
            pick_a,
            asset_key=f"pick:{pick_a.pick_id}",
            expectation=pick_a_expectation,
            env=env,
        )
        mv_b = self._price_pick(
            pick_b,
            asset_key=f"pick:{pick_b.pick_id}",
            expectation=pick_b_expectation,
            env=env,
        )

        v_a = float(mv_a.value.future)
        v_b = float(mv_b.value.future)

        # optionality gain must be symmetric w.r.t. A/B ordering
        gain_raw = max(v_a, v_b) - min(v_a, v_b)

        exp_a = float(pick_a_expectation.expected_pick_number) if (pick_a_expectation and pick_a_expectation.expected_pick_number is not None) else 16.0
        exp_b = float(pick_b_expectation.expected_pick_number) if (pick_b_expectation and pick_b_expectation.expected_pick_number is not None) else 16.0

        gap = abs(exp_a - exp_b)
        exercise_prob = _clamp(gap * cfg.swap_gap_to_prob_scale / 10.0, 0.15, 0.85)

        steps.append(
            ValuationStep(
                stage=ValuationStage.MARKET,
                mode=StepMode.ADD,
                code="SWAP_OPTION_GAIN",
                label="스왑 옵션 기대 이득(프리미엄)",
                delta=_vc(now=0.0, future=gain_raw),
                meta={
                    "exp_a": exp_a,
                    "exp_b": exp_b,
                    "mv_a_future": v_a,
                    "mv_b_future": v_b,
                    "pick_id_a": pick_a.pick_id,
                    "pick_id_b": pick_b.pick_id,
                },
            )
        )

        steps.append(
            ValuationStep(
                stage=ValuationStage.MARKET,
                mode=StepMode.MUL,
                code="SWAP_EXERCISE_PROB",
                label="스왑 행사 확률(근사)",
                factor=exercise_prob,
                meta={"gap": gap},
            )
        )

        steps.append(
            ValuationStep(
                stage=ValuationStage.MARKET,
                mode=StepMode.MUL,
                code="SWAP_EXERCISE_SCALE",
                label="스왑 옵션 스케일",
                factor=cfg.swap_exercise_scale,
                meta={},
            )
        )

        future = gain_raw * exercise_prob * cfg.swap_exercise_scale
        return MarketValuation(
            asset_key=asset_key,
            kind=AssetKind.SWAP,
            ref_id=str(snap.swap_id),
            value=_vc(now=0.0, future=future),
            steps=tuple(steps),
            meta={
                "pick_id_a": snap.pick_id_a,
                "pick_id_b": snap.pick_id_b,
                "owner_team": snap.owner_team,
                "active": snap.active,
            },
        )

    # -------------------------------------------------------------------------
    # Fixed asset pricing
    # -------------------------------------------------------------------------
    def _price_fixed(self, snap: FixedAssetSnapshot, *, asset_key: str) -> MarketValuation:
        cfg = self.config
        steps: List[ValuationStep] = []

        v = _safe_float(snap.value, 0.0)
        timing = (snap.attrs.get("timing") if isinstance(snap.attrs, dict) else None) or cfg.fixed_default_timing
        timing = str(timing).lower().strip()

        if timing == "now":
            value = _vc(now=v, future=0.0)
            steps.append(
                ValuationStep(
                    stage=ValuationStage.MARKET,
                    mode=StepMode.ADD,
                    code="FIXED_VALUE_NOW",
                    label="고정자산 가치(현재)",
                    delta=_vc(now=v, future=0.0),
                    meta={"timing": timing},
                )
            )
        else:
            value = _vc(now=0.0, future=v)
            steps.append(
                ValuationStep(
                    stage=ValuationStage.MARKET,
                    mode=StepMode.ADD,
                    code="FIXED_VALUE_FUTURE",
                    label="고정자산 가치(미래)",
                    delta=_vc(now=0.0, future=v),
                    meta={"timing": timing},
                )
            )

        return MarketValuation(
            asset_key=asset_key,
            kind=AssetKind.FIXED,
            ref_id=str(snap.asset_id),
            value=value,
            steps=tuple(steps),
            meta={"label": snap.label, "owner_team": snap.owner_team, "source_pick_id": snap.source_pick_id},
        )
