from __future__ import annotations

import asyncio

import pytest

import state

fastapi = pytest.importorskip("fastapi")
from app.api.routes import core
from state_modules.state_store import transaction


def _seed_home_dashboard_state() -> None:
    state.reset_state_for_dev()
    with transaction("test_seed_home_dashboard") as s:
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
                "phase": "regular",
            },
            {
                "game_id": "G2",
                "date": "2025-11-03",
                "home_team_id": "NYK",
                "away_team_id": "BOS",
                "status": "scheduled",
                "home_score": None,
                "away_score": None,
                "phase": "regular",
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
                        ]
                    }
                }
            }
        }


async def _fake_medical_overview(_tid: str):
    return {
        "summary": {
            "injury_status_counts": {"OUT": 1, "RETURNING": 0},
            "risk_tier_counts": {"HIGH": 2},
        },
        "watchlists": {
            "recent_injury_events": [
                {
                    "date": "2025-11-02",
                    "player_name": "Player A",
                    "injury_type": "Ankle",
                    "player_id": "p1",
                    "severity": "MED",
                    "body_part": "ANKLE",
                    "recovery_status": "DAY_TO_DAY",
                }
            ]
        },
    }


async def _fake_medical_alerts(_tid: str):
    return {
        "alert_level": "warn",
        "primary_alert_player": {"name": "Player A", "risk_tier": "HIGH"},
        "team_load_context": {"next_7d_back_to_back_count": 1, "next_7d_game_count": 4},
    }


async def _fake_risk_calendar(_tid: str, days: int = 14):
    assert days == 14
    return {"days": [{"date": "2025-11-03", "high_risk_count": 2}]}


def test_home_dashboard_reuses_schedule_computation_without_team_schedule(monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_home_dashboard_state()

    async def _raise_team_schedule(*_args, **_kwargs):
        raise AssertionError("team_schedule should not be called from api_home_dashboard")

    def _raise_full(*_args, **_kwargs):
        raise AssertionError("export_full_state_snapshot should not be used")

    def _raise_workflow(*_args, **_kwargs):
        raise AssertionError("export_workflow_state should not be used")

    monkeypatch.setattr(core, "team_schedule", _raise_team_schedule)
    monkeypatch.setattr(core.state, "export_full_state_snapshot", _raise_full)
    monkeypatch.setattr(core.state, "export_workflow_state", _raise_workflow)

    monkeypatch.setattr(core, "get_team_detail", lambda _tid: {"summary": {"wins": 1, "losses": 0, "win_pct": 1.0, "payroll": 100, "cap_space": 20}})
    monkeypatch.setattr(core, "get_conference_standings_table", lambda: {
        "east": [{"team_id": "BOS", "rank": 1, "gb_display": "-", "l10": "1-0", "strk": "W1"}],
        "west": [{"team_id": "NYK", "rank": 10, "gb_display": "5.0", "l10": "0-1", "strk": "L1"}],
    })
    monkeypatch.setattr(core, "api_medical_team_overview", _fake_medical_overview)
    monkeypatch.setattr(core, "api_medical_team_alerts", _fake_medical_alerts)
    monkeypatch.setattr(core, "api_medical_team_risk_calendar", _fake_risk_calendar)

    out = asyncio.run(core.api_home_dashboard("BOS"))

    assert out["team_id"] == "BOS"
    assert out["current_date"] == "2025-11-02"
    assert out["next_game"]["game"]["game_id"] == "G2"
    assert out["snapshot"]["health"]["out_count"] == 1
    assert out["snapshot"]["health"]["high_risk_count"] == 2
    assert len(out["priorities"]) >= 2
    assert out["risk_calendar"][0]["date"] == "2025-11-03"
