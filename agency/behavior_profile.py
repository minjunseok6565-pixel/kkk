from __future__ import annotations

"""Behavior profile helpers for player agency.

This module turns *static* mental traits and *dynamic* stances into a small set
of high-level behavioral knobs.

Why does this exist?
--------------------
In a Football Manager–style agency system, the same situation should lead to
different behaviors for different players. The low-level mental traits
(M_WorkEthic, M_Coachability, M_Ambition, M_Loyalty, M_Ego, M_Adaptability)
should therefore map to a few "behavioral" dimensions that are:

- easy to tune
- cheap to compute
- deterministic
- DB-free

These knobs are later used by tick / negotiation layers to decide:
- how patient a player is before escalating
- whether they prefer private talks vs agent/media pressure
- how hard they bargain (narrow compromise band / walk away)

All functions here are pure.
"""

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Tuple

from .utils import clamp01, mental_norm


@dataclass(frozen=True, slots=True)
class BehaviorProfile:
    """High-level behavioral profile derived from mental + stance.

    All values are normalized to 0..1.

    - patience: higher => tolerates issues longer, slower escalation
    - publicness: higher => prefers agent/media escalation
    - hardball: higher => smaller compromise window, more likely to reject
    - professionalism: higher => keeps issues internal, recovers faster
    """

    patience: float
    publicness: float
    hardball: float
    professionalism: float


def compute_behavior_profile(
    *,
    mental: Mapping[str, Any],
    trust: Any = 0.5,
    stance_skepticism: Any = 0.0,
    stance_resentment: Any = 0.0,
    stance_hardball: Any = 0.0,
) -> Tuple[BehaviorProfile, Dict[str, Any]]:
    """Compute behavior profile and a breakdown meta dict.

    Args:
        mental: mapping of mental traits (0..100 or 0..1).
        trust: relationship trust (0..1).
        stance_*: dynamic stance values (0..1).

    Returns:
        (BehaviorProfile, meta)
    """
    # Base mental traits (0..1)
    work = mental_norm(mental, "work_ethic")
    coach = mental_norm(mental, "coachability")
    amb = mental_norm(mental, "ambition")
    loy = mental_norm(mental, "loyalty")
    ego = mental_norm(mental, "ego")
    adapt = mental_norm(mental, "adaptability")

    tr = float(clamp01(trust))
    sk = float(clamp01(stance_skepticism))
    rs = float(clamp01(stance_resentment))
    hb = float(clamp01(stance_hardball))

    # ------------------------------
    # Patience: willingness to keep things private longer.
    #
    # High coachability/loyalty/work ethic => patient.
    # High ego/ambition/resentment => impatient.
    # Trust increases patience.
    # ------------------------------
    patience_raw = (
        0.40 * coach
        + 0.25 * loy
        + 0.20 * adapt
        + 0.25 * work
        + 0.15 * tr
        - 0.35 * ego
        - 0.20 * amb
        - 0.30 * rs
        - 0.10 * hb
    )
    patience = float(clamp01(patience_raw))

    # ------------------------------
    # Publicness: preference to use agent/media pressure.
    #
    # High ego/ambition => public.
    # High loyalty/work ethic => internal.
    # Resentment increases publicness; trust decreases it.
    # ------------------------------
    public_raw = (
        0.45 * ego
        + 0.25 * amb
        + 0.25 * (1.0 - coach)
        + 0.25 * rs
        + 0.10 * hb
        - 0.20 * loy
        - 0.15 * work
        - 0.10 * tr
    )
    publicness = float(clamp01(public_raw))

    # ------------------------------
    # Hardball: willingness to narrow compromise band / walk away.
    #
    # Ego/ambition push hardball upward; coachability/adaptability downward.
    # Skepticism and prior hardball stance further push it.
    # ------------------------------
    hardball_raw = (
        0.40 * ego
        + 0.25 * amb
        + 0.20 * rs
        + 0.25 * sk
        + 0.35 * hb
        - 0.25 * coach
        - 0.20 * adapt
        - 0.10 * loy
    )
    hardball = float(clamp01(hardball_raw))

    # ------------------------------
    # Professionalism: tendency to keep it internal and bounce back.
    #
    # Work ethic + coachability + adaptability drive professionalism.
    # Ego/resentment reduce it.
    # ------------------------------
    prof_raw = (
        0.45 * work
        + 0.25 * coach
        + 0.20 * adapt
        + 0.10 * loy
        + 0.10 * tr
        - 0.25 * ego
        - 0.25 * rs
        - 0.10 * hb
    )
    professionalism = float(clamp01(prof_raw))

    prof = BehaviorProfile(
        patience=float(patience),
        publicness=float(publicness),
        hardball=float(hardball),
        professionalism=float(professionalism),
    )

    meta: Dict[str, Any] = {
        "mental": {
            "work_ethic": float(work),
            "coachability": float(coach),
            "ambition": float(amb),
            "loyalty": float(loy),
            "ego": float(ego),
            "adaptability": float(adapt),
        },
        "trust": float(tr),
        "stance": {"skepticism": float(sk), "resentment": float(rs), "hardball": float(hb)},
        "raw": {
            "patience": float(patience_raw),
            "publicness": float(public_raw),
            "hardball": float(hardball_raw),
            "professionalism": float(prof_raw),
        },
        "profile": {
            "patience": float(prof.patience),
            "publicness": float(prof.publicness),
            "hardball": float(prof.hardball),
            "professionalism": float(prof.professionalism),
        },
    }
    return prof, meta


def suggest_negotiation_max_rounds(
    *,
    base_rounds: int,
    mental: Mapping[str, Any],
    trust: Any = 0.5,
    stance_resentment: Any = 0.0,
) -> int:
    """Suggest a max number of negotiation rounds (1..3).

    This is intentionally *simple* and deterministic:
    - coachability/adaptability/loyalty/work_ethic => more rounds
    - ego/ambition/resentment => fewer rounds

    Call sites may clamp further or override per issue type.
    """
    work = mental_norm(mental, "work_ethic")
    coach = mental_norm(mental, "coachability")
    amb = mental_norm(mental, "ambition")
    loy = mental_norm(mental, "loyalty")
    ego = mental_norm(mental, "ego")
    adapt = mental_norm(mental, "adaptability")

    tr = float(clamp01(trust))
    rs = float(clamp01(stance_resentment))

    adj = 0
    if coach + adapt >= 1.35:
        adj += 1
    if loy >= 0.75:
        adj += 1
    if work >= 0.75:
        adj += 1
    if tr >= 0.70:
        adj += 1

    if ego >= 0.78:
        adj -= 1
    if amb >= 0.80:
        adj -= 1
    if rs >= 0.60:
        adj -= 1

    # clamp to 1..3 for UX; beyond that tends to feel spammy.
    out = int(base_rounds) + int(adj)
    if out < 1:
        out = 1
    if out > 3:
        out = 3
    return int(out)
