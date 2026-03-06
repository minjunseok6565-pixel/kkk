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
            {
                "game_id": "G3",
                "date": "2025-11-01",
                "home_team_id": "NYK",
                "away_team_id": "MIA",
                "status": "final",
                "home_score": 112,
                "away_score": 107,
                "phase": "regular",
            },
            {
                "game_id": "G4",
                "date": "2025-11-01",
                "home_team_id": "GSW",
                "away_team_id": "HOU",
                "status": "final",
                "home_score": 121,
                "away_score": 119,
                "phase": "regular",
            },
            {
                "game_id": "G5",
                "date": "2025-11-01",
                "home_team_id": "CHI",
                "away_team_id": "DET",
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
            "G3": {"game_id": "G3", "home_team_id": "NYK", "away_team_id": "MIA", "date": "2025-11-01"},
            "G4": {"game_id": "G4", "home_team_id": "GSW", "away_team_id": "HOU", "date": "2025-11-01"},
            "G5": {"game_id": "G5", "home_team_id": "CHI", "away_team_id": "DET", "date": "2025-11-01"},
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
            "G3": {
                "game": {"game_id": "G3", "overtime_periods": 0},
                "final": {"NYK": 112, "MIA": 107},
                "teams": {"NYK": {"players": []}, "MIA": {"players": []}},
                "replay_events": [],
                "linescore": [],
            },
            "G4": {
                "game": {"game_id": "G4", "overtime_periods": 1},
                "final": {"GSW": 121, "HOU": 119},
                "teams": {"GSW": {"players": []}, "HOU": {"players": []}},
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
    assert out["tabs"]["default"] == "gamecast"
    assert len(out["matchups"]["completed"]) == 2
    assert len(out["matchups"]["upcoming"]) == 1
    assert len(out["same_day_results"]) == 2
    assert {x["game_id"] for x in out["same_day_results"]} == {"G3", "G4"}


def test_api_game_result_public_allows_non_participant_viewer() -> None:
    _seed_game_result_state()

    out = asyncio.run(core.api_game_result_public("G1", "NYK"))

    assert out["game_id"] == "G1"
    assert out["header"]["user_team_id"] == "NYK"
    assert out["header"]["user_team_record_after_game"] is None
    assert out["header"]["opponent_record_after_game"] is None
    assert {x["game_id"] for x in out["same_day_results"]} == {"G3", "G4"}


def test_api_game_result_public_includes_ot_status_label() -> None:
    _seed_game_result_state()

    out = asyncio.run(core.api_game_result_public("G1", "NYK"))

    cards = {x["game_id"]: x for x in out["same_day_results"]}
    assert cards["G3"]["status_label"] == "Final"
    assert cards["G4"]["status_label"] == "Final/OT"


def test_pbp_description_humanizes_shot_codes() -> None:
    player_lookup = {"p1": "Kristaps Porziņģis"}
    ev = {"player_id": "p1", "outcome": "SHOT_3_CS", "points": 3, "event_type": "SCORE"}

    desc = core._build_pbp_description("made_3pt", ev, player_lookup)

    assert desc == "Kristaps Porziņģis makes a three-point catch-and-shoot jumper."


def test_pbp_description_humanizes_rebound_abbreviations() -> None:
    player_lookup = {"p2": "Jalen Duren"}
    ev = {"player_id": "p2", "outcome": "orb", "event_type": "REB"}

    desc = core._build_pbp_description("rebound", ev, player_lookup)

    assert desc == "Jalen Duren grabs an offensive rebound."


def test_pbp_description_turnover_and_steal_commentary() -> None:
    player_lookup = {"p3": "Stephen Curry", "p4": "Jrue Holiday"}
    ev = {
        "player_id": "p3",
        "outcome": "BAD_PASS",
        "event_type": "TURNOVER",
        "stealer_pid": "p4",
    }

    desc = core._build_pbp_description("turnover", ev, player_lookup)

    assert desc == "Stephen Curry commits a bad pass, and Jrue Holiday gets the steal."
