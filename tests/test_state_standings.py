from __future__ import annotations

from state_modules.state_standings import (
    apply_final_game,
    compute_standings_rows,
    create_empty_standings_cache,
    ensure_cache_consistency,
    rebuild_cache_from_games,
)


TEAM_MAP = {
    "BOS": {"conference": "east", "division": "atlantic"},
    "NYK": {"conference": "east", "division": "atlantic"},
    "LAL": {"conference": "west", "division": "pacific"},
}


def _final(game_id: str, home: str, away: str, hs: int, as_: int) -> dict:
    return {
        "game_id": game_id,
        "phase": "regular",
        "status": "final",
        "home_team_id": home,
        "away_team_id": away,
        "home_score": hs,
        "away_score": as_,
    }


def test_apply_final_game_updates_records_and_metadata() -> None:
    cache = create_empty_standings_cache(["BOS", "NYK", "LAL"], season_id="2025-26")

    cache, applied = apply_final_game(cache, _final("G1", "BOS", "NYK", 110, 99), TEAM_MAP)

    assert applied is True
    assert cache["applied_game_ids"]["G1"] is True
    assert cache["built_from"]["regular_final_count"] == 1
    assert cache["records_by_team"]["BOS"]["wins"] == 1
    assert cache["records_by_team"]["BOS"]["home_wins"] == 1
    assert cache["records_by_team"]["BOS"]["conf_wins"] == 1
    assert cache["records_by_team"]["BOS"]["div_wins"] == 1
    assert cache["records_by_team"]["BOS"]["recent10"] == [1]
    assert cache["records_by_team"]["BOS"]["streak_type"] == "W"
    assert cache["records_by_team"]["BOS"]["streak_len"] == 1
    assert cache["records_by_team"]["NYK"]["losses"] == 1


def test_apply_final_game_skips_duplicate_game_id() -> None:
    cache = create_empty_standings_cache(["BOS", "NYK"], season_id="2025-26")
    game = _final("G1", "BOS", "NYK", 100, 90)

    cache, applied_1 = apply_final_game(cache, game, TEAM_MAP)
    cache, applied_2 = apply_final_game(cache, game, TEAM_MAP)

    assert applied_1 is True
    assert applied_2 is False
    assert cache["records_by_team"]["BOS"]["wins"] == 1
    assert cache["built_from"]["regular_final_count"] == 1


def test_compute_standings_rows_builds_rank_and_gb() -> None:
    games = [
        _final("G1", "BOS", "NYK", 110, 99),
        _final("G2", "NYK", "BOS", 105, 102),
        _final("G3", "BOS", "LAL", 120, 98),
    ]
    cache = rebuild_cache_from_games(["BOS", "NYK", "LAL"], games, TEAM_MAP, season_id="2025-26")

    east_rows = compute_standings_rows(cache, TEAM_MAP, conference="east")

    assert len(east_rows) == 2
    assert east_rows[0]["team_id"] == "BOS"
    assert east_rows[0]["rank"] == 1
    assert east_rows[0]["gb_display"] == "-"
    assert east_rows[0]["l10"] == "2-1"
    assert east_rows[0]["strk"] == "W1"
    assert east_rows[1]["team_id"] == "NYK"
    assert east_rows[1]["gb"] > 0


def test_ensure_cache_consistency_reports_missing_and_extra() -> None:
    games = [_final("G1", "BOS", "NYK", 110, 99), _final("G2", "NYK", "BOS", 105, 102)]
    cache = create_empty_standings_cache(["BOS", "NYK"], season_id="2025-26")
    cache, _ = apply_final_game(cache, games[0], TEAM_MAP)
    cache["applied_game_ids"]["GX"] = True

    report = ensure_cache_consistency(cache, games)

    assert report["is_consistent"] is False
    assert report["missing_game_ids"] == ["G2"]
    assert report["extra_game_ids"] == ["GX"]
