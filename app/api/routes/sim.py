from __future__ import annotations

from fastapi import APIRouter, HTTPException

import state
from sim.league_sim import (
    advance_league_until,
    auto_advance_to_next_user_game_day,
    progress_next_user_game_day,
    simulate_single_game,
)
from app.schemas.common import (
    AdvanceLeagueRequest,
    AutoAdvanceToNextUserGameDayRequest,
    ProgressNextUserGameDayRequest,
    SimGameRequest,
)

router = APIRouter()






@router.post("/api/simulate-game")
async def api_simulate_game(req: SimGameRequest):
    """matchengine_v3를 사용해 한 경기를 시뮬레이션한다.

    NOTE (SSOT 계약):
    - Home/Away SSOT는 league_sim.simulate_single_game 내부에서 GameContext로 생성/주입된다.
    - server는 엔진을 직접 호출하지 않으며(직접 호출 금지), 결과는 어댑터+validator 관문을 통과한 V2만 반환한다.
    """
    try:
        result = simulate_single_game(
            home_team_id=req.home_team_id,
            away_team_id=req.away_team_id,
            game_date=req.game_date,
            home_tactics=req.home_tactics,
            away_tactics=req.away_tactics,
        )
        return result
    except ValueError as e:
        # 팀을 찾지 못한 경우 등
        raise HTTPException(status_code=404, detail=str(e))


# -------------------------------------------------------------------------
# 리그 자동 진행 API (다른 팀 경기 일괄 시뮬레이션)
# -------------------------------------------------------------------------
@router.post("/api/advance-league")
async def api_advance_league(req: AdvanceLeagueRequest):
    """target_date까지 (유저 팀 경기를 제외한) 리그 전체 경기를 자동 시뮬레이션."""
    prev_date = state.get_current_date_as_date().isoformat()
    try:
        simulated = advance_league_until(
            target_date_str=req.target_date,
            user_team_id=req.user_team_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    db_path = state.get_db_path()

    college_checkpoints, scouting_checkpoints = _run_monthly_checkpoints(
        db_path=str(db_path),
        from_date=str(prev_date),
        to_date=str(req.target_date),
        api_key=req.apiKey,
    )

    return {
        "target_date": req.target_date,
        "simulated_count": len(simulated),
        "simulated_games": simulated,
        "college_checkpoints": college_checkpoints,
        "scouting_checkpoints": scouting_checkpoints,
    }


def _run_monthly_checkpoints(*, db_path: str, from_date: str, to_date: str, api_key: str | None):

    try:
        from college.service import run_monthly_watch_and_stats_checkpoints

        college_checkpoints = run_monthly_watch_and_stats_checkpoints(
            str(db_path),
            from_date=str(from_date),
            to_date=str(to_date),
            min_inclusion_prob=0.35,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"college monthly checkpoints failed: {e}") from e

    try:
        from scouting.service import run_monthly_scouting_checkpoints

        scouting_checkpoints = run_monthly_scouting_checkpoints(
            str(db_path),
            from_date=str(from_date),
            to_date=str(to_date),
            api_key=api_key,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"scouting monthly checkpoints failed: {e}") from e
    
    return college_checkpoints, scouting_checkpoints


@router.post("/api/game/progress-next-user-game-day")
async def api_progress_next_user_game_day(req: ProgressNextUserGameDayRequest):
    prev_date = state.get_current_date_as_date().isoformat()
    try:
        result = progress_next_user_game_day(req.user_team_id, mode=req.mode)
    except ValueError as e:
        msg = str(e)
        if msg.startswith("INVALID_MODE"):
            raise HTTPException(status_code=400, detail=msg)
        if msg.startswith("NO_NEXT_USER_GAME"):
            raise HTTPException(status_code=409, detail=msg)
        if msg.startswith("USER_GAME_NOT_TODAY"):
            raise HTTPException(status_code=409, detail=msg)
        if msg.startswith("USER_GAME_ALREADY_FINAL"):
            raise HTTPException(status_code=409, detail=msg)
        raise HTTPException(status_code=400, detail=msg)

    current_after = state.get_current_date_as_date().isoformat()
    db_path = state.get_db_path()
    college_checkpoints, scouting_checkpoints = _run_monthly_checkpoints(
        db_path=str(db_path),
        from_date=str(prev_date),
        to_date=str(current_after),
        api_key=req.apiKey,
    )
    result["college_checkpoints"] = college_checkpoints
    result["scouting_checkpoints"] = scouting_checkpoints
    return result


@router.post("/api/game/auto-advance-to-next-user-game-day")
async def api_auto_advance_to_next_user_game_day(req: AutoAdvanceToNextUserGameDayRequest):
    prev_date = state.get_current_date_as_date().isoformat()
    try:
        result = auto_advance_to_next_user_game_day(req.user_team_id)
    except ValueError as e:
        msg = str(e)
        if msg.startswith("NO_NEXT_USER_GAME"):
            raise HTTPException(status_code=409, detail=msg)
        raise HTTPException(status_code=400, detail=msg)

    current_after = state.get_current_date_as_date().isoformat()
    db_path = state.get_db_path()
    college_checkpoints, scouting_checkpoints = _run_monthly_checkpoints(
        db_path=str(db_path),
        from_date=str(prev_date),
        to_date=str(current_after),
        api_key=req.apiKey,
    )
    result["college_checkpoints"] = college_checkpoints
    result["scouting_checkpoints"] = scouting_checkpoints
    return result
