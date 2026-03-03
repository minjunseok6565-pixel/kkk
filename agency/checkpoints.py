from __future__ import annotations

"""Agency checkpoint triggers.

This file mirrors training.checkpoints:
- It decides *when* to run a monthly tick.
- It collects the minimal statistics required from GameState (state snapshot).

Integration plan
----------------
Call maybe_run_monthly_agency_tick(...) from the game simulation pipeline
(after a game is finalized) in a try/except block.

Like growth tick, this is idempotent via DB meta keys.
"""

import logging
from datetime import date
from typing import Any, Dict, Mapping, Optional, Tuple

from .config import AgencyConfig, DEFAULT_CONFIG
from .month_context import collect_month_splits
from .service import apply_monthly_agency_tick


logger = logging.getLogger(__name__)


def _prev_month_key(month_key: str) -> Optional[str]:
    """YYYY-MM -> previous YYYY-MM."""
    try:
        y, m = month_key.split("-")
        year = int(y)
        month = int(m)
    except Exception:
        return None
    if month <= 1:
        return f"{year - 1:04d}-12"
    return f"{year:04d}-{month - 1:02d}"


def _collect_month_minutes_and_games_from_state_snapshot(
    state_snapshot: Mapping[str, Any],
    *,
    month_key: str,
) -> Tuple[Dict[str, float], Dict[str, int]]:
    """Aggregate minutes + games played per player for regular-season games in month_key."""
    minutes: Dict[str, float] = {}
    games: Dict[str, int] = {}

    games_list = state_snapshot.get("games") or []
    game_results = state_snapshot.get("game_results") or {}
    if not isinstance(games_list, list) or not isinstance(game_results, Mapping):
        return minutes, games

    # Filter game_ids that belong to the month.
    game_ids = [
        str(g.get("game_id"))
        for g in games_list
        if isinstance(g, Mapping)
        and str(g.get("date") or "").startswith(str(month_key))
        and str(g.get("phase") or "regular") == "regular"
        and str(g.get("status") or "final") == "final"
        and g.get("game_id")
    ]

    for gid in game_ids:
        gr = game_results.get(gid)
        if not isinstance(gr, Mapping):
            continue
        teams = gr.get("teams") or {}
        if not isinstance(teams, Mapping):
            continue
        for _tid, team_obj in teams.items():
            if not isinstance(team_obj, Mapping):
                continue
            players = team_obj.get("players") or []
            if not isinstance(players, list):
                continue
            for row in players:
                if not isinstance(row, Mapping):
                    continue
                pid = row.get("PlayerID")
                if not pid:
                    continue
                pid_s = str(pid)
                try:
                    mins = float(row.get("MIN") or 0.0)
                except Exception:
                    mins = 0.0

                # Only count a "game played" if minutes > 0.
                if mins > 0.0:
                    games[pid_s] = int(games.get(pid_s, 0) + 1)
                    minutes[pid_s] = float(minutes.get(pid_s, 0.0) + mins)

    return minutes, games


def _collect_month_team_win_pct(
    state_snapshot: Mapping[str, Any],
    *,
    month_key: str,
) -> Dict[str, float]:
    """Compute team win% for regular-season finals in month_key."""
    games_list = state_snapshot.get("games") or []
    if not isinstance(games_list, list):
        return {}

    wins: Dict[str, int] = {}
    losses: Dict[str, int] = {}

    for g in games_list:
        if not isinstance(g, Mapping):
            continue
        if str(g.get("phase") or "regular") != "regular":
            continue
        if str(g.get("status") or "") != "final":
            continue
        d = str(g.get("date") or "")
        if not d.startswith(str(month_key)):
            continue

        home = str(g.get("home_team_id") or "").upper()
        away = str(g.get("away_team_id") or "").upper()
        if not home or not away:
            continue

        try:
            hs = int(g.get("home_score") or 0)
            as_ = int(g.get("away_score") or 0)
        except Exception:
            continue

        winner = home if hs >= as_ else away
        loser = away if winner == home else home

        wins[winner] = int(wins.get(winner, 0) + 1)
        losses[loser] = int(losses.get(loser, 0) + 1)

    out: Dict[str, float] = {}
    teams = set(wins.keys()) | set(losses.keys())
    for tid in teams:
        w = int(wins.get(tid, 0))
        l = int(losses.get(tid, 0))
        denom = w + l
        out[tid] = float(w / denom) if denom > 0 else 0.5

    return out


def _collect_month_team_games_by_date(
    state_snapshot: Mapping[str, Any],
    *,
    month_key: str,
) -> Dict[str, Dict[str, int]]:
    """Return mapping date_iso -> {team_id: games_count} for the month.

    Used by the agency service layer to synthesize DNP presence for players who
    never appear in boxscores (common when match engine omits MIN=0 rows).
    """
    games_list = state_snapshot.get("games") or []
    if not isinstance(games_list, list):
        return {}

    out: Dict[str, Dict[str, int]] = {}
    mk = str(month_key)

    for g in games_list:
        if not isinstance(g, Mapping):
            continue
        if str(g.get("phase") or "regular") != "regular":
            continue
        if str(g.get("status") or "") != "final":
            continue

        d = str(g.get("date") or "")
        if not d.startswith(mk):
            continue
        date_iso = str(d)[:10]

        home = str(g.get("home_team_id") or "").upper()
        away = str(g.get("away_team_id") or "").upper()
        if not home or not away:
            continue

        by_team = out.setdefault(date_iso, {})
        by_team[home] = int(by_team.get(home, 0) + 1)
        by_team[away] = int(by_team.get(away, 0) + 1)

    return out


def _collect_month_splits_from_state_snapshot(
    state_snapshot: Mapping[str, Any],
    *,
    month_key: str,
    cfg: AgencyConfig,
) -> Dict[str, Any]:
    """Return per-player month split objects.

    If split collection fails or produces no data despite games existing,
    callers should skip the tick rather than producing incorrect league-wide
    zero-minute evaluations.
    """
    try:
        return collect_month_splits(
            state_snapshot,
            month_key=str(month_key),
            cfg=cfg.month_context,
            phase="regular",
        )
    except Exception:
        return {}



def _month_schedule_status(state_snapshot: Mapping[str, Any], *, month_key: str) -> tuple[bool, bool]:
    """Return (is_complete, any_games).

    - any_games: schedule contains at least one regular game in that month.
    - is_complete: all those games are final.
    """
    league = state_snapshot.get("league") or {}
    ms = league.get("master_schedule") or {}
    games = ms.get("games") or []
    if not isinstance(games, list):
        return (True, False)

    any_found = False
    for g in games:
        if not isinstance(g, Mapping):
            continue
        if str(g.get("phase") or "regular") != "regular":
            continue
        d = str(g.get("date") or "")
        if not d.startswith(str(month_key)):
            continue
        any_found = True
        if str(g.get("status")) != "final":
            return (False, True)

    # If schedule has no games in that month, treat as "no-op".
    return (True, any_found)


def maybe_run_monthly_agency_tick(
    *,
    db_path: str,
    game_date_iso: str,
    state_snapshot: Optional[Mapping[str, Any]] = None,
    cfg: AgencyConfig = DEFAULT_CONFIG,
) -> Dict[str, Any]:
    """Run the monthly agency tick if the previous month hasn't been processed."""

    gd = str(game_date_iso)[:10]
    month_now = str(gd)[:7]
    month_to_process = _prev_month_key(month_now)
    if not month_to_process:
        return {"ok": True, "skipped": True, "reason": "invalid_date"}

    # Lazy state snapshot.
    if state_snapshot is None:
        import state

        state_snapshot = state.export_full_state_snapshot()

    is_complete, any_games = _month_schedule_status(state_snapshot, month_key=month_to_process)
    if not any_games:
        return {"ok": True, "skipped": True, "reason": "no_games", "month": month_to_process}

    if not is_complete:
        return {"ok": True, "skipped": True, "reason": "month_incomplete", "month": month_to_process}

    league = state_snapshot.get("league") or {}
    try:
        season_year = int(league.get("season_year") or 0)
    except Exception:
        season_year = 0
    if season_year <= 0:
        return {"ok": True, "skipped": True, "reason": "no_season_year"}

    month_splits_by_player = _collect_month_splits_from_state_snapshot(
        state_snapshot,
        month_key=month_to_process,
        cfg=cfg,
    )
    if not month_splits_by_player:
        # Safety: if we cannot attribute minutes reliably, skip rather than
        # incorrectly treating everyone as 0 minutes.
        return {"ok": True, "skipped": True, "reason": "missing_boxscore_data", "month": month_to_process}

    team_win_pct_by_team = _collect_month_team_win_pct(state_snapshot, month_key=month_to_process)
    team_games_by_date = _collect_month_team_games_by_date(state_snapshot, month_key=month_to_process)

    # Use game_time helpers to produce UTC-like timestamp.
    try:
        import game_time

        now_iso = game_time.utc_like_from_date_iso(gd)
    except Exception:
        now_iso = str(gd)

    return apply_monthly_agency_tick(
        db_path=str(db_path),
        season_year=int(season_year),
        month_key=str(month_to_process),
        month_splits_by_player=month_splits_by_player,
        team_win_pct_by_team=team_win_pct_by_team,
        team_games_by_date=team_games_by_date,
        now_iso=str(now_iso),
        cfg=cfg,
    )


def ensure_last_regular_month_agency_tick(
    *,
    db_path: str,
    now_date_iso: str,
    state_snapshot: Optional[Mapping[str, Any]] = None,
    cfg: AgencyConfig = DEFAULT_CONFIG,
) -> Dict[str, Any]:
    """Ensure the *last* regular-season month receives its agency tick.

    This mirrors training.ensure_last_regular_month_tick for parity.
    """
    if state_snapshot is None:
        import state

        state_snapshot = state.export_full_state_snapshot()

    games_list = state_snapshot.get("games") or []
    if not isinstance(games_list, list) or not games_list:
        return {"ok": True, "skipped": True, "reason": "no_regular_games"}

    last_month: Optional[str] = None
    for g in games_list:
        if not isinstance(g, Mapping):
            continue
        if str(g.get("phase") or "regular") != "regular":
            continue
        if str(g.get("status") or "") != "final":
            continue
        d = str(g.get("date") or "")
        if len(d) < 7:
            continue
        mk = d[:7]
        if last_month is None or mk > last_month:
            last_month = mk

    if not last_month:
        return {"ok": True, "skipped": True, "reason": "no_month"}

    # Process this last month if not done.
    league = state_snapshot.get("league") or {}
    try:
        season_year = int(league.get("season_year") or 0)
    except Exception:
        season_year = 0
    if season_year <= 0:
        return {"ok": True, "skipped": True, "reason": "no_season_year"}

    is_complete, any_games = _month_schedule_status(state_snapshot, month_key=last_month)
    if not any_games:
        return {"ok": True, "skipped": True, "reason": "no_games", "month": last_month}
    if not is_complete:
        return {"ok": True, "skipped": True, "reason": "month_incomplete", "month": last_month}

    month_splits_by_player = _collect_month_splits_from_state_snapshot(
        state_snapshot,
        month_key=last_month,
        cfg=cfg,
    )
    if not month_splits_by_player:
        return {"ok": True, "skipped": True, "reason": "missing_boxscore_data", "month": last_month}

    team_win_pct_by_team = _collect_month_team_win_pct(state_snapshot, month_key=last_month)
    team_games_by_date = _collect_month_team_games_by_date(state_snapshot, month_key=last_month)

    try:
        import game_time

        now_iso = game_time.utc_like_from_date_iso(str(now_date_iso)[:10])
    except Exception:
        now_iso = str(now_date_iso)[:10]

    return apply_monthly_agency_tick(
        db_path=str(db_path),
        season_year=int(season_year),
        month_key=str(last_month),
        month_splits_by_player=month_splits_by_player,
        team_win_pct_by_team=team_win_pct_by_team,
        team_games_by_date=team_games_by_date,
        now_iso=str(now_iso),
        cfg=cfg,
    )


