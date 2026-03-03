from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Set

from league_repo import LeagueRepo
from schema import normalize_team_id

from two_way_repo import get_active_two_way_players_by_team, get_two_way_games_used

TWO_WAY_GAME_LIMIT = 50


@dataclass(frozen=True)
class TwoWayEligibility:
    home_exclude: Set[str]
    away_exclude: Set[str]
    reasons: Dict[str, str]


def get_two_way_exclusions_for_game(
    *,
    repo: LeagueRepo,
    home_team_id: str,
    away_team_id: str,
    phase: str,
    season_year: int,
) -> TwoWayEligibility:
    home = str(normalize_team_id(home_team_id, strict=True)).upper()
    away = str(normalize_team_id(away_team_id, strict=True)).upper()
    ph = str(phase or "regular").strip().lower()

    reasons: Dict[str, str] = {}
    with repo.transaction() as cur:
        home_tw = get_active_two_way_players_by_team(cur, home)
        away_tw = get_active_two_way_players_by_team(cur, away)

        if ph in {"play_in", "playoffs"}:
            for pid in home_tw | away_tw:
                reasons[pid] = "POSTSEASON_INELIGIBLE_TWO_WAY"
            return TwoWayEligibility(home_exclude=set(home_tw), away_exclude=set(away_tw), reasons=reasons)

        home_ex: Set[str] = set()
        for pid in home_tw:
            used = get_two_way_games_used(cur, pid, int(season_year))
            if used >= TWO_WAY_GAME_LIMIT:
                home_ex.add(pid)
                reasons[pid] = "TWO_WAY_GAME_LIMIT_REACHED"

        away_ex: Set[str] = set()
        for pid in away_tw:
            used = get_two_way_games_used(cur, pid, int(season_year))
            if used >= TWO_WAY_GAME_LIMIT:
                away_ex.add(pid)
                reasons[pid] = "TWO_WAY_GAME_LIMIT_REACHED"

    return TwoWayEligibility(home_exclude=home_ex, away_exclude=away_ex, reasons=reasons)
