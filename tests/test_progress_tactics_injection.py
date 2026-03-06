from __future__ import annotations

from datetime import date

import sim.league_sim as league_sim


def test_progress_next_user_game_day_injects_saved_tactics_for_user_away(monkeypatch):
    calls = {"simulate": None}

    monkeypatch.setattr(league_sim, "get_current_date_as_date", lambda: date(2025, 11, 1))

    entries = [
        {"date": "2025-11-03", "home_team_id": "NYK", "away_team_id": "BOS"},
        {"date": "2025-11-03", "home_team_id": "NYK", "away_team_id": "BOS"},
    ]
    monkeypatch.setattr(league_sim, "_find_next_user_game_entry", lambda **_kwargs: entries.pop(0))
    monkeypatch.setattr(league_sim, "advance_league_until", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(league_sim, "set_current_date", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(league_sim, "get_team_tactics_snapshot", lambda _tid: {"tactics": {"offenseScheme": "Spread_HeavyPnR"}})

    def _fake_simulate_single_game(**kwargs):
        calls["simulate"] = kwargs
        return {"game_id": "UG1", "home_team_id": kwargs["home_team_id"], "away_team_id": kwargs["away_team_id"]}

    monkeypatch.setattr(league_sim, "simulate_single_game", _fake_simulate_single_game)

    out = league_sim.progress_next_user_game_day("BOS", mode="auto_if_needed")

    assert out["game_day"]["user_game"]["game_id"] == "UG1"
    assert calls["simulate"]["home_tactics"] is None
    assert calls["simulate"]["away_tactics"] == {"offenseScheme": "Spread_HeavyPnR"}


def test_progress_next_user_game_day_keeps_tactics_none_when_not_saved(monkeypatch):
    calls = {"simulate": None}

    monkeypatch.setattr(league_sim, "get_current_date_as_date", lambda: date(2025, 11, 3))

    entries = [
        {"date": "2025-11-03", "home_team_id": "BOS", "away_team_id": "MIA"},
        {"date": "2025-11-03", "home_team_id": "BOS", "away_team_id": "MIA"},
    ]
    monkeypatch.setattr(league_sim, "_find_next_user_game_entry", lambda **_kwargs: entries.pop(0))
    monkeypatch.setattr(league_sim, "advance_league_until", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(league_sim, "set_current_date", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(league_sim, "get_team_tactics_snapshot", lambda _tid: {})

    def _fake_simulate_single_game(**kwargs):
        calls["simulate"] = kwargs
        return {"game_id": "UG2", "home_team_id": kwargs["home_team_id"], "away_team_id": kwargs["away_team_id"]}

    monkeypatch.setattr(league_sim, "simulate_single_game", _fake_simulate_single_game)

    out = league_sim.progress_next_user_game_day("BOS", mode="auto_if_needed")

    assert out["game_day"]["user_game"]["game_id"] == "UG2"
    assert calls["simulate"]["home_tactics"] is None
    assert calls["simulate"]["away_tactics"] is None
