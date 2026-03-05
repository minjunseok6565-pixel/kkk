from __future__ import annotations

import state
from state_modules.state_store import transaction


def _seed_state_for_accessor_tests() -> None:
    state.reset_state_for_dev()
    with transaction("test_seed_state_accessors") as s:
        s["active_season_id"] = "2025-26"
        s["league"]["season_year"] = 2025
        s["league"]["draft_year"] = 2026
        s["league"]["current_date"] = "2025-11-01"
        s["league"]["master_schedule"]["games"] = [
            {
                "game_id": "G1",
                "date": "2025-11-01",
                "home_team_id": "BOS",
                "away_team_id": "LAL",
                "status": "scheduled",
                "home_score": None,
                "away_score": None,
            }
        ]
        s["league"]["master_schedule"]["by_id"] = {
            "G1": {
                "game_id": "G1",
                "date": "2025-11-01",
                "home_team_id": "BOS",
                "away_team_id": "LAL",
            }
        }
        s["game_results"] = {"G1": {"game": {"game_id": "G1"}, "final": {"BOS": 100, "LAL": 98}}}
        s["player_stats"] = {"p1": {"games": 1, "totals": {"PTS": 20}}}
        s["phase_results"]["playoffs"]["game_results"] = {"PG1": {"game": {"game_id": "PG1"}}}
        s["phase_results"]["playoffs"]["player_stats"] = {"p9": {"games": 2, "totals": {"PTS": 50}}}


def test_get_league_schedule_snapshot_returns_only_required_branches() -> None:
    _seed_state_for_accessor_tests()

    snap = state.get_league_schedule_snapshot()

    assert snap["active_season_id"] == "2025-26"
    assert snap["current_date"] == "2025-11-01"
    assert snap["master_schedule"]["games"][0]["game_id"] == "G1"
    assert snap["master_schedule"]["by_id"]["G1"]["home_team_id"] == "BOS"
    assert "game_results" not in snap


def test_get_game_results_snapshot_supports_phase_selection() -> None:
    _seed_state_for_accessor_tests()

    regular = state.get_game_results_snapshot()
    playoffs = state.get_game_results_snapshot(phase="playoffs")
    unknown = state.get_game_results_snapshot(phase="unknown")

    assert "G1" in regular
    assert "PG1" in playoffs
    assert unknown == {}


def test_get_player_stats_snapshot_supports_phase_selection() -> None:
    _seed_state_for_accessor_tests()

    regular = state.get_player_stats_snapshot()
    playoffs = state.get_player_stats_snapshot(phase="playoffs")
    unknown = state.get_player_stats_snapshot(phase="unknown")

    assert regular["p1"]["totals"]["PTS"] == 20
    assert playoffs["p9"]["games"] == 2
    assert unknown == {}


def test_standings_cache_accessors_roundtrip() -> None:
    _seed_state_for_accessor_tests()

    state.clear_standings_cache()
    state.set_standings_cache_built_from(season_id="2025-26", regular_final_count=12)
    state.mark_standings_cache_game_applied("G0001")
    state.upsert_standings_cache_record(
        "BOS",
        {
            "wins": 10,
            "losses": 2,
            "pf": 1300,
            "pa": 1200,
            "home_wins": 6,
            "home_losses": 1,
            "away_wins": 4,
            "away_losses": 1,
            "div_wins": 3,
            "div_losses": 1,
            "conf_wins": 8,
            "conf_losses": 2,
            "recent10": [1, 1, 1, 0, 1, 1, 1, 1, 0, 1],
            "streak_type": "W",
            "streak_len": 2,
        },
    )

    cache = state.get_standings_cache_snapshot()
    assert cache["version"] == 1
    assert cache["built_from"]["season_id"] == "2025-26"
    assert cache["built_from"]["regular_final_count"] == 12
    assert cache["applied_game_ids"]["G0001"] is True
    assert cache["records_by_team"]["BOS"]["wins"] == 10


def test_set_standings_cache_fills_missing_shape() -> None:
    _seed_state_for_accessor_tests()

    state.set_standings_cache({"records_by_team": {}})
    cache = state.get_standings_cache_snapshot()

    assert cache["version"] == 1
    assert cache["built_from"]["season_id"] is None
    assert cache["built_from"]["regular_final_count"] == 0
    assert cache["applied_game_ids"] == {}
    assert cache["records_by_team"] == {}
