from __future__ import annotations

"""Play-In logic: schedule, simulate, and apply results.

State shape is the SSOT for play-in:
- postseason.play_in is a dict with keys 'east'/'west'
- each conf has 'matchups' with keys:
    - seven_vs_eight
    - nine_vs_ten
    - final
- each matchup holds {home, away, date, result}
"""

from copy import deepcopy
from typing import Any, Dict, List, Optional, Tuple

import state

from . import ids
from .seeding import pick_home_advantage
from .schedule import safe_date_fromisoformat
from sim.match_runner import run_simulated_game, summarize_v2_result


def _conference_participants(field: Dict[str, Any], conf_key: str) -> Dict[int, Dict[str, Any]]:
    participants: Dict[int, Dict[str, Any]] = {}
    for entry in (field.get(conf_key, {}) or {}).get("play_in", []) or []:
        seed = entry.get("seed")
        if isinstance(seed, int):
            participants[int(seed)] = dict(entry)
    return participants


def conference_play_in_template(
    conf_key: str,
    field: Dict[str, Any],
    *,
    season_year: int,
    start_date: str,
    final_date: str,
) -> Dict[str, Any]:
    participants = _conference_participants(field, conf_key)
    seeds = participants

    # NBA policy: higher seed hosts (7 hosts 8, 9 hosts 10).
    m_7v8 = {
        "game_id": ids.make_play_in_game_id(season_year, conf_key, "7V8"),
        "home": seeds.get(7),
        "away": seeds.get(8),
        "date": start_date,
        "result": None,
    }
    m_9v10 = {
        "game_id": ids.make_play_in_game_id(season_year, conf_key, "9V10"),
        "home": seeds.get(9),
        "away": seeds.get(10),
        "date": start_date,
        "result": None,
    }
    m_final = {
        "game_id": ids.make_play_in_game_id(season_year, conf_key, "FINAL"),
        "home": None,
        "away": None,
        "date": final_date,
        "result": None,
    }

    return {
        "conference": conf_key,
        "participants": participants,
        "matchups": {"seven_vs_eight": m_7v8, "nine_vs_ten": m_9v10, "final": m_final},
        "seed7": None,
        "seed8": None,
        "eliminated": [],
    }


def build_play_in_state(
    field: Dict[str, Any],
    *,
    season_year: int,
    start_date: str,
    final_date: str,
) -> Dict[str, Any]:
    return {
        "east": conference_play_in_template(
            "east", field, season_year=season_year, start_date=start_date, final_date=final_date
        ),
        "west": conference_play_in_template(
            "west", field, season_year=season_year, start_date=start_date, final_date=final_date
        ),
    }


def apply_play_in_results(conf_state: Dict[str, Any]) -> None:
    """Update seed7/seed8/eliminated and final matchup participants in-place."""
    matchups = conf_state.get("matchups", {}) or {}
    conf_state["seed7"] = None
    conf_state["seed8"] = None
    eliminated: List[str] = []

    seven_res = (matchups.get("seven_vs_eight") or {}).get("result")
    nine_res = (matchups.get("nine_vs_ten") or {}).get("result")
    final_res = (matchups.get("final") or {}).get("result")

    main_loser: Optional[Dict[str, Any]] = None
    lower_winner: Optional[Dict[str, Any]] = None

    # 7 vs 8 => winner is seed7, loser goes to final
    if seven_res:
        winner = seven_res.get("winner")
        if winner:
            p7 = conf_state.get("participants", {}).get(7)
            p8 = conf_state.get("participants", {}).get(8)
            if p7 and p8:
                conf_state["seed7"] = p7 if winner == p7.get("team_id") else p8
                main_loser = p8 if conf_state["seed7"] is p7 else p7

    # 9 vs 10 => winner goes to final, loser eliminated
    if nine_res:
        winner = nine_res.get("winner")
        p9 = conf_state.get("participants", {}).get(9)
        p10 = conf_state.get("participants", {}).get(10)
        if winner and p9 and p10:
            lower_winner = p9 if winner == p9.get("team_id") else p10
            lower_loser = p10 if lower_winner is p9 else p9
            if lower_loser and lower_loser.get("team_id"):
                eliminated.append(str(lower_loser["team_id"]))

    # Final => winner is seed8, loser eliminated
    if final_res:
        winner = final_res.get("winner")
        home_team_id = final_res.get("home_team_id")
        away_team_id = final_res.get("away_team_id")
        home_entry = None
        away_entry = None
        for entry in (conf_state.get("participants", {}) or {}).values():
            if entry.get("team_id") == home_team_id:
                home_entry = entry
            if entry.get("team_id") == away_team_id:
                away_entry = entry
        if winner and home_entry and away_entry:
            conf_state["seed8"] = home_entry if winner == home_entry.get("team_id") else away_entry
            loser_entry = away_entry if conf_state["seed8"] is home_entry else home_entry
            if loser_entry and loser_entry.get("team_id"):
                eliminated.append(str(loser_entry["team_id"]))

    conf_state["eliminated"] = eliminated

    # If final not played yet, but participants known, set (home, away) now.
    if not final_res and main_loser and lower_winner:
        home, away = pick_home_advantage(main_loser, lower_winner)
        matchups["final"]["home"], matchups["final"]["away"] = home, away


def _simulate_play_in_game(
    *,
    home_entry: Dict[str, Any],
    away_entry: Dict[str, Any],
    game_id: str,
    game_date: str,
) -> Dict[str, Any]:
    # Update in-game date SSOT
    state.set_current_date(str(game_date))

    sim = run_simulated_game(
        game_id=str(game_id),
        game_date=str(game_date),
        phase="play_in",
        home_team_id=str(home_entry["team_id"]),
        away_team_id=str(away_entry["team_id"]),
        update_in_game_date=False,  # already set
    )
    v2 = sim["game_result_v2"]
    summary = summarize_v2_result(v2, game_date_override=str(game_date))
    summary["phase"] = "play_in"
    return summary


def auto_play_in_conf(conf_state: Dict[str, Any], my_team_id: Optional[str]) -> None:
    """Auto-simulate play-in games that do not involve the user team."""
    matchups = conf_state.get("matchups", {}) or {}

    for key in ("seven_vs_eight", "nine_vs_ten"):
        matchup = matchups.get(key)
        if not matchup or matchup.get("result"):
            continue
        home = matchup.get("home")
        away = matchup.get("away")
        if not home or not away:
            continue
        if my_team_id and my_team_id in {home.get("team_id"), away.get("team_id")}:
            continue

        matchup["result"] = _simulate_play_in_game(
            home_entry=home,
            away_entry=away,
            game_id=str(matchup.get("game_id")),
            game_date=str(matchup.get("date")),
        )

    apply_play_in_results(conf_state)

    final_matchup = matchups.get("final")
    if not final_matchup:
        return

    home = final_matchup.get("home")
    away = final_matchup.get("away")
    if home and away and not final_matchup.get("result"):
        if not my_team_id or my_team_id not in {home.get("team_id"), away.get("team_id")}:
            final_matchup["result"] = _simulate_play_in_game(
                home_entry=home,
                away_entry=away,
                game_id=str(final_matchup.get("game_id")),
                game_date=str(final_matchup.get("date")),
            )
    apply_play_in_results(conf_state)


def is_play_in_complete(play_in_state: Dict[str, Any]) -> bool:
    for conf_key in ("east", "west"):
        conf_state = (play_in_state or {}).get(conf_key) or {}
        if not conf_state.get("seed7") or not conf_state.get("seed8"):
            return False
    return True


def find_user_conference(play_in_state: Dict[str, Any], my_team_id: str) -> Optional[str]:
    for conf_key, conf_state in (play_in_state or {}).items():
        participants = (conf_state or {}).get("participants", {}) or {}
        if any(p.get("team_id") == my_team_id for p in participants.values()):
            return str(conf_key)
    return None


def play_my_team_next_game(play_in_state: Dict[str, Any], my_team_id: str) -> Tuple[Dict[str, Any], bool]:
    """Play the next pending play-in game involving the user team.

    Returns: (updated_play_in_state, did_play_any)
    """
    if not my_team_id:
        return play_in_state, False

    play_in_state = deepcopy(play_in_state or {})

    conf_key = find_user_conference(play_in_state, my_team_id)
    if conf_key is None:
        return play_in_state, False

    conf_state = play_in_state.get(conf_key) or {}
    matchups = conf_state.get("matchups", {}) or {}

    # Priority order: user's next pending game.
    for key in ("seven_vs_eight", "nine_vs_ten", "final"):
        matchup = matchups.get(key)
        if not matchup or matchup.get("result"):
            continue
        home = matchup.get("home")
        away = matchup.get("away")
        if not home or not away:
            continue
        if my_team_id not in {home.get("team_id"), away.get("team_id")}:
            continue

        matchup["result"] = _simulate_play_in_game(
            home_entry=home,
            away_entry=away,
            game_id=str(matchup.get("game_id")),
            game_date=str(matchup.get("date")),
        )
        apply_play_in_results(conf_state)

        # After user's game, auto-play the rest of this conference.
        auto_play_in_conf(conf_state, my_team_id=my_team_id)

        play_in_state[conf_key] = conf_state
        return play_in_state, True

    return play_in_state, False
