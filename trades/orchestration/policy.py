from __future__ import annotations

from dataclasses import asdict
import hashlib
import random
from typing import Any, Dict, List, Optional, Set, Tuple

from ..valuation.types import DealVerdict
from .types import ActorPlan, OrchestrationConfig

try:
    from team_situation import TeamSituation  # type: ignore
except Exception:  # pragma: no cover
    try:
        from data.team_situation import TeamSituation  # type: ignore
    except Exception:  # pragma: no cover
        TeamSituation = object  # type: ignore


def _avg_deadline_pressure(tick_ctx: Any) -> float:
    pressures = []
    for ts in (getattr(tick_ctx, "team_situations", {}) or {}).values():
        try:
            c = getattr(ts, "constraints", None)
            pressures.append(float(getattr(c, "deadline_pressure", 0.0) or 0.0))
        except Exception:
            continue
    return (sum(pressures) / len(pressures)) if pressures else 0.0



def _clamp01(x: float) -> float:
    try:
        xf = float(x)
    except Exception:
        return 0.0
    if xf <= 0.0:
        return 0.0
    if xf >= 1.0:
        return 1.0
    return xf

def rush_scalar_from_avg_pressure(avg_pressure: float, *, config: OrchestrationConfig) -> float:
    """0..1 scalar indicating how deep we are into the deadline "rush" phase."""
    avg = _clamp01(_safe_float(avg_pressure, 0.0))
    start = float(getattr(config, "rush_pressure_start", 0.65) or 0.65)
    full = float(getattr(config, "rush_pressure_full", 0.90) or 0.90)
    if full <= start:
        return 1.0 if avg >= start else 0.0
    if avg <= start:
        return 0.0
    if avg >= full:
        return 1.0
    return _clamp01((avg - start) / max(1e-6, (full - start)))

def compute_active_team_budget(tick_ctx: Any, *, config: OrchestrationConfig) -> int:
    avg = _avg_deadline_pressure(tick_ctx)
    n = int(config.min_active_teams) + int(round(float(config.deadline_bonus_active_teams) * avg))
    n = max(int(config.min_active_teams), min(int(config.max_active_teams), n))
    return n


def _stable_seed_int(*parts: str) -> int:
    raw = "|".join([str(p) for p in parts]).encode("utf-8")
    digest = hashlib.sha1(raw).digest()
    return int.from_bytes(digest[:8], "big", signed=False)


def _stable_u01(*parts: str) -> float:
    # 0.0 <= u < 1.0, deterministic
    seed = _stable_seed_int(*parts)
    return (seed / float(2**64))


def market_day_rhythm(tick_ctx: Any, *, config: OrchestrationConfig) -> Tuple[float, str, Dict[str, Any]]:
    """
    (A-5) 무거운 날/조용한 날 리듬.
    day_mult는 '같은 리그/같은 날짜'면 항상 동일(재현 가능).
    """
    if not bool(getattr(config, "enable_market_day_rhythm", True)):
        return 1.0, "DISABLED", {"avg_deadline_pressure": _avg_deadline_pressure(tick_ctx)}

    avg = float(_avg_deadline_pressure(tick_ctx))
    today = getattr(tick_ctx, "current_date", None)
    today_iso = str(today.isoformat()) if today else "UNKNOWN_DATE"
    league_fp = f"{getattr(tick_ctx, 'db_path', '')}|{getattr(tick_ctx, 'season_year', '')}"

    # NOTE: tick_nonce를 섞지 않음 => 그 날의 시장 분위기는 고정(현실감 ↑)
    seed = _stable_seed_int(str(config.seed_salt), "market_day_rhythm", league_fp, today_iso)
    r = random.Random(seed)

    p_cap = float(getattr(config, "market_day_prob_cap", 0.35))
    p_spike = float(getattr(config, "market_day_spike_prob_base", 0.08)) + float(
        getattr(config, "market_day_spike_prob_deadline_bonus", 0.12)
    ) * avg
    p_slump = float(getattr(config, "market_day_slump_prob_base", 0.10)) - float(
        getattr(config, "market_day_slump_prob_deadline_reduction", 0.08)
    ) * avg

    # clamp
    p_spike = max(0.0, min(p_cap, p_spike))
    p_slump = max(0.0, min(p_cap, p_slump))

    u = r.random()
    if u < p_spike:
        lo = float(getattr(config, "market_day_spike_mult_lo", 1.15))
        hi = float(getattr(config, "market_day_spike_mult_hi", 1.35))
        mult = r.uniform(lo, hi)
        kind = "HEAVY"
    elif u > (1.0 - p_slump):
        lo = float(getattr(config, "market_day_slump_mult_lo", 0.75))
        hi = float(getattr(config, "market_day_slump_mult_hi", 0.90))
        mult = r.uniform(lo, hi)
        kind = "SLOW"
    else:
        lo = float(getattr(config, "market_day_normal_mult_lo", 0.90))
        hi = float(getattr(config, "market_day_normal_mult_hi", 1.10))
        # triangular(mode=1.0): normal day는 1.0에 몰리게
        mult = r.triangular(lo, hi, 1.0)
        kind = "NORMAL"

    meta = {
        "avg_deadline_pressure": avg,
        "p_spike": p_spike,
        "p_slump": p_slump,
        "day_kind": kind,
        "day_mult": float(mult),
        "today": today_iso,
    }
    return float(mult), str(kind), meta


def apply_day_rhythm_to_budget(base_n: int, *, day_mult: float, config: OrchestrationConfig) -> int:
    """
    활동 팀수(n)에 day_mult 반영. 너무 요동치지 않게 strength로 완화.
    """
    if not bool(getattr(config, "enable_market_day_rhythm", True)):
        return int(base_n)
    strength = float(getattr(config, "market_day_budget_strength", 0.80))
    mult = 1.0 + strength * (float(day_mult) - 1.0)
    return int(round(float(base_n) * mult))


def assign_dynamic_max_results(
    plans: List[ActorPlan],
    *,
    tick_ctx: Any,
    config: OrchestrationConfig,
    day_mult: float,
) -> List[ActorPlan]:
    """
    (A-4) 팀별 max_results 동적 스케일링:
    - activity_score 랭크 기반
    - day_mult 반영
    - 팀별 미세 지터(안정 해시)로 '항상 똑같은 분배' 느낌 제거
    """
    if not plans:
        return []

    if not bool(getattr(config, "enable_dynamic_per_team_max_results", True)):
        # legacy/fallback: 모두 동일
        return [
            ActorPlan(
                team_id=str(getattr(p, "team_id", "")).upper(),
                activity_score=float(getattr(p, "activity_score", 0.0) or 0.0),
                max_results=int(config.per_team_max_results),
                activity_breakdown=getattr(p, "activity_breakdown", None),
                activity_tags=getattr(p, "activity_tags", None),
                effective_pressure=getattr(p, "effective_pressure", None),
                pressure_tier=getattr(p, "pressure_tier", None),
            )
            for p in plans
        ]

    min_r = int(getattr(config, "per_team_min_results", 3))
    max_r = int(getattr(config, "per_team_max_results_cap", 9))
    if max_r < min_r:
        max_r = min_r

    gamma = float(getattr(config, "per_team_results_activity_gamma", 0.70))
    day_exp = float(getattr(config, "per_team_results_day_exponent", 0.75))
    jitter = float(getattr(config, "per_team_results_team_jitter_strength", 0.08))

    today = getattr(tick_ctx, "current_date", None)
    today_iso = str(today.isoformat()) if today else "UNKNOWN_DATE"
    league_fp = f"{getattr(tick_ctx, 'db_path', '')}|{getattr(tick_ctx, 'season_year', '')}"

    ordered = sorted(plans, key=lambda a: (float(a.activity_score), a.team_id), reverse=True)
    n = len(ordered)

    out: List[ActorPlan] = []
    for i, p in enumerate(ordered):
        # top=1.0, bottom=0.0
        pct = 1.0 if n <= 1 else (1.0 - (float(i) / float(n - 1)))
        boost = pct ** gamma

        k = float(min_r) + (float(max_r - min_r) * boost)

        if bool(getattr(config, "enable_market_day_rhythm", True)):
            k *= float(day_mult) ** day_exp

        if jitter > 0.0:
            u = _stable_u01(str(config.seed_salt), "team_jitter", league_fp, today_iso, str(p.team_id))
            k *= (1.0 + ((u - 0.5) * 2.0 * jitter))

        k_int = int(round(k))
        k_int = max(min_r, min(max_r, k_int))

        out.append(
            ActorPlan(
                team_id=str(getattr(p, "team_id", "")).upper(),
                activity_score=float(getattr(p, "activity_score", 0.0) or 0.0),
                max_results=k_int,
                activity_breakdown=getattr(p, "activity_breakdown", None),
                activity_tags=getattr(p, "activity_tags", None),
                effective_pressure=getattr(p, "effective_pressure", None),
                pressure_tier=getattr(p, "pressure_tier", None),
            )
        )

    return out



def compute_ai_ai_commit_cap_v2(
    tick_ctx: Any,
    *,
    config: OrchestrationConfig,
    rng: Any = None,
) -> Tuple[int, Dict[str, Any]]:
    """AI↔AI 커밋 cap을 deadline_pressure 평균 + 러시(rush)로 동적으로 조절.

    Returns:
      (cap_int, meta)
    """
    avg = _avg_deadline_pressure(tick_ctx)
    low = float(config.ai_ai_cap_pressure_low)
    high = float(config.ai_ai_cap_pressure_high)
    cap_min = int(config.ai_ai_cap_min)
    cap_max = int(config.ai_ai_cap_max)

    if cap_max < cap_min:
        cap_max = cap_min

    cap_rush_max = int(getattr(config, "ai_ai_cap_rush_max", cap_max) or cap_max)
    if cap_rush_max < cap_max:
        cap_rush_max = cap_max

    rush_scalar = rush_scalar_from_avg_pressure(avg, config=config)
    rush_extra = float(max(0, cap_rush_max - cap_max)) * float(rush_scalar)

    use_stochastic = bool(getattr(config, "ai_ai_cap_stochastic", True))
    idle_p = float(getattr(config, "ai_ai_cap_idle_trade_prob", 0.0) or 0.0)
    use_stoch_round = bool(getattr(config, "ai_ai_cap_stochastic_rounding", True))

    r = rng if rng is not None else random

    meta: Dict[str, Any] = {
        "avg_deadline_pressure": float(avg),
        "rush_scalar": float(rush_scalar),
        "cap_min": int(cap_min),
        "cap_max": int(cap_max),
        "cap_rush_max": int(cap_rush_max),
        "rush_extra": float(rush_extra),
        "idle_trade_allowed": False,
        "cap_f": None,
    }

    # Base cap in float space (before integerization)
    if avg <= low:
        if use_stochastic and idle_p > 0.0:
            try:
                if float(getattr(r, "random")()) < idle_p:  # type: ignore[misc]
                    meta["idle_trade_allowed"] = True
                    cap = max(cap_min, 1)
                    return int(cap), meta
            except Exception:
                pass
        return int(cap_min), meta

    if avg >= high:
        cap_f = float(cap_max) + float(rush_extra)
    else:
        # Linear interpolation between cap_min and cap_max.
        t = (avg - low) / max(1e-6, (high - low))
        base_cap_f = float(cap_min) + (float(cap_max - cap_min) * float(t))
        cap_f = float(base_cap_f) + float(rush_extra)

    # Clamp to [cap_min, cap_rush_max]
    if cap_f <= float(cap_min):
        meta["cap_f"] = float(cap_min)
        return int(cap_min), meta
    if cap_f >= float(cap_rush_max):
        meta["cap_f"] = float(cap_rush_max)
        return int(cap_rush_max), meta

    meta["cap_f"] = float(cap_f)

    # Integerize
    if use_stochastic and use_stoch_round:
        lo_i = int(cap_f)  # floor for non-negative
        hi_i = min(cap_rush_max, lo_i + 1)
        frac = float(cap_f - lo_i)
        if frac <= 0.0:
            return int(max(cap_min, min(cap_rush_max, lo_i))), meta
        try:
            u = float(getattr(r, "random")())  # type: ignore[misc]
            return int(hi_i if u < frac else max(cap_min, lo_i)), meta
        except Exception:
            pass

    return int(round(cap_f)), meta


def compute_ai_ai_commit_cap(tick_ctx: Any, *, config: OrchestrationConfig, rng: Any = None) -> int:
    """
    AI↔AI 커밋 cap을 deadline_pressure 평균에 따라 동적으로 조절.

    기본(기존 동작):
    - pressure_low 이하: ai_ai_cap_min
    - pressure_high 이상: ai_ai_cap_max
    - low/high 사이: 선형 보간 + (옵션) stochastic rounding
    """
    cap, _meta = compute_ai_ai_commit_cap_v2(tick_ctx, config=config, rng=rng)
    return int(cap)


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


def _posture_factor(posture: str) -> float:
    p = str(posture or "STAND_PAT").upper()
    if p == "AGGRESSIVE_BUY":
        return 1.00
    if p in {"SELL"}:
        return 0.95
    if p in {"SOFT_BUY", "SOFT_SELL"}:
        return 0.70
    if p == "STAND_PAT":
        return 0.30
    return 0.40


def _base_activity_raw(urgency: float, deadline_pressure: float) -> float:
    # Existing intent: urgency dominates slightly, deadline pressure still matters.
    return _clamp01((0.55 * _safe_float(urgency)) + (0.45 * _safe_float(deadline_pressure)))


def _get_signals(ts: TeamSituation) -> Any:
    try:
        return getattr(ts, "signals", None)
    except Exception:
        return None


def _signals_snapshot(sig: Any) -> Dict[str, Any]:
    # Keep snapshot minimal and stable (for telemetry/UI).
    return {
        "conf_rank": getattr(sig, "conf_rank", None),
        "gb_to_6th": getattr(sig, "gb_to_6th", None),
        "gb_to_10th": getattr(sig, "gb_to_10th", None),
        "re_sign_pressure": getattr(sig, "re_sign_pressure", None),
        "expiring_top8_count": getattr(sig, "expiring_top8_count", None),
        "trend": getattr(sig, "trend", None),
        "last10_win_pct": getattr(sig, "last10_win_pct", None),
    }


def _gb_closeness(gb: Any, window: float) -> float:
    """Convert games-behind to 0..1 closeness within a window (smaller gb => higher closeness)."""
    w = float(window) if float(window) > 1e-6 else 1.0
    g = _safe_float(gb, 999.0)
    if g < 0.0:
        g = 0.0
    return _clamp01((w - g) / w)


# Signals → activity components weights/caps (tunable, but keep conservative for commercial stability).
_BUBBLE_W = 0.20
_CONTRACT_W = 0.16
_MOMENTUM_W = 0.10
_BOOST_CAP = 0.25

# Bubble windows (NBA fan intuition): within ~3.5 games of 6-seed, within ~2.5 games of 10-seed.
_BUBBLE6_WINDOW = 3.5
_BUBBLE10_WINDOW = 2.5


def _bubble_pressure(sig: Any, *, deadline_pressure: float) -> Tuple[float, Dict[str, Any]]:
    """
    Bubble pressure: teams near key cut lines (6th / 10th) get more incentive to trade.

    Important: gb_to_6th/10th are clamped to >=0 in TeamSituationSignals (above-line teams become 0),
    so we gate by conf_rank and apply a small penalty for above-line ranks to avoid over-activation.
    """
    dbg: Dict[str, Any] = {
        "value": 0.0,
        "close6": 0.0,
        "close10": 0.0,
        "w6": 0.0,
        "w10": 0.0,
        "above6_penalty_applied": False,
        "above10_penalty_applied": False,
        "bubble_scale": 0.45 + (0.55 * _clamp01(_safe_float(deadline_pressure))),
    }

    conf_rank = getattr(sig, "conf_rank", None)
    if conf_rank is None:
        return 0.0, dbg
    r = _safe_int(conf_rank, -1)
    if r <= 0:
        return 0.0, dbg

    gb6 = getattr(sig, "gb_to_6th", None)
    gb10 = getattr(sig, "gb_to_10th", None)

    close6 = _gb_closeness(gb6, _BUBBLE6_WINDOW)
    close10 = _gb_closeness(gb10, _BUBBLE10_WINDOW)

    # Rank gates: who is actually "in the bubble" in a way fans expect.
    if r <= 3:
        w6, w10 = 0.0, 0.0
    elif r <= 6:
        w6, w10 = 0.25, 0.05
    elif r <= 10:
        w6, w10 = 1.00, 0.10
    elif r <= 12:
        w6, w10 = 0.35, 1.00
    else:
        w6, w10 = 0.0, 0.0

    # Above-line penalty: for teams already above the line, gb_to_* can be 0;
    # we dampen the closeness so they don't look like bubble teams.
    try:
        gb6f = _safe_float(gb6, 999.0)
        if r <= 6 and gb6f <= 0.25:
            close6 *= 0.35
            dbg["above6_penalty_applied"] = True
    except Exception:
        pass
    try:
        gb10f = _safe_float(gb10, 999.0)
        if r <= 10 and gb10f <= 0.25:
            close10 *= 0.25
            dbg["above10_penalty_applied"] = True
    except Exception:
        pass

    bubble = _clamp01(max(w6 * close6, w10 * close10))

    dbg["value"] = bubble
    dbg["close6"] = float(close6)
    dbg["close10"] = float(close10)
    dbg["w6"] = float(w6)
    dbg["w10"] = float(w10)
    return bubble, dbg


def _contract_pressure(sig: Any, *, deadline_pressure: float) -> Tuple[float, Dict[str, Any]]:
    """
    Contract timing pressure: expiring key rotation + re-sign difficulty push teams to act.
    """
    cm_scale = 0.70 + (0.30 * _clamp01(_safe_float(deadline_pressure)))
    re_sign = _clamp01(_safe_float(getattr(sig, "re_sign_pressure", 0.0), 0.0))
    exp_cnt = _safe_int(getattr(sig, "expiring_top8_count", 0), 0)
    exp_norm = _clamp01(_safe_float(exp_cnt) / 3.0)  # 3+ meaningful expirings => max pressure
    val = _clamp01((0.65 * re_sign) + (0.35 * exp_norm))
    dbg: Dict[str, Any] = {
        "value": val,
        "re_sign_pressure": re_sign,
        "expiring_top8_count": exp_cnt,
        "exp_norm": exp_norm,
        "cm_scale": cm_scale,
    }
    return val, dbg


def _momentum_pressure(sig: Any, *, deadline_pressure: float) -> Tuple[float, Dict[str, Any]]:
    """
    Momentum pressure: extreme hot/cold stretches cause short-term all-in or panic moves.
    """
    cm_scale = 0.70 + (0.30 * _clamp01(_safe_float(deadline_pressure)))
    last10 = _clamp01(_safe_float(getattr(sig, "last10_win_pct", 0.5), 0.5))
    trend = _safe_float(getattr(sig, "trend", 0.0), 0.0)

    # Extreme last10: start ramping beyond 0.65 / below 0.35, saturate by ~0.80 / ~0.20.
    hot = _clamp01((last10 - 0.65) / 0.15)
    cold = _clamp01((0.35 - last10) / 0.15)
    extreme10 = float(max(hot, cold))

    trend_abs = _clamp01(abs(float(trend)) / 0.18)
    val = _clamp01((0.55 * extreme10) + (0.45 * trend_abs))

    dbg: Dict[str, Any] = {
        "value": val,
        "last10_win_pct": last10,
        "trend": float(trend),
        "extreme10": extreme10,
        "trend_abs": trend_abs,
        "cm_scale": cm_scale,
    }
    return val, dbg


def _signals_boost(
    bubble: float, contract: float, momentum: float, *, deadline_pressure: float
) -> Tuple[float, Dict[str, Any]]:
    bubble_scale = 0.45 + (0.55 * _clamp01(_safe_float(deadline_pressure)))
    cm_scale = 0.70 + (0.30 * _clamp01(_safe_float(deadline_pressure)))

    bubble_term = float(_BUBBLE_W) * float(bubble) * float(bubble_scale)
    contract_term = float(_CONTRACT_W) * float(contract) * float(cm_scale)
    momentum_term = float(_MOMENTUM_W) * float(momentum) * float(cm_scale)
    raw_boost = float(bubble_term + contract_term + momentum_term)
    applied = float(min(float(_BOOST_CAP), raw_boost))

    dbg: Dict[str, Any] = {
        "raw_boost": raw_boost,
        "cap": float(_BOOST_CAP),
        "applied": applied,
        "bubble_term": bubble_term,
        "contract_term": contract_term,
        "momentum_term": momentum_term,
        "weights": {
            "bubble_w": float(_BUBBLE_W),
            "contract_w": float(_CONTRACT_W),
            "momentum_w": float(_MOMENTUM_W),
        },
    }
    return applied, dbg


def team_activity_breakdown(ts: TeamSituation) -> Dict[str, Any]:
    """
    Compute a stable breakdown for team activity selection.

    Output is designed to be safe to log/telemetry and later reused for UI ("왜 움직였나").
    """
    posture = str(getattr(ts, "trade_posture", "STAND_PAT") or "STAND_PAT").upper()
    urgency = _safe_float(getattr(ts, "urgency", 0.0), 0.0)
    c = getattr(ts, "constraints", None)
    deadline_pressure = _safe_float(getattr(c, "deadline_pressure", 0.0) if c is not None else 0.0, 0.0)

    posture_factor = _posture_factor(posture)
    base_raw = _base_activity_raw(urgency, deadline_pressure)

    sig = _get_signals(ts)
    signals_present = sig is not None

    # Defaults (signals missing or parsing fails).
    bubble_val = 0.0
    contract_val = 0.0
    momentum_val = 0.0
    bubble_dbg: Dict[str, Any] = {
        "value": 0.0,
        "close6": 0.0,
        "close10": 0.0,
        "w6": 0.0,
        "w10": 0.0,
        "above6_penalty_applied": False,
        "above10_penalty_applied": False,
        "bubble_scale": 0.45 + (0.55 * _clamp01(deadline_pressure)),
    }
    contract_dbg: Dict[str, Any] = {
        "value": 0.0,
        "re_sign_pressure": 0.0,
        "expiring_top8_count": 0,
        "exp_norm": 0.0,
        "cm_scale": 0.70 + (0.30 * _clamp01(deadline_pressure)),
    }
    momentum_dbg: Dict[str, Any] = {
        "value": 0.0,
        "last10_win_pct": 0.5,
        "trend": 0.0,
        "extreme10": 0.0,
        "trend_abs": 0.0,
        "cm_scale": 0.70 + (0.30 * _clamp01(deadline_pressure)),
    }
    boost_dbg: Dict[str, Any] = {
        "raw_boost": 0.0,
        "cap": float(_BOOST_CAP),
        "applied": 0.0,
        "bubble_term": 0.0,
        "contract_term": 0.0,
        "momentum_term": 0.0,
        "weights": {
            "bubble_w": float(_BUBBLE_W),
            "contract_w": float(_CONTRACT_W),
            "momentum_w": float(_MOMENTUM_W),
        },
    }

    boost = 0.0
    signals_snapshot: Dict[str, Any] = {
        "conf_rank": None,
        "gb_to_6th": None,
        "gb_to_10th": None,
        "re_sign_pressure": None,
        "expiring_top8_count": None,
        "trend": None,
        "last10_win_pct": None,
    }

    if signals_present:
        try:
            signals_snapshot = _signals_snapshot(sig)
        except Exception:
            pass
        try:
            bubble_val, bubble_dbg = _bubble_pressure(sig, deadline_pressure=deadline_pressure)
        except Exception:
            bubble_val, bubble_dbg = 0.0, bubble_dbg
        try:
            contract_val, contract_dbg = _contract_pressure(sig, deadline_pressure=deadline_pressure)
        except Exception:
            contract_val, contract_dbg = 0.0, contract_dbg
        try:
            momentum_val, momentum_dbg = _momentum_pressure(sig, deadline_pressure=deadline_pressure)
        except Exception:
            momentum_val, momentum_dbg = 0.0, momentum_dbg
        try:
            boost, boost_dbg = _signals_boost(
                bubble_val, contract_val, momentum_val, deadline_pressure=deadline_pressure
            )
        except Exception:
            boost, boost_dbg = 0.0, boost_dbg

    raw_plus_boost = float(base_raw + float(boost))
    raw2 = _clamp01(raw_plus_boost)
    activity_score = float(posture_factor) * float(raw2)

    # Optional tags for UI/telemetry (stable, conservative thresholds).
    tags: list[str] = []
    try:
        cr = signals_snapshot.get("conf_rank")
        cr_i = _safe_int(cr, -1) if cr is not None else -1
        if bubble_val > 0.0:
            if 7 <= cr_i <= 10 and float(bubble_dbg.get("close6", 0.0) or 0.0) > 0.25:
                tags.append("BUBBLE_TOP6_CHASE")
            if 11 <= cr_i <= 12 and float(bubble_dbg.get("close10", 0.0) or 0.0) > 0.25:
                tags.append("BUBBLE_PLAYIN_PUSH")
        if contract_val >= 0.60:
            tags.append("CONTRACT_CRUNCH")
        l10 = _safe_float(momentum_dbg.get("last10_win_pct", 0.5), 0.5)
        if l10 >= 0.70:
            tags.append("MOMENTUM_HOT")
        elif l10 <= 0.30:
            tags.append("MOMENTUM_COLD")
    except Exception:
        pass

    return {
        "posture": posture,
        "posture_factor": float(posture_factor),
        "urgency": float(urgency),
        "deadline_pressure": float(deadline_pressure),
        "base_raw": float(base_raw),
        "signals_present": bool(signals_present),
        "signals": signals_snapshot,
        "components": {
            "bubble": bubble_dbg,
            "contract": contract_dbg,
            "momentum": momentum_dbg,
        },
        "boost": boost_dbg,
        "raw_plus_boost": float(raw_plus_boost),
        "raw2": float(raw2),
        "activity_score": float(activity_score),
        "tags": tags,
    }


def team_activity_score(ts: TeamSituation) -> float:
    # Keep external behavior: a single float used as selection weight.
    try:
        bd = team_activity_breakdown(ts)
        return float(bd.get("activity_score", 0.0) or 0.0)
    except Exception:
        # Last-resort fallback to prior simple behavior.
        posture = str(getattr(ts, "trade_posture", "STAND_PAT") or "STAND_PAT").upper()
        urgency = float(getattr(ts, "urgency", 0.0) or 0.0)
        c = getattr(ts, "constraints", None)
        deadline_pressure = float(getattr(c, "deadline_pressure", 0.0) or 0.0)
        pf = _posture_factor(posture)
        raw = (0.55 * urgency) + (0.45 * deadline_pressure)
        return pf * max(0.0, raw)



def effective_pressure_from_team_situation(
    ts: TeamSituation,
    *,
    config: OrchestrationConfig,
) -> Tuple[float, Dict[str, Any]]:
    """Team-specific 'effective' deadline pressure (0..1)."""
    dbg: Dict[str, Any] = {"deadline_pressure": 0.0, "urgency": 0.0, "boost_norm": 0.0, "posture": "STAND_PAT", "posture_term": 0.0, "effective_pressure": 0.0}
    deadline_pressure = 0.0
    urgency = 0.0
    boost_norm = 0.0
    posture = "STAND_PAT"
    try:
        bd = team_activity_breakdown(ts)
        if isinstance(bd, dict):
            deadline_pressure = _clamp01(_safe_float(bd.get("deadline_pressure"), 0.0))
            urgency = _clamp01(_safe_float(bd.get("urgency"), 0.0))
            posture = str(bd.get("posture") or posture).upper()
            boost = bd.get("boost")
            applied = _safe_float(boost.get("applied"), 0.0) if isinstance(boost, dict) else 0.0
            cap = float(globals().get("_BOOST_CAP", 0.25) or 0.25)
            if cap <= 1e-6:
                cap = 0.25
            boost_norm = _clamp01(applied / cap)
    except Exception:
        try:
            c = getattr(ts, "constraints", None)
            deadline_pressure = _clamp01(_safe_float(getattr(c, "deadline_pressure", 0.0), 0.0))
        except Exception:
            deadline_pressure = 0.0

    p = str(posture or "STAND_PAT").upper()
    if p == "AGGRESSIVE_BUY":
        posture_term = 1.0
    elif p in {"SELL"}:
        posture_term = 0.85
    elif p in {"SOFT_BUY", "SOFT_SELL"}:
        posture_term = 0.55
    elif p == "STAND_PAT":
        posture_term = 0.0
    else:
        posture_term = 0.25

    eff = _clamp01((0.55 * float(deadline_pressure)) + (0.25 * float(urgency)) + (0.15 * float(boost_norm)) + (0.05 * float(posture_term)))
    dbg.update({"deadline_pressure": float(deadline_pressure), "urgency": float(urgency), "boost_norm": float(boost_norm), "posture": str(posture), "posture_term": float(posture_term), "effective_pressure": float(eff)})
    return float(eff), dbg


def pressure_tier_from_effective_pressure(p: float, *, config: OrchestrationConfig) -> str:
    pf = _clamp01(_safe_float(p, 0.0))
    rush_th = float(getattr(config, "pressure_tier_rush_threshold", 0.85) or 0.85)
    high_th = float(getattr(config, "pressure_tier_high_threshold", 0.60) or 0.60)
    if rush_th < high_th:
        rush_th = high_th
    mid_th = float(min(high_th, max(0.25, high_th * 0.50)))
    if pf >= rush_th:
        return "RUSH"
    if pf >= high_th:
        return "HIGH"
    if pf >= mid_th:
        return "MID"
    return "LOW"


def build_team_pressure_maps(tick_ctx: Any, *, config: OrchestrationConfig) -> Tuple[Dict[str, float], Dict[str, str], Dict[str, Any]]:
    eff_by_team: Dict[str, float] = {}
    tier_by_team: Dict[str, str] = {}
    tier_counts: Dict[str, int] = {"LOW": 0, "MID": 0, "HIGH": 0, "RUSH": 0}
    for tid, ts in (getattr(tick_ctx, "team_situations", {}) or {}).items():
        team_id = str(tid).upper()
        try:
            eff, _dbg = effective_pressure_from_team_situation(ts, config=config)
        except Exception:
            eff = 0.0
        eff_by_team[team_id] = float(eff)
        tier = pressure_tier_from_effective_pressure(eff, config=config)
        tier_by_team[team_id] = str(tier)
        tier_counts[tier] = int(tier_counts.get(tier, 0)) + 1
    avg_eff = (sum(eff_by_team.values()) / len(eff_by_team)) if eff_by_team else 0.0
    return eff_by_team, tier_by_team, {"avg_effective_pressure": float(avg_eff), "pressure_tier_counts": dict(tier_counts)}


def cooldown_days_after_executed_trade(team_pressure: float, rush_scalar: float, *, config: OrchestrationConfig) -> int:
    base = int(getattr(config, "cooldown_days_after_executed_trade", 5) or 5)
    min_days = int(getattr(config, "cooldown_executed_trade_min_days", 1) or 1)
    if base < min_days:
        base, min_days = min_days, base
    low = float(getattr(config, "cooldown_executed_trade_pressure_low", 0.15) or 0.15)
    high = float(getattr(config, "cooldown_executed_trade_pressure_high", 0.85) or 0.85)
    if high < low:
        high = low
    p = _clamp01(max(_safe_float(team_pressure, 0.0), _safe_float(rush_scalar, 0.0)))
    if p <= low:
        t = 0.0
    elif p >= high:
        t = 1.0
    else:
        t = (p - low) / max(1e-6, (high - low))
    days_i = int(round(float(base) - (float(base - min_days) * float(t))))
    return int(max(int(min_days), min(int(base), days_i)))


def allow_second_trade_today(*, tick_ctx: Any, team_id: str, traded_count_today: int, team_pressure: float, rush_scalar: float, config: OrchestrationConfig, human_team_ids: Optional[Set[str]] = None) -> Tuple[bool, Dict[str, Any]]:
    tid = str(team_id).upper()
    cnt = _safe_int(traded_count_today, 0)
    meta: Dict[str, Any] = {"team_id": tid, "traded_count_today": int(cnt), "team_pressure": float(_clamp01(_safe_float(team_pressure, 0.0))), "rush_scalar": float(_clamp01(_safe_float(rush_scalar, 0.0))), "threshold": float(getattr(config, "allow_second_trade_pressure_threshold", 0.80) or 0.80), "prob": float(getattr(config, "allow_second_trade_prob", 0.35) or 0.35), "u": None, "reason": None}
    if cnt <= 0:
        meta["reason"] = "FIRST_TRADE"
        return True, meta
    max_trades = int(getattr(config, "max_trades_per_team_per_tick_rush", 2) or 2)
    if cnt >= max_trades:
        meta["reason"] = "MAX_TRADES_REACHED"
        return False, meta
    if bool(getattr(config, "never_allow_multi_trade_for_humans", True)) and human_team_ids:
        if tid in {str(x).upper() for x in human_team_ids if x}:
            meta["reason"] = "HUMAN_TEAM_BLOCKED"
            return False, meta
    if float(meta["team_pressure"]) < float(meta["threshold"]):
        meta["reason"] = "PRESSURE_BELOW_THRESHOLD"
        return False, meta
    prob = _clamp01(float(meta["prob"]))
    today = getattr(tick_ctx, "current_date", None)
    today_iso = str(today.isoformat()) if today else "UNKNOWN_DATE"
    league_fp = f"{getattr(tick_ctx, 'db_path', '')}|{getattr(tick_ctx, 'season_year', '')}"
    u = _stable_u01(str(getattr(config, "seed_salt", "")), "allow_second_trade_today", league_fp, today_iso, tid, str(cnt))
    meta["u"] = float(u)
    allow = bool(float(u) < float(prob))
    meta["reason"] = "ALLOWED" if allow else "BERNOULLI_REJECT"
    return allow, meta

def should_offer_to_user(prop: Any, *, config: OrchestrationConfig) -> bool:
    try:
        return float(getattr(prop, "score", 0.0) or 0.0) >= float(config.user_offer_min_score)
    except Exception:
        return False



def user_reject_offer_probability(*, tone: str, pressure: float, config: OrchestrationConfig) -> float:
    """
    Probability of sending a REJECT-based user offer (PROBE / LOWBALL).

    Design goals:
    - Avoid spam: even if a deal is within the "reject window", we only send it sometimes.
    - Deadline realism: pressure increases the chance of PROBE/LOWBALL offers.

    p = clamp01(base + bonus * clamp01(pressure))
    """
    t = str(tone or "").upper()
    p01 = _clamp01(pressure)

    if t == "PROBE":
        base = float(config.probe_base_prob)
        bonus = float(config.probe_pressure_bonus)
        return _clamp01(base + bonus * p01)
    if t == "LOWBALL":
        base = float(config.lowball_base_prob)
        bonus = float(config.lowball_pressure_bonus)
        return _clamp01(base + bonus * p01)
    return 0.0

def is_ai_ai_auto_commit(prop: Any) -> bool:
    try:
        return (
            prop.buyer_decision.verdict == DealVerdict.ACCEPT
            and prop.seller_decision.verdict == DealVerdict.ACCEPT
        )
    except Exception:
        return False


def office_gate_ok(prop: Any, *, config: OrchestrationConfig) -> Tuple[bool, Dict[str, Any]]:
    """
    '리그 오피스 게이트' — 극단/버그성 ACCEPT를 커밋 전에 차단.

    오케스트레이션 역할 범위를 넘지 않기 위해:
    - 새로운 평가를 하지 않는다.
    - DealGenerator가 이미 제공한 score/evaluation/decision 값으로만 안전 컷을 둔다.

    반환:
      (ok, info)
    """
    info: Dict[str, Any] = {}

    # 1) score 하한
    try:
        score = float(getattr(prop, "score", 0.0) or 0.0)
    except Exception:
        score = 0.0
    info["score"] = score
    if score < float(config.ai_ai_office_gate_min_score):
        return False, {"reason": "SCORE_TOO_LOW", **info}

    # 2) decision/eval 일관성 검사 + 최소 margin 컷
    min_margin = float(config.ai_ai_office_gate_min_margin)
    slack = float(config.ai_ai_office_gate_overpay_slack)

    def _side_check(side: str) -> Tuple[bool, Dict[str, Any]]:
        ev = getattr(prop, f"{side}_eval", None)
        dec = getattr(prop, f"{side}_decision", None)
        if ev is None or dec is None:
            # 정보가 없다면 보수적으로 통과(오케스트레이션이 강제 판단하면 범위 초과/거래량 급감 가능)
            return True, {"side": side, "note": "MISSING_EVAL_OR_DECISION"}

        # 가능한 필드: net_surplus / required_surplus / overpay_allowed
        try:
            net_surplus = float(getattr(ev, "net_surplus", 0.0) or 0.0)
        except Exception:
            net_surplus = 0.0
        try:
            required_surplus = float(getattr(dec, "required_surplus", 0.0) or 0.0)
        except Exception:
            required_surplus = 0.0
        try:
            overpay_allowed = float(getattr(dec, "overpay_allowed", 0.0) or 0.0)
        except Exception:
            overpay_allowed = 0.0

        margin = net_surplus - required_surplus
        detail = {
            "side": side,
            "net_surplus": net_surplus,
            "required_surplus": required_surplus,
            "overpay_allowed": overpay_allowed,
            "margin": margin,
        }

        # 일관성: margin이 -overpay_allowed 보다 훨씬 낮으면 ACCEPT가 논리적으로 이상할 가능성이 큼
        if margin < -(overpay_allowed + slack):
            return False, {"reason": "MARGIN_BELOW_OVERPAY_ALLOWED", **detail}

        # 극단 방지(너무 큰 적자 margin은 리그가 veto)
        if margin < min_margin:
            return False, {"reason": "MARGIN_TOO_NEGATIVE", **detail}

        return True, detail

    ok_b, info_b = _side_check("buyer")
    ok_s, info_s = _side_check("seller")
    info["buyer"] = info_b
    info["seller"] = info_s

    if not ok_b or not ok_s:
        # reason은 side info에 들어있음
        reason = (info_b.get("reason") if not ok_b else info_s.get("reason")) or "VETO"
        return False, {"reason": reason, **info}

    return True, {"reason": "OK", **info}
