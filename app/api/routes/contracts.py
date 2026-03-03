from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException

import game_time
import state
from league_repo import LeagueRepo
from league_service import LeagueService
from schema import normalize_team_id, normalize_player_id
from app.schemas.contracts import (
    ContractNegotiationAcceptCounterRequest,
    ContractNegotiationCancelRequest,
    ContractNegotiationCommitRequest,
    ContractNegotiationOfferRequest,
    ContractNegotiationStartRequest,
    ReSignOrExtendRequest,
    ReleaseToFARequest,
    SignFreeAgentRequest,
    TwoWayNegotiationCommitRequest,
    TwoWayNegotiationDecisionRequest,
    TwoWayNegotiationStartRequest,
)
from app.services.cache_facade import _try_ui_cache_refresh_players
from app.services.contract_facade import _commit_accepted_contract_negotiation, _validate_repo_integrity

router = APIRouter()
logger = logging.getLogger(__name__)






























@router.get("/api/contracts/free-agents")
async def api_contracts_free_agents(q: str = "", limit: int = 200):
    """List free-agent candidates (players without an active team assignment)."""
    qv = str(q or "").strip().lower()
    lim = max(1, min(int(limit or 200), 1000))

    try:
        db_path = state.get_db_path()
        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT
                    p.player_id,
                    p.name,
                    p.pos,
                    p.age,
                    p.ovr,
                    p.attrs_json,
                    r.team_id
                FROM players p
                LEFT JOIN roster r
                    ON r.player_id = p.player_id
                   AND r.status = 'active'
                WHERE (r.player_id IS NULL OR UPPER(COALESCE(r.team_id, '')) = 'FA')
                ORDER BY p.ovr DESC, p.name ASC, p.player_id ASC
                """
            ).fetchall()

        out: List[Dict[str, Any]] = []
        for row in rows:
            pid = str(row["player_id"])
            name = str(row["name"] or "")
            if qv and qv not in pid.lower() and qv not in name.lower():
                continue
            attrs = json.loads(row["attrs_json"]) if row["attrs_json"] else {}
            out.append({
                "player_id": pid,
                "name": name,
                "pos": row["pos"],
                "age": row["age"],
                "overall": row["ovr"],
                "current_team_id": (str(row["team_id"]).upper() if row["team_id"] else None),
                "has_active_team": bool(row["team_id"] and str(row["team_id"]).upper() != "FA"),
                "attrs": attrs,
            })
            if len(out) >= lim:
                break

        return {
            "count": len(out),
            "query": q,
            "limit": lim,
            "players": out,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"free-agent list failed: {e}")

@router.post("/api/contracts/release-to-fa")
async def api_contracts_release_to_fa(req: ReleaseToFARequest):
    """Release a player to free agency (DB write)."""
    try:
        db_path = state.get_db_path()
        in_game_date = state.get_current_date_as_date()
        with LeagueRepo(db_path) as repo:
            svc = LeagueService(repo)
            event = svc.release_player_to_free_agency(
                player_id=req.player_id,
                released_date=req.released_date or in_game_date,
            )
        _validate_repo_integrity(db_path)
        event_dict = event.to_dict()
        affected = event_dict.get("affected_player_ids") or []
        _try_ui_cache_refresh_players(list(affected), context="contracts.release_to_fa")
        return {"ok": True, "event": event_dict}
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Release-to-FA failed: {e}")


# -------------------------------------------------------------------------
# Contract Negotiation API (player agency - mandatory path)
# -------------------------------------------------------------------------


@router.post("/api/contracts/negotiation/start")
async def api_contracts_negotiation_start(req: ContractNegotiationStartRequest):
    """Start a contract negotiation session (state-backed)."""
    try:
        from contracts.negotiation.service import start_contract_negotiation
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Negotiation module import failed: {exc}")

    try:
        db_path = state.get_db_path()
        now_iso = game_time.now_utc_like_iso()
        out = start_contract_negotiation(
            db_path=str(db_path),
            team_id=req.team_id,
            player_id=req.player_id,
            mode=req.mode,
            valid_days=req.valid_days,
            now_iso=str(now_iso),
        )
        return out
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/contracts/negotiation/offer")
async def api_contracts_negotiation_offer(req: ContractNegotiationOfferRequest):
    """Submit a team offer; player may ACCEPT / COUNTER / REJECT / WALK."""
    try:
        from contracts.negotiation.service import submit_contract_offer
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Negotiation module import failed: {exc}")

    try:
        db_path = state.get_db_path()
        now_iso = game_time.now_utc_like_iso()
        out = submit_contract_offer(
            db_path=str(db_path),
            session_id=req.session_id,
            offer_payload=req.offer,
            now_iso=str(now_iso),
        )
        return out
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/contracts/negotiation/accept-counter")
async def api_contracts_negotiation_accept_counter(req: ContractNegotiationAcceptCounterRequest):
    """Accept the last counter offer proposed by the player."""
    try:
        from contracts.negotiation.service import accept_last_counter
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Negotiation module import failed: {exc}")

    try:
        db_path = state.get_db_path()
        now_iso = game_time.now_utc_like_iso()
        out = accept_last_counter(
            db_path=str(db_path),
            session_id=req.session_id,
            now_iso=str(now_iso),
        )
        return out
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/contracts/negotiation/commit")
async def api_contracts_negotiation_commit(req: ContractNegotiationCommitRequest):
    """Commit an ACCEPTED session (SSOT contract write)."""
    try:
        from contracts.negotiation.store import get_session
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Negotiation module import failed: {exc}")

    db_path = state.get_db_path()
    signed_date_iso = req.signed_date or state.get_current_date_as_date().isoformat()

    try:
        session = get_session(str(req.session_id))
    except Exception as exc:
        raise HTTPException(status_code=404, detail={"code": "NEGOTIATION_NOT_FOUND", "message": str(exc)})

    return _commit_accepted_contract_negotiation(
        db_path=str(db_path),
        session_id=str(req.session_id),
        expected_team_id=str(session.get("team_id") or ""),
        expected_player_id=str(session.get("player_id") or ""),
        signed_date_iso=str(signed_date_iso),
        allowed_modes=None,
    )


@router.post("/api/contracts/negotiation/cancel")
async def api_contracts_negotiation_cancel(req: ContractNegotiationCancelRequest):
    """Cancel/close a negotiation session (no SSOT DB write)."""
    try:
        from contracts.negotiation.store import close_session, get_session
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Negotiation module import failed: {exc}")

    try:
        session = get_session(str(req.session_id))
        close_session(str(req.session_id), phase="WALKED", status="CLOSED")
        return {"ok": True, "session_id": str(req.session_id), "team_id": session.get("team_id"), "player_id": session.get("player_id")}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/api/contracts/sign-free-agent")
async def api_contracts_sign_free_agent(req: SignFreeAgentRequest):
    """Sign a free agent (DB write).

    Commercial enforcement:
    - This endpoint cannot bypass negotiation.
    - It requires a contract negotiation session_id in ACCEPTED phase.
    - The signed terms are taken from the session's agreed_offer (not from request payload).
    """
    try:
        db_path = state.get_db_path()
        signed_date_iso = req.signed_date or state.get_current_date_as_date().isoformat()

        out = _commit_accepted_contract_negotiation(
            db_path=str(db_path),
            session_id=str(req.session_id),
            expected_team_id=req.team_id,
            expected_player_id=req.player_id,
            signed_date_iso=str(signed_date_iso),
            allowed_modes={"SIGN_FA"},
        )
        _validate_repo_integrity(str(db_path))
        return out
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Sign-free-agent failed: {e}")


@router.post("/api/contracts/re-sign-or-extend")
async def api_contracts_re_sign_or_extend(req: ReSignOrExtendRequest):
    """Re-sign / extend a player (DB write).

    Commercial enforcement:
    - This endpoint cannot bypass negotiation.
    - It requires a contract negotiation session_id in ACCEPTED phase.
    - The signed terms are taken from the session's agreed_offer (not from request payload).
    """
    try:
        db_path = state.get_db_path()
        signed_date_iso = req.signed_date or state.get_current_date_as_date().isoformat()

        out = _commit_accepted_contract_negotiation(
            db_path=str(db_path),
            session_id=str(req.session_id),
            expected_team_id=req.team_id,
            expected_player_id=req.player_id,
            signed_date_iso=str(signed_date_iso),
            allowed_modes={"RE_SIGN", "EXTEND"},
        )
        _validate_repo_integrity(str(db_path))
        return out
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Re-sign/extend failed: {e}")


@router.post("/api/contracts/two-way/negotiation/start")
async def api_two_way_negotiation_start(req: TwoWayNegotiationStartRequest):
    try:
        from contracts.two_way_service import start_two_way_negotiation

        out = start_two_way_negotiation(
            db_path=str(state.get_db_path()),
            team_id=req.team_id,
            player_id=req.player_id,
            valid_days=req.valid_days,
            now_iso=game_time.now_utc_like_iso(),
        )
        return out
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/contracts/two-way/negotiation/decision")
async def api_two_way_negotiation_decision(req: TwoWayNegotiationDecisionRequest):
    try:
        from contracts.two_way_service import decide_two_way_negotiation

        out = decide_two_way_negotiation(session_id=str(req.session_id), accept=bool(req.accept))
        return out
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/contracts/two-way/negotiation/commit")
async def api_two_way_negotiation_commit(req: TwoWayNegotiationCommitRequest):
    try:
        from contracts.two_way_service import commit_two_way_negotiation

        out = commit_two_way_negotiation(
            db_path=str(state.get_db_path()),
            session_id=str(req.session_id),
            signed_date_iso=req.signed_date or state.get_current_date_as_date().isoformat(),
        )
        _validate_repo_integrity(str(state.get_db_path()))
        return out
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
