from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, Iterable

from fastapi import APIRouter, HTTPException

import state
from config import ALL_TEAM_IDS, TEAM_TO_CONF_DIV
from sim.league_sim import (
    advance_league_until,
    auto_advance_to_next_user_game_day,
    progress_next_user_game_day,
    simulate_single_game,
)
from trades.orchestration import run_trade_orchestration_tick
from state_modules.state_standings import apply_final_game, create_empty_standings_cache
from app.schemas.common import (
    AdvanceLeagueRequest,
    AutoAdvanceToNextUserGameDayRequest,
    ProgressNextUserGameDayRequest,
    SimGameRequest,
)

router = APIRouter()


def _iter_unique_game_ids(game_ids: Iterable[Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for gid in game_ids:
        s = str(gid or "").strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _apply_standings_cache_incremental_updates(*, game_ids: Iterable[Any]) -> Dict[str, int]:
    """Apply incremental standings-cache updates for the provided game_ids.

    Notes:
    - Uses current master_schedule as SSOT for final scores/status.
    - Safe to call repeatedly: duplicate applies are skipped by `applied_game_ids`.
    """
    target_ids = _iter_unique_game_ids(game_ids)
    if not target_ids:
        return {"candidates": 0, "applied": 0, "missing": 0}

    schedule = state.get_league_schedule_snapshot() or {}
    master_schedule = schedule.get("master_schedule") if isinstance(schedule, dict) else {}
    master_schedule = master_schedule if isinstance(master_schedule, dict) else {}
    by_id = master_schedule.get("by_id") if isinstance(master_schedule.get("by_id"), dict) else {}
    games = master_schedule.get("games") if isinstance(master_schedule.get("games"), list) else []
    games_by_id = {str(g.get("game_id") or ""): g for g in games if isinstance(g, dict) and g.get("game_id")}

    season_id = str(schedule.get("active_season_id") or "") or None
    cache = state.get_standings_cache_snapshot() or {}
    if not isinstance(cache, dict) or not isinstance(cache.get("records_by_team"), dict):
        cache = create_empty_standings_cache(list(ALL_TEAM_IDS), season_id=season_id)

    applied = 0
    missing = 0
    for gid in target_ids:
        g = by_id.get(gid) if isinstance(by_id, dict) else None
        if not isinstance(g, dict):
            g = games_by_id.get(gid)
        if not isinstance(g, dict):
            missing += 1
            continue
        cache, changed = apply_final_game(cache, g, TEAM_TO_CONF_DIV)
        if changed:
            applied += 1

    state.set_standings_cache(cache)
    return {"candidates": len(target_ids), "applied": int(applied), "missing": int(missing)}


def _run_trade_orchestration_for_date(*, user_team_id: str, tick_date: date) -> Dict[str, Any]:
    """Run trade orchestration for a specific in-game date and summarize outcome."""
    user_tid = str(user_team_id or "").upper().strip()
    if not user_tid:
        return {
            "ok": False,
            "error": "MISSING_USER_TEAM_ID",
            "tick_date": tick_date.isoformat(),
            "message": "user_team_id is required for daily trade orchestration",
        }

    try:
        report = run_trade_orchestration_tick(
            current_date=tick_date,
            user_team_id=user_tid,
            dry_run=False,
            validate_integrity=False,
        )
    except Exception as exc:
        return {
            "ok": False,
            "error": type(exc).__name__,
            "tick_date": tick_date.isoformat(),
            "message": "daily trade orchestration failed",
        }

    promo = getattr(report, "promotion", None)
    return {
        "ok": True,
        "tick_date": str(getattr(report, "tick_date", tick_date.isoformat())),
        "skipped": bool(getattr(report, "skipped", False)),
        "skip_reason": str(getattr(report, "skip_reason", "") or ""),
        "active_teams_count": len(getattr(report, "active_teams", []) or []),
        "user_offer_sessions_created": len(getattr(promo, "user_offer_sessions", []) or []) if promo is not None else 0,
        "executed_trade_events": len(getattr(promo, "executed_trade_events", []) or []) if promo is not None else 0,
        "errors": list(getattr(promo, "errors", []) or []) if promo is not None else [],
    }


def _iter_dates_exclusive_to_inclusive(*, from_exclusive: str, to_inclusive: str) -> list[date]:
    """Return dates in (from_exclusive, to_inclusive], with same-day fallback to [to_inclusive]."""
    start = date.fromisoformat(str(from_exclusive)[:10])
    end = date.fromisoformat(str(to_inclusive)[:10])

    if start >= end:
        return [end]

    out: list[date] = []
    d = start + timedelta(days=1)
    while d <= end:
        out.append(d)
        d += timedelta(days=1)
    return out


def _run_trade_orchestration_catchup(*, user_team_id: str, from_exclusive: str, to_inclusive: str) -> Dict[str, Any]:
    """Run orchestration once per in-game date in the requested range."""
    try:
        days = _iter_dates_exclusive_to_inclusive(from_exclusive=from_exclusive, to_inclusive=to_inclusive)
    except Exception as exc:
        return {
            "ok": False,
            "from_exclusive": str(from_exclusive),
            "to_inclusive": str(to_inclusive),
            "error": type(exc).__name__,
            "message": "invalid orchestration date range",
            "runs": [],
            "summary": {
                "requested_days": 0,
                "executed_days": 0,
                "skipped_days": 0,
                "error_days": 1,
            },
        }

    runs = [
        _run_trade_orchestration_for_date(user_team_id=user_team_id, tick_date=d)
        for d in days
    ]
    return {
        "ok": all(bool(r.get("ok")) for r in runs),
        "from_exclusive": str(from_exclusive),
        "to_inclusive": str(to_inclusive),
        "runs": runs,
        "summary": {
            "requested_days": len(days),
            "executed_days": sum(1 for r in runs if bool(r.get("ok")) and not bool(r.get("skipped"))),
            "skipped_days": sum(1 for r in runs if bool(r.get("skipped"))),
            "error_days": sum(1 for r in runs if not bool(r.get("ok"))),
        },
    }






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
        _apply_standings_cache_incremental_updates(game_ids=[result.get("game_id")])
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
    _apply_standings_cache_incremental_updates(
        game_ids=[g.get("game_id") for g in simulated if isinstance(g, dict)]
    )

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
        "trade_orchestration": _run_trade_orchestration_catchup(
            user_team_id=req.user_team_id,
            from_exclusive=prev_date,
            to_inclusive=req.target_date,
        ),
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

    game_ids: list[str] = []
    auto_ids = ((result.get("auto_advance") or {}).get("simulated_game_ids") or []) if isinstance(result, dict) else []
    if isinstance(auto_ids, list):
        game_ids.extend([str(x) for x in auto_ids])
    user_gid = (((result.get("game_day") or {}).get("user_game") or {}).get("game_id")) if isinstance(result, dict) else None
    if user_gid:
        game_ids.append(str(user_gid))
    other_ids = ((result.get("game_day") or {}).get("other_game_ids") or []) if isinstance(result, dict) else []
    if isinstance(other_ids, list):
        game_ids.extend([str(x) for x in other_ids])
    _apply_standings_cache_incremental_updates(game_ids=game_ids)

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
    result["trade_orchestration"] = _run_trade_orchestration_catchup(
        user_team_id=req.user_team_id,
        from_exclusive=prev_date,
        to_inclusive=current_after,
    )
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

    auto_ids = ((result.get("auto_advance") or {}).get("simulated_game_ids") or []) if isinstance(result, dict) else []
    _apply_standings_cache_incremental_updates(game_ids=auto_ids if isinstance(auto_ids, list) else [])

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
    result["trade_orchestration"] = _run_trade_orchestration_catchup(
        user_team_id=req.user_team_id,
        from_exclusive=prev_date,
        to_inclusive=current_after,
    )
    return result
