from __future__ import annotations

import asyncio

import pytest

state = pytest.importorskip("state")
pytest.importorskip("fastapi")
from app.api.routes import tactics
from app.schemas.tactics import TeamTacticsUpsertRequest


def test_team_tactics_put_get_roundtrip_and_store_engine_shape() -> None:
    state.reset_state_for_dev()

    payload = {
        "offenseScheme": "Spread_HeavyPnR",
        "defenseScheme": "Drop",
        "starters": [{"pid": "p1", "offenseRole": "Primary_Engine", "defenseRole": "POA", "minutes": 34}],
        "rotation": [{"pid": "p2", "offenseRole": "Spot_Up_Wing", "defenseRole": "HELP", "minutes": 16}],
    }

    put_out = asyncio.run(
        tactics.api_put_team_tactics(
            "bos",
            TeamTacticsUpsertRequest(tactics=payload),
        )
    )
    assert put_out["ok"] is True
    assert put_out["team_id"] == "BOS"
    assert put_out["tactics"]["offenseScheme"] == "Spread_HeavyPnR"

    saved = state.get_team_tactics_snapshot("BOS")
    stored_tactics = saved.get("tactics") or {}
    assert stored_tactics.get("offense_scheme") == "Spread_HeavyPnR"
    assert stored_tactics.get("defense_scheme") == "Drop"
    assert stored_tactics.get("lineup", {}).get("starters") == ["p1"]
    assert stored_tactics.get("rotation_offense_role_by_pid", {}).get("p1") == "Primary_Engine"
    assert stored_tactics.get("defense_role_overrides", {}).get("POA") == "p1"

    get_out = asyncio.run(tactics.api_get_team_tactics("BOS"))
    assert get_out["team_id"] == "BOS"
    assert get_out["tactics"]["offenseScheme"] == "Spread_HeavyPnR"
    assert get_out["tactics"]["defenseScheme"] == "Drop"
    assert isinstance(get_out["updated_at_turn"], int)


def test_team_tactics_get_empty_returns_null_tactics() -> None:
    state.reset_state_for_dev()

    out = asyncio.run(tactics.api_get_team_tactics("LAL"))
    assert out["team_id"] == "LAL"
    assert out["tactics"] is None
    assert out["updated_at_turn"] is None
