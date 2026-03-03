from __future__ import annotations

"""Orchestration layer for contract negotiations (DB I/O + state sessions).

This module is intended to be called by server endpoints.

Key responsibilities:
- Load player + roster context from SQLite (LeagueRepo)
- Derive negotiation inputs (mental traits, leverage, team win%)
- Create and update in-memory negotiation sessions (store)
- Commit accepted deals into the DB via LeagueService
"""

from dataclasses import asdict, replace
from typing import Any, Dict, Mapping, Optional, Tuple

import game_time

from league_repo import LeagueRepo
from league_service import LeagueService, CapViolationError
from schema import normalize_player_id, normalize_team_id

from .config import ContractNegotiationConfig, DEFAULT_CONTRACT_NEGOTIATION_CONFIG
from .engine import build_player_position, evaluate_offer
from .errors import (
    ContractNegotiationError,
    NEGOTIATION_BAD_PAYLOAD,
    NEGOTIATION_CLOSED,
    NEGOTIATION_COMMIT_FAILED,
    NEGOTIATION_COMMIT_NOT_ACCEPTED,
    NEGOTIATION_EXPIRED,
    NEGOTIATION_INVALID_MODE,
    NEGOTIATION_INVALID_OFFER,
)
from .store import (
    append_message,
    bump_lowball_strikes,
    bump_round,
    close_session,
    create_session,
    get_session,
    set_agency_snapshot,
    set_agreed_offer,
    set_last_counter,
    set_last_decision,
    set_last_offer,
    set_phase,
    set_player_position,
    set_player_snapshot,
    set_status,
    set_team_snapshot,
    set_valid_until,
)
from .types import ContractOffer, NegotiationDecision, PlayerPosition
from .utils import coerce_date_iso, date_add_days, safe_float, safe_int


def _extract_salary_cap_from_state() -> Optional[float]:
    """Best-effort SSOT salary cap extraction.

    SSOT: state.get_league_context_snapshot().trade_rules.salary_cap

    Returns None when unavailable.
    """
    try:
        import state

        ctx = state.get_league_context_snapshot() or {}
        if not isinstance(ctx, Mapping):
            return None
        trade_rules = ctx.get("trade_rules") if isinstance(ctx, Mapping) else None
        if not isinstance(trade_rules, Mapping):
            trade_rules = {}
        cap = trade_rules.get("salary_cap")
        cap_f = float(safe_float(cap, 0.0))
        return cap_f if cap_f > 0.0 else None
    except Exception:
        return None


def _with_salary_cap(cfg: ContractNegotiationConfig) -> ContractNegotiationConfig:
    """Return a cfg where salary_cap is populated (if possible).

    - If cfg already has salary_cap (>0), it is preserved.
    - Otherwise we read salary_cap from SSOT (state.trade_rules).
    """
    try:
        existing = float(safe_float(getattr(cfg, "salary_cap", None), 0.0))
        if existing > 0.0:
            return cfg
    except Exception:
        pass

    cap = _extract_salary_cap_from_state()
    if cap is None:
        return cfg

    try:
        return replace(cfg, salary_cap=float(cap))
    except Exception:
        return cfg


def _now_iso() -> str:
    return game_time.now_utc_like_iso()


def _get_team_win_pct(team_id: str) -> float:
    """Best-effort team win% from current league state."""
    try:
        from team_utils import get_conference_standings
    except Exception:
        return 0.5

    try:
        standings = get_conference_standings()
        for conf in ("east", "west"):
            for row in standings.get(conf, []) or []:
                if str(row.get("team_id") or "").upper() == str(team_id).upper():
                    wp = row.get("win_pct")
                    return float(safe_float(wp, 0.5))
    except Exception:
        return 0.5
    return 0.5


def _extract_mental(attrs: Mapping[str, Any]) -> Dict[str, int]:
    """Extract mental traits into canonical keys.

    Returns a dict with keys: work_ethic, coachability, ambition, loyalty, ego, adaptability (0..100).
    Missing values default to 50.
    """
    try:
        from agency.config import DEFAULT_CONFIG as _AGENCY_DEFAULT
        from agency.utils import extract_mental_from_attrs as _extract
        return _extract(attrs or {}, keys=_AGENCY_DEFAULT.mental_attr_keys)
    except Exception:
        # Fallback: attempt common keys directly; else default 50.
        keys = {
            "work_ethic": "M_WorkEthic",
            "coachability": "M_Coachability",
            "ambition": "M_Ambition",
            "loyalty": "M_Loyalty",
            "ego": "M_Ego",
            "adaptability": "M_Adaptability",
        }
        out: Dict[str, int] = {}
        for k, attr_key in keys.items():
            try:
                v = attrs.get(attr_key)
            except Exception:
                v = None
            try:
                iv = int(v) if v is not None else 50
            except Exception:
                iv = 50
            iv = 0 if iv < 0 else 100 if iv > 100 else iv
            out[k] = int(iv)
        return out


def _compute_role_and_leverage_for_team(
    *,
    team_roster: list[Mapping[str, Any]],
    candidate_player_id: str,
    candidate_ovr: int,
    candidate_salary: float,
) -> Tuple[str, float]:
    """Compute (role_bucket, leverage) relative to the team roster.

    Uses agency.expectations if available; otherwise falls back to a simple rank-based model.
    """
    try:
        from agency.config import DEFAULT_CONFIG as _AGENCY_DEFAULT
        from agency.expectations import compute_team_expectations

        snaps: list[dict] = []
        seen: set[str] = set()
        for r in team_roster or []:
            pid = str(r.get("player_id") or "")
            if not pid or pid in seen:
                continue
            seen.add(pid)
            snaps.append(
                {
                    "player_id": pid,
                    "ovr": safe_int(r.get("ovr"), 0),
                    "salary_amount": float(safe_float(r.get("salary_amount"), 0.0)),
                }
            )

        if str(candidate_player_id) not in seen:
            snaps.append(
                {
                    "player_id": str(candidate_player_id),
                    "ovr": int(candidate_ovr),
                    "salary_amount": float(candidate_salary),
                }
            )

        exp_map = compute_team_expectations(snaps, config=_AGENCY_DEFAULT.expectations)
        exp = exp_map.get(str(candidate_player_id))
        if exp is None:
            return ("UNKNOWN", 0.35)
        return (str(exp.role_bucket), float(exp.leverage))
    except Exception:
        # Fallback: rank by OVR, then salary.
        rows: list[tuple] = []
        for r in team_roster or []:
            pid = str(r.get("player_id") or "")
            if not pid:
                continue
            rows.append((pid, safe_int(r.get("ovr"), 0), float(safe_float(r.get("salary_amount"), 0.0))))
        if str(candidate_player_id) not in {pid for pid, *_ in rows}:
            rows.append((str(candidate_player_id), int(candidate_ovr), float(candidate_salary)))
        rows.sort(key=lambda x: (-int(x[1]), -float(x[2]), str(x[0])))
        n = len(rows)
        rank = 1
        for idx, (pid, _ovr, _sal) in enumerate(rows):
            if pid == str(candidate_player_id):
                rank = idx + 1
                break
        # role bucket cutoffs tuned for NBA roster sizes
        if n <= 0:
            role = "UNKNOWN"
        elif rank <= 1:
            role = "FRANCHISE"
        elif rank <= min(3, n):
            role = "STAR"
        elif rank <= min(5, n):
            role = "STARTER"
        elif rank <= min(8, n):
            role = "ROTATION"
        elif rank <= min(11, n):
            role = "BENCH"
        else:
            role = "GARBAGE"
        leverage = 1.0 if n <= 1 else 1.0 - (rank - 1) / float(n - 1)
        leverage = 0.0 if leverage < 0.0 else 1.0 if leverage > 1.0 else leverage
        return (role, float(leverage))


def _load_agency_snapshot_for_context(
    *,
    cur,
    player_id: str,
    team_id: str,
    mode: str,
) -> Dict[str, Any]:
    """Best-effort agency snapshot; neutral if not applicable."""
    # Default neutral
    out = {
        "trust": 0.5,
        "minutes_frustration": 0.0,
        "team_frustration": 0.0,
        "trade_request_level": 0,
        "cooldown_contract_until": None,
    }

    try:
        from agency.repo import get_player_agency_states
    except Exception:
        return out

    try:
        states = get_player_agency_states(cur, [str(player_id)])
        st = states.get(str(player_id))
        if not st:
            return out

        # Only apply relationship signals when negotiating with the team this state refers to.
        # For FA negotiations with a new team, we do NOT reuse old-team trust/frustration.
        if str(st.get("team_id") or "").upper() != str(team_id).upper():
            return out

        out.update(
            {
                "trust": float(safe_float(st.get("trust"), 0.5)),
                "minutes_frustration": float(safe_float(st.get("minutes_frustration"), 0.0)),
                "team_frustration": float(safe_float(st.get("team_frustration"), 0.0)),
                "trade_request_level": int(safe_int(st.get("trade_request_level"), 0)),
                "cooldown_contract_until": st.get("cooldown_contract_until"),
            }
        )
        return out
    except Exception:
        return out


def start_contract_negotiation(
    db_path: str,
    team_id: str,
    player_id: str,
    *,
    mode: str = "SIGN_FA",
    now_iso: Optional[str] = None,
    valid_days: Optional[int] = None,
    team_win_pct: Optional[float] = None,
    cfg: ContractNegotiationConfig = DEFAULT_CONTRACT_NEGOTIATION_CONFIG,
    repo: LeagueRepo | None = None,
) -> Dict[str, Any]:
    """Create a new negotiation session."""
    tid = str(normalize_team_id(team_id, strict=True)).upper()
    pid = str(normalize_player_id(player_id, strict=False, allow_legacy_numeric=True))
    mode_u = str(mode or "SIGN_FA").upper()

    if mode_u not in {"SIGN_FA", "RE_SIGN", "EXTEND"}:
        raise ContractNegotiationError(
            NEGOTIATION_INVALID_MODE,
            "Invalid negotiation mode",
            {"mode": mode},
        )

    now = str(now_iso or _now_iso())
    now_date = coerce_date_iso(now, default=None) or now[:10]

    managed = repo is None
    r = repo or LeagueRepo(db_path)
    try:
        # Read context
        with r.transaction() as cur:
            player = r.get_player(pid)
            current_team = str(r.get_team_id_by_player(pid)).upper()

            if mode_u == "SIGN_FA" and current_team != "FA":
                raise ContractNegotiationError(
                    NEGOTIATION_INVALID_MODE,
                    "Player is not a free agent",
                    {"player_id": pid, "team_id": current_team},
                )
            if mode_u in {"RE_SIGN", "EXTEND"} and current_team != tid:
                raise ContractNegotiationError(
                    NEGOTIATION_INVALID_MODE,
                    "Player is not on this team for re-sign/extend",
                    {"player_id": pid, "team_id": current_team, "negotiating_team": tid},
                )
            roster = r.get_team_roster(tid)

            # Current salary from SSOT roster table (players table does not store salary).
            salary_amount = r.get_salary_amount(pid)
            candidate_salary = float(safe_float(salary_amount, 0.0))

            role_bucket, leverage = _compute_role_and_leverage_for_team(
                team_roster=roster,
                candidate_player_id=pid,
                candidate_ovr=safe_int(player.get("ovr"), 0),
                candidate_salary=candidate_salary,
            )

            agency_snapshot = _load_agency_snapshot_for_context(cur=cur, player_id=pid, team_id=tid, mode=mode_u)

        mental = _extract_mental(player.get("attrs") or {})

        if team_win_pct is None:
            team_win_pct = _get_team_win_pct(tid)

        player_snapshot = {
            "player_id": pid,
            "name": player.get("name"),
            "pos": player.get("pos"),
            "age": safe_int(player.get("age"), 27),
            "ovr": safe_int(player.get("ovr"), 0),
            "salary_amount": float(candidate_salary),
            "mental": mental,
            "role_bucket": role_bucket,
            "leverage": float(leverage),
        }
        team_snapshot = {
            "team_id": tid,
            "win_pct": float(safe_float(team_win_pct, 0.5)),
        }

        cfg_eff = _with_salary_cap(cfg)

        pos = build_player_position(
            player_snapshot,
            team_snapshot,
            agency_snapshot,
            mode=mode_u,
            cfg=cfg_eff,
        )

        days = int(valid_days) if isinstance(valid_days, int) else int(cfg_eff.session_valid_days_default)
        valid_until = date_add_days(now_date, days)

        session = create_session(
            tid,
            pid,
            mode=mode_u,
            valid_until_iso=valid_until,
            max_rounds=int(pos.max_rounds),
            player_snapshot=player_snapshot,
            team_snapshot=team_snapshot,
            agency_snapshot=agency_snapshot,
            player_position=pos.to_payload(),
            constraints={},
        )

        append_message(
            session["session_id"],
            "SYSTEM",
            f"Contract negotiation started ({mode_u}).",
            meta={"team_id": tid, "player_id": pid, "valid_until": valid_until},
        )

        return session
    finally:
        if managed:
            try:
                r.close()
            except Exception:
                pass


def _is_expired(session: Mapping[str, Any], now_iso: str) -> bool:
    vu = session.get("valid_until")
    if not vu:
        return False
    now_d = coerce_date_iso(now_iso, default=None) or str(now_iso)[:10]
    vu_d = coerce_date_iso(vu, default=None) or str(vu)[:10]
    try:
        return str(now_d) > str(vu_d)
    except Exception:
        return False


def submit_contract_offer(
    db_path: str,
    session_id: str,
    offer_payload: Mapping[str, Any],
    *,
    now_iso: Optional[str] = None,
    cfg: ContractNegotiationConfig = DEFAULT_CONTRACT_NEGOTIATION_CONFIG,
) -> Dict[str, Any]:
    """Submit a team offer and receive the player's response."""
    now = str(now_iso or _now_iso())
    session = get_session(session_id)

    if str(session.get("status") or "").upper() != "ACTIVE":
        raise ContractNegotiationError(
            NEGOTIATION_CLOSED,
            "Negotiation session is closed",
            {"session_id": session_id, "status": session.get("status")},
        )

    if _is_expired(session, now):
        close_session(session_id, phase="EXPIRED", status="EXPIRED")
        raise ContractNegotiationError(
            NEGOTIATION_EXPIRED,
            "Negotiation session expired",
            {"session_id": session_id, "valid_until": session.get("valid_until")},
        )

    # Parse offer
    try:
        offer = ContractOffer.from_payload(offer_payload)
    except Exception as exc:
        raise ContractNegotiationError(
            NEGOTIATION_INVALID_OFFER,
            "Invalid offer payload",
            {"error": str(exc)},
        ) from exc

    # Evaluate
    cfg_eff = _with_salary_cap(cfg)
    decision: NegotiationDecision = evaluate_offer(session, offer, cfg=cfg_eff)

    # Persist session tracking
    set_last_offer(session_id, offer.to_payload())
    set_last_decision(session_id, decision.to_payload())

    # Strike tracking (insulting offers)
    try:
        meta = decision.meta or {}
        if bool(meta.get("is_insulting")) and safe_int(meta.get("new_lowball_strikes"), 0) > safe_int(meta.get("lowball_strikes"), 0):
            bump_lowball_strikes(session_id, 1)
    except Exception:
        pass

    # Round increments for every submitted offer (even if accepted)
    try:
        bump_round(session_id, 1)
    except Exception:
        pass

    # Apply status/phase transitions
    verdict = str(decision.verdict)
    if verdict == "ACCEPT":
        set_agreed_offer(session_id, offer.to_payload())
        set_phase(session_id, "ACCEPTED")
        append_message(session_id, "PLAYER", "Accepted the offer.", meta=decision.meta)
    elif verdict == "COUNTER":
        if decision.counter_offer is not None:
            set_last_counter(session_id, decision.counter_offer.to_payload())
        set_phase(session_id, "NEGOTIATING")
        append_message(session_id, "PLAYER", "Countered the offer.", meta=decision.meta)
    elif verdict == "REJECT":
        set_phase(session_id, "NEGOTIATING")
        append_message(session_id, "PLAYER", "Rejected the offer.", meta=decision.meta)
    elif verdict == "WALK":
        set_phase(session_id, "WALKED")
        close_session(session_id, phase="WALKED", status="CLOSED")
        append_message(session_id, "PLAYER", "Walked away from negotiations.", meta=decision.meta)
    else:
        set_phase(session_id, "NEGOTIATING")

    # Return response payload (include updated session snapshot for convenience)
    out_session = get_session(session_id)
    return {
        "session": out_session,
        "offer": offer.to_payload(),
        "decision": decision.to_payload(),
    }


def accept_last_counter(
    db_path: str,
    session_id: str,
    *,
    now_iso: Optional[str] = None,
) -> Dict[str, Any]:
    """Accept the player's last counter offer (no new offer required)."""
    now = str(now_iso or _now_iso())
    session = get_session(session_id)

    if str(session.get("status") or "").upper() != "ACTIVE":
        raise ContractNegotiationError(
            NEGOTIATION_CLOSED,
            "Negotiation session is closed",
            {"session_id": session_id, "status": session.get("status")},
        )

    if _is_expired(session, now):
        close_session(session_id, phase="EXPIRED", status="EXPIRED")
        raise ContractNegotiationError(
            NEGOTIATION_EXPIRED,
            "Negotiation session expired",
            {"session_id": session_id, "valid_until": session.get("valid_until")},
        )

    counter = session.get("last_counter")
    if not counter:
        raise ContractNegotiationError(
            NEGOTIATION_INVALID_OFFER,
            "No counter offer to accept",
            {"session_id": session_id},
        )

    # Validate counter payload
    try:
        offer = ContractOffer.from_payload(counter)
    except Exception as exc:
        raise ContractNegotiationError(
            NEGOTIATION_INVALID_OFFER,
            "Stored counter offer is invalid",
            {"error": str(exc)},
        ) from exc

    set_agreed_offer(session_id, offer.to_payload())
    set_phase(session_id, "ACCEPTED")
    append_message(session_id, "TEAM", "Accepted the counter offer.")
    out_session = get_session(session_id)
    return {"session": out_session, "agreed_offer": offer.to_payload()}


def commit_contract_negotiation(
    db_path: str,
    session_id: str,
    *,
    signed_date_iso: Optional[str] = None,
    now_iso: Optional[str] = None,
    repo: LeagueRepo | None = None,
) -> Dict[str, Any]:
    """Commit an accepted negotiation to the DB as a contract signing."""
    now = str(now_iso or _now_iso())
    session = get_session(session_id)

    if str(session.get("phase") or "").upper() != "ACCEPTED" or not session.get("agreed_offer"):
        raise ContractNegotiationError(
            NEGOTIATION_COMMIT_NOT_ACCEPTED,
            "Negotiation is not accepted",
            {"session_id": session_id, "phase": session.get("phase")},
        )

    if str(session.get("status") or "").upper() != "ACTIVE":
        raise ContractNegotiationError(
            NEGOTIATION_CLOSED,
            "Negotiation session is closed",
            {"session_id": session_id, "status": session.get("status")},
        )

    offer_payload = session.get("agreed_offer") or {}
    try:
        offer = ContractOffer.from_payload(offer_payload)
    except Exception as exc:
        raise ContractNegotiationError(
            NEGOTIATION_INVALID_OFFER,
            "Agreed offer payload is invalid",
            {"error": str(exc)},
        ) from exc

    tid = str(session.get("team_id") or "").upper()
    pid = str(session.get("player_id") or "")
    mode_u = str(session.get("mode") or "SIGN_FA").upper()

    signed_date = coerce_date_iso(signed_date_iso, default=None) or (coerce_date_iso(now, default=None) or now[:10])

    managed = repo is None
    r = repo or LeagueRepo(db_path)
    try:
        svc = LeagueService(r)
        if mode_u == "SIGN_FA":
            ev = svc.sign_free_agent(
                tid,
                pid,
                signed_date=signed_date,
                years=int(offer.years),
                salary_by_year={int(k): float(v) for k, v in offer.salary_by_year.items()},
                options=[dict(x) for x in (offer.options or [])],
            )
        elif mode_u in {"RE_SIGN", "EXTEND"}:
            ev = svc.re_sign_or_extend(
                tid,
                pid,
                signed_date=signed_date,
                years=int(offer.years),
                salary_by_year={int(k): float(v) for k, v in offer.salary_by_year.items()},
                options=[dict(x) for x in (offer.options or [])],
            )
        else:
            raise ContractNegotiationError(
                NEGOTIATION_INVALID_MODE,
                "Unsupported negotiation mode for commit",
                {"mode": mode_u},
            )

        # Close session
        close_session(session_id, phase="ACCEPTED", status="CLOSED")
        append_message(session_id, "SYSTEM", "Contract committed to DB.")

        return {
            "session": get_session(session_id),
            "signed_date": signed_date,
            "contract_offer": offer.to_payload(),
            "service_event": getattr(ev, "payload", ev),
        }
    except CapViolationError as exc:
        # v1 cap enforcement: translate into a negotiation commit failure with structured details
        raise ContractNegotiationError(
            NEGOTIATION_COMMIT_FAILED,
            getattr(exc, "message", "Cap violation"),
            {
                "code": getattr(exc, "code", "CAP_VIOLATION"),
                "message": getattr(exc, "message", str(exc)),
                "details": getattr(exc, "details", None),
            },
        ) from exc
    except ContractNegotiationError:
        raise
    except Exception as exc:
        raise ContractNegotiationError(
            NEGOTIATION_COMMIT_FAILED,
            "Failed to commit contract",
            {"error": str(exc)},
        ) from exc
    finally:
        if managed:
            try:
                r.close()
            except Exception:
                pass
