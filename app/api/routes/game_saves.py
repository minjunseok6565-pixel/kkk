from __future__ import annotations

from fastapi import APIRouter, HTTPException

from save_service import (
    SaveError,
    create_new_game,
    get_save_slot_detail,
    list_save_slots,
    load_game,
    save_game,
    set_save_user_team,
)
import state
from app.schemas.game_save import GameLoadRequest, GameNewRequest, GameSaveRequest, GameSetUserTeamRequest

router = APIRouter()








@router.post("/api/game/new")
async def api_game_new(req: GameNewRequest):
    try:
        return create_new_game(
            slot_name=req.slot_name,
            slot_id=req.slot_id,
            season_year=req.season_year,
            user_team_id=req.user_team_id,
            overwrite_if_exists=bool(req.overwrite_if_exists),
        )
    except SaveError as exc:
        msg = str(exc)
        status = 409 if "already exists" in msg else 400
        raise HTTPException(status_code=status, detail=msg)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to create new game: {exc}")


@router.post("/api/game/save")
async def api_game_save(req: GameSaveRequest):
    try:
        return save_game(slot_id=req.slot_id, save_name=req.save_name, note=req.note)
    except SaveError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to save game: {exc}")


@router.get("/api/game/saves")
async def api_game_saves():
    try:
        return list_save_slots()
    except SaveError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to list saves: {exc}")


@router.get("/api/game/saves/{slot_id}")
async def api_game_save_detail(slot_id: str, strict: bool = False):
    try:
        return get_save_slot_detail(slot_id=slot_id, strict=bool(strict))
    except SaveError as exc:
        msg = str(exc)
        status = 404 if "not found" in msg else 400
        raise HTTPException(status_code=status, detail=msg)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to get save detail: {exc}")


@router.post("/api/game/load")
async def api_game_load(req: GameLoadRequest):
    try:
        return load_game(
            slot_id=req.slot_id,
            strict=bool(req.strict),
            expected_save_version=req.expected_save_version,
        )
    except SaveError as exc:
        msg = str(exc)
        status = 404 if "not found" in msg else 409 if "mismatch" in msg else 400
        raise HTTPException(status_code=status, detail=msg)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to load game: {exc}")




@router.post("/api/game/set-user-team")
async def api_game_set_user_team(req: GameSetUserTeamRequest):
    try:
        return set_save_user_team(slot_id=req.slot_id, user_team_id=req.user_team_id)
    except SaveError as exc:
        msg = str(exc)
        status = 404 if "not found" in msg else 400
        raise HTTPException(status_code=status, detail=msg)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to set user team: {exc}")


@router.get("/api/debug/schedule-summary")
async def debug_schedule_summary():
    """마스터 스케줄 생성/검증용 디버그 엔드포인트."""
    return state.get_schedule_summary()
