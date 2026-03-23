from trades.valuation.env import ValuationEnv
from trades.valuation.market_pricing import MarketPricer, MarketPricingConfig
from trades.valuation.types import ContractOptionSnapshot, ContractSnapshot, PlayerSnapshot


def _env() -> ValuationEnv:
    trade_rules = {
        "salary_cap": 154_647_000,
        "first_apron": 178_000_000,
        "second_apron": 189_000_000,
    }
    return ValuationEnv.from_trade_rules(trade_rules, current_season_year=2026)


def _player_with_option(*, option_type: str, option_status: str, option_salary: float) -> PlayerSnapshot:
    return PlayerSnapshot(
        kind="player",
        player_id=f"p-{option_type}-{option_status}-{int(option_salary)}",
        age=27,
        ovr=84,
        salary_amount=20_000_000,
        contract=ContractSnapshot(
            contract_id="c1",
            player_id="p1",
            team_id="LAL",
            status="ACTIVE",
            signed_date="2025-07-01",
            start_season_year=2025,
            years=2,
            salary_by_year={2026: 20_000_000, 2027: float(option_salary)},
            options=[
                ContractOptionSnapshot(
                    season_year=2027,
                    type=option_type,
                    status=option_status,
                    decision_date=None,
                )
            ],
        ),
    )


def test_team_option_adds_positive_contract_option_delta() -> None:
    pricer = MarketPricer(config=MarketPricingConfig())
    _, meta = pricer._contract_value_delta(
        _player_with_option(option_type="TEAM", option_status="PENDING", option_salary=22_000_000),
        env=_env(),
    )

    option_total = float(meta["option_delta"]["total"])
    assert option_total > 0.0


def test_player_option_is_negative_and_cheaper_option_is_less_negative() -> None:
    pricer = MarketPricer(config=MarketPricingConfig())

    _, meta_cheap = pricer._contract_value_delta(
        _player_with_option(option_type="PLAYER", option_status="PENDING", option_salary=18_000_000),
        env=_env(),
    )
    _, meta_expensive = pricer._contract_value_delta(
        _player_with_option(option_type="PLAYER", option_status="PENDING", option_salary=35_000_000),
        env=_env(),
    )

    cheap_total = float(meta_cheap["option_delta"]["total"])
    expensive_total = float(meta_expensive["option_delta"]["total"])

    assert cheap_total < 0.0
    assert expensive_total < 0.0
    # Cheap player option should be penalized less than expensive player option.
    assert cheap_total > expensive_total
