from __future__ import annotations

import asyncio

import pytest

import state

fastapi = pytest.importorskip("fastapi")
from app.api.routes import core
from state_modules.state_store import transaction


def _seed_schedule_state() -> None:
    state.reset_state_for_dev()
    with transaction("test_seed_team_schedule") as s:
        s["active_season_id"] = "2025-26"
        s["league"]["season_year"] = 2025
        s["league"]["draft_year"] = 2026
        s["league"]["current_date"] = "2025-11-02"
        s["league"]["master_schedule"]["games"] = [
            {
                "game_id": "G1",
                "date": "2025-11-01",
                "home_team_id": "BOS",
                "away_team_id": "LAL",
                "status": "final",
                "home_score": 105,
                "away_score": 99,
            },
            {
                "game_id": "G2",
                "date": "2025-11-03",
                "home_team_id": "NYK",
                "away_team_id": "BOS",
                "status": "scheduled",
                "home_score": None,
                "away_score": None,
            },
        ]
        s["league"]["master_schedule"]["by_id"] = {
            "G1": {"game_id": "G1", "home_team_id": "BOS", "away_team_id": "LAL", "date": "2025-11-01"},
            "G2": {"game_id": "G2", "home_team_id": "NYK", "away_team_id": "BOS", "date": "2025-11-03"},
        }
        s["game_results"] = {
            "G1": {
                "teams": {
                    "BOS": {
                        "players": [
                            {"PlayerID": "p1", "Name": "J. Tatum", "PTS": 35, "REB": 9, "AST": 6},
                            {"PlayerID": "p2", "Name": "J. Brown", "PTS": 22, "REB": 5, "AST": 4},
                        ]
                    }
                }
            }
        }


def test_team_schedule_uses_lightweight_state_accessors(monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_schedule_state()

    def _raise_full(*_args, **_kwargs):
        raise AssertionError("export_full_state_snapshot should not be used")

    def _raise_workflow(*_args, **_kwargs):
        raise AssertionError("export_workflow_state should not be used")

    monkeypatch.setattr(core.state, "export_full_state_snapshot", _raise_full)
    monkeypatch.setattr(core.state, "export_workflow_state", _raise_workflow)

    out = asyncio.run(core.team_schedule("BOS"))

    assert out["team_id"] == "BOS"
    assert out["season_id"] == "2025-26"
    assert out["current_date"] == "2025-11-02"
    assert len(out["games"]) == 2

    completed = out["games"][0]
    upcoming = out["games"][1]

    assert completed["game_id"] == "G1"
    assert completed["result"]["display"] == "W 105-99"
    assert completed["record_after_game"]["display"] == "1-0"
    assert completed["leaders"]["points"]["name"] == "J. Tatum"

    assert upcoming["game_id"] == "G2"
    assert upcoming["result"] is None
    assert upcoming["tipoff_time"] is not None
