from __future__ import annotations

from fastapi import APIRouter, HTTPException

import state
from sim.league_sim import advance_league_until, simulate_single_game
from app.schemas.common import AdvanceLeagueRequest, SimGameRequest

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

    # 2차: 월별 대학 스탯 스냅샷(변동성 모델) + watch-run(사전 빅보드) 체크포인트 갱신
    try:
        from college.service import run_monthly_watch_and_stats_checkpoints

        college_checkpoints = run_monthly_watch_and_stats_checkpoints(
            str(db_path),
            from_date=str(prev_date),
            to_date=str(req.target_date),
            min_inclusion_prob=0.35,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"college monthly checkpoints failed: {e}") from e

    # 3차: 월별 스카우팅 리포트 체크포인트(유저 선택 기반)
    # - ACTIVE assignment가 없으면 no-op이어야 한다.
    # - 월말 기준 14일 이내 배정된 스카우터는 해당 월 리포트를 작성하지 않는다.
    try:
        from scouting.service import run_monthly_scouting_checkpoints

        scouting_checkpoints = run_monthly_scouting_checkpoints(
            str(db_path),
            from_date=str(prev_date),
            to_date=str(req.target_date),
            api_key=req.apiKey,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"scouting monthly checkpoints failed: {e}") from e
    
    return {
        "target_date": req.target_date,
        "simulated_count": len(simulated),
        "simulated_games": simulated,
        "college_checkpoints": college_checkpoints,
        "scouting_checkpoints": scouting_checkpoints,
    }
