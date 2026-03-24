from __future__ import annotations

from pathlib import Path

from league_repo import LeagueRepo
from league_service import CapViolationError, LeagueService
from state_modules.state_store import transaction
import state


def _configure_state_for_contracts() -> None:
    state.reset_state_for_dev()
    state.set_current_date("2027-07-01")
    with transaction("test_configure_bird_re_sign") as s:
        s["active_season_id"] = "2027-28"
        s["league"]["season_year"] = 2027
        s["league"]["draft_year"] = 2028
        s["league"]["trade_rules"] = {
            "salary_cap": 200_000_000,
            "first_apron": 220_000_000,
            "second_apron": 240_000_000,
            "contract_aav_max_pct_by_exp": {
                "le_6": 0.25,
                "7_9": 0.30,
                "ge_10": 0.35,
            },
        }


def _seed_player_for_re_sign(repo: LeagueRepo, *, pid: str, salary: int) -> None:
    now = "2027-07-01T00:00:00Z"
    repo._conn.execute(
        """
        INSERT INTO players(player_id, name, pos, age, exp, height_in, weight_lb, ovr, attrs_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """,
        (pid, "Bird Test", "G", 27, 6, 76, 200, 75, "{}", now, now),
    )
    repo._conn.execute(
        """
        INSERT INTO roster(player_id, team_id, salary_amount, status, updated_at)
        VALUES (?, ?, ?, 'active', ?);
        """,
        (pid, "FA", salary, now),
    )
    repo._conn.commit()


def test_re_sign_bird_non_success_and_releases_cap_hold(tmp_path: Path) -> None:
    _configure_state_for_contracts()
    db_path = tmp_path / "svc_bird_success.sqlite"

    with LeagueRepo(str(db_path)) as repo:
        repo.init_db()
        _seed_player_for_re_sign(repo, pid="P100001", salary=10_000_000)
        repo.upsert_team_bird_rights(
            [{
                "season_year": 2027,
                "team_id": "BOS",
                "player_id": "P100001",
                "bird_type": "NON_BIRD",
                "tenure_years_same_team": 1,
                "is_renounced": 0,
            }]
        )
        repo.upsert_team_cap_holds(
            [{
                "season_year": 2027,
                "team_id": "BOS",
                "player_id": "P100001",
                "source_type": "BIRD",
                "bird_type": "NON_BIRD",
                "hold_amount": 12_000_000,
                "is_released": 0,
            }]
        )

        svc = LeagueService(repo)
        event = svc.re_sign(
            "BOS",
            "P100001",
            contract_channel="BIRD_NON",
            signed_date="2027-07-01",
            years=2,
            salary_by_year={2027: 12_000_000, 2028: 12_600_000},
        )

        assert event.payload["contract_channel"] == "BIRD_NON"
        holds = repo.list_team_cap_holds("BOS", 2027, active_only=False)
        assert len(holds) == 1
        assert int(holds[0]["is_released"]) == 1
        assert holds[0]["released_reason"] == "SIGNED"


def test_re_sign_bird_non_raise_limit_exceeded(tmp_path: Path) -> None:
    _configure_state_for_contracts()
    db_path = tmp_path / "svc_bird_raise_fail.sqlite"

    with LeagueRepo(str(db_path)) as repo:
        repo.init_db()
        _seed_player_for_re_sign(repo, pid="P100002", salary=10_000_000)
        repo.upsert_team_bird_rights(
            [{
                "season_year": 2027,
                "team_id": "BOS",
                "player_id": "P100002",
                "bird_type": "NON_BIRD",
                "tenure_years_same_team": 1,
                "is_renounced": 0,
            }]
        )

        svc = LeagueService(repo)
        try:
            svc.re_sign(
                "BOS",
                "P100002",
                contract_channel="BIRD_NON",
                signed_date="2027-07-01",
                years=2,
                salary_by_year={2027: 12_000_000, 2028: 13_000_000},  # >5% raise
            )
            assert False, "expected CapViolationError"
        except CapViolationError as exc:
            assert exc.code == "BIRD_RAISE_LIMIT_EXCEEDED"


def test_re_sign_bird_channel_rejects_renounced_right(tmp_path: Path) -> None:
    _configure_state_for_contracts()
    db_path = tmp_path / "svc_bird_renounced.sqlite"

    with LeagueRepo(str(db_path)) as repo:
        repo.init_db()
        _seed_player_for_re_sign(repo, pid="P100003", salary=10_000_000)
        repo.upsert_team_bird_rights(
            [{
                "season_year": 2027,
                "team_id": "BOS",
                "player_id": "P100003",
                "bird_type": "NON_BIRD",
                "tenure_years_same_team": 1,
                "is_renounced": 1,
            }]
        )

        svc = LeagueService(repo)
        try:
            svc.re_sign(
                "BOS",
                "P100003",
                contract_channel="BIRD_NON",
                signed_date="2027-07-01",
                years=2,
                salary_by_year={2027: 12_000_000, 2028: 12_600_000},
            )
            assert False, "expected CapViolationError"
        except CapViolationError as exc:
            assert exc.code == "BIRD_RIGHT_NOT_AVAILABLE"


def test_re_sign_bird_full_years_exceeded(tmp_path: Path) -> None:
    _configure_state_for_contracts()
    db_path = tmp_path / "svc_bird_years_fail.sqlite"

    with LeagueRepo(str(db_path)) as repo:
        repo.init_db()
        _seed_player_for_re_sign(repo, pid="P100004", salary=10_000_000)
        repo.upsert_team_bird_rights(
            [{
                "season_year": 2027,
                "team_id": "BOS",
                "player_id": "P100004",
                "bird_type": "FULL_BIRD",
                "tenure_years_same_team": 3,
                "is_renounced": 0,
            }]
        )

        svc = LeagueService(repo)
        try:
            svc.re_sign(
                "BOS",
                "P100004",
                contract_channel="BIRD_FULL",
                signed_date="2027-07-01",
                years=6,
                salary_by_year={
                    2027: 20_000_000,
                    2028: 21_600_000,
                    2029: 23_328_000,
                    2030: 25_194_240,
                    2031: 27_209_779,
                    2032: 29_386_561,
                },
            )
            assert False, "expected CapViolationError"
        except CapViolationError as exc:
            assert exc.code == "BIRD_YEARS_EXCEEDED"


def test_re_sign_rejects_non_bird_contract_channel(tmp_path: Path) -> None:
    _configure_state_for_contracts()
    db_path = tmp_path / "svc_bird_non_bird_channel.sqlite"

    with LeagueRepo(str(db_path)) as repo:
        repo.init_db()
        _seed_player_for_re_sign(repo, pid="P100005", salary=10_000_000)
        repo.upsert_team_bird_rights(
            [{
                "season_year": 2027,
                "team_id": "BOS",
                "player_id": "P100005",
                "bird_type": "NON_BIRD",
                "tenure_years_same_team": 1,
                "is_renounced": 0,
            }]
        )

        svc = LeagueService(repo)
        try:
            svc.re_sign(
                "BOS",
                "P100005",
                contract_channel="STANDARD_FA",
                signed_date="2027-07-01",
                years=2,
                salary_by_year={2027: 12_000_000, 2028: 12_600_000},
            )
            assert False, "expected CapViolationError"
        except CapViolationError as exc:
            assert exc.code == "BIRD_CHANNEL_REQUIRED"


def test_sign_free_agent_with_minimum_channel_is_supported(tmp_path: Path) -> None:
    _configure_state_for_contracts()
    db_path = tmp_path / "svc_sign_fa_minimum.sqlite"

    with LeagueRepo(str(db_path)) as repo:
        repo.init_db()
        _seed_player_for_re_sign(repo, pid="P100006", salary=1_000_000)

        svc = LeagueService(repo)
        event = svc.sign_free_agent_with_channel(
            "BOS",
            "P100006",
            contract_channel="MINIMUM",
            signed_date="2027-07-01",
            years=1,
            salary_by_year={2027: 1_000_000},
        )

        assert event.type == "sign_free_agent"
        assert event.payload["to_team"] == "BOS"
