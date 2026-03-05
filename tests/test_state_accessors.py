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
