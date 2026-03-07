from __future__ import annotations

import asyncio

import pytest

import state
from state_modules.state_store import transaction


fastapi = pytest.importorskip("fastapi")
from app.api.routes import sim


def _seed_schedule_for_sim_hook() -> None:
    state.reset_state_for_dev()
    with transaction("test_seed_schedule_for_sim_hook") as s:
        s["active_season_id"] = "2025-26"
        s["league"]["season_year"] = 2025
        s["league"]["draft_year"] = 2026
        s["league"]["current_date"] = "2025-11-02"
        s["league"]["master_schedule"]["games"] = [
            {
                "game_id": "G1",
                "date": "2025-11-01",
                "phase": "regular",
                "status": "final",
                "home_team_id": "BOS",
                "away_team_id": "NYK",
                "home_score": 110,
                "away_score": 99,
            },
            {
                "game_id": "G2",
                "date": "2025-11-02",
                "phase": "regular",
                "status": "final",
                "home_team_id": "LAL",
                "away_team_id": "BOS",
                "home_score": 90,
                "away_score": 95,
            },
        ]
        s["league"]["master_schedule"]["by_id"] = {
            "G1": s["league"]["master_schedule"]["games"][0],
            "G2": s["league"]["master_schedule"]["games"][1],
        }


def test_apply_standings_cache_incremental_updates_applies_games() -> None:
    _seed_schedule_for_sim_hook()

    out = sim._apply_standings_cache_incremental_updates(game_ids=["G1", "G2"])
    cache = state.get_standings_cache_snapshot()

    assert out["candidates"] == 2
    assert out["applied"] == 2
    assert cache["applied_game_ids"]["G1"] is True
    assert cache["records_by_team"]["BOS"]["wins"] == 2
    assert cache["records_by_team"]["BOS"]["losses"] == 0


def test_api_advance_league_applies_incremental_hook(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[list[str]] = []
    orchestration_called: list[dict[str, object]] = []

    monkeypatch.setattr(sim, "advance_league_until", lambda target_date_str, user_team_id=None: [{"game_id": "G1"}, {"game_id": "G2"}])
    monkeypatch.setattr(sim, "_run_monthly_checkpoints", lambda **_kwargs: ([], []))
    monkeypatch.setattr(sim.state, "get_current_date_as_date", lambda: __import__("datetime").date(2025, 11, 1))
    monkeypatch.setattr(sim.state, "get_db_path", lambda: "dummy.db")
    monkeypatch.setattr(
        sim,
        "_apply_standings_cache_incremental_updates",
        lambda *, game_ids: called.append([str(x) for x in game_ids]) or {"candidates": 2, "applied": 2, "missing": 0},
    )
    monkeypatch.setattr(
        sim,
        "_run_daily_trade_orchestration",
        lambda *, user_team_id: orchestration_called.append({"user_team_id": user_team_id}) or {"ok": True, "tick_date": "2025-11-02"},
    )

    req = sim.AdvanceLeagueRequest(target_date="2025-11-02", user_team_id="BOS")
    out = asyncio.run(sim.api_advance_league(req))

    assert out["simulated_count"] == 2
    assert called == [["G1", "G2"]]
    assert orchestration_called == [{"user_team_id": "BOS"}]
    assert out["trade_orchestration"]["ok"] is True


def test_api_progress_next_user_game_day_collects_all_game_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[list[str]] = []
    orchestration_called: list[dict[str, object]] = []

    monkeypatch.setattr(
        sim,
        "progress_next_user_game_day",
        lambda _user_team_id, mode="auto_if_needed": {
            "auto_advance": {"simulated_game_ids": ["GA1", "GA2"]},
            "game_day": {
                "user_game": {"game_id": "UG1"},
                "other_game_ids": ["OG1"],
            },
        },
    )
    monkeypatch.setattr(sim, "_run_monthly_checkpoints", lambda **_kwargs: ([], []))
    monkeypatch.setattr(sim.state, "get_current_date_as_date", lambda: __import__("datetime").date(2025, 11, 2))
    monkeypatch.setattr(sim.state, "get_db_path", lambda: "dummy.db")
    monkeypatch.setattr(
        sim,
        "_apply_standings_cache_incremental_updates",
        lambda *, game_ids: called.append([str(x) for x in game_ids]) or {"candidates": 4, "applied": 4, "missing": 0},
    )
    monkeypatch.setattr(
        sim,
        "_run_daily_trade_orchestration",
        lambda *, user_team_id: orchestration_called.append({"user_team_id": user_team_id}) or {"ok": True, "tick_date": "2025-11-02"},
    )

    req = sim.ProgressNextUserGameDayRequest(user_team_id="BOS", mode="auto_if_needed")
    _ = asyncio.run(sim.api_progress_next_user_game_day(req))

    assert called == [["GA1", "GA2", "UG1", "OG1"]]
    assert orchestration_called == [{"user_team_id": "BOS"}]


def test_api_auto_advance_to_next_user_game_day_triggers_trade_orchestration(monkeypatch: pytest.MonkeyPatch) -> None:
    orchestration_called: list[dict[str, object]] = []

    monkeypatch.setattr(
        sim,
        "auto_advance_to_next_user_game_day",
        lambda _user_team_id: {
            "auto_advance": {"simulated_game_ids": ["GA1", "GA2"]},
        },
    )
    monkeypatch.setattr(sim, "_run_monthly_checkpoints", lambda **_kwargs: ([], []))
    monkeypatch.setattr(sim.state, "get_current_date_as_date", lambda: __import__("datetime").date(2025, 11, 2))
    monkeypatch.setattr(sim.state, "get_db_path", lambda: "dummy.db")
    monkeypatch.setattr(sim, "_apply_standings_cache_incremental_updates", lambda *, game_ids: {"candidates": len(list(game_ids or [])), "applied": 0, "missing": 0})
    monkeypatch.setattr(
        sim,
        "_run_daily_trade_orchestration",
        lambda *, user_team_id: orchestration_called.append({"user_team_id": user_team_id}) or {"ok": True, "tick_date": "2025-11-02"},
    )

    req = sim.AutoAdvanceToNextUserGameDayRequest(user_team_id="BOS")
    out = asyncio.run(sim.api_auto_advance_to_next_user_game_day(req))

    assert orchestration_called == [{"user_team_id": "BOS"}]
    assert out["trade_orchestration"]["ok"] is True
