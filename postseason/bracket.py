from __future__ import annotations

"""Playoffs bracket generation and progression.

This module maintains the legacy bracket/series structure expected by:
- UI (static/NBA.html)
- news_ai.refresh_playoff_news()

Enhancements over the legacy implementation:
- deterministic series_id/game_id (via postseason.ids)
- correct SSOT usage for context building (via sim.match_runner)
- centralized scheduling policy (via postseason.schedule)
"""

from copy import deepcopy
from typing import Any, Dict, List, Optional, Tuple

import state

from . import ids
from .schedule import compute_series_next_game_date, next_round_start
from .seeding import pick_home_advantage
from sim.match_runner import run_simulated_game, summarize_v2_result


def series_template(
    *,
    season_year: int,
    conf: str,
    round_name: str,
    matchup_label: str,
    start_date: str,
    home_adv: Dict[str, Any],
    road: Dict[str, Any],
    round_code: str,
    series_label: str,
    best_of: int = 7,
) -> Dict[str, Any]:
    sid = ids.make_series_id(int(season_year), conf, round_code, series_label)
    return {
        "series_id": sid,
        "round": round_name,
        "matchup": matchup_label,
        "home_court": home_adv.get("team_id"),
        "road": road.get("team_id"),
        "home_entry": home_adv,
        "road_entry": road,
        "games": [],
        "wins": {str(home_adv.get("team_id")): 0, str(road.get("team_id")): 0},
        "best_of": int(best_of),
        "winner": None,
        "start_date": str(start_date),
    }


def is_series_finished(series: Dict[str, Any]) -> bool:
    if series.get("winner"):
        return True
    wins = series.get("wins") or {}
    best_of = int(series.get("best_of") or 7)
    needed = best_of // 2 + 1
    return any(int(v or 0) >= needed for v in wins.values())


def simulate_one_series_game(series: Dict[str, Any]) -> Dict[str, Any]:
    """Simulate exactly one pending game for the series (mutates series in-place)."""
    if is_series_finished(series):
        return series

    games = series.get("games") or []
    game_idx = len(games)
    best_of = int(series.get("best_of") or 7)
    if game_idx >= best_of:
        return series

    # 2-2-1-1-1
    from postseason.schedule import HOME_PATTERN_7

    higher_is_home = HOME_PATTERN_7[game_idx]
    home_id = str(series["home_court"]) if higher_is_home else str(series["road"])
    away_id = str(series["road"]) if higher_is_home else str(series["home_court"])

    game_date = compute_series_next_game_date(series, game_idx)
    # Update in-game date SSOT (used by news and potential other subsystems).
    state.set_current_date(str(game_date))

    sid = str(series.get("series_id") or "")
    gid = ids.make_series_game_id(sid, game_idx + 1)

    sim = run_simulated_game(
        game_id=gid,
        game_date=str(game_date),
        phase="playoffs",
        home_team_id=home_id,
        away_team_id=away_id,
        update_in_game_date=False,  # already set above
    )
    v2 = sim["game_result_v2"]
    summary = summarize_v2_result(v2, game_date_override=str(game_date))
    summary["phase"] = "playoffs"

    series.setdefault("games", []).append(summary)

    wins = series.setdefault("wins", {})
    winner_tid = summary.get("winner")
    if winner_tid:
        wins[winner_tid] = int(wins.get(winner_tid, 0) or 0) + 1

        needed = best_of // 2 + 1
        if int(wins[winner_tid]) >= needed:
            series["winner"] = (
                series.get("home_entry") if series.get("home_entry", {}).get("team_id") == winner_tid else series.get("road_entry")
            )

    return series


def round_series(bracket: Dict[str, Any], round_name: str) -> List[Dict[str, Any]]:
    if round_name == "Conference Quarterfinals":
        return (bracket.get("east", {}).get("quarterfinals") or []) + (bracket.get("west", {}).get("quarterfinals") or [])
    if round_name == "Conference Semifinals":
        return (bracket.get("east", {}).get("semifinals") or []) + (bracket.get("west", {}).get("semifinals") or [])
    if round_name == "Conference Finals":
        finals = []
        if bracket.get("east", {}).get("finals"):
            finals.append(bracket["east"]["finals"])
        if bracket.get("west", {}).get("finals"):
            finals.append(bracket["west"]["finals"])
        return finals
    if round_name == "NBA Finals":
        f = bracket.get("finals")
        return [f] if f else []
    return []


# ---------------------------------------------------------------------------
# Bracket construction helpers
# ---------------------------------------------------------------------------

def _conference_quarterfinals(
    *,
    season_year: int,
    conf_key: str,
    seeds: Dict[int, Dict[str, Any]],
    start_date: str,
) -> List[Dict[str, Any]]:
    qf_pairs = [(1, 8), (4, 5), (3, 6), (2, 7)]
    results: List[Dict[str, Any]] = []
    for high, low in qf_pairs:
        team_high = seeds.get(high)
        team_low = seeds.get(low)
        if not team_high or not team_low:
            continue
        home, road = pick_home_advantage(team_high, team_low)
        results.append(
            series_template(
                season_year=int(season_year),
                conf=conf_key,
                round_name="Conference Quarterfinals",
                matchup_label=f"{high} vs {low}",
                start_date=str(start_date),
                home_adv=home,
                road=road,
                round_code="R1",
                series_label=f"{high}V{low}",
            )
        )
    return results


def _conference_semifinals_from_qf(
    *,
    season_year: int,
    conf_key: str,
    qf_list: List[Dict[str, Any]],
    start_date: str,
) -> List[Dict[str, Any]]:
    def _find_winner(matchup_prefix: str) -> Optional[Dict[str, Any]]:
        for s in qf_list or []:
            if str(s.get("matchup") or "").startswith(matchup_prefix):
                return s.get("winner")
        return None

    inputs = [
        (_find_winner("1 vs 8"), _find_winner("4 vs 5")),
        (_find_winner("2 vs 7"), _find_winner("3 vs 6")),
    ]
    results: List[Dict[str, Any]] = []
    for idx, (a, b) in enumerate(inputs, start=1):
        if not a or not b:
            continue
        home, road = pick_home_advantage(a, b)
        results.append(
            series_template(
                season_year=int(season_year),
                conf=conf_key,
                round_name="Conference Semifinals",
                matchup_label=f"SF{idx}",
                start_date=str(start_date),
                home_adv=home,
                road=road,
                round_code="R2",
                series_label=f"SF{idx}",
            )
        )
    return results


def _conference_finals_from_sf(
    *,
    season_year: int,
    conf_key: str,
    sf_list: List[Dict[str, Any]],
    start_date: str,
) -> Optional[Dict[str, Any]]:
    if len(sf_list or []) < 2:
        return None
    if not all(s.get("winner") for s in (sf_list or [])):
        return None
    home, road = pick_home_advantage(sf_list[0]["winner"], sf_list[1]["winner"])
    return series_template(
        season_year=int(season_year),
        conf=conf_key,
        round_name="Conference Finals",
        matchup_label="CF",
        start_date=str(start_date),
        home_adv=home,
        road=road,
        round_code="CF",
        series_label="CF",
    )


def _finals_from_conf(
    *,
    season_year: int,
    east_final: Optional[Dict[str, Any]],
    west_final: Optional[Dict[str, Any]],
    start_date: str,
) -> Optional[Dict[str, Any]]:
    if not east_final or not west_final:
        return None
    if not east_final.get("winner") or not west_final.get("winner"):
        return None
    home, road = pick_home_advantage(east_final["winner"], west_final["winner"])
    return series_template(
        season_year=int(season_year),
        conf="finals",
        round_name="NBA Finals",
        matchup_label="FINALS",
        start_date=str(start_date),
        home_adv=home,
        road=road,
        round_code="F",
        series_label="FIN",
    )


def initialize_playoffs(
    *,
    season_year: int,
    seeds_by_conf: Dict[str, Dict[int, Dict[str, Any]]],
    start_date: str,
) -> Dict[str, Any]:
    """Build the playoffs state (no state writes here)."""
    bracket = {
        "east": {
            "quarterfinals": _conference_quarterfinals(
                season_year=int(season_year),
                conf_key="east",
                seeds=seeds_by_conf.get("east", {}) or {},
                start_date=str(start_date),
            ),
            "semifinals": [],
            "finals": None,
        },
        "west": {
            "quarterfinals": _conference_quarterfinals(
                season_year=int(season_year),
                conf_key="west",
                seeds=seeds_by_conf.get("west", {}) or {},
                start_date=str(start_date),
            ),
            "semifinals": [],
            "finals": None,
        },
        "finals": None,
    }

    return {
        "engine_version": "postseason.v2",
        "seeds": seeds_by_conf,
        "bracket": bracket,
        "current_round": "Conference Quarterfinals",
        "start_date": str(start_date),
        # Optional helpers (safe to ignore by UI)
        "calendar": {},
        "game_specs": {},
    }


def advance_round_if_ready(playoffs_state: Dict[str, Any], *, season_year: int) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    """Advance bracket if current round is fully complete.

    Returns: (updated_playoffs_state, champion_entry_or_none)
    """
    playoffs = deepcopy(playoffs_state or {})
    bracket = playoffs.get("bracket", {}) or {}
    current_round = playoffs.get("current_round", "Conference Quarterfinals")

    # --- QF -> SF
    if current_round == "Conference Quarterfinals":
        qf_series = round_series(bracket, current_round)
        if qf_series and all(is_series_finished(s) for s in qf_series):
            start = next_round_start(qf_series) or playoffs.get("start_date") or state.get_current_date_as_date().isoformat()
            bracket["east"]["semifinals"] = _conference_semifinals_from_qf(
                season_year=int(season_year),
                conf_key="east",
                qf_list=bracket.get("east", {}).get("quarterfinals") or [],
                start_date=str(start),
            )
            bracket["west"]["semifinals"] = _conference_semifinals_from_qf(
                season_year=int(season_year),
                conf_key="west",
                qf_list=bracket.get("west", {}).get("quarterfinals") or [],
                start_date=str(start),
            )
            playoffs["current_round"] = "Conference Semifinals"
            playoffs["bracket"] = bracket
            return playoffs, None

    # --- SF -> CF
    if current_round == "Conference Semifinals":
        sf_series = round_series(bracket, current_round)
        if sf_series and all(is_series_finished(s) for s in sf_series):
            start = next_round_start(sf_series) or playoffs.get("start_date") or state.get_current_date_as_date().isoformat()
            bracket["east"]["finals"] = _conference_finals_from_sf(
                season_year=int(season_year),
                conf_key="east",
                sf_list=bracket.get("east", {}).get("semifinals") or [],
                start_date=str(start),
            )
            bracket["west"]["finals"] = _conference_finals_from_sf(
                season_year=int(season_year),
                conf_key="west",
                sf_list=bracket.get("west", {}).get("semifinals") or [],
                start_date=str(start),
            )
            playoffs["current_round"] = "Conference Finals"
            playoffs["bracket"] = bracket
            return playoffs, None

    # --- CF -> Finals
    if current_round == "Conference Finals":
        cf_series = round_series(bracket, current_round)
        if cf_series and all(is_series_finished(s) for s in cf_series):
            start = next_round_start(cf_series) or playoffs.get("start_date") or state.get_current_date_as_date().isoformat()
            bracket["finals"] = _finals_from_conf(
                season_year=int(season_year),
                east_final=bracket.get("east", {}).get("finals"),
                west_final=bracket.get("west", {}).get("finals"),
                start_date=str(start),
            )
            playoffs["current_round"] = "NBA Finals"
            playoffs["bracket"] = bracket
            return playoffs, None

    # --- Finals -> Champion
    if current_round == "NBA Finals":
        finals = bracket.get("finals")
        if finals and is_series_finished(finals):
            champion = finals.get("winner")
            return playoffs, champion

    return playoffs, None


def find_my_series(playoffs_state: Dict[str, Any], my_team_id: str) -> Optional[Dict[str, Any]]:
    bracket = (playoffs_state or {}).get("bracket", {}) or {}
    round_name = (playoffs_state or {}).get("current_round", "Conference Quarterfinals")
    for series in round_series(bracket, str(round_name)):
        if not series:
            continue
        if my_team_id in {series.get("home_court"), series.get("road")}:
            return series
    return None
