from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, List, Literal, Optional
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import Field

import game_time
import state
from config import ALL_TEAM_IDS
from league_repo import LeagueRepo
from league_service import LeagueService
from schema import normalize_player_id, normalize_team_id
from app.schemas.common import EmptyRequest
from app.schemas.draft import (
    DraftAutoSelectionsRequest,
    DraftCombineRequest,
    DraftInterviewsRequest,
    DraftRecordPickRequest,
    DraftWorkoutsRequest,
)
from app.schemas.offseason import (
    AgencyEventRespondRequest,
    AgencyUserActionRequest,
    OffseasonContractsProcessRequest,
    TeamOptionDecideRequest,
    TeamOptionPendingRequest,
)
from app.services.cache_facade import _try_ui_cache_refresh_players
from app.services.contract_facade import _validate_repo_integrity
from team_utils import ui_cache_refresh_players, ui_cache_rebuild_all

router = APIRouter()


























@router.post("/api/season/enter-offseason")
async def api_enter_offseason(req: EmptyRequest):
    """플레이오프 우승 확정 이후, 다음 시즌으로 전환하고 오프시즌(날짜 구간)으로 진입한다.

    Design notes:
    - 이 엔드포인트는 '오프시즌 진입(날짜 이동)'만 수행한다.
    - 대학 시즌 마감/선언 생성, 오프시즌 계약 처리, 드래프트(로터리/정산/지명 기록/적용)는
      아래의 stepwise 오프시즌 API를 단계별로 호출해 실행한다.
    - 실제 시즌 전환(state.start_new_season)은 드래프트 적용 이후에만 수행한다.
    """
    post = state.get_postseason_snapshot() or {}
    champion = post.get("champion")
    if not champion:
        raise HTTPException(status_code=400, detail="Champion not decided yet.")

    league_ctx = state.get_league_context_snapshot() or {}
    try:
        season_year = int(league_ctx.get("season_year") or 0)
    except Exception:
        season_year = 0
    if season_year <= 0:
        raise HTTPException(status_code=500, detail="Invalid season_year in state.")

    next_year = season_year + 1

    # Skeleton offseason: move to an offseason date window where there are no scheduled games.
    offseason_start = f"{next_year}-07-01"
    state.set_current_date(offseason_start)

    # Best-effort UI cache rebuild (derived, non-authoritative).
    try:
        ui_cache_rebuild_all()
    except Exception:
        pass

    return {
        "ok": True,
        "prev_champion": champion,
        "from_season_year": int(season_year),
        "draft_year": int(next_year),
        "offseason_start": offseason_start,
        "steps": [
            "/api/offseason/college/finalize",
            "/api/offseason/contracts/process",
            "/api/offseason/retirement/process",
            "/api/offseason/training/apply-growth",
            "/api/offseason/draft/lottery",
            "/api/offseason/draft/settle",
            "/api/offseason/draft/combine",
            "/api/offseason/draft/workouts",
            "/api/offseason/draft/interviews",
            "/api/offseason/draft/withdrawals",
            "/api/offseason/draft/selections/auto",
            "/api/offseason/draft/selections/pick",
            "/api/offseason/draft/apply",
        ],
    }


# -------------------------------------------------------------------------
# Offseason (stepwise) API: College finalize -> Contracts -> Draft -> Apply
# -------------------------------------------------------------------------


@router.post("/api/offseason/college/finalize")
async def api_offseason_college_finalize(req: EmptyRequest):
    """대학 시즌 마감(스탯 생성) + 드래프트 선언 생성(SSOT=DB).

    NOTE:
    - season_year: 현재 league_ctx 기준(막 끝난 시즌)
    - draft_year: season_year+1
    - 구현은 idempotent 하도록 college.service가 내부에서 guard 한다.
    """
    league_ctx = state.get_league_context_snapshot() or {}
    try:
        season_year = int(league_ctx.get("season_year") or 0)
    except Exception:
        season_year = 0
    if season_year <= 0:
        raise HTTPException(status_code=500, detail="Invalid season_year in state.")

    draft_year = season_year + 1
    try:
        db_path = state.get_db_path()
        from college.service import finalize_season_and_generate_entries

        finalize_season_and_generate_entries(db_path=db_path, season_year=season_year, draft_year=draft_year)
        return {"ok": True, "season_year": int(season_year), "draft_year": int(draft_year)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/offseason/contracts/process")
async def api_offseason_contracts_process(req: OffseasonContractsProcessRequest):
    """오프시즌 계약 처리(만료/옵션/연장/트레이드 정산 등)."""
    league_ctx = state.get_league_context_snapshot() or {}
    try:
        from_year = int(league_ctx.get("season_year") or 0)
    except Exception:
        from_year = 0
    if from_year <= 0:
        raise HTTPException(status_code=500, detail="Invalid season_year in state.")

    to_year = from_year + 1

    # Hard gate: user's TEAM options must be decided before running offseason contracts processing.
    team_id = normalize_team_id(req.user_team_id)
    if not team_id:
        raise HTTPException(status_code=400, detail="Invalid user_team_id.")
    try:
        db_path = state.get_db_path()
        with LeagueRepo(db_path) as repo:
            repo.init_db()
            svc = LeagueService(repo)
            pending = svc.list_pending_team_options(str(team_id), season_year=int(to_year))
        if pending:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "TEAM_OPTION_DECISION_REQUIRED",
                    "message": "Pending TEAM options must be decided before offseason contracts processing.",
                    "team_id": str(team_id),
                    "season_year": int(to_year),
                    "pending_team_options": list(pending),
                    "count": int(len(pending)),
                },
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to check pending TEAM options: {e}")

    try:
        from contracts.offseason import process_offseason
        from contracts.options_policy import make_ai_team_option_decision_policy

        # Use in-game date for all contract/offseason decisions & transaction logs.
        # Fail-loud: do NOT fall back to OS date (timeline immersion).
        try:
            in_game_date = state.get_current_date_as_date()
        except Exception as e:
            raise HTTPException(status_code=500, detail="Failed to read in-game date from state.") from e
        if not hasattr(in_game_date, "isoformat"):
            raise HTTPException(status_code=500, detail="Invalid in-game date object in state.")
        decision_date_iso = in_game_date.isoformat()

        # process_offseason은 DB를 갱신하지만 state를 직접 mutate 하진 않는다.
        snap = state.export_full_state_snapshot()

        # Best-effort: ensure the final regular-season month agency tick is applied
        # before offseason contract processing (idempotent).
        try:
            from agency.checkpoints import ensure_last_regular_month_agency_tick

            final_agency_tick = ensure_last_regular_month_agency_tick(
                db_path=str(db_path),
                now_date_iso=str(decision_date_iso),
                state_snapshot=snap,
            )
        except Exception:
            final_agency_tick = {"ok": True, "skipped": True, "reason": "tick_check_failed"}

        result = process_offseason(
            snap,
            from_season_year=int(from_year),
            to_season_year=int(to_year),
            decision_date_iso=str(decision_date_iso),
            decision_policy=make_ai_team_option_decision_policy(user_team_id=str(team_id)),
        )
        return {
            "ok": True,
            "from_season_year": int(from_year),
            "to_season_year": int(to_year),
            "final_regular_month_agency_tick": final_agency_tick,
            "result": result,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/offseason/retirement/preview")
async def api_offseason_retirement_preview(req: EmptyRequest):
    """오프시즌 은퇴 결정 미리보기(확정 전)."""
    _ = req
    league_ctx = state.get_league_context_snapshot() or {}
    try:
        from_year = int(league_ctx.get("season_year") or 0)
    except Exception:
        from_year = 0
    if from_year <= 0:
        raise HTTPException(status_code=500, detail="Invalid season_year in state.")
    to_year = int(from_year) + 1

    try:
        in_game_date = state.get_current_date_as_date().isoformat()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read in-game date from state: {e}")

    try:
        from retirement.service import preview_offseason_retirement

        out = preview_offseason_retirement(
            db_path=str(state.get_db_path()),
            season_year=int(to_year),
            decision_date_iso=str(in_game_date),
        )
        return out
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/offseason/retirement/process")
async def api_offseason_retirement_process(req: EmptyRequest):
    """오프시즌 은퇴 확정 처리(해당 시즌 1회, idempotent)."""
    _ = req
    league_ctx = state.get_league_context_snapshot() or {}
    try:
        from_year = int(league_ctx.get("season_year") or 0)
    except Exception:
        from_year = 0
    if from_year <= 0:
        raise HTTPException(status_code=500, detail="Invalid season_year in state.")
    to_year = int(from_year) + 1

    try:
        in_game_date = state.get_current_date_as_date().isoformat()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read in-game date from state: {e}")

    try:
        from retirement.service import process_offseason_retirement

        out = process_offseason_retirement(
            db_path=str(state.get_db_path()),
            season_year=int(to_year),
            decision_date_iso=str(in_game_date),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"offseason retirement failed: {e}")

    # Best-effort UI cache rebuild.
    try:
        ui_cache_rebuild_all()
    except Exception:
        pass

    return out


@router.post("/api/offseason/training/apply-growth")
async def api_offseason_training_apply_growth(req: EmptyRequest):
    """오프시즌 성장/훈련 적용 (Step 2).

    - SSOT: players.attrs_json 업데이트
    - players.age +1
    - Growth profile 생성/업데이트
    - Idempotent: 같은 시즌 전환에 대해 1회만 적용
    """
    _ = req
    league_ctx = state.get_league_context_snapshot() or {}
    try:
        from_year = int(league_ctx.get("season_year") or 0)
    except Exception:
        from_year = 0
    if from_year <= 0:
        raise HTTPException(status_code=500, detail="Invalid season_year in state.")
    to_year = int(from_year) + 1

    try:
        in_game_date = state.get_current_date_as_date().isoformat()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read in-game date from state: {e}")

    db_path = state.get_db_path()
    workflow_state = state.export_workflow_state()

    # If the last regular-season month tick hasn't been applied yet (common when
    # jumping into offseason), apply it now (idempotent).
    snap = state.export_full_state_snapshot()

    try:
        from training.checkpoints import ensure_last_regular_month_tick
        final_month_tick = ensure_last_regular_month_tick(
            db_path=str(db_path),
            now_date_iso=str(in_game_date),
            state_snapshot=snap,
        )
    except Exception:
        final_month_tick = {"ok": True, "skipped": True, "reason": "tick_check_failed"}

    # Same parity checkpoint for player agency (idempotent).
    try:
        from agency.checkpoints import ensure_last_regular_month_agency_tick

        final_agency_tick = ensure_last_regular_month_agency_tick(
            db_path=str(db_path),
            now_date_iso=str(in_game_date),
            state_snapshot=snap,
        )
    except Exception:
        final_agency_tick = {"ok": True, "skipped": True, "reason": "tick_check_failed"}

    from training.service import apply_offseason_growth

    try:
        result = apply_offseason_growth(
            db_path=str(db_path),
            from_season_year=int(from_year),
            to_season_year=int(to_year),
            in_game_date_iso=str(in_game_date),
            workflow_state=workflow_state,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"offseason growth failed: {e}")

    # Best-effort UI cache rebuild.
    try:
        ui_cache_rebuild_all()
    except Exception:
        pass

    result["final_regular_month_tick"] = final_month_tick
    result["final_regular_month_agency_tick"] = final_agency_tick
    return result


# -------------------------------------------------------------------------
# Agency (player autonomy) API
# -------------------------------------------------------------------------


@router.get("/api/agency/player/{player_id}")
async def api_agency_get_player(player_id: str, limit: int = 50, offset: int = 0, season_year: Optional[int] = None):
    """Get a player's current agency state + recent events."""
    pid = str(normalize_player_id(player_id, strict=False, allow_legacy_numeric=True))
    if not pid:
        raise HTTPException(status_code=400, detail="Invalid player_id")

    db_path = state.get_db_path()
    with LeagueRepo(db_path) as repo:
        repo.init_db()
        try:
            from agency import repo as agency_repo

            with repo.transaction() as cur:
                st_map = agency_repo.get_player_agency_states(cur, [pid])
                st = st_map.get(pid)
                events = agency_repo.list_agency_events(
                    cur,
                    player_id=pid,
                    season_year=int(season_year) if season_year is not None else None,
                    limit=int(limit),
                    offset=int(offset),
                )
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to read agency data: {e}")

    return {"ok": True, "player_id": pid, "state": st, "events": events}


@router.get("/api/agency/team/{team_id}/events")
async def api_agency_get_team_events(
    team_id: str,
    limit: int = 50,
    offset: int = 0,
    season_year: Optional[int] = None,
    event_type: Optional[str] = None,
):
    """List agency events for a team (UI feed)."""
    tid = str(normalize_team_id(team_id)).upper()
    if not tid:
        raise HTTPException(status_code=400, detail="Invalid team_id")

    db_path = state.get_db_path()
    with LeagueRepo(db_path) as repo:
        repo.init_db()
        try:
            from agency import repo as agency_repo

            with repo.transaction() as cur:
                events = agency_repo.list_agency_events(
                    cur,
                    team_id=tid,
                    season_year=int(season_year) if season_year is not None else None,
                    event_type=str(event_type) if event_type else None,
                    limit=int(limit),
                    offset=int(offset),
                )
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to list agency events: {e}")

    return {"ok": True, "team_id": tid, "events": events}


@router.get("/api/agency/events")
async def api_agency_get_events(
    limit: int = 50,
    offset: int = 0,
    season_year: Optional[int] = None,
    event_type: Optional[str] = None,
):
    """List league-wide agency events (debug / commissioner feed)."""
    db_path = state.get_db_path()
    with LeagueRepo(db_path) as repo:
        repo.init_db()
        try:
            from agency import repo as agency_repo

            with repo.transaction() as cur:
                events = agency_repo.list_agency_events(
                    cur,
                    season_year=int(season_year) if season_year is not None else None,
                    event_type=str(event_type) if event_type else None,
                    limit=int(limit),
                    offset=int(offset),
                )
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to list agency events: {e}")

    return {"ok": True, "events": events}


@router.post("/api/agency/events/respond")
async def api_agency_events_respond(req: AgencyEventRespondRequest):
    """Respond to an agency event (user chooses how to handle demands/promises).

    This is the *mandatory* user-facing response path:
    it records the user's response, mutates player agency state, and (optionally)
    creates promises to be resolved later.
    """
    db_path = state.get_db_path()
    in_game_date = state.get_current_date_as_date().isoformat()
    now_date = req.now_date or in_game_date

    try:
        from agency.interaction_service import AgencyInteractionError, respond_to_agency_event
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Agency interaction module import failed: {exc}")

    try:
        out = respond_to_agency_event(
            db_path=str(db_path),
            user_team_id=req.user_team_id,
            event_id=req.event_id,
            response_type=req.response_type,
            response_payload=req.response_payload,
            now_date_iso=str(now_date),
            strict_promises=True,
        )
        pid = out.get("player_id")
        if pid:
            _try_ui_cache_refresh_players([str(pid)], context="agency.events.respond")
        return out
    except AgencyInteractionError as e:
        # Map well-known error codes to stable HTTP semantics.
        code = str(e.code or "")
        if code in {"AGENCY_EVENT_NOT_FOUND"}:
            raise HTTPException(status_code=404, detail={"code": code, "message": e.message, "details": e.details})
        if code in {"AGENCY_EVENT_TEAM_MISMATCH", "AGENCY_PLAYER_NOT_ON_TEAM"}:
            raise HTTPException(status_code=409, detail={"code": code, "message": e.message, "details": e.details})
        if code in {"AGENCY_PROMISE_SCHEMA_MISSING"}:
            raise HTTPException(status_code=500, detail={"code": code, "message": e.message, "details": e.details})
        raise HTTPException(status_code=400, detail={"code": code, "message": e.message, "details": e.details})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



@router.post("/api/agency/actions/apply")
async def api_agency_actions_apply(req: AgencyUserActionRequest):
    """User-initiated agency actions (proactive management).

    Examples: meet player, praise, warn, set expectations, start extension talks.
    This records an agency event, updates player agency state, and may create a promise.
    """
    db_path = state.get_db_path()
    in_game_date = state.get_current_date_as_date().isoformat()
    now_date = req.now_date or in_game_date

    league_ctx = state.get_league_context_snapshot() or {}
    try:
        sy = int(league_ctx.get("season_year") or 0)
    except Exception:
        sy = 0

    try:
        from agency.interaction_service import AgencyInteractionError, apply_user_agency_action
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Agency interaction module import failed: {exc}")

    try:
        out = apply_user_agency_action(
            db_path=str(db_path),
            user_team_id=req.user_team_id,
            player_id=req.player_id,
            season_year=int(sy),
            action_type=req.action_type,
            action_payload=req.action_payload,
            now_date_iso=str(now_date),
            strict_promises=True,
        )
        pid = out.get("player_id")
        if pid:
            _try_ui_cache_refresh_players([str(pid)], context="agency.actions.apply")
        return out
    except AgencyInteractionError as e:
        code = str(e.code or "")
        if code in {"AGENCY_PLAYER_NOT_ON_TEAM"}:
            raise HTTPException(status_code=409, detail={"code": code, "message": e.message, "details": e.details})
        if code in {"AGENCY_PROMISE_SCHEMA_MISSING"}:
            raise HTTPException(status_code=500, detail={"code": code, "message": e.message, "details": e.details})
        raise HTTPException(status_code=400, detail={"code": code, "message": e.message, "details": e.details})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/api/offseason/options/team/pending")
async def api_offseason_team_options_pending(req: TeamOptionPendingRequest):
    """유저 팀의 다음 시즌 TEAM 옵션(PENDING) 목록 조회."""
    league_ctx = state.get_league_context_snapshot() or {}
    try:
        from_year = int(league_ctx.get("season_year") or 0)
    except Exception:
        from_year = 0
    if from_year <= 0:
        raise HTTPException(status_code=500, detail="Invalid season_year in state.")

    to_year = from_year + 1

    team_id = normalize_team_id(req.user_team_id)
    if not team_id:
        raise HTTPException(status_code=400, detail="Invalid user_team_id.")

    try:
        db_path = state.get_db_path()
        with LeagueRepo(db_path) as repo:
            repo.init_db()
            svc = LeagueService(repo)
            pending = svc.list_pending_team_options(str(team_id), season_year=int(to_year))
        return {
            "ok": True,
            "team_id": str(team_id),
            "season_year": int(to_year),
            "count": int(len(pending)),
            "pending_team_options": list(pending),
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list pending TEAM options: {e}")


@router.post("/api/offseason/options/team/decide")
async def api_offseason_team_options_decide(req: TeamOptionDecideRequest):
    """유저 팀 TEAM 옵션 행사/거절 결정 커밋(DB write)."""
    league_ctx = state.get_league_context_snapshot() or {}
    try:
        from_year = int(league_ctx.get("season_year") or 0)
    except Exception:
        from_year = 0
    if from_year <= 0:
        raise HTTPException(status_code=500, detail="Invalid season_year in state.")

    to_year = from_year + 1

    team_id = normalize_team_id(req.user_team_id)
    if not team_id:
        raise HTTPException(status_code=400, detail="Invalid user_team_id.")

    decisions = list(req.decisions or [])
    if not decisions:
        raise HTTPException(status_code=400, detail="decisions must not be empty.")

    # Use in-game date for all contract/offseason decisions & transaction logs.
    # Fail-loud: do NOT fall back to OS date (timeline immersion).
    try:
        in_game_date = state.get_current_date_as_date()
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to read in-game date from state.") from e
    if not hasattr(in_game_date, "isoformat"):
        raise HTTPException(status_code=500, detail="Invalid in-game date object in state.")
    decision_date_iso = in_game_date.isoformat()

    try:
        db_path = state.get_db_path()
        events: List[Dict[str, Any]] = []
        affected_player_ids: List[str] = []

        with LeagueRepo(db_path) as repo:
            repo.init_db()
            svc = LeagueService(repo)

            # All-or-nothing: apply all decisions within one DB transaction.
            with repo.transaction():
                for item in decisions:
                    ev = svc.apply_team_option_decision(
                        contract_id=str(item.contract_id),
                        season_year=int(to_year),
                        decision=str(item.decision),
                        expected_team_id=str(team_id),
                        decision_date=str(decision_date_iso),
                    )
                    evd = ev.to_dict()
                    events.append(evd)
                    pid = evd.get("player_id")
                    if pid:
                        affected_player_ids.append(str(pid))

            remaining = svc.list_pending_team_options(str(team_id), season_year=int(to_year))

        _validate_repo_integrity(db_path)
        _try_ui_cache_refresh_players(list(sorted(set(affected_player_ids))), context="offseason.team_options.decide")

        return {
            "ok": True,
            "team_id": str(team_id),
            "season_year": int(to_year),
            "applied": int(len(events)),
            "events": events,
            "remaining_pending_count": int(len(remaining)),
        }
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to apply TEAM option decisions: {e}")


@router.post("/api/offseason/draft/lottery")
async def api_offseason_draft_lottery(req: EmptyRequest):
    """드래프트 1~4픽 로터리(플랜 생성/저장)."""
    league_ctx = state.get_league_context_snapshot() or {}
    try:
        from_year = int(league_ctx.get("season_year") or 0)
    except Exception:
        from_year = 0
    if from_year <= 0:
        raise HTTPException(status_code=500, detail="Invalid season_year in state.")
    draft_year = from_year + 1

    try:
        from draft.pipeline import run_lottery

        db_path = state.get_db_path()
        snap = state.export_full_state_snapshot()
        plan = run_lottery(state_snapshot=snap, db_path=db_path, draft_year=int(draft_year))
        return {"ok": True, "draft_year": int(draft_year), "plan": plan.to_dict()}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/offseason/draft/settle")
async def api_offseason_draft_settle(req: EmptyRequest):
    """픽 정산(보호/스왑) + 최종 지명 턴 생성."""
    league_ctx = state.get_league_context_snapshot() or {}
    try:
        from_year = int(league_ctx.get("season_year") or 0)
    except Exception:
        from_year = 0
    if from_year <= 0:
        raise HTTPException(status_code=500, detail="Invalid season_year in state.")
    draft_year = from_year + 1

    try:
        from draft.pipeline import run_settlement

        db_path = state.get_db_path()
        events, turns = run_settlement(db_path=db_path, draft_year=int(draft_year))
        return {
            "ok": True,
            "draft_year": int(draft_year),
            "settlement_events": list(events),
            "turns": [t.to_dict() for t in turns],
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/offseason/draft/combine")
async def api_offseason_draft_combine(req: DraftCombineRequest):
    """드래프트 컴바인 실행 + 결과 DB 저장."""
    league_ctx = state.get_league_context_snapshot() or {}
    try:
        from_year = int(league_ctx.get("season_year") or 0)
    except Exception:
        from_year = 0
    if from_year <= 0:
        raise HTTPException(status_code=500, detail="Invalid season_year in state.")
    draft_year = from_year + 1

    try:
        from draft.events import run_combine

        db_path = state.get_db_path()
        result = run_combine(db_path=db_path, draft_year=int(draft_year), rng_seed=req.rng_seed)
        return {"ok": True, "draft_year": int(draft_year), "result": result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/offseason/draft/workouts")
async def api_offseason_draft_workouts(req: DraftWorkoutsRequest):
    """팀 워크아웃 실행 + 결과 DB 저장."""
    league_ctx = state.get_league_context_snapshot() or {}
    try:
        from_year = int(league_ctx.get("season_year") or 0)
    except Exception:
        from_year = 0
    if from_year <= 0:
        raise HTTPException(status_code=500, detail="Invalid season_year in state.")
    draft_year = from_year + 1

    try:
        from draft.events import run_workouts

        db_path = state.get_db_path()
        team_id = normalize_team_id(req.team_id)
        if not team_id:
            raise HTTPException(status_code=400, detail="Invalid team_id.")

        try:
            max_invites = int(req.max_invites)
        except Exception:
            max_invites = 0
        if max_invites < 1:
            raise HTTPException(status_code=400, detail="max_invites must be >= 1.")

        invited = list(req.invited_prospect_temp_ids or [])
        result = run_workouts(
            db_path=db_path,
            draft_year=int(draft_year),
            team_id=str(team_id),
            invited_prospect_temp_ids=invited,
            max_invites=int(max_invites),
            rng_seed=req.rng_seed,
        )
        return {
            "ok": True,
            "draft_year": int(draft_year),
            "team_id": str(team_id),
            "invited_count": int(len(invited)),
            "max_invites": int(max_invites),
            "result": result,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# -------------------------------------------------------------------------
# Draft Interviews (user-controlled; private to viewer team)
# -------------------------------------------------------------------------


@router.get("/api/offseason/draft/interviews/questions")
async def api_offseason_draft_interview_questions():
    """인터뷰 질문 목록(서버 정의)을 반환한다. (UI 미구현이어도 API만 준비)"""
    try:
        from draft.interviews import list_interview_questions

        questions = list_interview_questions()
        return {"ok": True, "questions": questions}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/offseason/draft/interviews")
async def api_offseason_draft_interviews(req: DraftInterviewsRequest):
    """팀 인터뷰 실행 + 결과 DB 저장. (유저가 선택한 질문 기반)"""
    league_ctx = state.get_league_context_snapshot() or {}
    try:
        from_year = int(league_ctx.get("season_year") or 0)
    except Exception:
        from_year = 0
    if from_year <= 0:
        raise HTTPException(status_code=500, detail="Invalid season_year in state.")
    draft_year = from_year + 1

    try:
        from draft.interviews import run_interviews

        db_path = state.get_db_path()
        team_id = normalize_team_id(req.team_id)
        if not team_id:
            raise HTTPException(status_code=400, detail="Invalid team_id.")

        items = list(req.interviews or [])
        if len(items) == 0:
            # Allow skipping (no DB writes), consistent with workouts endpoint behavior.
            return {
                "ok": True,
                "draft_year": int(draft_year),
                "team_id": str(team_id),
                "skipped": True,
                "result": {"written": 0, "skipped": 0},
            }

        interviews: List[Dict[str, Any]] = []
        for it in items:
            pid = str(it.prospect_temp_id or "").strip()
            if not pid:
                raise HTTPException(status_code=400, detail="prospect_temp_id is required.")
            qids = [str(x).strip() for x in (it.selected_question_ids or []) if str(x).strip()]
            # For v1, enforce exactly 3 picks here (UI can still choose 3).
            if len(qids) != 3:
                raise HTTPException(status_code=400, detail="selected_question_ids must have exactly 3 items.")
            if len(set(qids)) != len(qids):
                raise HTTPException(status_code=400, detail="selected_question_ids must be unique.")
            interviews.append({"prospect_temp_id": pid, "selected_question_ids": qids})

        result = run_interviews(
            db_path=db_path,
            draft_year=int(draft_year),
            team_id=str(team_id),
            interviews=interviews,
            rng_seed=req.rng_seed,
        )
        return {
            "ok": True,
            "draft_year": int(draft_year),
            "team_id": str(team_id),
            "requested": int(len(interviews)),
            "result": result,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/offseason/draft/withdrawals")
async def api_offseason_draft_withdrawals(req: EmptyRequest):
    """드래프트 철회(언더클래스만 복귀) 단계 실행 + DB 반영."""
    league_ctx = state.get_league_context_snapshot() or {}
    try:
        from_year = int(league_ctx.get("season_year") or 0)
    except Exception:
        from_year = 0
    if from_year <= 0:
        raise HTTPException(status_code=500, detail="Invalid season_year in state.")
    draft_year = from_year + 1

    try:
        from draft.withdrawals import run_withdrawals

        db_path = state.get_db_path()
        result = run_withdrawals(db_path=db_path, draft_year=int(draft_year))
        return {"ok": True, "draft_year": int(draft_year), "result": result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# -------------------------------------------------------------------------
# Draft Experts Big Board (Sam Vecenie-style)
# -------------------------------------------------------------------------


@router.get("/api/offseason/draft/experts")
async def api_offseason_draft_experts():
    """드래프트 전문가(외부 빅보드 작성자) 목록."""
    try:
        # Local import (avoid extra import work at server boot).
        from draft.expert_bigboard import list_experts, PHASE_PRE_COMBINE, PHASE_POST_COMBINE, PHASE_AUTO

        return {
            "ok": True,
            "experts": list_experts(),
            "phases": [PHASE_PRE_COMBINE, PHASE_POST_COMBINE],
            "default_phase": PHASE_AUTO,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list draft experts: {e}") from e


@router.get("/api/offseason/draft/bigboard/expert")
async def api_offseason_draft_bigboard_expert(
    expert_id: str,
    phase: Optional[str] = None,
    draft_year: Optional[int] = None,
    limit: Optional[int] = None,
    pool_mode: Optional[str] = "auto",
    watch_run_id: Optional[str] = None,
    watch_min_prob: Optional[float] = None,
):
    """특정 전문가의 Big Board 생성(불완전 정보 + 바이어스 + 상단 수렴 앵커링).

    Query:
      - expert_id (required)
      - phase: "pre_combine" | "post_combine" | "auto" (default: auto)
      - draft_year: override (default: state.season_year + 1)
      - limit: optional number of prospects in output
      - pool_mode: "declared" | "watch" | "auto" (default: auto)
      - watch_run_id: explicit watch run id (only for pool_mode=watch/auto fallback)
      - watch_min_prob: inclusion threshold for watch pool (declare_prob >= threshold)
    """
    eid = str(expert_id or "").strip()
    if not eid:
        raise HTTPException(status_code=400, detail="expert_id is required.")

    # draft_year default is state.season_year + 1, but allow override even if state is not ready.
    if draft_year is None:
        league_ctx = state.get_league_context_snapshot() or {}
        try:
            from_year = int(league_ctx.get("season_year") or 0)
        except Exception:
            from_year = 0
        if from_year <= 0:
            raise HTTPException(status_code=500, detail="Invalid season_year in state (draft_year not provided).")
        dy = int(from_year) + 1
    else:
        try:
            dy = int(draft_year)
        except Exception:
            raise HTTPException(status_code=400, detail="draft_year must be an integer.")
        if dy <= 0:
            raise HTTPException(status_code=400, detail="draft_year must be > 0.")

    lim: Optional[int] = None
    if limit is not None:
        try:
            lim = int(limit)
        except Exception:
            raise HTTPException(status_code=400, detail="limit must be an integer.")
        if lim <= 0:
            raise HTTPException(status_code=400, detail="limit must be > 0.")

    ph = str(phase or "auto").strip()

    try:
        from draft.expert_bigboard import generate_expert_bigboard

        db_path = state.get_db_path()
        result = generate_expert_bigboard(
            db_path=str(db_path),
            draft_year=int(dy),
            expert_id=str(eid),
            phase=str(ph),
            limit=lim,
            include_debug_axes=False,
            pool_mode=pool_mode or "auto",
            watch_run_id=watch_run_id,
            watch_min_prob=watch_min_prob,
        )
        return result
    except KeyError as e:
        # Unknown expert_id
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate expert bigboard: {e}") from e


@router.get("/api/offseason/draft/bundle")
async def api_offseason_draft_bundle(
    draft_year: Optional[int] = None,
    pool_season_year: Optional[int] = None,
    pool_limit: Optional[int] = None,
    viewer_team_id: Optional[str] = None,
):
    """현재 저장된 플랜(로터리 결과) 기반으로 드래프트 번들(턴/세션/풀) 생성."""
    league_ctx = state.get_league_context_snapshot() or {}
    try:
        from_year = int(league_ctx.get("season_year") or 0)
    except Exception:
        from_year = 0
    if from_year <= 0:
        raise HTTPException(status_code=500, detail="Invalid season_year in state.")

    dy = int(draft_year) if draft_year is not None else int(from_year) + 1
    pysy = int(pool_season_year) if pool_season_year is not None else int(from_year)

    try:
        from draft.engine import prepare_bundle_from_saved_plan

        db_path = state.get_db_path()

        # Viewer team is optional. If provided, normalize it so per-team workout visibility works.
        vt = None
        if viewer_team_id is not None:
            vt = normalize_team_id(viewer_team_id)
            if not vt:
                raise HTTPException(status_code=400, detail="Invalid viewer_team_id.")
        
        snap = state.export_full_state_snapshot()
        bundle = prepare_bundle_from_saved_plan(
            snap,
            db_path=db_path,
            draft_year=int(dy),
            pool_season_year=int(pysy),
            pool_limit=pool_limit,
            session_meta={"trigger": "api_bundle", "viewer_team_id": (str(vt) if vt else None)},
        )
        # Fog-of-war: return ONLY public payload (no ovr/attrs/potential leak, no db_path leak).
        # viewer_team_id controls whether team-private workout results are included.
        return bundle.to_public_dict(viewer_team_id=(str(vt) if vt else None))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/offseason/draft/selections/auto")
async def api_offseason_draft_selections_auto(req: DraftAutoSelectionsRequest):
    """저장된 플랜 기반으로 남은 픽을 자동 선택(draft_selections에 기록)."""
    league_ctx = state.get_league_context_snapshot() or {}
    try:
        from_year = int(league_ctx.get("season_year") or 0)
    except Exception:
        from_year = 0
    if from_year <= 0:
        raise HTTPException(status_code=500, detail="Invalid season_year in state.")
    draft_year = from_year + 1

    try:
        from draft.engine import prepare_bundle_from_saved_plan, auto_run_selections

        # Use in-game date for selection timestamps (draft_selections.selected_at).
        # Fail-loud: do NOT fall back to OS date (timeline immersion).
        try:
            in_game_date = state.get_current_date_as_date()
        except Exception as e:
            raise HTTPException(status_code=500, detail="Failed to read in-game date from state.") from e
        if not hasattr(in_game_date, "isoformat"):
            raise HTTPException(status_code=500, detail="Invalid in-game date object in state.")
        selected_at_iso = in_game_date.isoformat()

        db_path = state.get_db_path()
        snap = state.export_full_state_snapshot()
        bundle = prepare_bundle_from_saved_plan(
            snap,
            db_path=db_path,
            draft_year=int(draft_year),
            pool_season_year=int(from_year),
            pool_limit=None,
            session_meta={"trigger": "api_auto"},
        )

        team_ids = None
        if req.stop_on_user_controlled_team_ids is not None:
            cleaned = [normalize_team_id(t) for t in (req.stop_on_user_controlled_team_ids or [])]
            cleaned = [t for t in cleaned if t]
            if cleaned:
                team_ids = cleaned

        # Fail-closed: 유저팀 목록이 없으면 기본은 "멈춤".
        # (명시적으로 allow_autopick_user_team=true를 준 경우만 예외)
        if (not req.allow_autopick_user_team) and (not team_ids):
            raise ValueError(
                "stop_on_user_controlled_team_ids is required unless allow_autopick_user_team=true"
            )

        picks = auto_run_selections(
            bundle=bundle,
            selected_at_iso=str(selected_at_iso),
            max_picks=req.max_picks,
            stop_on_user_controlled_team_ids=team_ids,
            allow_autopick_user_team=bool(req.allow_autopick_user_team),
            source="draft_auto",
        )
        # Fog-of-war: return only public-safe pick payloads.
        out_picks = []
        for p in (picks or []):
            out_picks.append(p.to_public_dict() if hasattr(p, "to_public_dict") else p.to_dict())
        return {"ok": True, "draft_year": int(draft_year), "picks": out_picks}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/offseason/draft/selections/pick")
async def api_offseason_draft_selections_pick(req: DraftRecordPickRequest):
    """현재 커서(온더클락) 픽을 1개 기록(draft_selections에 저장)."""
    league_ctx = state.get_league_context_snapshot() or {}
    try:
        from_year = int(league_ctx.get("season_year") or 0)
    except Exception:
        from_year = 0
    if from_year <= 0:
        raise HTTPException(status_code=500, detail="Invalid season_year in state.")
    draft_year = from_year + 1

    try:
        from draft.engine import prepare_bundle_from_saved_plan, record_pick_and_save_selection

        # Use in-game date for selection timestamps (draft_selections.selected_at).
        # Fail-loud: do NOT fall back to OS date (timeline immersion).
        try:
            in_game_date = state.get_current_date_as_date()
        except Exception as e:
            raise HTTPException(status_code=500, detail="Failed to read in-game date from state.") from e
        if not hasattr(in_game_date, "isoformat"):
            raise HTTPException(status_code=500, detail="Invalid in-game date object in state.")
        selected_at_iso = in_game_date.isoformat()

        db_path = state.get_db_path()
        snap = state.export_full_state_snapshot()
        bundle = prepare_bundle_from_saved_plan(
            snap,
            db_path=db_path,
            draft_year=int(draft_year),
            pool_season_year=int(from_year),
            pool_limit=None,
            session_meta={"trigger": "api_pick"},
        )

        pick = record_pick_and_save_selection(
            bundle=bundle,
            prospect_temp_id=str(req.prospect_temp_id),
            selected_at_iso=str(selected_at_iso),
            source=str(req.source or "draft_user"),
            meta=(dict(req.meta) if isinstance(req.meta, dict) else None),
        )
        # Fog-of-war: scrub meta defensively (even if AI/meta accidentally includes sensitive keys).
        pick_payload = pick.to_public_dict() if hasattr(pick, "to_public_dict") else pick.to_dict()
        return {"ok": True, "draft_year": int(draft_year), "pick": pick_payload}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/offseason/draft/apply")
async def api_offseason_draft_apply(req: EmptyRequest):
    """draft_selections -> 실제 DB 적용(draft_results/roster/contract/tx), 이후 시즌 전환."""
    league_ctx = state.get_league_context_snapshot() or {}
    try:
        from_year = int(league_ctx.get("season_year") or 0)
    except Exception:
        from_year = 0
    if from_year <= 0:
        raise HTTPException(status_code=500, detail="Invalid season_year in state.")

    to_year = from_year + 1
    draft_year = int(to_year)

    try:
        db_path = state.get_db_path()

        # Hard gate: contracts offseason processing must be completed before draft apply.
        required_meta_key = f"contracts_offseason_done_{to_year}"
        with LeagueRepo(db_path) as _repo:
            _repo.init_db()
            row = _repo._conn.execute("SELECT value FROM meta WHERE key=?;", (required_meta_key,)).fetchone()
            ok = bool(row is not None and str(row["value"]) == "1")
        if not ok:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "CONTRACTS_OFFSEASON_NOT_PROCESSED",
                    "message": "Run /api/offseason/contracts/process (and decide TEAM options if required) before draft apply.",
                    "required_meta_key": str(required_meta_key),
                    "season_year": int(to_year),
                },
            )

        # Hard gate: retirement offseason processing should be completed before draft apply.
        retirement_meta_key = f"retirement_processed_{to_year}"
        with LeagueRepo(db_path) as _repo:
            _repo.init_db()
            row = _repo._conn.execute("SELECT value FROM meta WHERE key=?;", (retirement_meta_key,)).fetchone()
            retirement_ok = bool(row is not None and str(row["value"]) == "1")
        if not retirement_ok:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "RETIREMENT_OFFSEASON_NOT_PROCESSED",
                    "message": "Run /api/offseason/retirement/process before draft apply.",
                    "required_meta_key": str(retirement_meta_key),
                    "season_year": int(to_year),
                },
            )

        # 1) Apply picks (SSOT: draft_results)
        from draft.pipeline import apply_selections

        # Use in-game date for transaction logs (avoid OS date mismatch).
        # Fail-loud: do NOT fall back to OS date (timeline immersion).
        try:
            in_game_date = state.get_current_date_as_date()
        except Exception as e:
            raise HTTPException(status_code=500, detail="Failed to read in-game date from state.") from e
        if not hasattr(in_game_date, "isoformat"):
            raise HTTPException(status_code=500, detail="Invalid in-game date object in state.")
        tx_date_iso = in_game_date.isoformat()

        # Inject CapModel built from SSOT (state.trade_rules) to avoid duplicated cap math.
        try:
            from cap_model import CapModel
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"CapModel import failed: {exc}")

        trade_rules = league_ctx.get("trade_rules") if isinstance(league_ctx, dict) else None
        if not isinstance(trade_rules, dict):
            trade_rules = {}
        cap_model = CapModel.from_trade_rules(trade_rules, current_season_year=int(from_year))

        applied_count = int(
            apply_selections(
                db_path=db_path,
                draft_year=int(draft_year),
                tx_date_iso=tx_date_iso,
                cap_model=cap_model,
            )
        )

        # 2) Resolve undrafted declared players into pro routes (FA / retirement)
        from draft.undrafted import resolve_undrafted_to_pro

        undrafted_result = resolve_undrafted_to_pro(
            db_path=db_path,
            draft_year=int(draft_year),
            tx_date_iso=str(tx_date_iso),
        )

        # Mark draft completed AFTER undrafted resolution to avoid leaving DECLARED players behind.
        meta_key = f"draft_completed_{draft_year}"
        with LeagueRepo(db_path) as repo:
            repo.init_db()
            repo._conn.execute(
                "INSERT INTO meta(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value;",
                (meta_key, "1"),
            )
            repo._conn.commit()

        # 3) College offseason advance (hidden; auto after NBA draft)
        from college.service import advance_offseason as _college_advance_offseason
        _college_advance_offseason(db_path=db_path, from_season_year=int(from_year), to_season_year=int(to_year))

        # 4) Now perform the actual season transition (NO auto-offseason hooks)
        transition = state.start_new_season(int(to_year), rebuild_schedule=True)

        # Best-effort UI cache rebuild after apply + season transition.
        try:
            ui_cache_rebuild_all()
        except Exception:
            pass

        return {
            "ok": True,
            "draft_year": int(draft_year),
            "applied_count": int(applied_count),
            "undrafted": undrafted_result,
            "college_advanced_to": int(to_year),
            "season_transition": transition,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/season/start-regular-season")
async def api_start_regular_season(req: EmptyRequest):
    """오프시즌(또는 임의 시점)에서 정규시즌 시작 직전으로 날짜를 이동한다.

    IMPORTANT:
    - advance_league_until()은 current_date+1부터 진행하므로, 개막일 게임을 스킵하지 않게
      season_start '전날'로 세팅한다.
    """
    league_ctx = state.get_league_context_snapshot() or {}
    season_start = league_ctx.get("season_start")
    if not season_start:
        raise HTTPException(status_code=500, detail="season_start is missing. Schedule not initialized?")

    try:
        ss = date.fromisoformat(str(season_start))
    except ValueError:
        raise HTTPException(status_code=500, detail=f"Invalid season_start format: {season_start}")

    start_day_minus_1 = (ss - timedelta(days=1)).isoformat()
    state.set_current_date(start_day_minus_1)

    return {
        "ok": True,
        "current_date": state.get_current_date(),
        "season_start": str(season_start),
    }
