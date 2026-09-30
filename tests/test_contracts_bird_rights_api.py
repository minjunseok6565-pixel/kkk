from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import state
pytest.importorskip("fastapi")
from app.api.routes import contracts as contracts_route
from app.schemas.contracts import BirdRightsRenounceRequest
from league_repo import LeagueRepo
from state_modules.state_store import transaction


def _configure_state(db_path: Path) -> None:
    state.reset_state_for_dev()
    state.set_current_date("2027-07-01")
    state.set_db_path(str(db_path))
    with transaction("test_contracts_bird_api_state") as s:
        s["active_season_id"] = "2027-28"
        s["league"]["season_year"] = 2027
        s["league"]["draft_year"] = 2028


def _seed_right_and_hold(repo: LeagueRepo) -> None:
    repo.upsert_team_bird_rights(
        [{
            "season_year": 2027,
            "team_id": "BOS",
            "player_id": "P300001",
            "bird_type": "NON_BIRD",
            "tenure_years_same_team": 1,
            "is_renounced": 0,
        }]
    )
    repo.upsert_team_cap_holds(
        [{
            "season_year": 2027,
            "team_id": "BOS",
            "player_id": "P300001",
            "source_type": "BIRD",
            "bird_type": "NON_BIRD",
            "hold_amount": 12_000_000,
            "is_released": 0,
        }]
    )


def test_bird_rights_renounce_and_list_endpoints(tmp_path: Path) -> None:
    db_path = tmp_path / "bird_api.sqlite"
    _configure_state(db_path)

    with LeagueRepo(str(db_path)) as repo:
        repo.init_db()
        _seed_right_and_hold(repo)

    renounce_out = asyncio.run(
        contracts_route.api_contracts_bird_rights_renounce(
            BirdRightsRenounceRequest(team_id="BOS", player_id="P300001", season_year=2027)
        )
    )
    assert renounce_out["ok"] is True
    assert renounce_out["rights_changed"] is True
    assert renounce_out["cap_hold_released"] is True

    rights_out = asyncio.run(contracts_route.api_contracts_bird_rights(team_id="BOS", season_year=2027))
    assert rights_out["ok"] is True
    assert rights_out["count"] == 1
    assert rights_out["items"][0]["is_renounced"] == 1

    holds_out = asyncio.run(contracts_route.api_contracts_cap_holds(team_id="BOS", season_year=2027, active_only=False))
    assert holds_out["ok"] is True
    assert holds_out["count"] == 1
    assert holds_out["items"][0]["is_released"] == 1
