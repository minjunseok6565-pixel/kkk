from __future__ import annotations

"""Pure grievance logic for trade-offer exposure.

Scope
-----
This module is intentionally DB-free. It computes whether and how much to bump
existing agency axes when a trade offer is exposed to players:

- "You are trying to trade me" -> team_frustration
- "You are trying to recruit same-position player" -> role_frustration

Design constraints
------------------
- No new frustration axis.
- Deterministic outcomes via stable_u01.
- Use existing signals only (mental 6, role_bucket, leverage, ovr, pos,
  trade_request_level).
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .utils import clamp01, make_event_id, mental_norm, safe_float, safe_int, stable_u01


ROLE_TIER: Dict[str, int] = {
    "FRANCHISE": 0,
    "STAR": 1,
    "STARTER": 2,
    "ROTATION": 3,
    "BENCH": 4,
    "GARBAGE": 5,
    "UNKNOWN": 6,
}


@dataclass(frozen=True, slots=True)
class TradeOfferGrievanceConfig:
    # PUBLIC_OFFER: deterministic targeted grievance bump (lower than leak).
    public_targeted_delta_base: float = 0.055
    public_targeted_delta_mental_weight: float = 0.055
    public_targeted_delta_status_weight: float = 0.040
    public_targeted_delta_context_weight: float = 0.020
    public_targeted_delta_resilience_weight: float = 0.040
    public_targeted_delta_min: float = 0.025
    public_targeted_delta_max: float = 0.140

    # PRIVATE_OFFER_LEAKED: deterministic (no roll) targeted grievance bump.
    leaked_targeted_delta_base: float = 0.12
    leaked_targeted_delta_mental_weight: float = 0.10
    leaked_targeted_delta_status_weight: float = 0.08
    leaked_targeted_delta_context_weight: float = 0.05
    leaked_targeted_delta_resilience_weight: float = 0.06
    leaked_targeted_delta_min: float = 0.08
    leaked_targeted_delta_max: float = 0.30

    # trade_request_level policy for leak-targeted grievance.
    trade_request_level_max: int = 2
    leaked_targeted_active_request_dampen: float = 0.45

    same_pos_base_prob: float = 0.18
    same_pos_delta_base: float = 0.03
    same_pos_delta_scale: float = 0.08
    same_pos_min_leverage: float = 0.28
    same_pos_max_ovr_gap: int = 3
    same_pos_max_role_tier_gap: int = 2

    event_type_targeted_public: str = "TRADE_TARGETED_OFFER_PUBLIC"
    event_type_targeted_leaked: str = "TRADE_TARGETED_OFFER_LEAKED"
    event_type_same_pos_recruit: str = "SAME_POS_RECRUIT_ATTEMPT"


@dataclass(frozen=True, slots=True)
class PlayerSnapshot:
    player_id: str
    team_id: str
    pos: str
    ovr: int
    mental: Mapping[str, Any] = field(default_factory=dict)

    role_bucket: str = "UNKNOWN"
    leverage: float = 0.0

    trade_request_level: int = 0
    team_frustration: float = 0.0
    role_frustration: float = 0.0


@dataclass(frozen=True, slots=True)
class ProposedStateUpdate:
    player_id: str
    team_frustration: float
    role_frustration: float
    team_frustration_delta: float
    role_frustration_delta: float


@dataclass(frozen=True, slots=True)
class ProposedAgencyEvent:
    event_id: str
    player_id: str
    team_id: str
    season_year: int
    date: str
    event_type: str
    severity: float
    payload: Dict[str, Any]


@dataclass(frozen=True, slots=True)
class TradeOfferGrievanceResult:
    updates: List[ProposedStateUpdate]
    events: List[ProposedAgencyEvent]
    skipped: List[Dict[str, Any]]
    meta: Dict[str, Any]


def _uniq_str(values: Sequence[str]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for v in values:
        s = str(v or "")
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _mental_reactivity(mental: Mapping[str, Any]) -> float:
    work = mental_norm(mental, "work_ethic")
    coach = mental_norm(mental, "coachability")
    amb = mental_norm(mental, "ambition")
    loy = mental_norm(mental, "loyalty")
    ego = mental_norm(mental, "ego")
    adapt = mental_norm(mental, "adaptability")

    raw = (
        0.35 * ego
        + 0.25 * amb
        + 0.15 * (1.0 - loy)
        + 0.10 * (1.0 - coach)
        + 0.10 * (1.0 - adapt)
        + 0.05 * (1.0 - work)
    )
    return float(clamp01(raw))


def _status_weight(role_bucket: str, leverage: float) -> float:
    rb = str(role_bucket or "UNKNOWN").upper()
    role_bonus = {
        "FRANCHISE": 0.25,
        "STAR": 0.18,
        "STARTER": 0.12,
        "ROTATION": 0.06,
    }.get(rb, 0.0)
    raw = 0.55 * float(clamp01(leverage)) + role_bonus
    return float(clamp01(raw))


def _norm_pos(pos: Any) -> str:
    return str(pos or "").strip().upper()


def _event_type_for_targeted(trigger_source: str, cfg: TradeOfferGrievanceConfig) -> str:
    if str(trigger_source).upper() == "PRIVATE_OFFER_LEAKED":
        return str(cfg.event_type_targeted_leaked or "TRADE_TARGETED_OFFER_LEAKED").upper()
    return str(cfg.event_type_targeted_public or "TRADE_TARGETED_OFFER_PUBLIC").upper()


def _resilience(mental: Mapping[str, Any]) -> float:
    loy = mental_norm(mental, "loyalty")
    coach = mental_norm(mental, "coachability")
    adapt = mental_norm(mental, "adaptability")
    work = mental_norm(mental, "work_ethic")
    raw = 0.30 * loy + 0.25 * coach + 0.25 * adapt + 0.20 * work
    return float(clamp01(raw))


def _targeted_delta(*, source: str, cfg: TradeOfferGrievanceConfig, react: float, status_w: float, resilience: float, team_frustration: float) -> float:
    tfr = float(clamp01(safe_float(team_frustration, 0.0)))
    if str(source).upper() == "PRIVATE_OFFER_LEAKED":
        raw = (
            float(cfg.leaked_targeted_delta_base)
            + float(cfg.leaked_targeted_delta_mental_weight) * float(react)
            + float(cfg.leaked_targeted_delta_status_weight) * float(status_w)
            + float(cfg.leaked_targeted_delta_context_weight) * tfr
            - float(cfg.leaked_targeted_delta_resilience_weight) * float(resilience)
        )
        return float(max(float(cfg.leaked_targeted_delta_min), min(float(cfg.leaked_targeted_delta_max), float(raw))))

    raw = (
        float(cfg.public_targeted_delta_base)
        + float(cfg.public_targeted_delta_mental_weight) * float(react)
        + float(cfg.public_targeted_delta_status_weight) * float(status_w)
        + float(cfg.public_targeted_delta_context_weight) * tfr
        - float(cfg.public_targeted_delta_resilience_weight) * float(resilience)
    )
    return float(max(float(cfg.public_targeted_delta_min), min(float(cfg.public_targeted_delta_max), float(raw))))


def compute_trade_offer_grievances(
    *,
    proposer_team_id: str,
    outgoing_player_ids: Sequence[str],
    incoming_player_ids: Sequence[str],
    players_by_id: Mapping[str, PlayerSnapshot],
    season_year: int,
    now_date_iso: str,
    trigger_source: str,
    session_id: Optional[str] = None,
    cfg: TradeOfferGrievanceConfig = TradeOfferGrievanceConfig(),
) -> TradeOfferGrievanceResult:
    """Compute grievance impacts caused by one offer exposure.

    trigger_source:
      - PUBLIC_OFFER
      - PRIVATE_OFFER_LEAKED
    """

    team_id = str(proposer_team_id or "").upper()
    source = str(trigger_source or "").upper()
    if source not in {"PUBLIC_OFFER", "PRIVATE_OFFER_LEAKED"}:
        source = "PUBLIC_OFFER"

    outgoing = _uniq_str([str(x) for x in (outgoing_player_ids or [])])
    incoming = _uniq_str([str(x) for x in (incoming_player_ids or [])])

    updates: Dict[str, ProposedStateUpdate] = {}
    events: List[ProposedAgencyEvent] = []
    skipped: List[Dict[str, Any]] = []

    # --------------------------------------------------------------
    # A) "You are trying to trade me" -> team_frustration
    # --------------------------------------------------------------
    for pid in outgoing:
        p = players_by_id.get(pid)
        if p is None:
            skipped.append({"player_id": pid, "reason": "PLAYER_NOT_FOUND"})
            continue
        if str(p.team_id).upper() != team_id:
            skipped.append({"player_id": pid, "reason": "NOT_ON_PROPOSER_TEAM"})
            continue
        tr_level = int(safe_int(p.trade_request_level, 0))
        if source != "PRIVATE_OFFER_LEAKED" and tr_level > 0:
            skipped.append({"player_id": pid, "reason": "TRADE_REQUEST_ALREADY_ACTIVE"})
            continue
        if source == "PRIVATE_OFFER_LEAKED" and tr_level >= int(max(0, safe_int(cfg.trade_request_level_max, 2))):
            skipped.append(
                {
                    "player_id": pid,
                    "reason": "TRADE_REQUEST_AT_MAX",
                    "trade_request_level": int(tr_level),
                }
            )
            continue

        react = _mental_reactivity(p.mental)
        status_w = _status_weight(p.role_bucket, p.leverage)
        resil = _resilience(p.mental)
        p_fire = 1.0
        roll = 0.0
        if source == "PRIVATE_OFFER_LEAKED":
            delta = _targeted_delta(
                source=source,
                cfg=cfg,
                react=react,
                status_w=status_w,
                resilience=resil,
                team_frustration=float(p.team_frustration),
            )
            if tr_level > 0:
                delta *= float(clamp01(safe_float(cfg.leaked_targeted_active_request_dampen, 0.45)))
                delta = float(max(0.0, delta))
        else:
            # PUBLIC is also deterministic; unlike leak, active trade-request players are skipped above.
            delta = _targeted_delta(
                source=source,
                cfg=cfg,
                react=react,
                status_w=status_w,
                resilience=resil,
                team_frustration=float(p.team_frustration),
            )
        tfr0 = float(clamp01(safe_float(p.team_frustration, 0.0)))
        rfr0 = float(clamp01(safe_float(p.role_frustration, 0.0)))
        tfr1 = float(clamp01(tfr0 + delta))

        updates[pid] = ProposedStateUpdate(
            player_id=pid,
            team_frustration=float(tfr1),
            role_frustration=float(rfr0),
            team_frustration_delta=float(tfr1 - tfr0),
            role_frustration_delta=0.0,
        )

        ev_type = _event_type_for_targeted(source, cfg)
        ev_id = make_event_id("agency_trade_offer_grievance", season_year, now_date_iso[:10], session_id or "", ev_type, pid)
        events.append(
            ProposedAgencyEvent(
                event_id=ev_id,
                player_id=pid,
                team_id=team_id,
                season_year=int(season_year),
                date=str(now_date_iso)[:10],
                event_type=ev_type,
                severity=float(clamp01(0.35 + 0.65 * delta)),
                payload={
                    "trigger_source": source,
                    "session_id": session_id,
                    "team_frustration_delta": float(tfr1 - tfr0),
                    "reactivity": float(react),
                    "status_weight": float(status_w),
                    "p_fire": float(p_fire),
                    "roll": float(roll),
                    "trade_request_level": int(tr_level),
                },
            )
        )

    # --------------------------------------------------------------
    # B) "You recruit same-position player" -> role_frustration
    # --------------------------------------------------------------
    incoming_players = [players_by_id.get(pid) for pid in incoming if players_by_id.get(pid) is not None]
    incumbents = [
        p
        for p in players_by_id.values()
        if str(p.team_id).upper() == team_id and p.player_id not in set(outgoing)
    ]

    for inc in incumbents:
        if float(clamp01(inc.leverage)) < float(cfg.same_pos_min_leverage):
            continue

        inc_tier = ROLE_TIER.get(str(inc.role_bucket or "UNKNOWN").upper(), ROLE_TIER["UNKNOWN"])
        inc_pos = _norm_pos(inc.pos)
        if not inc_pos:
            continue

        best_match: Optional[PlayerSnapshot] = None
        best_gap = 10**9
        for target in incoming_players:
            tgt_pos = _norm_pos(target.pos)
            if not tgt_pos or tgt_pos != inc_pos:
                continue
            gap = abs(int(safe_int(target.ovr, 0)) - int(safe_int(inc.ovr, 0)))
            if gap > int(cfg.same_pos_max_ovr_gap):
                continue
            tgt_tier = ROLE_TIER.get(str(target.role_bucket or "UNKNOWN").upper(), ROLE_TIER["UNKNOWN"])
            if abs(inc_tier - tgt_tier) > int(cfg.same_pos_max_role_tier_gap):
                continue
            if gap < best_gap:
                best_match = target
                best_gap = gap

        if best_match is None:
            continue

        react = _mental_reactivity(inc.mental)
        status_w = _status_weight(inc.role_bucket, inc.leverage)
        closeness = float(clamp01(1.0 - (float(best_gap) / max(1.0, float(cfg.same_pos_max_ovr_gap)))))

        p_fire = float(clamp01(cfg.same_pos_base_prob + 0.22 * react + 0.20 * status_w + 0.20 * closeness))
        roll = stable_u01(
            "trade_offer_grievance",
            source,
            session_id or "",
            now_date_iso,
            inc.player_id,
            "same_pos",
            best_match.player_id,
        )
        if roll > p_fire:
            continue

        delta = float(clamp01(cfg.same_pos_delta_base + cfg.same_pos_delta_scale * (0.45 * react + 0.35 * status_w + 0.20 * closeness)))
        tfr0 = float(clamp01(safe_float(inc.team_frustration, 0.0)))
        rfr0 = float(clamp01(safe_float(inc.role_frustration, 0.0)))
        rfr1 = float(clamp01(rfr0 + delta))

        prev = updates.get(inc.player_id)
        if prev is None:
            updates[inc.player_id] = ProposedStateUpdate(
                player_id=inc.player_id,
                team_frustration=float(tfr0),
                role_frustration=float(rfr1),
                team_frustration_delta=0.0,
                role_frustration_delta=float(rfr1 - rfr0),
            )
        else:
            updates[inc.player_id] = ProposedStateUpdate(
                player_id=inc.player_id,
                team_frustration=float(prev.team_frustration),
                role_frustration=float(rfr1),
                team_frustration_delta=float(prev.team_frustration_delta),
                role_frustration_delta=float(rfr1 - rfr0),
            )

        ev_type = str(cfg.event_type_same_pos_recruit or "SAME_POS_RECRUIT_ATTEMPT").upper()
        ev_id = make_event_id(
            "agency_trade_offer_grievance",
            season_year,
            now_date_iso[:10],
            session_id or "",
            ev_type,
            inc.player_id,
            best_match.player_id,
        )
        events.append(
            ProposedAgencyEvent(
                event_id=ev_id,
                player_id=inc.player_id,
                team_id=team_id,
                season_year=int(season_year),
                date=str(now_date_iso)[:10],
                event_type=ev_type,
                severity=float(clamp01(0.30 + 0.70 * delta)),
                payload={
                    "trigger_source": source,
                    "session_id": session_id,
                    "incoming_player_id": best_match.player_id,
                    "incoming_pos": _norm_pos(best_match.pos),
                    "incumbent_pos": inc_pos,
                    "incoming_ovr": int(safe_int(best_match.ovr, 0)),
                    "incumbent_ovr": int(safe_int(inc.ovr, 0)),
                    "ovr_gap_abs": int(best_gap),
                    "role_frustration_delta": float(rfr1 - rfr0),
                    "reactivity": float(react),
                    "status_weight": float(status_w),
                    "closeness": float(closeness),
                    "p_fire": float(p_fire),
                    "roll": float(roll),
                },
            )
        )

    return TradeOfferGrievanceResult(
        updates=list(updates.values()),
        events=events,
        skipped=skipped,
        meta={
            "trigger_source": source,
            "proposer_team_id": team_id,
            "outgoing_count": int(len(outgoing)),
            "incoming_count": int(len(incoming)),
            "updates_count": int(len(updates)),
            "events_count": int(len(events)),
        },
    )
