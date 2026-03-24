from __future__ import annotations

from pathlib import Path

import state
from contracts.offseason import process_offseason
from league_repo import LeagueRepo


def test_offseason_creates_bird_rights_and_cap_holds_for_released_player(tmp_path: Path) -> None:
    state.reset_state_for_dev()
    state.set_current_date("2027-07-01")

    db_path = tmp_path / "offseason_bird.sqlite"
    game_state = {
        "league": {
            "db_path": str(db_path),
        }
    }

    with LeagueRepo(str(db_path)) as repo:
        repo.init_db()
        now = "2027-07-01T00:00:00Z"

        # Seed one player on BOS with a 2-year deal ending at 2026 season.
        repo._conn.execute(
            """
            INSERT INTO players(player_id, name, pos, age, exp, height_in, weight_lb, ovr, attrs_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            ("P000001", "Test Player", "G", 26, 2, 76, 200, 75, "{}", now, now),
        )
        repo._conn.execute(
            """
            INSERT INTO roster(player_id, team_id, salary_amount, status, updated_at)
            VALUES (?, ?, ?, 'active', ?);
            """,
            ("P000001", "BOS", 10_000_000, now),
        )
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
                "C_BIRD_1",
                "P000001",
                "BOS",
                "2025-26",
                "2026-27",
                '{"2025":9000000,"2026":10000000}',
                "STANDARD",
                now,
                now,
                "2025-07-01",
                2025,
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
            ("P000001", "C_BIRD_1", now),
        )
        repo._conn.commit()

    out = process_offseason(
        game_state,
        from_season_year=2026,
        to_season_year=2027,
        decision_date_iso="2027-07-01",
    )

    transition = out.get("contracts_transition") or {}
    assert int(transition.get("released") or 0) == 1
    assert int(transition.get("bird_rights_created") or 0) == 1
    assert int(transition.get("cap_holds_created") or 0) == 1

    with LeagueRepo(str(db_path)) as repo:
        right = repo.get_bird_right("P000001", "BOS", 2027)
        assert right is not None
        assert right["bird_type"] == "EARLY_BIRD"
        assert int(right["tenure_years_same_team"]) == 2

        holds = repo.list_team_cap_holds("BOS", 2027, active_only=True)
        assert len(holds) == 1
        assert int(holds[0]["hold_amount"]) == 13_000_000  # 10m * 1.3
        assert holds[0]["bird_type"] == "EARLY_BIRD"
