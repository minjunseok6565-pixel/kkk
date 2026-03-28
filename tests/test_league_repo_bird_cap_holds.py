from __future__ import annotations

import json
from pathlib import Path

from league_repo import LeagueRepo
import state


def _mk_repo(tmp_path: Path) -> LeagueRepo:
    state.reset_state_for_dev()
    state.set_current_date("2026-07-01")
    db_path = tmp_path / "bird_rights_test.sqlite"
    repo = LeagueRepo(str(db_path))
    repo.init_db()
    return repo


def _insert_player(repo: LeagueRepo, *, player_id: str, name: str = "Player") -> None:
    now = "2026-07-01T00:00:00Z"
    repo._conn.execute(
        """
        INSERT INTO players(player_id, name, pos, age, exp, height_in, weight_lb, ovr, attrs_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """,
        (str(player_id), str(name), "G", 25, 3, 76, 200, 75, "{}", now, now),
    )


def _insert_active_roster(repo: LeagueRepo, *, player_id: str, team_id: str, salary_amount: int) -> None:
    now = "2026-07-01T00:00:00Z"
    repo._conn.execute(
        """
        INSERT INTO roster(player_id, team_id, salary_amount, status, updated_at)
        VALUES (?, ?, ?, 'active', ?);
        """,
        (str(player_id), str(team_id).upper(), int(salary_amount), now),
    )


def test_init_db_creates_bird_tables(tmp_path: Path) -> None:
    with _mk_repo(tmp_path) as repo:
        rows = repo._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table';"
        ).fetchall()
        names = {str(r[0]) for r in rows}

    assert "team_bird_rights" in names
    assert "team_cap_holds" in names
    assert "team_dead_caps" in names


def test_bird_rights_upsert_list_get_renounce(tmp_path: Path) -> None:
    with _mk_repo(tmp_path) as repo:
        repo.upsert_team_bird_rights(
            [
                {
                    "season_year": 2027,
                    "team_id": "lal",
                    "player_id": "p1",
                    "bird_type": "early_bird",
                    "tenure_years_same_team": 2,
                }
            ]
        )

        got = repo.get_bird_right("p1", "LAL", 2027)
        assert got is not None
        assert got["bird_type"] == "EARLY_BIRD"
        assert got["is_renounced"] == 0

        listed = repo.list_team_bird_rights("LAL", 2027)
        assert len(listed) == 1
        assert listed[0]["player_id"] == "p1"

        changed = repo.renounce_bird_right("p1", "LAL", 2027, "2027-07-01T00:00:00Z")
        assert changed is True

        got2 = repo.get_bird_right("p1", "LAL", 2027)
        assert got2 is not None
        assert got2["is_renounced"] == 1


def test_cap_holds_upsert_list_sum_release(tmp_path: Path) -> None:
    with _mk_repo(tmp_path) as repo:
        repo.upsert_team_cap_holds(
            [
                {
                    "season_year": 2027,
                    "team_id": "BOS",
                    "player_id": "p1",
                    "source_type": "BIRD",
                    "bird_type": "FULL_BIRD",
                    "hold_amount": 15000000,
                },
                {
                    "season_year": 2027,
                    "team_id": "BOS",
                    "player_id": "p2",
                    "source_type": "BIRD",
                    "bird_type": "NON_BIRD",
                    "hold_amount": 6000000,
                },
            ]
        )

        assert repo.sum_active_cap_holds("BOS", 2027) == 21000000
        active = repo.list_team_cap_holds("BOS", 2027, active_only=True)
        assert len(active) == 2

        released = repo.release_cap_hold("p2", "BOS", 2027, "RENOUNCE", "2027-07-01T00:00:00Z")
        assert released is True

        assert repo.sum_active_cap_holds("BOS", 2027) == 15000000
        active2 = repo.list_team_cap_holds("BOS", 2027, active_only=True)
        assert [r["player_id"] for r in active2] == ["p1"]

        all_rows = repo.list_team_cap_holds("BOS", 2027, active_only=False)
        by_pid = {r["player_id"]: r for r in all_rows}
        assert by_pid["p2"]["is_released"] == 1
        assert by_pid["p2"]["released_reason"] == "RENOUNCE"


def test_dead_caps_upsert_list_sum_void(tmp_path: Path) -> None:
    with _mk_repo(tmp_path) as repo:
        repo.upsert_team_dead_caps(
            [
                {
                    "team_id": "BOS",
                    "player_id": "p1",
                    "origin_contract_id": "C1",
                    "source_type": "WAIVE",
                    "applied_season_year": 2027,
                    "amount": 11_000_000,
                    "meta_json": {"kind": "waive"},
                },
                {
                    "team_id": "BOS",
                    "player_id": "p1",
                    "origin_contract_id": "C1",
                    "source_type": "WAIVE",
                    "applied_season_year": 2028,
                    "amount": 12_000_000,
                    "meta_json": {"kind": "waive"},
                },
                {
                    "team_id": "BOS",
                    "player_id": "p2",
                    "origin_contract_id": "C2",
                    "source_type": "STRETCH",
                    "applied_season_year": 2027,
                    "amount": 6_000_000,
                    "meta_json": {"kind": "stretch"},
                },
            ]
        )
        # ON CONFLICT(update) path: same logical key updates amount.
        repo.upsert_team_dead_caps(
            [
                {
                    "team_id": "BOS",
                    "player_id": "p2",
                    "origin_contract_id": "C2",
                    "source_type": "STRETCH",
                    "applied_season_year": 2027,
                    "amount": 6_500_000,
                    "meta_json": {"kind": "stretch", "updated": True},
                }
            ]
        )

        assert repo.sum_active_dead_caps("BOS", 2027) == 17_500_000
        assert repo.sum_active_dead_caps("BOS", 2028) == 12_000_000

        active_2027 = repo.list_team_dead_caps("BOS", 2027, active_only=True)
        assert len(active_2027) == 2
        by_pid = {r["player_id"]: r for r in active_2027}
        assert by_pid["p2"]["amount"] == 6_500_000
        assert isinstance(by_pid["p2"]["meta_json"], dict)
        assert by_pid["p2"]["meta_json"]["updated"] is True

        changed = repo.void_dead_caps("p2", "BOS", "CORRECTION", "2027-07-01T00:00:00Z", season_year=2027)
        assert changed == 1
        assert repo.sum_active_dead_caps("BOS", 2027) == 11_000_000
        all_2027 = repo.list_team_dead_caps("BOS", 2027, active_only=False)
        by_pid_all = {r["player_id"]: r for r in all_2027}
        assert by_pid_all["p2"]["is_voided"] == 1
        assert by_pid_all["p2"]["voided_reason"] == "CORRECTION"


def test_get_league_average_salary_for_season(tmp_path: Path) -> None:
    with _mk_repo(tmp_path) as repo:
        # p1: contract season salary should override roster salary
        _insert_player(repo, player_id="p1", name="P1")
        _insert_active_roster(repo, player_id="p1", team_id="LAL", salary_amount=1_000_000)

        # p2: no active contract -> roster salary fallback
        _insert_player(repo, player_id="p2", name="P2")
        _insert_active_roster(repo, player_id="p2", team_id="BOS", salary_amount=2_000_000)

        # p3: FA should be excluded
        _insert_player(repo, player_id="p3", name="P3")
        _insert_active_roster(repo, player_id="p3", team_id="FA", salary_amount=50_000_000)

        now = "2026-07-01T00:00:00Z"
        repo._conn.execute(
            """
            INSERT INTO contracts(
                contract_id, player_id, team_id,
                start_season_id, end_season_id,
                salary_by_season_json, contract_type,
                is_active, created_at, updated_at,
                signed_date, start_season_year, years,
                options_json, status, contract_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                "c1",
                "p1",
                "LAL",
                "2026-27",
                "2028-29",
                json.dumps({"2027": 3_000_000}),
                "STANDARD",
                now,
                now,
                "2026-07-01",
                2027,
                2,
                "[]",
                "ACTIVE",
                "{}",
            ),
        )
        repo._conn.execute(
            """
            INSERT INTO active_contracts(player_id, contract_id, updated_at)
            VALUES (?, ?, ?);
            """,
            ("p1", "c1", now),
        )
        repo._conn.commit()

        avg = repo.get_league_average_salary(2027)
        assert avg == 2_500_000
