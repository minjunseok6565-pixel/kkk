from __future__ import annotations

import logging
import datetime as _dt
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException

import game_time
import state
from league_repo import LeagueRepo
from app.schemas.practice import (
    TeamPracticePlanRequest,
    TeamPracticePreviewRequest,
    TeamPracticeSessionRequest,
)
from app.schemas.training import PlayerTrainingPlanRequest, TeamTrainingPlanRequest

router = APIRouter()
logger = logging.getLogger(__name__)


def _resolve_practice_fallback_schemes(team_id: str) -> tuple[Optional[str], Optional[str]]:
    fb_off = None
    fb_def = None
    try:
        from sim import roster_adapter as _roster_adapter
        from matchengine_v3.tactics import canonical_defense_scheme

        cfg = _roster_adapter._build_tactics_config(None)
        _roster_adapter._apply_default_coach_preset(team_id, cfg)
        _roster_adapter._apply_coach_preset_tactics(team_id, cfg, None)
        fb_off = str(cfg.offense_scheme)
        fb_def = canonical_defense_scheme(cfg.defense_scheme)
    except Exception:
        fb_off, fb_def = (None, None)
    return fb_off, fb_def


def _build_days_to_next_game_map(team_id: str, date_from: str, date_to: str) -> Dict[str, Optional[int]]:
    start = _dt.date.fromisoformat(date_from)
    end = _dt.date.fromisoformat(date_to)
    out: Dict[str, Optional[int]] = {}
    day = start
    while day <= end:
        day_iso = day.isoformat()
        try:
            out[day_iso] = state.get_days_to_next_game(team_id=team_id, date_iso=day_iso)
        except Exception:
            logger.exception(
                "state.get_days_to_next_game failed (practice range). team=%s date=%s",
                team_id,
                day_iso,
            )
            out[day_iso] = None
        day += _dt.timedelta(days=1)
    return out








@router.get("/api/training/team/{team_id}")
async def api_get_team_training_plan(team_id: str, season_year: Optional[int] = None):
    """Get a team training plan (default if missing)."""
    db_path = state.get_db_path()
    league_ctx = state.get_league_context_snapshot() or {}
    sy = int(season_year or (league_ctx.get("season_year") or 0))
    if sy <= 0:
        raise HTTPException(status_code=500, detail="Invalid season_year in state.")

    from training.service import get_or_default_team_plan

    with LeagueRepo(db_path) as repo:
        repo.init_db()
        plan, is_default = get_or_default_team_plan(repo=repo, team_id=str(team_id).upper(), season_year=sy)
    return {"team_id": str(team_id).upper(), "season_year": sy, "plan": plan, "is_default": bool(is_default)}


@router.post("/api/training/team/set")
async def api_set_team_training_plan(req: TeamTrainingPlanRequest):
    """Set a team training plan."""
    db_path = state.get_db_path()
    league_ctx = state.get_league_context_snapshot() or {}
    sy = int(req.season_year or (league_ctx.get("season_year") or 0))
    if sy <= 0:
        raise HTTPException(status_code=500, detail="Invalid season_year in state.")

    from training.service import set_team_plan

    now_iso = state.get_current_date_as_date().isoformat()
    plan = {"focus": req.focus, "intensity": req.intensity, "weights": req.weights or {}}
    return set_team_plan(
        db_path=str(db_path),
        team_id=str(req.team_id).upper(),
        season_year=sy,
        plan=plan,
        now_iso=now_iso,
    )


@router.get("/api/training/player/{player_id}")
async def api_get_player_training_plan(player_id: str, season_year: Optional[int] = None):
    """Get a player training plan (default if missing)."""
    db_path = state.get_db_path()
    league_ctx = state.get_league_context_snapshot() or {}
    sy = int(season_year or (league_ctx.get("season_year") or 0))
    if sy <= 0:
        raise HTTPException(status_code=500, detail="Invalid season_year in state.")

    from training.service import get_or_default_player_plan

    with LeagueRepo(db_path) as repo:
        repo.init_db()
        try:
            p = repo.get_player(str(player_id))
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))
        attrs = p.get("attrs") or {}
        plan, is_default = get_or_default_player_plan(repo=repo, player_id=str(player_id), season_year=sy, attrs=attrs)
    return {"player_id": str(player_id), "season_year": sy, "plan": plan, "is_default": bool(is_default)}


@router.post("/api/training/player/set")
async def api_set_player_training_plan(req: PlayerTrainingPlanRequest):
    """Set a player training plan."""
    db_path = state.get_db_path()
    league_ctx = state.get_league_context_snapshot() or {}
    sy = int(req.season_year or (league_ctx.get("season_year") or 0))
    if sy <= 0:
        raise HTTPException(status_code=500, detail="Invalid season_year in state.")

    from training.service import set_player_plan

    now_iso = state.get_current_date_as_date().isoformat()
    plan = {"primary": req.primary, "secondary": req.secondary, "intensity": req.intensity}
    return set_player_plan(
        db_path=str(db_path),
        player_id=str(req.player_id),
        season_year=sy,
        plan=plan,
        now_iso=now_iso,
        is_user_set=True,
    )


# -------------------------------------------------------------------------
# Practice API (team sessions)
# -------------------------------------------------------------------------


@router.get("/api/practice/team/{team_id}/plan")
async def api_get_team_practice_plan(team_id: str, season_year: Optional[int] = None):
    """Get a team practice plan (default if missing)."""
    db_path = state.get_db_path()
    league_ctx = state.get_league_context_snapshot() or {}
    sy = int(season_year or (league_ctx.get("season_year") or 0))
    if sy <= 0:
        raise HTTPException(status_code=500, detail="Invalid season_year in state.")

    from practice.service import get_or_default_team_practice_plan

    with LeagueRepo(db_path) as repo:
        repo.init_db()
        plan, is_default = get_or_default_team_practice_plan(repo=repo, team_id=str(team_id).upper(), season_year=sy)
    return {"team_id": str(team_id).upper(), "season_year": sy, "plan": plan, "is_default": bool(is_default)}


@router.post("/api/practice/team/plan/set")
async def api_set_team_practice_plan(req: TeamPracticePlanRequest):
    """Set a team practice plan."""
    db_path = state.get_db_path()
    league_ctx = state.get_league_context_snapshot() or {}
    sy = int(req.season_year or (league_ctx.get("season_year") or 0))
    if sy <= 0:
        raise HTTPException(status_code=500, detail="Invalid season_year in state.")

    from practice.service import set_team_practice_plan

    now_iso = state.get_current_date_as_date().isoformat()
    plan = {"mode": req.mode}
    return set_team_practice_plan(
        db_path=str(db_path),
        team_id=str(req.team_id).upper(),
        season_year=sy,
        plan=plan,
        now_iso=now_iso,
    )


@router.get("/api/practice/team/{team_id}/session")
async def api_get_team_practice_session(
    team_id: str,
    date_iso: str,
    season_year: Optional[int] = None,
):
    """Get (and auto-resolve) a practice session for a specific date."""
    db_path = state.get_db_path()
    league_ctx = state.get_league_context_snapshot() or {}
    sy = int(season_year or (league_ctx.get("season_year") or 0))
    if sy <= 0:
        raise HTTPException(status_code=500, detail="Invalid season_year in state.")

    tid = str(team_id).upper()
    d = game_time.require_date_iso(date_iso, field="date_iso")
    now_iso = game_time.utc_like_from_date_iso(d, field="date_iso")

    # Schedule-context hint for AUTO practice AI (best-effort).
    d2g: Optional[int] = None
    try:
        d2g = state.get_days_to_next_game(team_id=tid, date_iso=d)
    except Exception:
        logger.exception("state.get_days_to_next_game failed (practice session). team=%s date=%s", tid, d)
        d2g = None

    # Best-effort fallback schemes from coach presets.
    fb_off, fb_def = _resolve_practice_fallback_schemes(tid)

    with LeagueRepo(db_path) as repo:
        repo.init_db()

        # Stable roster pid ordering for scrimmage autofill.
        roster_rows = repo.get_team_roster(tid)
        roster_pids = [str(r.get("player_id")) for r in (roster_rows or []) if r.get("player_id")]

        # Resolve inside a single transaction for determinism.
        from practice import repo as p_repo
        from practice import types as p_types
        from practice.service import resolve_practice_session

        with repo.transaction() as cur:
            raw, is_user_set = p_repo.get_team_practice_session(cur, team_id=tid, season_year=sy, date_iso=d)
            if raw is None:
                sess = resolve_practice_session(
                    cur,
                    team_id=tid,
                    season_year=sy,
                    date_iso=d,
                    fallback_off_scheme=fb_off,
                    fallback_def_scheme=fb_def,
                    roster_pids=roster_pids,
                    days_to_next_game=d2g,
                    now_iso=now_iso,
                )
                is_user_set = False
            else:
                sess = p_types.normalize_session(raw)

    return {"team_id": tid, "season_year": sy, "date_iso": d, "session": sess, "is_user_set": bool(is_user_set)}


@router.get("/api/practice/team/{team_id}/sessions")
async def api_list_team_practice_sessions(
    team_id: str,
    season_year: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
):
    """List stored practice sessions (does not auto-generate missing dates)."""
    db_path = state.get_db_path()
    league_ctx = state.get_league_context_snapshot() or {}
    sy = int(season_year or (league_ctx.get("season_year") or 0))
    if sy <= 0:
        raise HTTPException(status_code=500, detail="Invalid season_year in state.")

    from practice.service import list_team_practice_sessions

    with LeagueRepo(db_path) as repo:
        repo.init_db()
        rows = list_team_practice_sessions(
            repo=repo,
            team_id=str(team_id).upper(),
            season_year=sy,
            date_from=date_from,
            date_to=date_to,
        )
    return {"team_id": str(team_id).upper(), "season_year": sy, "sessions": rows}


@router.get("/api/practice/team/{team_id}/sessions/resolve")
async def api_resolve_team_practice_sessions(
    team_id: str,
    date_from: str,
    date_to: str,
    season_year: Optional[int] = None,
    include_games: bool = False,
    only_missing: bool = True,
    max_days: int = 42,
):
    """Resolve a date-range sessions map in one request.

    Existing stored rows are returned as-is, and missing rows can be auto-resolved/persisted.
    """
    db_path = state.get_db_path()
    league_ctx = state.get_league_context_snapshot() or {}
    sy = int(season_year or (league_ctx.get("season_year") or 0))
    if sy <= 0:
        raise HTTPException(status_code=500, detail="Invalid season_year in state.")

    tid = str(team_id).upper()
    df = game_time.require_date_iso(date_from, field="date_from")
    dt = game_time.require_date_iso(date_to, field="date_to")
    if dt < df:
        raise HTTPException(status_code=400, detail="date_to must be >= date_from")

    safe_max_days = max(1, min(int(max_days), 84))

    d2g_map = _build_days_to_next_game_map(tid, df, dt)
    skip_dates = set()
    if not include_games:
        skip_dates = {d for d, d2g in d2g_map.items() if d2g == 0}

    fb_off, fb_def = _resolve_practice_fallback_schemes(tid)

    from practice.service import resolve_team_practice_sessions_in_range

    with LeagueRepo(db_path) as repo:
        repo.init_db()
        roster_rows = repo.get_team_roster(tid)
        roster_pids = [str(r.get("player_id")) for r in (roster_rows or []) if r.get("player_id")]
        try:
            result = resolve_team_practice_sessions_in_range(
                repo=repo,
                team_id=tid,
                season_year=sy,
                date_from=df,
                date_to=dt,
                fallback_off_scheme=fb_off,
                fallback_def_scheme=fb_def,
                roster_pids=roster_pids,
                days_to_next_game_by_date=d2g_map,
                skip_dates=skip_dates,
                only_missing=bool(only_missing),
                max_days=safe_max_days,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    return {
        "team_id": tid,
        "season_year": sy,
        "date_from": df,
        "date_to": dt,
        "sessions": result.get("sessions") or {},
        "meta": result.get("meta") or {},
    }




@router.post("/api/practice/team/{team_id}/preview")
async def api_preview_team_practice_effect(team_id: str, req: TeamPracticePreviewRequest):
    """Preview practice effects for one date/session without any writes."""
    db_path = state.get_db_path()
    league_ctx = state.get_league_context_snapshot() or {}
    sy = int(req.season_year or (league_ctx.get("season_year") or 0))
    if sy <= 0:
        raise HTTPException(status_code=500, detail="Invalid season_year in state.")

    tid = str(team_id).upper()
    d = game_time.require_date_iso(req.date_iso, field="date_iso")

    from practice import config as p_cfg
    from practice import types as p_types

    session = p_types.normalize_session({
        "type": req.type,
        "offense_scheme_key": req.offense_scheme_key,
        "defense_scheme_key": req.defense_scheme_key,
        "participant_pids": req.participant_pids or [],
        "non_participant_type": req.non_participant_type,
    })

    with LeagueRepo(db_path) as repo:
        repo.init_db()
        roster_rows = repo.get_team_roster(tid)
        roster_pids = [str(r.get("player_id")) for r in (roster_rows or []) if r.get("player_id")]

    by_pid: Dict[str, Dict[str, Any]] = {}
    for pid in roster_pids:
        eff_type = p_types.effective_type_for_pid(session, pid)
        by_pid[pid] = {
            "effective_type": eff_type,
            "intensity_mult": float(p_types.intensity_for_session_type(eff_type)),
            "sharpness_delta": float(p_cfg.SHARPNESS_DELTA.get(eff_type, 0.0) or 0.0),
        }

    sess_type = str(session.get("type") or "FILM").upper()
    fam_gain = float(p_cfg.FAMILIARITY_GAIN.get(sess_type, 0.0) or 0.0)

    return {
        "team_id": tid,
        "season_year": sy,
        "date_iso": d,
        "session": session,
        "preview": {
            "intensity_mult_by_pid": by_pid,
            "sharpness_delta_by_type": dict(p_cfg.SHARPNESS_DELTA),
            "familiarity_gain": {
                "session_type": sess_type,
                "offense_gain": fam_gain if sess_type in ("OFF_TACTICS", "FILM", "SCRIMMAGE") else 0.0,
                "defense_gain": fam_gain if sess_type in ("DEF_TACTICS", "FILM", "SCRIMMAGE") else 0.0,
            },
        },
    }


@router.get("/api/readiness/team/{team_id}/familiarity")
async def api_get_team_familiarity_status(
    team_id: str,
    season_year: Optional[int] = None,
    scheme_type: Optional[str] = None,
    as_of_date: Optional[str] = None,
):
    """Read-only familiarity status for all/specific scheme types."""
    db_path = state.get_db_path()
    league_ctx = state.get_league_context_snapshot() or {}
    sy = int(season_year or (league_ctx.get("season_year") or 0))
    if sy <= 0:
        raise HTTPException(status_code=500, detail="Invalid season_year in state.")

    tid = str(team_id).upper()
    st_filter = str(scheme_type).strip().lower() if scheme_type is not None else None
    if st_filter not in (None, "offense", "defense"):
        raise HTTPException(status_code=400, detail="scheme_type must be one of: offense, defense")

    as_of = game_time.require_date_iso(as_of_date, field="as_of_date") if as_of_date else None

    from readiness import formulas as r_f
    from readiness import repo as r_repo

    with LeagueRepo(db_path) as repo:
        repo.init_db()
        with repo.transaction() as cur:
            rows = r_repo.list_team_scheme_familiarity_states(
                cur,
                team_id=tid,
                season_year=sy,
                scheme_type=st_filter,
            )

    items: List[Dict[str, Any]] = []
    for (st, sk), row in sorted(rows.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        value = float((row or {}).get("value", 50.0) or 50.0)
        last_date = (row or {}).get("last_date")
        item: Dict[str, Any] = {
            "scheme_type": st,
            "scheme_key": sk,
            "value": value,
            "last_date": last_date,
        }
        if as_of is not None:
            last_dt = r_f.parse_date_iso(last_date)
            days = 0
            if last_dt is not None:
                days = max(0, int((_dt.date.fromisoformat(as_of) - last_dt).days))
            item["value_as_of"] = float(r_f.decay_familiarity_exp(value, days=days))
            item["as_of_date"] = as_of
        items.append(item)

    return {"team_id": tid, "season_year": sy, "scheme_type": st_filter, "items": items}


@router.get("/api/readiness/team/{team_id}/sharpness")
async def api_get_team_sharpness_distribution(
    team_id: str,
    season_year: Optional[int] = None,
    as_of_date: Optional[str] = None,
    include_players: bool = False,
):
    """Read-only team sharpness distribution for roster players."""
    db_path = state.get_db_path()
    league_ctx = state.get_league_context_snapshot() or {}
    sy = int(season_year or (league_ctx.get("season_year") or 0))
    if sy <= 0:
        raise HTTPException(status_code=500, detail="Invalid season_year in state.")

    tid = str(team_id).upper()
    as_of = game_time.require_date_iso(as_of_date, field="as_of_date") if as_of_date else None

    from readiness import formulas as r_f
    from readiness import repo as r_repo

    with LeagueRepo(db_path) as repo:
        repo.init_db()
        roster_rows = repo.get_team_roster(tid)
        roster_items = [
            {"player_id": str(r.get("player_id")), "name": r.get("name")}
            for r in (roster_rows or [])
            if r.get("player_id")
        ]

        pids = [it["player_id"] for it in roster_items]
        with repo.transaction() as cur:
            sharp_rows = r_repo.get_player_sharpness_states(cur, pids, season_year=sy) if pids else {}

    values: List[float] = []
    players: List[Dict[str, Any]] = []
    as_of_dt = _dt.date.fromisoformat(as_of) if as_of else None

    for item in roster_items:
        pid = item["player_id"]
        row = sharp_rows.get(pid) or {}
        base = float(row.get("sharpness", 50.0) or 50.0)
        last_date = row.get("last_date")
        val = base
        if as_of_dt is not None:
            last_dt = r_f.parse_date_iso(last_date)
            days = max(0, int((as_of_dt - last_dt).days)) if last_dt is not None else 0
            val = float(r_f.decay_sharpness_linear(base, days=days))
        values.append(val)
        if include_players:
            players.append({
                "player_id": pid,
                "name": item.get("name"),
                "sharpness": base,
                "sharpness_as_of": val if as_of_dt is not None else None,
                "last_date": last_date,
            })

    n = len(values)
    avg = float(sum(values) / n) if n > 0 else 0.0
    vmin = float(min(values)) if n > 0 else 0.0
    vmax = float(max(values)) if n > 0 else 0.0

    buckets = {"0_39": 0, "40_49": 0, "50_59": 0, "60_69": 0, "70_plus": 0}
    for v in values:
        if v < 40:
            buckets["0_39"] += 1
        elif v < 50:
            buckets["40_49"] += 1
        elif v < 60:
            buckets["50_59"] += 1
        elif v < 70:
            buckets["60_69"] += 1
        else:
            buckets["70_plus"] += 1

    return {
        "team_id": tid,
        "season_year": sy,
        "as_of_date": as_of,
        "distribution": {
            "count": n,
            "avg": avg,
            "min": vmin,
            "max": vmax,
            "low_sharp_count": sum(1 for v in values if v < 45.0),
            "buckets": buckets,
        },
        "players": players if include_players else None,
    }


@router.post("/api/practice/team/session/set")
async def api_set_team_practice_session(req: TeamPracticeSessionRequest):
    """Set a daily practice session (user-authored)."""
    db_path = state.get_db_path()
    league_ctx = state.get_league_context_snapshot() or {}
    sy = int(req.season_year or (league_ctx.get("season_year") or 0))
    if sy <= 0:
        raise HTTPException(status_code=500, detail="Invalid season_year in state.")

    from practice.service import set_team_practice_session

    now_iso = state.get_current_date_as_date().isoformat()

    session = {
        "type": req.type,
        "offense_scheme_key": req.offense_scheme_key,
        "defense_scheme_key": req.defense_scheme_key,
        "participant_pids": req.participant_pids or [],
        "non_participant_type": req.non_participant_type,
    }

    try:
        d = game_time.require_date_iso(req.date_iso, field="date_iso")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return set_team_practice_session(
        db_path=str(db_path),
        team_id=str(req.team_id).upper(),
        season_year=sy,
        date_iso=d,
        session=session,
        now_iso=now_iso,
        is_user_set=True,
    )
