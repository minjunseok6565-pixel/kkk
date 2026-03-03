from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import HTTPException

from league_repo import LeagueRepo
from league_service import LeagueService, CapViolationError
from schema import normalize_team_id, normalize_player_id

from app.services.cache_facade import _try_ui_cache_refresh_players


def _validate_repo_integrity(db_path: str) -> None:
    with LeagueRepo(db_path) as repo:
        # DB schema is guaranteed during server startup (state.startup_init_state()).
        repo.validate_integrity()


def _commit_accepted_contract_negotiation(
    *,
    db_path: str,
    session_id: str,
    expected_team_id: str,
    expected_player_id: str,
    signed_date_iso: str,
    allowed_modes: Optional[set[str]] = None,
) -> Dict[str, Any]:
    """Commit an ACCEPTED contract negotiation session by applying the SSOT contract write.

    This is the **enforcement gate** for commercial-grade 'player agency':
    - the signing endpoints cannot bypass the negotiation outcome
    - the contract terms used are always the session's agreed_offer

    Raises HTTPException on failure.
    """
    sid = str(session_id or "").strip()
    if not sid:
        raise HTTPException(status_code=400, detail={"code": "MISSING_SESSION_ID", "message": "session_id is required"})

    try:
        from contracts.negotiation.store import close_session, get_session
        from contracts.negotiation.types import ContractOffer
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Negotiation module import failed: {exc}")

    # Load and validate session
    try:
        session = get_session(sid)
    except Exception as exc:
        raise HTTPException(status_code=404, detail={"code": "NEGOTIATION_NOT_FOUND", "message": str(exc)})

    if str(session.get("kind") or "").upper() != "CONTRACT":
        raise HTTPException(status_code=409, detail={"code": "NEGOTIATION_KIND_MISMATCH", "session_id": sid})

    mode = str(session.get("mode") or "").upper()
    if allowed_modes is not None and mode not in allowed_modes:
        raise HTTPException(
            status_code=409,
            detail={"code": "NEGOTIATION_MODE_MISMATCH", "session_id": sid, "mode": mode, "allowed": sorted(allowed_modes)},
        )

    if str(session.get("status") or "").upper() != "ACTIVE":
        raise HTTPException(
            status_code=409,
            detail={"code": "NEGOTIATION_NOT_ACTIVE", "session_id": sid, "status": session.get("status")},
        )

    if str(session.get("phase") or "").upper() != "ACCEPTED":
        raise HTTPException(
            status_code=409,
            detail={"code": "NEGOTIATION_NOT_ACCEPTED", "session_id": sid, "phase": session.get("phase")},
        )

    team_norm = str(normalize_team_id(expected_team_id)).upper()
    pid_norm = str(normalize_player_id(expected_player_id, strict=False, allow_legacy_numeric=True))

    if str(session.get("team_id") or "").upper() != team_norm:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "NEGOTIATION_TEAM_MISMATCH",
                "session_id": sid,
                "expected_team_id": team_norm,
                "session_team_id": session.get("team_id"),
            },
        )

    if str(session.get("player_id") or "") != pid_norm:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "NEGOTIATION_PLAYER_MISMATCH",
                "session_id": sid,
                "expected_player_id": pid_norm,
                "session_player_id": session.get("player_id"),
            },
        )

    offer_payload = session.get("agreed_offer")
    if not isinstance(offer_payload, dict):
        raise HTTPException(
            status_code=409,
            detail={"code": "NEGOTIATION_NO_AGREED_OFFER", "session_id": sid},
        )

    # Normalize offer
    try:
        offer = ContractOffer.from_payload(offer_payload)
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "NEGOTIATION_BAD_OFFER", "session_id": sid, "message": str(exc)},
        )

    # Apply SSOT write via LeagueService (DB-backed).
    with LeagueRepo(db_path) as repo:
        svc = LeagueService(repo)
        try:
            if mode == "SIGN_FA":
                event = svc.sign_free_agent(
                    team_id=team_norm,
                    player_id=pid_norm,
                    signed_date=signed_date_iso,
                    years=int(offer.years),
                    salary_by_year=offer.salary_by_year,
                    options=[dict(x) for x in (offer.options or [])],
                )
            else:
                # RE_SIGN and EXTEND both map to the same SSOT operation.
                event = svc.re_sign_or_extend(
                    team_id=team_norm,
                    player_id=pid_norm,
                    signed_date=signed_date_iso,
                    years=int(offer.years),
                    salary_by_year=offer.salary_by_year,
                    options=[dict(x) for x in (offer.options or [])],
                )
        except CapViolationError as exc:
            # Rule-based rejection: return 409 instead of 500.
            raise HTTPException(
                status_code=409,
                detail={
                    "code": getattr(exc, "code", "CAP_VIOLATION"),
                    "message": getattr(exc, "message", str(exc)),
                    "details": getattr(exc, "details", None),
                },
            )

    # Close the session (idempotent-ish; no side effects on DB).
    try:
        close_session(sid, phase="ACCEPTED", status="CLOSED")
    except Exception:
        # Never fail contract commit due to in-memory session closure.
        pass

    event_dict = event.to_dict()
    affected = event_dict.get("affected_player_ids") or []
    _try_ui_cache_refresh_players(list(affected), context="contracts.negotiation.commit")
    return {"ok": True, "session_id": sid, "mode": mode, "team_id": team_norm, "player_id": pid_norm, "event": event_dict}
