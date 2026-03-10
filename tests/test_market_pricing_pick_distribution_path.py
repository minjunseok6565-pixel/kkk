from trades.valuation.env import ValuationEnv
from trades.valuation.market_pricing import MarketPricer
from trades.valuation.types import PickSnapshot, SwapSnapshot


def _env() -> ValuationEnv:
    return ValuationEnv.from_trade_rules({}, current_season_year=2026)


def test_price_pick_uses_distribution_and_skips_legacy_protection_expectation() -> None:
    pricer = MarketPricer()
    pick = PickSnapshot(
        kind="pick",
        pick_id="P1",
        year=2026,
        round=1,
        original_team="A",
        owner_team="B",
        protection={"type": "TOP_N", "n": 10},
    )
    mv = pricer.price_snapshot(
        pick,
        asset_key="pick:P1",
        env=_env(),
        pick_distribution={
            "ev_pick": 8.0,
            "variance": 4.0,
            "tail_upside_prob": 0.35,
            "tail_downside_prob": 0.05,
            "p10_pick": 2,
            "p50_pick": 8,
            "p90_pick": 18,
        },
    )

    codes = {s.code for s in mv.steps}
    assert "PICK_DISTRIBUTION_VARIANCE" in codes
    assert "PICK_DISTRIBUTION_TAIL_ADJUST" in codes
    assert "PICK_PROTECTION_EXPECTATION" not in codes
    assert mv.meta.get("uses_distribution") is True


def test_price_swap_skips_legacy_exercise_prob_when_both_distributions_exist() -> None:
    pricer = MarketPricer()
    pick_a = PickSnapshot(
        kind="pick",
        pick_id="PA",
        year=2026,
        round=1,
        original_team="A",
        owner_team="A",
        protection=None,
    )
    pick_b = PickSnapshot(
        kind="pick",
        pick_id="PB",
        year=2026,
        round=1,
        original_team="B",
        owner_team="B",
        protection=None,
    )
    swap = SwapSnapshot(
        kind="swap",
        swap_id="S1",
        pick_id_a="PA",
        pick_id_b="PB",
        year=2026,
        round=1,
        owner_team="A",
        active=True,
    )

    mv = pricer.price_snapshot(
        swap,
        asset_key="swap:S1",
        env=_env(),
        resolved_pick_a=pick_a,
        resolved_pick_b=pick_b,
        resolved_pick_a_distribution={"ev_pick": 4.0, "variance": 1.0},
        resolved_pick_b_distribution={"ev_pick": 20.0, "variance": 1.0},
    )

    codes = [s.code for s in mv.steps]
    assert "SWAP_EXERCISE_PROB_SKIPPED_V2" in codes
