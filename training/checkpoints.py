from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, Mapping, Optional

from .service import apply_monthly_growth


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


def _collect_month_minutes_from_state_snapshot(
    state_snapshot: Mapping[str, Any],
    *,
    month_key: str,
) -> Dict[str, float]:
    """Aggregate minutes per player for regular-season games in month_key."""
    out: Dict[str, float] = {}

    games = state_snapshot.get("games") or []
    game_results = state_snapshot.get("game_results") or {}
    if not isinstance(games, list) or not isinstance(game_results, Mapping):
        return out

    # Filter games that belong to the month.
    game_ids = [
        str(g.get("game_id"))
        for g in games
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
                try:
                    mins = float(row.get("MIN") or 0.0)
                except Exception:
                    mins = 0.0
                if mins <= 0.0:
                    continue
                out[str(pid)] = float(out.get(str(pid), 0.0) + mins)

    return out


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


def maybe_run_monthly_growth_tick(
    *,
    db_path: str,
    game_date_iso: str,
    state_snapshot: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Run the monthly growth tick if the previous month hasn't been processed.

    Trigger strategy
    ----------------
    We process month (M-1) when we enter month (M). That way the tick can use
    *actual minutes played* in the finished month.

    Idempotency is enforced via DB meta keys.
    """
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

    # If the month is not fully simulated yet, do nothing.
    if not is_complete:
        return {
            "ok": True,
            "skipped": True,
            "reason": "month_incomplete",
            "month": month_to_process,
        }

    league = state_snapshot.get("league") or {}
    try:
        season_year = int(league.get("season_year") or 0)
    except Exception:
        season_year = 0
    if season_year <= 0:
        return {"ok": True, "skipped": True, "reason": "no_season_year"}

    minutes_by_player = _collect_month_minutes_from_state_snapshot(state_snapshot, month_key=month_to_process)

    # Apply growth.
    return apply_monthly_growth(
        db_path=str(db_path),
        season_year=int(season_year),
        month_key=str(month_to_process),
        minutes_by_player=minutes_by_player,
        now_iso=str(gd),
    )


def ensure_last_regular_month_tick(
    *,
    db_path: str,
    now_date_iso: str,
    state_snapshot: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Ensure the *last* regular-season month receives its monthly growth tick.

    Why this exists
    ---------------
    Our main trigger processes month (M-1) when entering month (M).
    That means the final regular-season month (often April) may not be processed
    if you never simulate any game in the next month (e.g., if you jump straight
    to offseason after playoffs).
    """
    if state_snapshot is None:
        import state

        state_snapshot = state.export_full_state_snapshot()

    games = state_snapshot.get("games") or []
    if not isinstance(games, list) or not games:
        return {"ok": True, "skipped": True, "reason": "no_regular_games"}

    last_month: Optional[str] = None
    for g in games:
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
        return {"ok": True, "skipped": True, "reason": "no_final_regular_games"}

    is_complete, any_games = _month_schedule_status(state_snapshot, month_key=last_month)
    if not any_games:
        return {"ok": True, "skipped": True, "reason": "no_games", "month": last_month}
    if not is_complete:
        return {"ok": True, "skipped": True, "reason": "month_incomplete", "month": last_month}

    league = state_snapshot.get("league") or {}
    try:
        season_year = int(league.get("season_year") or 0)
    except Exception:
        season_year = 0
    if season_year <= 0:
        return {"ok": True, "skipped": True, "reason": "no_season_year"}

    minutes_by_player = _collect_month_minutes_from_state_snapshot(state_snapshot, month_key=last_month)
    return apply_monthly_growth(
        db_path=str(db_path),
        season_year=int(season_year),
        month_key=str(last_month),
        minutes_by_player=minutes_by_player,
        now_iso=str(now_date_iso)[:10],
    )
