from __future__ import annotations

import asyncio

import pytest

import state

fastapi = pytest.importorskip("fastapi")
from app.api.routes import core
from state_modules.state_store import transaction


def _seed_game_result_state() -> None:
    state.reset_state_for_dev()
    with transaction("test_seed_game_result") as s:
        s["active_season_id"] = "2025-26"
        s["league"]["season_year"] = 2025
        s["league"]["draft_year"] = 2026
        s["league"]["current_date"] = "2025-11-05"
        s["league"]["master_schedule"]["games"] = [
            {
                "game_id": "G0",
                "date": "2025-10-31",
                "home_team_id": "BOS",
                "away_team_id": "LAL",
                "status": "final",
                "home_score": 98,
                "away_score": 101,
                "phase": "regular",
            },
            {
                "game_id": "G1",
                "date": "2025-11-01",
                "home_team_id": "BOS",
                "away_team_id": "LAL",
                "status": "final",
                "home_score": 105,
                "away_score": 99,
                "phase": "regular",
            },
            {
                "game_id": "G2",
                "date": "2025-11-10",
                "home_team_id": "LAL",
                "away_team_id": "BOS",
                "status": "scheduled",
                "home_score": None,
                "away_score": None,
                "phase": "regular",
            },
        ]
        s["league"]["master_schedule"]["by_id"] = {
            "G0": {"game_id": "G0", "home_team_id": "BOS", "away_team_id": "LAL", "date": "2025-10-31"},
            "G1": {"game_id": "G1", "home_team_id": "BOS", "away_team_id": "LAL", "date": "2025-11-01"},
            "G2": {"game_id": "G2", "home_team_id": "LAL", "away_team_id": "BOS", "date": "2025-11-10"},
        }
        s["game_results"] = {
            "G0": {
                "game": {"game_id": "G0", "overtime_periods": 0},
                "final": {"BOS": 98, "LAL": 101},
                "teams": {"BOS": {"players": []}, "LAL": {"players": []}},
                "replay_events": [],
                "linescore": [],
            },
            "G1": {
                "game": {"game_id": "G1", "overtime_periods": 0},
                "final": {"BOS": 105, "LAL": 99},
                "teams": {
                    "BOS": {
                        "players": [
                            {"PlayerID": "p1", "Name": "J. Tatum", "PTS": 35, "REB": 9, "AST": 6},
                            {"PlayerID": "p2", "Name": "J. Brown", "PTS": 22, "REB": 5, "AST": 4},
                        ]
                    },
                    "LAL": {
                        "players": [
                            {"PlayerID": "p3", "Name": "L. James", "PTS": 29, "REB": 8, "AST": 7},
                        ]
                    },
                },
                "replay_events": [],
                "linescore": [],
            },
        }


def test_api_game_result_avoids_heavy_state_exports_and_team_schedule(monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_game_result_state()

    def _raise_full(*_args, **_kwargs):
        raise AssertionError("export_full_state_snapshot should not be used")

    def _raise_workflow(*_args, **_kwargs):
        raise AssertionError("export_workflow_state should not be used")

    async def _raise_team_schedule(*_args, **_kwargs):
        raise AssertionError("team_schedule should not be used")

    monkeypatch.setattr(core.state, "export_full_state_snapshot", _raise_full)
    monkeypatch.setattr(core.state, "export_workflow_state", _raise_workflow)
    monkeypatch.setattr(core, "team_schedule", _raise_team_schedule)

    out = asyncio.run(core.api_game_result("G1", "BOS"))

    assert out["game_id"] == "G1"
    assert out["header"]["user_team_record_after_game"] == "1-1"
    assert out["header"]["opponent_record_after_game"] == "1-1"
    assert out["leaders"]["points"]["home"]["name"] == "J. Tatum"
    assert out["leaders"]["points"]["away"]["name"] == "L. James"
    assert out["matchups"]["season_record"] == {"user_team_wins": 1, "user_team_losses": 1}
    assert len(out["matchups"]["completed"]) == 2
    assert len(out["matchups"]["upcoming"]) == 1
