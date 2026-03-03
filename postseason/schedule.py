from __future__ import annotations

"""Postseason scheduling helpers.

We keep the legacy NBA 2-2-1-1-1 home/away pattern and a simple rest-day policy:
- If the next game is at the same location as previous => +1 day
- If travel (home/away flips) => +2 days

This mirrors the original implementation but is factored into reusable helpers.
"""

from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

import state

# Higher-seed home-court pattern for a best-of-7 series.
HOME_PATTERN_7: List[bool] = [True, True, False, False, True, False, True]


def safe_date_fromisoformat(date_str: Optional[str]) -> Optional[date]:
    if not date_str:
        return None
    try:
        return date.fromisoformat(str(date_str)[:10])
    except Exception:
        return None


def regular_season_end_date() -> date:
    """Infer regular season end date from master schedule (SSOT).

    Fallbacks:
    - league.season_start + 180 days
    - current in-game date
    """
    snap = state.export_full_state_snapshot() or {}
    league = snap.get("league") or {}
    ms = league.get("master_schedule") or {}
    by_date = ms.get("by_date") or {}

    latest: Optional[date] = None
    if isinstance(by_date, dict):
        for ds in by_date.keys():
            parsed = safe_date_fromisoformat(str(ds))
            if parsed and (latest is None or parsed > latest):
                latest = parsed

    if latest:
        return latest

    season_start = safe_date_fromisoformat(league.get("season_start"))
    if season_start:
        return season_start + timedelta(days=180)

    return state.get_current_date_as_date()


def play_in_schedule_window() -> Tuple[date, date]:
    """Return (start_date, final_date) for the play-in window."""
    season_end = regular_season_end_date()
    start = season_end + timedelta(days=2)
    final_day = start + timedelta(days=2)
    return start, final_day


def play_in_end_date(play_in_state: Dict[str, Any]) -> Optional[date]:
    latest: Optional[date] = None
    for conf_state in (play_in_state or {}).values():
        matchups = conf_state.get("matchups") or {}
        for key in ("seven_vs_eight", "nine_vs_ten", "final"):
            d = safe_date_fromisoformat((matchups.get(key) or {}).get("date"))
            if d and (latest is None or d > latest):
                latest = d
    return latest


def round_latest_end(series_list: List[Dict[str, Any]]) -> Optional[date]:
    latest: Optional[date] = None
    for s in series_list or []:
        games = s.get("games") or []
        if not games:
            continue
        d = safe_date_fromisoformat(games[-1].get("date"))
        if d and (latest is None or d > latest):
            latest = d
    return latest


def next_round_start(series_list: List[Dict[str, Any]], buffer_days: int = 2) -> Optional[str]:
    latest = round_latest_end(series_list)
    if not latest:
        return None
    return (latest + timedelta(days=int(buffer_days))).isoformat()


def rest_days(prev_home_flag: bool, next_home_flag: bool) -> int:
    return 1 if bool(prev_home_flag) == bool(next_home_flag) else 2


def compute_series_next_game_date(series: Dict[str, Any], game_idx: int) -> str:
    """Compute the next game date for a given series at game index (0-based)."""
    if int(game_idx) <= 0:
        # Game 1: series start date.
        return str(series.get("start_date") or state.get_current_date_as_date().isoformat())

    games = series.get("games") or []
    last_game = games[-1] if games else {}
    last_date = safe_date_fromisoformat(last_game.get("date")) or state.get_current_date_as_date()

    prev_home_flag = HOME_PATTERN_7[game_idx - 1]
    next_home_flag = HOME_PATTERN_7[game_idx]
    days = rest_days(prev_home_flag, next_home_flag)
    return (last_date + timedelta(days=days)).isoformat()


def assign_round_openers(
    start_date_iso: str,
    series_ids: List[str],
    *,
    max_games_per_day: int = 4,
) -> Dict[str, str]:
    """Distribute Game 1 dates across 1~N days to avoid overloading a single day.

    This is optional UX sugar: existing UI doesn't require it.
    """
    sd = safe_date_fromisoformat(start_date_iso) or state.get_current_date_as_date()
    max_games_per_day = max(1, int(max_games_per_day))

    mapping: Dict[str, str] = {}
    day_offset = 0
    slot = 0
    for sid in series_ids:
        mapping[str(sid)] = (sd + timedelta(days=day_offset)).isoformat()
        slot += 1
        if slot >= max_games_per_day:
            slot = 0
            day_offset += 1
    return mapping
