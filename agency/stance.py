from __future__ import annotations

"""Dynamic stances (skepticism / resentment / hardball) for player agency.

These are *not* base mental traits.
They are short-to-mid-term attitudes shaped by interactions:
- broken promises
- fulfilled promises
- insulting negotiations

Why stances?
------------
In an FM-like system, repeated "broken promises" should change the player's
future behavior: more skeptical, less willing to compromise, quicker escalation.

We intentionally keep stances simple (3 floats 0..1) so they can:
- be stored in player_agency_state SSOT
- be tuned with a few config knobs
- affect multiple subsystems (event selection, negotiation, escalation)

This module is DB-free and deterministic.
"""

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Tuple

from .config import AgencyConfig
from .utils import clamp01, mental_norm, safe_float


@dataclass(frozen=True, slots=True)
class _StanceDefaults:
    # On broken promises
    skepticism_gain_broken: float = 0.10
    resentment_gain_broken: float = 0.10
    hardball_gain_broken: float = 0.08

    # On fulfilled promises
    skepticism_relief_fulfilled: float = 0.06
    resentment_relief_fulfilled: float = 0.05
    hardball_relief_fulfilled: float = 0.04

    # Monthly decay (recovery)
    skepticism_decay: float = 0.03
    resentment_decay: float = 0.02
    hardball_decay: float = 0.02

    # Recovery modifiers
    decay_trust_bonus: float = 0.04
    decay_work_ethic_bonus: float = 0.03
    decay_coachability_bonus: float = 0.02


def _get_stance_cfg(cfg: AgencyConfig) -> Any:
    # Planned name: cfg.stance
    if hasattr(cfg, "stance"):
        return getattr(cfg, "stance")
    return _StanceDefaults()


def _get_state_value(state: Any, key: str, default: Any = None) -> Any:
    if isinstance(state, Mapping):
        return state.get(key, default)
    return getattr(state, key, default)


def apply_monthly_stance_decay(
    *,
    state: Any,
    mental: Mapping[str, Any],
    cfg: AgencyConfig,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Apply monthly decay (recovery) to stance values.

    Args:
        state: agency state mapping/dataclass (needs trust + stance_*).
        mental: player mental traits.
        cfg: AgencyConfig

    Returns:
        (updates, meta)
    """
    scfg = _get_stance_cfg(cfg)

    trust = float(clamp01(_get_state_value(state, "trust", 0.5)))

    work = mental_norm(mental, "work_ethic")
    coach = mental_norm(mental, "coachability")

    mult = 1.0
    mult += float(getattr(scfg, "decay_trust_bonus", 0.04)) * trust
    mult += float(getattr(scfg, "decay_work_ethic_bonus", 0.03)) * work
    mult += float(getattr(scfg, "decay_coachability_bonus", 0.02)) * coach

    old_sk = float(clamp01(_get_state_value(state, "stance_skepticism", 0.0)))
    old_rs = float(clamp01(_get_state_value(state, "stance_resentment", 0.0)))
    old_hb = float(clamp01(_get_state_value(state, "stance_hardball", 0.0)))

    d_sk = float(getattr(scfg, "skepticism_decay", 0.03))
    d_rs = float(getattr(scfg, "resentment_decay", 0.02))
    d_hb = float(getattr(scfg, "hardball_decay", 0.02))

    new_sk = float(clamp01(old_sk * (1.0 - (d_sk * mult))))
    new_rs = float(clamp01(old_rs * (1.0 - (d_rs * mult))))
    new_hb = float(clamp01(old_hb * (1.0 - (d_hb * mult))))

    updates = {
        "stance_skepticism": float(new_sk),
        "stance_resentment": float(new_rs),
        "stance_hardball": float(new_hb),
    }

    meta: Dict[str, Any] = {
        "trust": float(trust),
        "mental": {"work_ethic": float(work), "coachability": float(coach)},
        "mult": float(mult),
        "decays": {"skepticism": float(d_sk), "resentment": float(d_rs), "hardball": float(d_hb)},
        "old": {"skepticism": float(old_sk), "resentment": float(old_rs), "hardball": float(old_hb)},
        "new": {"skepticism": float(new_sk), "resentment": float(new_rs), "hardball": float(new_hb)},
    }
    return updates, meta


def stance_deltas_on_promise_outcome(
    *,
    status: str,
    base_scale: Any,
    mental: Mapping[str, Any],
    cfg: AgencyConfig,
) -> Tuple[Dict[str, float], Dict[str, Any]]:
    """Compute stance deltas from a promise outcome.

    Args:
        status: "BROKEN" or "FULFILLED" (case-insensitive). Others => no-op.
        base_scale: scalar multiplier (e.g. leverage or promise impact scale).
        mental: player mental traits.
        cfg: AgencyConfig

    Returns:
        (deltas dict, meta)
    """
    scfg = _get_stance_cfg(cfg)
    s = str(status or "").upper()

    scale = float(max(0.0, safe_float(base_scale, 1.0)))

    work = mental_norm(mental, "work_ethic")
    coach = mental_norm(mental, "coachability")
    amb = mental_norm(mental, "ambition")
    loy = mental_norm(mental, "loyalty")
    ego = mental_norm(mental, "ego")
    adapt = mental_norm(mental, "adaptability")

    deltas = {"stance_skepticism": 0.0, "stance_resentment": 0.0, "stance_hardball": 0.0}

    if s == "BROKEN":
        # How sharply the player reacts to broken promises.
        intensity = 0.90 + (0.55 * ego) + (0.30 * amb) - (0.30 * loy) - (0.20 * coach) - (0.20 * work) + (0.10 * (1.0 - adapt))
        intensity = float(clamp01(intensity / 1.40))  # normalize

        deltas["stance_skepticism"] = float(getattr(scfg, "skepticism_gain_broken", 0.10)) * scale * (0.70 + 0.60 * intensity)
        deltas["stance_resentment"] = float(getattr(scfg, "resentment_gain_broken", 0.10)) * scale * (0.70 + 0.60 * intensity)
        deltas["stance_hardball"] = float(getattr(scfg, "hardball_gain_broken", 0.08)) * scale * (0.65 + 0.70 * intensity)

        meta = {
            "status": "BROKEN",
            "scale": float(scale),
            "intensity": float(intensity),
            "mental": {
                "work_ethic": float(work),
                "coachability": float(coach),
                "ambition": float(amb),
                "loyalty": float(loy),
                "ego": float(ego),
                "adaptability": float(adapt),
            },
        }
        return deltas, meta

    if s == "FULFILLED":
        # How strongly fulfilled promises repair skepticism/resentment.
        repair = 0.85 + (0.35 * loy) + (0.30 * coach) + (0.25 * work) + (0.20 * adapt) - (0.35 * ego) - (0.15 * amb)
        repair = float(clamp01(repair / 1.45))

        deltas["stance_skepticism"] = -float(getattr(scfg, "skepticism_relief_fulfilled", 0.06)) * scale * (0.65 + 0.80 * repair)
        deltas["stance_resentment"] = -float(getattr(scfg, "resentment_relief_fulfilled", 0.05)) * scale * (0.65 + 0.80 * repair)
        deltas["stance_hardball"] = -float(getattr(scfg, "hardball_relief_fulfilled", 0.04)) * scale * (0.60 + 0.90 * repair)

        meta = {
            "status": "FULFILLED",
            "scale": float(scale),
            "repair": float(repair),
            "mental": {
                "work_ethic": float(work),
                "coachability": float(coach),
                "ambition": float(amb),
                "loyalty": float(loy),
                "ego": float(ego),
                "adaptability": float(adapt),
            },
        }
        return deltas, meta

    return deltas, {"status": str(status), "scale": float(scale), "note": "no_op"}


def stance_deltas_on_offer_decision(
    *,
    verdict: str,
    mental: Mapping[str, Any],
    cfg: AgencyConfig,
    insulting: bool = False,
) -> Tuple[Dict[str, float], Dict[str, Any]]:
    """Compute stance deltas from an offer decision.

    This is used when a negotiation itself feels insulting (too low) and the
    player becomes more resentful/hardball.

    Args:
        verdict: "COUNTER" / "REJECT" / "WALKOUT" / "ACCEPT"
        insulting: whether the offer was considered insulting.

    Returns:
        (deltas dict, meta)
    """
    v = str(verdict or "").upper()

    work = mental_norm(mental, "work_ethic")
    coach = mental_norm(mental, "coachability")
    amb = mental_norm(mental, "ambition")
    loy = mental_norm(mental, "loyalty")
    ego = mental_norm(mental, "ego")
    adapt = mental_norm(mental, "adaptability")

    # Base intensity for "conflict" reactions.
    intensity = 0.80 + (0.55 * ego) + (0.20 * amb) - (0.20 * loy) - (0.15 * coach) - (0.15 * work) + (0.10 * (1.0 - adapt))
    intensity = float(clamp01(intensity / 1.35))

    deltas = {"stance_skepticism": 0.0, "stance_resentment": 0.0, "stance_hardball": 0.0}

    if v in ("ACCEPT", ""):
        return deltas, {"verdict": v, "note": "no_op"}

    if v == "COUNTER":
        # Counter is not inherently negative, but if the offer was insulting,
        # it still increases skepticism slightly.
        if insulting:
            deltas["stance_skepticism"] = 0.02 * (0.70 + 0.60 * intensity)
        return deltas, {"verdict": v, "insulting": bool(insulting), "intensity": float(intensity)}

    if v == "REJECT":
        base = 0.04
        if insulting:
            base *= 1.35
        deltas["stance_skepticism"] = base * (0.70 + 0.60 * intensity)
        deltas["stance_hardball"] = (base + 0.01) * (0.65 + 0.70 * intensity)
        deltas["stance_resentment"] = (base - 0.01) * (0.70 + 0.60 * intensity)
        return deltas, {"verdict": v, "insulting": bool(insulting), "intensity": float(intensity)}

    if v == "WALKOUT":
        base = 0.07
        if insulting:
            base *= 1.40
        deltas["stance_skepticism"] = base * (0.70 + 0.60 * intensity)
        deltas["stance_hardball"] = (base + 0.02) * (0.65 + 0.70 * intensity)
        deltas["stance_resentment"] = (base + 0.02) * (0.75 + 0.55 * intensity)
        return deltas, {"verdict": v, "insulting": bool(insulting), "intensity": float(intensity)}

    return deltas, {"verdict": v, "note": "unrecognized"}


def apply_stance_deltas(
    *,
    state: Any,
    deltas: Mapping[str, Any],
) -> Dict[str, Any]:
    """Apply stance deltas to a state mapping/dataclass (returns updates).

    This helper does not mutate the input state; call sites should merge the
    returned dict into their state.
    """
    old_sk = float(clamp01(_get_state_value(state, "stance_skepticism", 0.0)))
    old_rs = float(clamp01(_get_state_value(state, "stance_resentment", 0.0)))
    old_hb = float(clamp01(_get_state_value(state, "stance_hardball", 0.0)))

    d_sk = float(safe_float(deltas.get("stance_skepticism"), 0.0))
    d_rs = float(safe_float(deltas.get("stance_resentment"), 0.0))
    d_hb = float(safe_float(deltas.get("stance_hardball"), 0.0))

    return {
        "stance_skepticism": float(clamp01(old_sk + d_sk)),
        "stance_resentment": float(clamp01(old_rs + d_rs)),
        "stance_hardball": float(clamp01(old_hb + d_hb)),
    }
