from __future__ import annotations

import random
from typing import Dict, Optional, Tuple

from ..core import weighted_choice
from ..models import Player, TeamState

from .participants_roles import (
    ROLE_ENGINE_PRIMARY,
    ROLE_ENGINE_SECONDARY,
    ROLE_TRANSITION_ENGINE,
    ROLE_SHOT_CREATOR,
    ROLE_RIM_PRESSURE,
    ROLE_MOVEMENT_SHOOTER,
    ROLE_CUTTER_FINISHER,
    ROLE_CONNECTOR,
    ROLE_ROLL_MAN,
    ROLE_SHORTROLL_HUB,
    ROLE_POP_THREAT,
    ROLE_POST_ANCHOR,
)

from .participants_common import (
    _active,
    _clamp,
    _fill_candidates_with_top_k,
    _players_from_roles,
    _role_player,
    _shot_diet_info,
    choose_weighted_player,
)

# ---- Shooter selection (catch & shoot) ----


def choose_shooter_for_three(rng: random.Random, offense: TeamState, style: Optional[object] = None) -> Player:
    # Allow any on-court player to be the shooter; keep weighting by SHOT_3_CS.
    cand = _active(offense)
    info = _shot_diet_info(style)
    apply_bias = style is not None
    weights: Dict[str, float] = {}
    for p in cand:
        mult = 1.0
        if apply_bias:
            mult = 0.85 if p.pid in (info.get("primary_pid"), info.get("secondary_pid")) else 1.10
        weights[p.pid] = (max(p.get("SHOT_3_CS"), 1.0) ** 1.35) * mult
    pid = weighted_choice(rng, weights)
    for p in cand:
        if p.pid == pid:
            return p
    return cand[0]


def choose_shooter_for_mid(rng: random.Random, offense: TeamState, style: Optional[object] = None) -> Player:
    # Allow any on-court player to be the shooter; keep weighting by SHOT_MID_CS.
    cand = _active(offense)
    info = _shot_diet_info(style)
    apply_bias = style is not None
    weights: Dict[str, float] = {}
    for p in cand:
        mult = 1.0
        if apply_bias:
            mult = 0.85 if p.pid in (info.get("primary_pid"), info.get("secondary_pid")) else 1.10
        weights[p.pid] = (max(p.get("SHOT_MID_CS"), 1.0) ** 1.25) * mult
    pid = weighted_choice(rng, weights)
    for p in cand:
        if p.pid == pid:
            return p
    return cand[0]


# ---- Creator selection (pull-up / off-dribble) ----


_CREATOR_ROLE_PRIORITY: Tuple[str, ...] = (
    ROLE_SHOT_CREATOR,
    ROLE_ENGINE_PRIMARY,
    ROLE_ENGINE_SECONDARY,
    ROLE_RIM_PRESSURE,
    ROLE_TRANSITION_ENGINE,
    ROLE_CONNECTOR,
)


def choose_creator_for_pulloff(
    rng: random.Random,
    offense: TeamState,
    outcome: str,
    style: Optional[object] = None,
) -> Player:
    # 13-role candidates first, then fill so that ALL on-court players can be selected.
    key = "SHOT_3_OD" if outcome == "SHOT_3_OD" else "SHOT_MID_PU"

    # Role slots are canonical C13 keys.
    cand = _players_from_roles(offense, _CREATOR_ROLE_PRIORITY)

    # Previously capped to 3, which hard-limited distribution.
    # Use the on-court count (normally 5) so every player becomes a candidate.
    on_court_cap = len(_active(offense))
    cand = _fill_candidates_with_top_k(offense, cand, cap=on_court_cap, stat_key=key)

    info = _shot_diet_info(style)
    extra: Dict[str, float] = {}
    primary_pid = info.get("primary_pid")
    secondary_pid = info.get("secondary_pid")
    for p in cand:
        if p.pid == primary_pid:
            extra[p.pid] = float(info.get("w_primary", 1.0))
        elif p.pid == secondary_pid:
            extra[p.pid] = float(info.get("w_secondary", 1.0))

    return choose_weighted_player(rng, cand, key, power=1.20, extra_mult_by_pid=extra)


# ---- Rim finisher selection ----


_FINISH_ROLE_BASE: Tuple[str, ...] = (
    ROLE_RIM_PRESSURE,
    ROLE_CUTTER_FINISHER,
    ROLE_ROLL_MAN,
    ROLE_MOVEMENT_SHOOTER,
    ROLE_SHOT_CREATOR,
    ROLE_ENGINE_PRIMARY,
    ROLE_ENGINE_SECONDARY,
)

_FINISH_ROLE_PNR: Tuple[str, ...] = (
    ROLE_ROLL_MAN,
    ROLE_SHORTROLL_HUB,
    ROLE_POP_THREAT,
    ROLE_RIM_PRESSURE,
    ROLE_CUTTER_FINISHER,
    ROLE_SHOT_CREATOR,
    ROLE_ENGINE_PRIMARY,
    ROLE_ENGINE_SECONDARY,
)

_FINISH_ROLE_CUT: Tuple[str, ...] = (
    ROLE_CUTTER_FINISHER,
    ROLE_RIM_PRESSURE,
    ROLE_ROLL_MAN,
    ROLE_MOVEMENT_SHOOTER,
    ROLE_SHOT_CREATOR,
    ROLE_ENGINE_PRIMARY,
    ROLE_ENGINE_SECONDARY,
)


# Conservative dunk role multipliers (optional realism tuning).
# These are only applied when dunk_bias=True, on top of the FIN_* stat.
_DUNK_ROLE_MULT = {
    ROLE_RIM_PRESSURE: 1.10,
    ROLE_CUTTER_FINISHER: 1.06,
    ROLE_ROLL_MAN: 1.15,
    ROLE_SHORTROLL_HUB: 1.00,
    ROLE_MOVEMENT_SHOOTER: 1.00,
    ROLE_POP_THREAT: 0.80,
}

_MULT_MIN = 0.70
_MULT_MAX = 1.40


def _pid_role_mult_canon(team: TeamState, pid: str, role_mult: Dict[str, float]) -> float:
    """Return the max role multiplier for a pid (canonical C13 role keys)."""
    mult = 1.0
    roles = getattr(team, "roles", {}) or {}
    for role, rpid in roles.items():
        if str(rpid) != str(pid):
            continue
        rk = str(role or "").strip()
        mult = max(mult, float(role_mult.get(rk, 1.0)))
    return mult


def choose_finisher_rim(
    rng: random.Random,
    offense: TeamState,
    dunk_bias: bool = False,
    style: Optional[object] = None,
    base_action: Optional[str] = None,
) -> Player:
    # Choose who finishes at the rim.
    # Candidates are role-driven (C13), then filled with best rim-finishers from the
    # lineup to ensure robustness.
    key = "FIN_DUNK" if dunk_bias else "FIN_RIM"

    if base_action == "PnR":
        role_priority = _FINISH_ROLE_PNR
    elif base_action == "Cut":
        role_priority = _FINISH_ROLE_CUT
    else:
        role_priority = _FINISH_ROLE_BASE

    cand = _players_from_roles(offense, role_priority)
    cand = _fill_candidates_with_top_k(offense, cand, cap=4, stat_key=key)

    info = _shot_diet_info(style)
    extra: Dict[str, float] = {}
    for p in cand:
        mult = 1.0

        # PnR: prioritize style-selected screeners.
        if base_action == "PnR":
            if p.pid == info.get("screener1_pid"):
                mult *= 1.25
            elif p.pid == info.get("screener2_pid"):
                mult *= 1.10

        # Optional dunk realism: discourage pop-big dunk dominance.
        if dunk_bias:
            mult *= _pid_role_mult_canon(offense, p.pid, _DUNK_ROLE_MULT)

        extra[p.pid] = _clamp(mult, _MULT_MIN, _MULT_MAX)

    return choose_weighted_player(rng, cand, key, power=1.15, extra_mult_by_pid=extra)


# ---- Post target selection ----


_POST_FALLBACK_ROLES: Tuple[str, ...] = (
    ROLE_SHORTROLL_HUB,
    ROLE_POP_THREAT,
    ROLE_ROLL_MAN,
)


def choose_post_target(offense: TeamState) -> Player:
    # Prefer the Post_Anchor. If missing, fall back to the most post-capable big-ish option.
    p = _role_player(offense, ROLE_POST_ANCHOR)
    if p:
        return p

    cand = _players_from_roles(offense, _POST_FALLBACK_ROLES)
    if cand:
        # Deterministic: choose the best by POST_CONTROL (then POST_SCORE).
        return max(cand, key=lambda x: (x.get("POST_CONTROL"), x.get("POST_SCORE")))

    # Final fallback: pick best post controller from lineup.
    return max(_active(offense), key=lambda x: (x.get("POST_CONTROL"), x.get("POST_SCORE"), x.get("REB")))
