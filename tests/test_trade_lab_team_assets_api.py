from __future__ import annotations

import asyncio
import json
from datetime import date
from typing import Any, Dict, List

import pytest

pytest.importorskip("fastapi")

from app.api.routes import trades


class _FakeRepo:
    def __init__(self, _db_path: str, *, roster_rows: List[Dict[str, Any]], picks_map: Dict[str, Dict[str, Any]]) -> None:
        self._roster_rows = list(roster_rows)
        self._picks_map = dict(picks_map)

    def __enter__(self) -> "_FakeRepo":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def get_team_roster(self, _team_id: str) -> List[Dict[str, Any]]:
        return list(self._roster_rows)

    def get_draft_picks_map(self) -> Dict[str, Dict[str, Any]]:
        return dict(self._picks_map)



def _install_fake_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    *,
    roster_rows: List[Dict[str, Any]],
    picks_map: Dict[str, Dict[str, Any]],
    current_date: date = date(2025, 12, 11),
) -> None:
    monkeypatch.setattr(trades.state, "get_current_date_as_date", lambda: current_date)
    monkeypatch.setattr(trades.state, "get_db_path", lambda: "fake.db")

    def _fake_repo_factory(db_path: str):
        return _FakeRepo(db_path, roster_rows=roster_rows, picks_map=picks_map)

    monkeypatch.setattr(trades, "LeagueRepo", _fake_repo_factory)



def test_api_trade_lab_team_assets_success_filters_and_sorts(monkeypatch: pytest.MonkeyPatch) -> None:
    roster_rows = [
        {
            "player_id": "P3",
            "name": "Third",
            "pos": "PF",
            "age": 23,
            "ovr": 80,
            "salary_amount": 6000000,
            "attrs": {},
        },
        {
            "player_id": "P1",
            "name": "First",
            "pos": "SG",
            "age": 27,
            "ovr": 82,
            "salary_amount": 12000000,
            "attrs": {"injury": {"current": {"status": "OUT"}}},
        },
        {
            "player_id": "P2",
            "name": "Second",
            "pos": "PG",
            "age": 24,
            "ovr": 82,
            "salary_amount": 9000000,
            "attrs": {},
        },
    ]
    picks_map = {
        "HOU_2028_R1": {"owner_team": "HOU", "original_team": "HOU", "year": 2028, "round": 1, "protection": None},
        "HOU_2027_R1": {"owner_team": "HOU", "original_team": "BKN", "year": 2027, "round": 1, "protection": {"type": "TOP_N", "n": 10}},
        "HOU_2029_R2": {"owner_team": "HOU", "original_team": "HOU", "year": 2029, "round": 2, "protection": None},
        "MIA_2027_R1": {"owner_team": "MIA", "original_team": "MIA", "year": 2027, "round": 1, "protection": None},
    }
    _install_fake_dependencies(monkeypatch, roster_rows=roster_rows, picks_map=picks_map)

    out = asyncio.run(trades.api_trade_lab_team_assets("hou"))

    assert out["ok"] is True
    assert out["team_id"] == "HOU"
    assert out["current_date"] == "2025-12-11"

    # players: ovr desc, age asc, player_id asc
    assert [p["player_id"] for p in out["players"]] == ["P2", "P1", "P3"]
    assert out["players"][1]["injury"]["current"]["status"] == "OUT"

    # picks: owner_team == HOU and round == 1 only, sorted year asc
    assert [p["pick_id"] for p in out["first_round_picks"]] == ["HOU_2027_R1", "HOU_2028_R1"]
    assert all(int(p["round"]) == 1 for p in out["first_round_picks"])



def test_api_trade_lab_team_assets_invalid_team_returns_400(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_dependencies(monkeypatch, roster_rows=[], picks_map={})

    out = asyncio.run(trades.api_trade_lab_team_assets("NOT_A_TEAM"))

    assert out.status_code == 400
    body = json.loads(out.body.decode("utf-8"))
    assert body["ok"] is False
    assert body["error"]["code"] == "TRADE_LAB_INVALID_TEAM_ID"



def test_api_trade_lab_team_assets_reflects_current_date(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_dependencies(
        monkeypatch,
        roster_rows=[],
        picks_map={},
        current_date=date(2026, 1, 2),
    )

    out = asyncio.run(trades.api_trade_lab_team_assets("MIA"))

    assert out["current_date"] == "2026-01-02"



def test_api_trade_lab_team_assets_player_sort_tie_break_by_player_id(monkeypatch: pytest.MonkeyPatch) -> None:
    roster_rows = [
        {"player_id": "P20", "name": "Z", "pos": "C", "age": 24, "ovr": 81, "salary_amount": 1, "attrs": {}},
        {"player_id": "P10", "name": "A", "pos": "C", "age": 24, "ovr": 81, "salary_amount": 1, "attrs": {}},
    ]
    _install_fake_dependencies(monkeypatch, roster_rows=roster_rows, picks_map={})

    out = asyncio.run(trades.api_trade_lab_team_assets("MIA"))

    assert [p["player_id"] for p in out["players"]] == ["P10", "P20"]
