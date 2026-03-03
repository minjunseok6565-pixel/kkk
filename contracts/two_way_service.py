from __future__ import annotations

from typing import Any, Dict, Optional

import game_time
from contracts.negotiation.store import (
    append_message,
    close_session,
    create_session,
    get_session,
    set_phase,
)
from league_repo import LeagueRepo
from league_service import LeagueService
from schema import normalize_player_id, normalize_team_id
from two_way_repo import count_active_two_way_by_team


TWO_WAY_MAX_PER_TEAM = 3


def _now_iso() -> str:
    return game_time.now_utc_like_iso()


def start_two_way_negotiation(
    *,
    db_path: str,
    team_id: str,
    player_id: str,
    valid_days: Optional[int] = None,
    now_iso: Optional[str] = None,
) -> Dict[str, Any]:
    tid = str(normalize_team_id(team_id, strict=True)).upper()
    pid = str(normalize_player_id(player_id, strict=False, allow_legacy_numeric=True))
    now = str(now_iso or _now_iso())
    now_date = now[:10]
    days = int(valid_days) if isinstance(valid_days, int) and int(valid_days) > 0 else 7
    valid_until = now_date
    try:
        from contracts.negotiation.utils import date_add_days

        valid_until = date_add_days(now_date, days)
    except Exception:
        valid_until = now_date

    with LeagueRepo(db_path) as repo:
        with repo.transaction() as cur:
            _ = repo.get_player(pid)
            current_team = str(repo.get_team_id_by_player(pid)).upper()
            if current_team != "FA":
                raise ValueError(f"Player is not a free agent: team_id={current_team}")
            tw_n = count_active_two_way_by_team(cur, tid)
            if tw_n >= TWO_WAY_MAX_PER_TEAM:
                raise ValueError(f"Two-way slots full: team_id={tid} count={tw_n} max={TWO_WAY_MAX_PER_TEAM}")

    session = create_session(
        tid,
        pid,
        mode="SIGN_TWO_WAY",
        valid_until_iso=valid_until,
        max_rounds=1,
        constraints={"two_way": True, "salary_free": True, "max_per_team": TWO_WAY_MAX_PER_TEAM},
    )
    append_message(
        session["session_id"],
        "SYSTEM",
        "Two-way negotiation started.",
        meta={"team_id": tid, "player_id": pid, "valid_until": valid_until},
    )
    return session


def decide_two_way_negotiation(*, session_id: str, accept: bool) -> Dict[str, Any]:
    session = get_session(str(session_id))
    if str(session.get("status") or "").upper() != "ACTIVE":
        raise ValueError("Negotiation session is not active")
    if str(session.get("mode") or "").upper() != "SIGN_TWO_WAY":
        raise ValueError("Negotiation mode mismatch")

    if bool(accept):
        agreed = {
            "two_way": True,
            "start_season_year": 0,
            "years": 1,
            "salary_by_year": {},
            "non_monetary": {"salary_free": True},
        }
        from contracts.negotiation.store import set_agreed_offer

        set_agreed_offer(str(session_id), agreed)
        set_phase(str(session_id), "ACCEPTED")
        append_message(str(session_id), "PLAYER", "Accepted two-way contract.")
    else:
        set_phase(str(session_id), "WALKED")
        close_session(str(session_id), phase="WALKED", status="CLOSED")
        append_message(str(session_id), "PLAYER", "Rejected two-way contract.")

    return {"session": get_session(str(session_id)), "accepted": bool(accept)}


def commit_two_way_negotiation(
    *,
    db_path: str,
    session_id: str,
    signed_date_iso: Optional[str] = None,
) -> Dict[str, Any]:
    session = get_session(str(session_id))
    if str(session.get("mode") or "").upper() != "SIGN_TWO_WAY":
        raise ValueError("Negotiation mode mismatch")
    if str(session.get("phase") or "").upper() != "ACCEPTED":
        raise ValueError("Negotiation is not accepted")

    tid = str(session.get("team_id") or "").upper()
    pid = str(session.get("player_id") or "")
    signed = str(signed_date_iso or _now_iso()[:10])

    with LeagueRepo(db_path) as repo:
        svc = LeagueService(repo)
        ev = svc.sign_two_way(
            team_id=tid,
            player_id=pid,
            signed_date=signed,
        )

    close_session(str(session_id), phase="ACCEPTED", status="CLOSED")
    return {"ok": True, "session_id": str(session_id), "event": ev.to_dict()}
