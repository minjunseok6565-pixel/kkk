import unittest

from trades.valuation.env import ValuationEnv
from trades.valuation.market_pricing import MarketPricer
from trades.valuation.pick_distribution import PickDistributionBundle
from trades.valuation.types import PickSnapshot


class MarketPricingPickDistributionTests(unittest.TestCase):
    def test_pick_distribution_is_primary_input_when_present(self):
        engine = MarketPricer()
        snap = PickSnapshot(
            kind="pick",
            pick_id="pk1",
            year=2027,
            round=1,
            original_team="DET",
            owner_team="LAL",
            protection=None,
        )
        dist = PickDistributionBundle(
            pmf={1: 0.10, 2: 0.15, 8: 0.75},
            cdf={1: 0.10, 2: 0.25, 8: 1.0},
            ev_pick=6.4,
            variance=8.3,
            scenario_notes=("test",),
            compat_expected_pick_number=6.4,
            tail_upside_prob=0.25,
            tail_downside_prob=0.05,
        )
        env = ValuationEnv.from_trade_rules({"salary_cap": 140_000_000}, current_season_year=2026)

        mv = engine.price_snapshot(
            snap,
            asset_key="pick:pk1",
            env=env,
            pick_expectation=None,
            pick_distribution=dist,
        )

        self.assertEqual(mv.meta.get("pricing_input"), "pick_distribution")
        self.assertTrue(any(s.code == "PICK_DISTRIBUTION_ADJUST" for s in mv.steps))


if __name__ == "__main__":
    unittest.main()
