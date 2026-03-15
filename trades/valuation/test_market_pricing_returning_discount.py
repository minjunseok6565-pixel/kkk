from trades.valuation.market_pricing import MarketPricer, MarketPricingConfig


def test_current_injury_discount_applies_to_returning_too() -> None:
    pricer = MarketPricer()

    f_healthy, _ = pricer._inj_current_penalty_factor({"current": {"status": "HEALTHY", "is_out": False, "is_returning": False, "days_to_return": 120}})
    f_returning, _ = pricer._inj_current_penalty_factor({"current": {"status": "RETURNING", "is_out": False, "is_returning": True, "days_to_return": 120}})
    f_out, _ = pricer._inj_current_penalty_factor({"current": {"status": "OUT", "is_out": True, "is_returning": False, "days_to_return": 120}})

    assert f_healthy == 1.0
    assert f_out < 1.0
    assert f_returning < 1.0
    # RETURNING discount should be milder than OUT by default
    assert f_out < f_returning


def test_returning_multiplier_zero_disables_returning_penalty() -> None:
    pricer = MarketPricer(config=MarketPricingConfig(inj_current_returning_multiplier=0.0))

    f_returning, _ = pricer._inj_current_penalty_factor({"current": {"status": "RETURNING", "is_out": False, "is_returning": True, "days_to_return": 220}})
    assert f_returning == 1.0
