from __future__ import annotations

"""Postseason director (public API).

This file is the *only* place that mutates state for postseason flows.

Server endpoints should import and call functions from this module (or `postseason` package) directly.
"""

from copy import deepcopy
from datetime import timedelta
import logging
from typing import Any, Dict, Optional, Tuple

import state

from . import ids
from . import seeding
from . import schedule
from . import play_in as play_in_mod
from . import bracket as bracket_mod
from . import reset as reset_mod

logger = logging.getLogger(__name__)


def _ensure_postseason_state() -> Dict[str, Any]:
    postseason = state.get_postseason_snapshot()
    if not isinstance(postseason, dict):
        state.postseason_reset()
        postseason = state.get_postseason_snapshot()
    return postseason


def build_postseason_field() -> Dict[str, Any]:
    """Build postseason field from current standings and store in state."""
    field = seeding.build_postseason_field_from_standings()
    state.postseason_set_field(field)
    return field


def reset_postseason_state() -> Dict[str, Any]:
    """Reset postseason state + caches (safe)."""
    return reset_mod.reset_postseason_state()


def _find_my_seed(field: Dict[str, Any], my_team_id: str) -> Tuple[Optional[str], Optional[int]]:
    my_team_id = str(my_team_id).upper()
    for conf_key in ("east", "west"):
        conf_field = field.get(conf_key, {}) or {}
        for entry in (conf_field.get("auto_bids") or []) + (conf_field.get("play_in") or []):
            if str(entry.get("team_id") or "").upper() == my_team_id:
                seed = entry.get("seed")
                return conf_key, int(seed) if isinstance(seed, int) else None
    return None, None


def _build_playoff_seeds(field: Dict[str, Any], play_in_state: Dict[str, Any]) -> Dict[str, Dict[int, Dict[str, Any]]]:
    seeds_for_bracket: Dict[str, Dict[int, Dict[str, Any]]] = {"east": {}, "west": {}}
    for conf_key in ("east", "west"):
        conf_field = field.get(conf_key, {}) or {}
        conf_seeds = {
            int(entry["seed"]): dict(entry)
            for entry in conf_field.get("auto_bids", []) or []
            if isinstance(entry.get("seed"), int)
        }

        play_conf = (play_in_state or {}).get(conf_key) or {}
        seed7 = play_conf.get("seed7")
        seed8 = play_conf.get("seed8")

        if seed7:
            seed7_fixed = dict(seed7)
            seed7_fixed["seed"] = 7
            conf_seeds[7] = seed7_fixed
        if seed8:
            seed8_fixed = dict(seed8)
            seed8_fixed["seed"] = 8
            conf_seeds[8] = seed8_fixed

        seeds_for_bracket[conf_key] = conf_seeds
    return seeds_for_bracket


def _maybe_start_playoffs_from_play_in(field: Dict[str, Any], play_in_state: Dict[str, Any]) -> None:
    """If play-in complete, initialize playoffs bracket and store in state."""
    if not field or not play_in_state:
        return
    if not play_in_mod.is_play_in_complete(play_in_state):
        return

    season_year = ids.current_season_year()
    seeds = _build_playoff_seeds(field, play_in_state)

    # Determine playoff start date: after play-in end + 3 days (legacy).
    post = _ensure_postseason_state()
    play_in_end = schedule.safe_date_fromisoformat(post.get("play_in_end_date")) or schedule.play_in_end_date(play_in_state)
    if play_in_end is None:
        play_in_end = state.get_current_date_as_date()
    playoffs_start = play_in_end + timedelta(days=3)

    # Preserve recorded play-in window if present.
    play_in_start_date = post.get("play_in_start_date")
    play_in_end_date = post.get("play_in_end_date") or (play_in_end.isoformat() if play_in_end else None)
    state.postseason_set_dates(play_in_start_date, play_in_end_date, playoffs_start.isoformat())

    playoffs_state = bracket_mod.initialize_playoffs(
        season_year=int(season_year),
        seeds_by_conf=seeds,
        start_date=playoffs_start.isoformat(),
    )
    state.postseason_set_playoffs(playoffs_state)


def initialize_postseason(my_team_id: str, use_random_field: bool = False) -> Dict[str, Any]:
    """Initialize play-in + (later) playoffs state for the given user team."""
    reset_mod.reset_postseason_state()

    my_team_id = str(my_team_id).upper()
    state.postseason_set_my_team_id(my_team_id)

    field = seeding.build_random_postseason_field(my_team_id) if use_random_field else seeding.build_postseason_field_from_standings()
    state.postseason_set_field(field)

    season_year = ids.current_season_year()
    start_dt, final_dt = schedule.play_in_schedule_window()
    start_iso, final_iso = start_dt.isoformat(), final_dt.isoformat()

    play_in_state = play_in_mod.build_play_in_state(
        field,
        season_year=int(season_year),
        start_date=start_iso,
        final_date=final_iso,
    )

    # Save initial play-in state and dates.
    state.postseason_set_play_in(play_in_state)
    state.postseason_set_dates(start_iso, final_iso, None)

    my_conf, my_seed = _find_my_seed(field, my_team_id)

    # Auto-simulate play-in games not involving user team (legacy behavior).
    for conf_key in ("east", "west"):
        conf_state = play_in_state.get(conf_key) or {}
        if my_seed is not None and my_seed <= 6:
            # User team not in play-in. Simulate everything.
            play_in_mod.auto_play_in_conf(conf_state, my_team_id=None)
        else:
            # If this is the user's conference, skip user's games.
            skip = my_team_id if conf_key == my_conf else None
            play_in_mod.auto_play_in_conf(conf_state, my_team_id=skip)
        play_in_state[conf_key] = conf_state

    state.postseason_set_play_in(play_in_state)

    # If play-in is already complete (e.g., user is top-6), start playoffs now.
    _maybe_start_playoffs_from_play_in(field, play_in_state)

    return state.get_postseason_snapshot()


def play_my_team_play_in_game() -> Dict[str, Any]:
    post = _ensure_postseason_state()
    my_team_id = post.get("my_team_id")
    play_in_state = deepcopy(post.get("play_in"))

    if not my_team_id or not play_in_state:
        raise ValueError("Play-in state is not initialized with a user team")

    updated, did_play = play_in_mod.play_my_team_next_game(play_in_state, str(my_team_id))
    if not did_play:
        raise ValueError("No pending play-in game for the user team")

    state.postseason_set_play_in(updated)

    # If this completes the play-in, initialize playoffs.
    field = post.get("field") or {}
    _maybe_start_playoffs_from_play_in(field, updated)

    return state.get_postseason_snapshot()


def advance_my_team_one_game() -> Dict[str, Any]:
    post = _ensure_postseason_state()
    my_team_id = post.get("my_team_id")
    playoffs_state = deepcopy(post.get("playoffs"))

    if not my_team_id or not playoffs_state:
        raise ValueError("Playoffs are not initialized with a user team")

    bracket = playoffs_state.get("bracket", {}) or {}
    round_name = playoffs_state.get("current_round", "Conference Quarterfinals")

    my_series = bracket_mod.find_my_series(playoffs_state, str(my_team_id))
    if not my_series:
        raise ValueError("User team is not in an active playoff series")
    if bracket_mod.is_series_finished(my_series):
        raise ValueError("User team series has already finished")

    # 1) Simulate user's game
    bracket_mod.simulate_one_series_game(my_series)

    # 2) Simulate one game for every other series in the round (league-wide progress)
    my_series_id = my_series.get("series_id")
    for series in bracket_mod.round_series(bracket, str(round_name)):
        if not series:
            continue
        if my_series_id and series.get("series_id") == my_series_id:
            continue
        if bracket_mod.is_series_finished(series):
            continue
        bracket_mod.simulate_one_series_game(series)

    state.postseason_set_playoffs(playoffs_state)

    # 3) Advance round if ready, possibly decide champion
    season_year = ids.current_season_year()
    updated_playoffs, champion = bracket_mod.advance_round_if_ready(playoffs_state, season_year=int(season_year))
    state.postseason_set_playoffs(updated_playoffs)
    if champion:
        state.postseason_set_champion(champion)

    return state.get_postseason_snapshot()


def auto_advance_current_round() -> Dict[str, Any]:
    post = _ensure_postseason_state()
    playoffs_state = deepcopy(post.get("playoffs"))

    if not playoffs_state:
        raise ValueError("Playoffs are not initialized")

    bracket = playoffs_state.get("bracket", {}) or {}
    round_name = playoffs_state.get("current_round", "Conference Quarterfinals")

    for series in bracket_mod.round_series(bracket, str(round_name)):
        if not series:
            continue
        while not bracket_mod.is_series_finished(series):
            bracket_mod.simulate_one_series_game(series)

    state.postseason_set_playoffs(playoffs_state)

    season_year = ids.current_season_year()
    updated_playoffs, champion = bracket_mod.advance_round_if_ready(playoffs_state, season_year=int(season_year))
    state.postseason_set_playoffs(updated_playoffs)
    if champion:
        state.postseason_set_champion(champion)

    return state.get_postseason_snapshot()
