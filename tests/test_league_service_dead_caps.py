from __future__ import annotations

from pathlib import Path

from league_repo import LeagueRepo
from league_service import LeagueService
import state


def _mk_repo(tmp_path: Path) -> LeagueRepo:
    state.reset_state_for_dev()
    state.set_current_date("2026-07-01")
    state.set_active_season_id("2026-27")
    db_path = tmp_path / "dead_caps_service_test.sqlite"
    repo = LeagueRepo(str(db_path))
    repo.init_db()
    return repo


def _seed_player_with_contract(repo: LeagueRepo, *, player_id: str, team_id: str, contract_id: str, salary_map: dict[int, int]) -> None:
    now = "2026-07-01T00:00:00Z"
    repo._conn.execute(
        """
        INSERT INTO players(player_id, name, pos, age, exp, height_in, weight_lb, ovr, attrs_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """,
        (str(player_id), str(player_id), "F", 27, 5, 79, 225, 80, "{}", now, now),
    )
    repo._conn.execute(
        """
        INSERT INTO roster(player_id, team_id, salary_amount, status, updated_at)
        VALUES (?, ?, ?, 'active', ?);
        """,
        (str(player_id), str(team_id).upper(), int(salary_map[min(salary_map.keys())]), now),
    )
    salary_by_year_json = "{" + ",".join([f'\"{int(k)}\":{int(v)}' for k, v in sorted(salary_map.items())]) + "}"
    repo._conn.execute(
        """
        INSERT INTO contracts(
            contract_id, player_id, team_id, start_season_id, end_season_id,
            salary_by_season_json, contract_type, is_active, created_at, updated_at,
            signed_date, start_season_year, years, options_json, status, contract_json
        )
        VALUES (?, ?, ?, NULL, NULL, ?, 'STANDARD', 1, ?, ?, ?, ?, ?, '[]', 'ACTIVE', '{}');
        """,
        (
            str(contract_id),
            str(player_id),
            str(team_id).upper(),
            salary_by_year_json,
            now,
            now,
            "2026-07-01",
            min(salary_map.keys()),
            len(salary_map),
        ),
    )
    repo._conn.execute(
        "INSERT INTO active_contracts(player_id, contract_id, updated_at) VALUES (?, ?, ?);",
        (str(player_id), str(contract_id), now),
    )
    repo._conn.commit()


def test_waive_player_creates_dead_cap_rows_and_moves_to_fa(tmp_path: Path) -> None:
    with _mk_repo(tmp_path) as repo:
        _seed_player_with_contract(
            repo,
            player_id="P000001",
            team_id="BOS",
            contract_id="C0001",
            salary_map={2026: 11_000_000, 2027: 12_000_000, 2028: 13_000_000},
        )
        svc = LeagueService(repo)
        ev = svc.waive_player(team_id="BOS", player_id="P000001", waived_date="2026-07-01")

        assert ev.type == "waive_player"
        assert ev.payload["to_team"] == "FA"
        assert ev.payload["dead_cap_schedule"] == {2026: 11_000_000, 2027: 12_000_000, 2028: 13_000_000}

        roster_row = repo._conn.execute(
            "SELECT team_id FROM roster WHERE player_id='P000001';"
        ).fetchone()
        assert roster_row is not None
        assert str(roster_row["team_id"]).upper() == "FA"

        assert repo.sum_active_dead_caps("BOS", 2026) == 11_000_000
        assert repo.sum_active_dead_caps("BOS", 2027) == 12_000_000
        assert repo.sum_active_dead_caps("BOS", 2028) == 13_000_000


def test_stretch_player_splits_dead_cap_and_cap_sum_includes_dead_caps(tmp_path: Path) -> None:
    with _mk_repo(tmp_path) as repo:
        _seed_player_with_contract(
            repo,
            player_id="P000002",
            team_id="BOS",
            contract_id="C0002",
            salary_map={2026: 11_000_000, 2027: 12_000_000, 2028: 13_000_000},
        )
        svc = LeagueService(repo)
        ev = svc.stretch_player(
            team_id="BOS",
            player_id="P000002",
            stretch_years=7,
            stretched_date="2026-07-01",
        )

        assert ev.type == "stretch_player"
        sched = ev.payload["dead_cap_schedule"]
        assert len(sched) == 7
        assert sum(int(v) for v in sched.values()) == 36_000_000

        with svc._atomic() as cur:
            cap_2026 = svc._compute_team_cap_salary_with_holds_in_cur(cur, "BOS", 2026)
        # Player moved to FA, so payroll becomes 0; cap salary should equal this season dead cap amount.
        assert cap_2026 == int(sched[2026])
