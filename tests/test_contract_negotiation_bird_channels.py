from __future__ import annotations

from pathlib import Path

import pytest

import state
from contracts.negotiation.service import (
    _validate_offer_channel_policy,
    start_contract_negotiation,
)
from contracts.negotiation.types import ContractOffer
from contracts.negotiation.errors import (
    ContractNegotiationError,
    NEGOTIATION_INVALID_MODE,
    NEGOTIATION_INVALID_OFFER,
)
from league_repo import LeagueRepo
from state_modules.state_store import transaction


def _configure_state(db_path: Path) -> None:
    state.reset_state_for_dev()
    state.set_current_date("2027-07-01")
    state.set_db_path(str(db_path))
    with transaction("test_negotiation_bird_state") as s:
        s["active_season_id"] = "2027-28"
        s["league"]["season_year"] = 2027
        s["league"]["draft_year"] = 2028
        s["league"]["trade_rules"] = {
            "salary_cap": 200_000_000,
            "first_apron": 220_000_000,
            "second_apron": 240_000_000,
        }


def _seed_re_sign_player(repo: LeagueRepo, *, pid: str = "P200001", team_id: str = "FA") -> None:
    now = "2027-07-01T00:00:00Z"
    repo._conn.execute(
        """
        INSERT INTO players(player_id, name, pos, age, exp, height_in, weight_lb, ovr, attrs_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """,
        (pid, "Negotiation Player", "G", 27, 4, 76, 200, 75, "{}", now, now),
    )
    repo._conn.execute(
        """
        INSERT INTO roster(player_id, team_id, salary_amount, status, updated_at)
        VALUES (?, ?, ?, 'active', ?);
        """,
        (pid, team_id, 10_000_000, now),
    )
    repo._conn.commit()


def test_start_re_sign_negotiation_exposes_bird_channel_when_right_available(tmp_path: Path) -> None:
    db_path = tmp_path / "neg_bird_channels.sqlite"
    _configure_state(db_path)

    with LeagueRepo(str(db_path)) as repo:
        repo.init_db()
        _seed_re_sign_player(repo)
        repo.upsert_team_bird_rights(
            [{
                "season_year": 2027,
                "team_id": "BOS",
                "player_id": "P200001",
                "bird_type": "NON_BIRD",
                "tenure_years_same_team": 1,
                "is_renounced": 0,
            }]
        )

        session = start_contract_negotiation(
            db_path=str(db_path),
            team_id="BOS",
            player_id="P200001",
            mode="RE_SIGN",
            repo=repo,
        )

    channels = [str(x).upper() for x in (session.get("available_contract_channels") or [])]
    assert channels == ["BIRD_NON"]


def test_start_re_sign_negotiation_rejects_when_bird_right_renounced(tmp_path: Path) -> None:
    db_path = tmp_path / "neg_bird_channels_renounced.sqlite"
    _configure_state(db_path)

    with LeagueRepo(str(db_path)) as repo:
        repo.init_db()
        _seed_re_sign_player(repo, pid="P200002")
        repo.upsert_team_bird_rights(
            [{
                "season_year": 2027,
                "team_id": "BOS",
                "player_id": "P200002",
                "bird_type": "NON_BIRD",
                "tenure_years_same_team": 1,
                "is_renounced": 1,
            }]
        )

        with pytest.raises(ContractNegotiationError) as exc:
            start_contract_negotiation(
                db_path=str(db_path),
                team_id="BOS",
                player_id="P200002",
                mode="RE_SIGN",
                repo=repo,
            )

    assert exc.value.code == NEGOTIATION_INVALID_MODE


def test_validate_offer_channel_policy_rejects_renounced_bird_offer(tmp_path: Path) -> None:
    db_path = tmp_path / "neg_bird_validate.sqlite"
    _configure_state(db_path)

    with LeagueRepo(str(db_path)) as repo:
        repo.init_db()
        _seed_re_sign_player(repo, pid="P200003")
        repo.upsert_team_bird_rights(
            [{
                "season_year": 2027,
                "team_id": "BOS",
                "player_id": "P200003",
                "bird_type": "NON_BIRD",
                "tenure_years_same_team": 1,
                "is_renounced": 1,
            }]
        )

    session = {
        "mode": "RE_SIGN",
        "team_id": "BOS",
        "player_id": "P200003",
        "constraints": {
            "available_contract_channels": ["BIRD_NON"],
            "negotiation_season_year": 2027,
        },
    }
    offer = ContractOffer.from_payload(
        {
            "start_season_year": 2027,
            "years": 1,
            "salary_by_year": {2027: 12_000_000},
            "contract_channel": "BIRD_NON",
        }
    )

    with pytest.raises(ContractNegotiationError) as exc:
        _validate_offer_channel_policy(db_path=str(db_path), session=session, offer=offer)

    assert exc.value.code == NEGOTIATION_INVALID_OFFER


def test_validate_offer_channel_policy_rejects_non_bird_channel_for_re_sign(tmp_path: Path) -> None:
    db_path = tmp_path / "neg_re_sign_non_bird_channel.sqlite"
    _configure_state(db_path)

    with LeagueRepo(str(db_path)) as repo:
        repo.init_db()
        _seed_re_sign_player(repo, pid="P200004")
        repo.upsert_team_bird_rights(
            [{
                "season_year": 2027,
                "team_id": "BOS",
                "player_id": "P200004",
                "bird_type": "NON_BIRD",
                "tenure_years_same_team": 1,
                "is_renounced": 0,
            }]
        )

    session = {
        "mode": "RE_SIGN",
        "team_id": "BOS",
        "player_id": "P200004",
        "constraints": {
            "available_contract_channels": ["BIRD_NON"],
            "negotiation_season_year": 2027,
        },
    }
    offer = ContractOffer.from_payload(
        {
            "start_season_year": 2027,
            "years": 1,
            "salary_by_year": {2027: 2_000_000},
            "contract_channel": "STANDARD_FA",
        }
    )

    with pytest.raises(ContractNegotiationError) as exc:
        _validate_offer_channel_policy(db_path=str(db_path), session=session, offer=offer)

    assert exc.value.code == NEGOTIATION_INVALID_OFFER
