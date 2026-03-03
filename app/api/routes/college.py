from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException

import state
from college.ui import (
    get_college_draft_pool,
    get_college_meta,
    get_college_player_detail,
    get_college_team_cards,
    get_college_team_detail,
    list_college_players,
)
from app.schemas.draft import DraftWatchRecomputeRequest

router = APIRouter()



@router.get("/api/college/meta")
async def api_college_meta():
    try:
        return get_college_meta()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/college/teams")
async def api_college_teams(season_year: Optional[int] = None):
    try:
        return get_college_team_cards(season_year=season_year)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/college/team-detail/{college_team_id}")
async def api_college_team_detail(
    college_team_id: str,
    season_year: Optional[int] = None,
    include_attrs: bool = False,
):
    try:
        return get_college_team_detail(
            college_team_id,
            season_year=season_year,
            include_attrs=include_attrs,
        )
    except ValueError as e:
        msg = str(e)
        status = 404 if "not found" in msg.lower() else 400
        raise HTTPException(status_code=status, detail=msg)


@router.get("/api/college/players")
async def api_college_players(
    season_year: Optional[int] = None,
    status: Optional[str] = None,
    college_team_id: Optional[str] = None,
    draft_year: Optional[int] = None,
    declared_only: bool = False,
    q: Optional[str] = None,
    sort: str = "pts",
    order: str = "desc",
    include_attrs: bool = False,
    include_decision: bool = False,
    limit: int = 200,
    offset: int = 0,
):
    try:
        return list_college_players(
            season_year=season_year,
            status=status,
            college_team_id=college_team_id,
            draft_year=draft_year,
            declared_only=declared_only,
            q=q,
            sort=sort,
            order=order,
            include_attrs=include_attrs,
            include_decision=include_decision,
            limit=limit,
            offset=offset,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/college/player/{player_id}")
async def api_college_player(
    player_id: str,
    draft_year: Optional[int] = None,
    include_stats_history: bool = True,
):
    try:
        return get_college_player_detail(
            player_id,
            draft_year=draft_year,
            include_stats_history=include_stats_history,
        )
    except ValueError as e:
        msg = str(e)
        status = 404 if "not found" in msg.lower() else 400
        raise HTTPException(status_code=status, detail=msg)


@router.get("/api/college/draft-pool/{draft_year}")
async def api_college_draft_pool(
    draft_year: int,
    season_year: Optional[int] = None,
    limit: Optional[int] = None,
    pool_mode: Optional[str] = "auto",
    watch_run_id: Optional[str] = None,
    watch_min_prob: Optional[float] = None,
):
    try:
        return get_college_draft_pool(
            draft_year,
            season_year=season_year,
            limit=limit,
            pool_mode=pool_mode or "auto",
            watch_run_id=watch_run_id,
            watch_min_prob=watch_min_prob,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/college/draft-watch/recompute")
async def api_college_draft_watch_recompute(req: DraftWatchRecomputeRequest):
    """(Dev/Admin) Recompute a pre-declaration watch snapshot for a given draft_year/period.

    This writes:
      - draft_watch_runs
      - draft_watch_probs

    It does NOT affect the declared pool (college_draft_entries).
    """
    # draft_year default: state.season_year + 1
    if req.draft_year is None:
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
            dy = int(req.draft_year)
        except Exception:
            raise HTTPException(status_code=400, detail="draft_year must be an integer.")
        if dy <= 0:
            raise HTTPException(status_code=400, detail="draft_year must be > 0.")

    # as_of_date default: current in-game date
    as_of = str(req.as_of_date or state.get_current_date_as_date().isoformat())
    period_key = str(req.period_key or as_of[:7])

    # season_year default: draft_year - 1
    sy = int(req.season_year) if req.season_year is not None else (dy - 1)
    if sy <= 0:
        raise HTTPException(status_code=400, detail="season_year must be > 0.")

    min_prob = float(req.min_inclusion_prob) if req.min_inclusion_prob is not None else 0.35
    force = bool(req.force)

    try:
        from college.service import recompute_draft_watch_run

        db_path = state.get_db_path()
        return recompute_draft_watch_run(
            str(db_path),
            draft_year=int(dy),
            as_of_date=as_of,
            period_key=period_key,
            season_year=int(sy),
            min_inclusion_prob=float(min_prob),
            force=force,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to recompute draft watch run: {e}") from e
