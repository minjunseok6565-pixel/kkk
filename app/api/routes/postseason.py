from __future__ import annotations

import random

from fastapi import APIRouter, HTTPException

import state
from config import TEAM_TO_CONF_DIV
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


@router.post("/api/dev/postseason/fast-resolve")
async def api_dev_postseason_fast_resolve(req: PostseasonSetupRequest):
    """DEV 전용: 포스트시즌 최소 정보(16시드 + 챔피언)를 랜덤 생성한다.

    목적:
    - /api/season/enter-offseason 진입 조건(champion) 충족
    - 드래프트 로터리/오더 계산에 필요한 playoff 16팀 정보(seeds)만 빠르게 제공

    주의:
    - 실제 경기 시뮬레이션/스케줄 ingest를 수행하지 않는다.
    """
    my_team_id = str(req.my_team_id or "").upper()
    if not my_team_id:
        raise HTTPException(status_code=400, detail="Invalid my_team_id.")

    league_ctx = state.get_league_context_snapshot() or {}
    try:
        season_year = int(league_ctx.get("season_year") or 0)
    except Exception:
        season_year = 0
    if season_year <= 0:
        raise HTTPException(status_code=500, detail="Invalid season_year in state.")

    east_pool = [tid for tid, meta in TEAM_TO_CONF_DIV.items() if str((meta or {}).get("conference") or "") == "East"]
    west_pool = [tid for tid, meta in TEAM_TO_CONF_DIV.items() if str((meta or {}).get("conference") or "") == "West"]
    if len(east_pool) < 8 or len(west_pool) < 8:
        raise HTTPException(status_code=500, detail="Invalid conference team pool for postseason fast resolve.")

    east_seeded = random.sample(east_pool, 8)
    west_seeded = random.sample(west_pool, 8)

    # Keep user's team inside playoff field for UX consistency when possible.
    if my_team_id in east_pool and my_team_id not in east_seeded:
        east_seeded[-1] = my_team_id
    if my_team_id in west_pool and my_team_id not in west_seeded:
        west_seeded[-1] = my_team_id

    east_seeds = {i + 1: {"team_id": tid, "seed": i + 1, "conference": "east"} for i, tid in enumerate(east_seeded)}
    west_seeds = {i + 1: {"team_id": tid, "seed": i + 1, "conference": "west"} for i, tid in enumerate(west_seeded)}

    playoff_teams = east_seeded + west_seeded
    champion = random.choice(playoff_teams)

    state.postseason_reset()
    state.postseason_set_my_team_id(my_team_id)
    state.postseason_set_playoffs(
        {
            "season_year": int(season_year),
            "current_round": "Finals",
            "seeds": {"east": east_seeds, "west": west_seeds},
            "bracket": {},
            "meta": {"mode": "DEV_FAST_RESOLVE"},
        }
    )
    state.postseason_set_champion(champion)

    return {
        "ok": True,
        "mode": "DEV_FAST_RESOLVE",
        "season_year": int(season_year),
        "my_team_id": my_team_id,
        "champion": champion,
        "playoff_teams": playoff_teams,
        "postseason": state.get_postseason_snapshot(),
    }


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
