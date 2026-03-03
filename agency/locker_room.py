from __future__ import annotations

"""Team-level locker room dynamics (v2).

This module is **pure logic**:
- No DB I/O
- Deterministic outcomes (stable_u01)

It is used by agency/service.py as an additional "team pass" after per-player
monthly ticks and promise evaluation.

Goals
-----
- Represent "room temperature" (how tense the locker room feels)
- Provide cheap, NBA-flavored social contagion
- Optionally emit a team meeting event when temperature is high

SSOT boundary
------------
- Inputs: player_agency_state snapshots (already in-memory)
- Outputs: small state deltas + optional event dict

The event dict is compatible with agency.repo.insert_agency_events.
"""

from dataclasses import dataclass
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple

from .config import AgencyConfig
from .utils import clamp, clamp01, make_event_id, stable_u01


# ---------------------------------------------------------------------------
# Influence model
# ---------------------------------------------------------------------------


_ROLE_INFLUENCE_MULT: Dict[str, float] = {
    "FRANCHISE": 1.60,
    "STAR": 1.35,
    "STARTER": 1.15,
    "ROTATION": 1.00,
    "BENCH": 0.85,
    "GARBAGE": 0.55,
    "UNKNOWN": 0.95,
}


def influence_for_state(state: Mapping[str, Any]) -> float:
    """Compute a cheap influence proxy in [~0.05, ~2.5]."""

    role = str(state.get("role_bucket") or "UNKNOWN").upper()
    lev = float(clamp01(state.get("leverage", 0.0)))

    mult = float(_ROLE_INFLUENCE_MULT.get(role, 1.0))

    # Give every player a baseline voice, then scale by leverage.
    inf = mult * (0.35 + 0.65 * lev)
    return float(clamp(inf, 0.05, 2.50))


def discontent_for_state(state: Mapping[str, Any]) -> float:
    """Compute a player's contribution to room tension in [0, 1].

    We bias toward team/chemistry issues but allow strong personal issues
    (role/contract/health/minutes) to spill into the room.
    """

    mfr = float(clamp01(state.get("minutes_frustration", 0.0)))
    tfr = float(clamp01(state.get("team_frustration", 0.0)))

    rfr = float(clamp01(state.get("role_frustration", 0.0)))
    cfr = float(clamp01(state.get("contract_frustration", 0.0)))
    hfr = float(clamp01(state.get("health_frustration", 0.0)))
    chfr = float(clamp01(state.get("chemistry_frustration", 0.0)))

    personal = max(rfr, mfr, cfr, hfr)

    return float(clamp01(max(tfr, chfr, 0.70 * personal)))


# ---------------------------------------------------------------------------
# Team temperature
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TeamTemperature:
    team_temperature: float
    leader_player_id: Optional[str]
    influence_by_player: Dict[str, float]
    discontent_by_player: Dict[str, float]


def compute_team_temperature(states_by_pid: Mapping[str, Mapping[str, Any]]) -> TeamTemperature:
    """Return weighted team temperature and supporting per-player maps."""

    inf_by: Dict[str, float] = {}
    dis_by: Dict[str, float] = {}

    num = 0.0
    den = 0.0

    for pid, st in (states_by_pid or {}).items():
        pid_s = str(pid)
        if not pid_s:
            continue
        inf = influence_for_state(st)
        dis = discontent_for_state(st)

        inf_by[pid_s] = float(inf)
        dis_by[pid_s] = float(dis)

        num += float(inf) * float(dis)
        den += float(inf)

    temp = float(num / den) if den > 1e-9 else 0.0
    temp = float(clamp01(temp))

    # Leader: highest influence (deterministic tie-breaker on player_id)
    leader_pid: Optional[str] = None
    if inf_by:
        leader_pid = sorted(inf_by.items(), key=lambda kv: (-float(kv[1]), str(kv[0])))[0][0]

    return TeamTemperature(
        team_temperature=temp,
        leader_player_id=leader_pid,
        influence_by_player=inf_by,
        discontent_by_player=dis_by,
    )


# ---------------------------------------------------------------------------
# Contagion + meeting event
# ---------------------------------------------------------------------------


def compute_contagion_deltas(
    *,
    team_temp: float,
    states_by_pid: Mapping[str, Mapping[str, Any]],
    cfg: AgencyConfig,
) -> Dict[str, Dict[str, float]]:
    """Return per-player deltas (NOT absolute values).

    We only push chemistry_frustration upward toward the team temperature.
    """

    ecfg = cfg.events

    threshold = float(getattr(ecfg, "locker_room_contagion_threshold", 0.50))
    strength = float(getattr(ecfg, "locker_room_contagion_strength", 0.06))

    if float(team_temp) <= float(threshold) or strength <= 0.0:
        return {}

    out: Dict[str, Dict[str, float]] = {}

    for pid, st in (states_by_pid or {}).items():
        pid_s = str(pid)
        if not pid_s:
            continue

        ch0 = float(clamp01(st.get("chemistry_frustration", 0.0)))
        if team_temp <= ch0:
            continue

        lev = float(clamp01(st.get("leverage", 0.0)))
        delta = float(strength) * float(team_temp - ch0) * (0.65 + 0.35 * lev)

        if delta <= 1e-9:
            continue

        out[pid_s] = {"chemistry_frustration": float(delta)}

    return out


def build_locker_room_meeting_event(
    *,
    team_id: str,
    season_year: int,
    month_key: str,
    now_date_iso: str,
    states_by_pid: Mapping[str, Mapping[str, Any]],
    cfg: AgencyConfig,
) -> Optional[Dict[str, Any]]:
    """Build a deterministic team meeting event dict, or None.

    Cooldown enforcement is handled in the service layer (DB query).
    """

    tid = str(team_id or "").upper()
    if not tid:
        return None

    ecfg = cfg.events

    min_players = int(getattr(ecfg, "locker_room_meeting_min_players", 8))
    if len(states_by_pid or {}) < max(1, min_players):
        return None

    tt = compute_team_temperature(states_by_pid)
    temp = float(tt.team_temperature)

    threshold = float(getattr(ecfg, "locker_room_meeting_threshold", 0.68))
    if temp < threshold:
        return None

    softness = max(1e-6, float(getattr(ecfg, "locker_room_meeting_softness", 0.14)))
    base_p = float(clamp01((temp - threshold) / softness))

    leader_pid = tt.leader_player_id
    if not leader_pid:
        return None

    # Favor meetings when a highly-influential player is part of the tension.
    leader_inf = float(tt.influence_by_player.get(leader_pid, 0.0))
    inf_norm = float(clamp01(leader_inf / 1.25))

    p = base_p * (0.80 + 0.20 * inf_norm)

    roll = stable_u01(tid, str(month_key), "locker_room_meeting", str(leader_pid))
    if roll >= p:
        return None

    et = str(cfg.event_types.get("locker_room_meeting", "LOCKER_ROOM_MEETING")).upper()

    # Severity: temperature + leader influence and discontent.
    leader_dis = float(tt.discontent_by_player.get(leader_pid, 0.0))
    severity = float(clamp01(0.75 * temp + 0.15 * inf_norm + 0.10 * leader_dis))

    # Explainability payload (keep it reasonably small)
    scored = []
    for pid, inf in tt.influence_by_player.items():
        dis = float(tt.discontent_by_player.get(pid, 0.0))
        scored.append((float(inf) * float(dis), pid, float(inf), float(dis)))
    scored.sort(reverse=True, key=lambda x: (x[0], x[1]))
    top = [
        {"player_id": pid, "influence": float(inf), "discontent": float(dis), "score": float(sc)}
        for (sc, pid, inf, dis) in scored[:5]
    ]

    affected = [str(pid) for pid in states_by_pid.keys() if str(pid)]

    payload = {
        "axis": "CHEMISTRY",
        "stage": "TEAM",
        "team_temperature": float(temp),
        "threshold": float(threshold),
        "leader_player_id": str(leader_pid),
        "leader_influence": float(leader_inf),
        "leader_discontent": float(leader_dis),
        "top_tension": top,
        "affected_player_ids": affected,
        "sample_players": int(len(affected)),
        "probability": float(p),
        "roll": float(roll),
        "month_key": str(month_key),
    }

    event_id = make_event_id("agency", tid, str(month_key), et, str(leader_pid))

    return {
        "event_id": event_id,
        "player_id": str(leader_pid),
        "team_id": tid,
        "season_year": int(season_year),
        "date": str(now_date_iso)[:10],
        "event_type": et,
        "severity": float(severity),
        "payload": payload,
    }
