from __future__ import annotations

"""Credibility (promise trustworthiness) for the agency system.

In Football Manager–style interaction, a promise should not always work.
Even if relationship trust is decent, players learn from history:
- repeated broken promises => "I don't believe you anymore"
- fulfilled promises => credibility grows

This module computes a *promise-type-specific* credibility score in [0, 1]
from:
- trust (relationship)
- memory (broken/fulfilled totals and by type)
- recent outcomes (last N)
- mental traits (loyalty/coachability/adaptability reduce suspicion;
  ego/ambition increase suspicion)

SSOT policy
-----------
Credibility is a *derived* value and should not be stored.
It can always be recomputed from agency state + memory.

All functions here are deterministic and DB-free.
"""

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Tuple

from .config import AgencyConfig
from .utils import clamp, clamp01, mental_norm, safe_float


@dataclass(frozen=True, slots=True)
class _CredibilityDefaults:
    recent_window: int = 6

    broken_total_w: float = 0.10
    broken_type_w: float = 0.18
    fulfilled_total_w: float = 0.05
    fulfilled_type_w: float = 0.08

    recent_broken_w: float = 0.10
    recent_fulfilled_w: float = 0.06

    min_accept_cred: float = 0.20


def _get_cred_cfg(cfg: AgencyConfig) -> Any:
    # Planned name: cfg.credibility
    if hasattr(cfg, "credibility"):
        return getattr(cfg, "credibility")
    # Alternative
    if hasattr(cfg, "cred"):
        return getattr(cfg, "cred")
    return _CredibilityDefaults()


def _get_state_value(state: Any, key: str, default: Any = None) -> Any:
    if isinstance(state, Mapping):
        return state.get(key, default)
    return getattr(state, key, default)


def _get_context(state: Any) -> Dict[str, Any]:
    ctx = _get_state_value(state, "context", None)
    if isinstance(ctx, dict):
        return ctx
    return {}


def _get_mem(state: Any) -> Dict[str, Any]:
    ctx = _get_context(state)
    mem = ctx.get("mem")
    if isinstance(mem, dict):
        return mem
    return {}


def _ratio(num: float, den: float, smoothing: float) -> float:
    # num/(num+den+smoothing) to reduce volatility for small samples.
    n = max(0.0, float(num))
    d = max(0.0, float(den))
    s = max(0.0, float(smoothing))
    return float(clamp01(n / max(n + d + s, 1e-9)))


def compute_credibility(
    *,
    state: Any,
    mental: Mapping[str, Any],
    promise_type: str,
    cfg: AgencyConfig,
) -> Tuple[float, Dict[str, Any]]:
    """Compute credibility in [0, 1] for a promise type.

    Args:
        state: player agency state mapping/dataclass (needs trust + context.mem).
        mental: player mental traits mapping.
        promise_type: e.g. "MINUTES", "ROLE", "HELP", "LOAD", "EXTENSION_TALKS".
        cfg: AgencyConfig (will later be expanded with CredibilityConfig).

    Returns:
        (credibility, meta)
    """
    ccfg = _get_cred_cfg(cfg)

    trust = float(clamp01(_get_state_value(state, "trust", 0.5)))

    mem = _get_mem(state)

    # Totals (missing => 0)
    broken_total = int(safe_float(mem.get("broken_promises_total"), 0.0))
    fulfilled_total = int(safe_float(mem.get("fulfilled_promises_total"), 0.0))

    broken_by_type = mem.get("broken_promises_by_type") or {}
    fulfilled_by_type = mem.get("fulfilled_promises_by_type") or {}

    b_type = int(safe_float((broken_by_type or {}).get(str(promise_type)), 0.0))
    f_type = int(safe_float((fulfilled_by_type or {}).get(str(promise_type)), 0.0))

    # Recent outcomes list (optional):
    # [{"type":"MINUTES","result":"BROKEN"|"FULFILLED"}, ...]
    # Backward compatibility: also accept older key/shape.
    recent = mem.get("promise_outcomes_recent")
    if not isinstance(recent, list):
        recent = mem.get("recent_promise_outcomes")
    if not isinstance(recent, list):
        recent = []

    # Prefer cfg window but be robust.
    window = int(getattr(ccfg, "recent_window", 6) or 6)
    if window <= 0:
        window = 6
    recent_slice = recent[-window:]

    recent_broken = 0
    recent_fulfilled = 0
    # We count recent outcomes regardless of type for "manager trustworthiness".
    for it in recent_slice:
        if not isinstance(it, dict):
            continue
        r = str(it.get("result") or it.get("status") or "").upper()
        if r == "BROKEN":
            recent_broken += 1
        elif r == "FULFILLED":
            recent_fulfilled += 1

    denom_recent = max(1, len(recent_slice))
    recent_broken_rate = float(clamp01(recent_broken / float(denom_recent)))
    recent_fulfilled_rate = float(clamp01(recent_fulfilled / float(denom_recent)))

    # Normalize totals with smoothing to avoid early volatility.
    b_total_r = _ratio(broken_total, fulfilled_total, smoothing=4.0)
    f_total_r = _ratio(fulfilled_total, broken_total, smoothing=4.0)

    b_type_r = _ratio(b_type, f_type, smoothing=2.0)
    f_type_r = _ratio(f_type, b_type, smoothing=2.0)

    w_b_total = float(getattr(ccfg, "broken_total_w", 0.10))
    w_b_type = float(getattr(ccfg, "broken_type_w", 0.18))
    w_f_total = float(getattr(ccfg, "fulfilled_total_w", 0.05))
    w_f_type = float(getattr(ccfg, "fulfilled_type_w", 0.08))
    w_r_b = float(getattr(ccfg, "recent_broken_w", 0.10))
    w_r_f = float(getattr(ccfg, "recent_fulfilled_w", 0.06))

    penalty = (w_b_total * b_total_r) + (w_b_type * b_type_r) + (w_r_b * recent_broken_rate)
    bonus = (w_f_total * f_total_r) + (w_f_type * f_type_r) + (w_r_f * recent_fulfilled_rate)

    raw = trust + bonus - penalty

    # Mental modulator.
    loy = mental_norm(mental, "loyalty")
    coach = mental_norm(mental, "coachability")
    adapt = mental_norm(mental, "adaptability")
    ego = mental_norm(mental, "ego")
    amb = mental_norm(mental, "ambition")

    mod = 0.85 + (0.25 * loy) + (0.20 * coach) + (0.15 * adapt) - (0.25 * ego) - (0.10 * amb)
    mod = float(clamp(mod, 0.55, 1.15))

    cred = float(clamp01(raw * mod))

    meta: Dict[str, Any] = {
        "promise_type": str(promise_type),
        "trust": float(trust),
        "raw": float(raw),
        "bonus": float(bonus),
        "penalty": float(penalty),
        "weights": {
            "broken_total_w": float(w_b_total),
            "broken_type_w": float(w_b_type),
            "fulfilled_total_w": float(w_f_total),
            "fulfilled_type_w": float(w_f_type),
            "recent_broken_w": float(w_r_b),
            "recent_fulfilled_w": float(w_r_f),
        },
        "totals": {
            "broken_total": int(broken_total),
            "fulfilled_total": int(fulfilled_total),
            "broken_type": int(b_type),
            "fulfilled_type": int(f_type),
            "broken_total_ratio": float(b_total_r),
            "fulfilled_total_ratio": float(f_total_r),
            "broken_type_ratio": float(b_type_r),
            "fulfilled_type_ratio": float(f_type_r),
        },
        "recent": {
            "window": int(window),
            "n": int(len(recent_slice)),
            "broken": int(recent_broken),
            "fulfilled": int(recent_fulfilled),
            "broken_rate": float(recent_broken_rate),
            "fulfilled_rate": float(recent_fulfilled_rate),
        },
        "mental_mod": {
            "loyalty": float(loy),
            "coachability": float(coach),
            "adaptability": float(adapt),
            "ego": float(ego),
            "ambition": float(amb),
            "mod": float(mod),
        },
        "credibility": float(cred),
        "min_accept_cred": float(getattr(ccfg, "min_accept_cred", 0.20)),
    }

    return cred, meta
