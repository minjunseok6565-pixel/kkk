from __future__ import annotations

from fastapi import APIRouter, HTTPException

import state
from postseason.director import (
    auto_advance_current_round,
    advance_my_team_one_game,
    build_postseason_field,
    initialize_postseason,
    play_my_team_play_in_game,
    reset_postseason_state,
)
from app.schemas.common import EmptyRequest, PostseasonSetupRequest

router = APIRouter()




@router.get("/api/postseason/field")
async def api_postseason_field():
    return build_postseason_field()


@router.get("/api/postseason/state")
async def api_postseason_state():
    return state.get_postseason_snapshot()


@router.post("/api/postseason/reset")
async def api_postseason_reset():
    return reset_postseason_state()


@router.post("/api/postseason/setup")
async def api_postseason_setup(req: PostseasonSetupRequest):
    try:
        return initialize_postseason(req.my_team_id, use_random_field=req.use_random_field)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/postseason/play-in/my-team-game")
async def api_play_in_my_team_game(req: EmptyRequest):
    try:
        return play_my_team_play_in_game()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/postseason/playoffs/advance-my-team-game")
async def api_playoffs_advance_my_team_game(req: EmptyRequest):
    try:
        return advance_my_team_one_game()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/postseason/playoffs/auto-advance-round")
async def api_playoffs_auto_advance_round(req: EmptyRequest):
    try:
        return auto_advance_current_round()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


 # -------------------------------------------------------------------------
# 시즌 전환 (오프시즌 진입 / 정규시즌 시작)
