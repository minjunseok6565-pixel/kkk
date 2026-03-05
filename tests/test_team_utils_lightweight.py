from __future__ import annotations

from contextlib import contextmanager

import team_utils


def test_compute_team_records_reads_from_lightweight_schedule_accessor(monkeypatch):
    monkeypatch.setattr(team_utils, "get_league_schedule_snapshot", lambda: {
        "master_schedule": {
            "games": [
                {"status": "final", "home_team_id": "BOS", "away_team_id": "LAL", "home_score": 101, "away_score": 99},
                {"status": "scheduled", "home_team_id": "BOS", "away_team_id": "LAL", "home_score": None, "away_score": None},
            ]
        }
    })
    monkeypatch.setattr(team_utils, "_list_active_team_ids", lambda: ["BOS", "LAL"])

    records = team_utils._compute_team_records()

    assert records["BOS"]["wins"] == 1
    assert records["LAL"]["losses"] == 1
    assert records["BOS"]["pf"] == 101
    assert records["LAL"]["pa"] == 101


def test_get_conference_standings_table_uses_lightweight_schedule_accessor(monkeypatch):
    monkeypatch.setattr(team_utils, "get_league_schedule_snapshot", lambda: {
        "master_schedule": {
            "games": [
                {"status": "final", "phase": "regular", "home_team_id": "BOS", "away_team_id": "NYK", "home_score": 100, "away_score": 90},
                {"status": "final", "phase": "regular", "home_team_id": "BOS", "away_team_id": "NYK", "home_score": 95, "away_score": 99},
            ]
        }
    })
    monkeypatch.setattr(team_utils, "_list_active_team_ids", lambda: ["BOS", "NYK"])

    table = team_utils.get_conference_standings_table()

    east = table["east"]
    assert len(east) >= 1
    bos = next(r for r in east if r["team_id"] == "BOS")
    assert bos["wins"] == 1
    assert bos["losses"] == 1


def test_get_team_detail_uses_player_stats_lightweight_accessor(monkeypatch):
    called = {"player_stats": 0}

    def _player_stats(*, phase="regular"):
        called["player_stats"] += 1
        assert phase == "regular"
        return {"p1": {"games": 10, "totals": {"PTS": 200}}}

    @contextmanager
    def _fake_repo_ctx():
        class _Repo:
            def get_team_roster(self, _tid):
                return []

        yield _Repo()

    monkeypatch.setattr(team_utils, "get_player_stats_snapshot", _player_stats)
    monkeypatch.setattr(team_utils, "_list_active_team_ids", lambda: ["BOS"])
    monkeypatch.setattr(team_utils, "_compute_team_records", lambda: {"BOS": {"wins": 1, "losses": 0, "pf": 100, "pa": 90}})
    monkeypatch.setattr(team_utils, "get_conference_standings", lambda: {"east": [{"team_id": "BOS", "rank": 1, "gb": 0.0}], "west": []})
    monkeypatch.setattr(team_utils, "_compute_team_payroll", lambda _tid: 120.0)
    monkeypatch.setattr(team_utils, "_compute_cap_space", lambda _tid: 15.0)
    monkeypatch.setattr(team_utils, "ui_teams_get", lambda: {})
    monkeypatch.setattr(team_utils, "_repo_ctx", _fake_repo_ctx)
    monkeypatch.setattr(team_utils, "get_league_context_snapshot", lambda: {"season_year": 2025})

    out = team_utils.get_team_detail("BOS")

    assert out["summary"]["team_id"] == "BOS"
    assert called["player_stats"] == 1
