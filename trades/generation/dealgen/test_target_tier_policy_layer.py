import unittest

from trades.generation.dealgen.types import DealGeneratorConfig, TargetCandidate, TierContext
from trades.generation.dealgen.utils import classify_target_tier


class TargetTierPolicyLayerTests(unittest.TestCase):
    def setUp(self):
        self.cand = TargetCandidate(
            player_id="p1",
            from_team="BOS",
            need_tag="WING",
            tag_strength=0.7,
            market_total=60.0,
            salary_m=18.0,
            remaining_years=2.0,
            age=27.0,
        )
        self.order = {"ROLE": 0, "STARTER": 1, "HIGH_STARTER": 2, "STAR": 3}

    def test_contender_deadline_pressure_pushes_upside(self):
        cfg = DealGeneratorConfig(tier_strategy_weight=1.0)
        contender = TierContext(
            market_percentile_league=0.65,
            buyer_competitive_tier="CONTENDER",
            buyer_trade_posture="AGGRESSIVE_BUY",
            buyer_time_horizon="WIN_NOW",
            buyer_urgency=0.95,
            buyer_deadline_pressure=0.95,
        )
        rebuild = TierContext(
            market_percentile_league=0.65,
            buyer_competitive_tier="REBUILD",
            buyer_trade_posture="SELL",
            buyer_time_horizon="REBUILD",
            buyer_urgency=0.1,
            buyer_deadline_pressure=0.1,
        )

        t_cont = classify_target_tier(target=self.cand, config=cfg, tier_ctx=contender)
        t_reb = classify_target_tier(target=self.cand, config=cfg, tier_ctx=rebuild)
        self.assertGreaterEqual(self.order[t_cont], self.order[t_reb])

    def test_contract_quality_proxy_affects_tier(self):
        cfg = DealGeneratorConfig(tier_contract_weight=1.0)
        good = TierContext(
            market_percentile_league=0.65,
            contract_control_direction=1.0,
            contract_matching_utility=1.0,
            contract_toxic_risk=0.0,
            contract_trigger_risk=0.0,
        )
        bad = TierContext(
            market_percentile_league=0.65,
            contract_control_direction=-1.0,
            contract_matching_utility=0.0,
            contract_toxic_risk=1.0,
            contract_trigger_risk=1.0,
        )

        t_good = classify_target_tier(target=self.cand, config=cfg, tier_ctx=good)
        t_bad = classify_target_tier(target=self.cand, config=cfg, tier_ctx=bad)
        self.assertGreaterEqual(self.order[t_good], self.order[t_bad])


if __name__ == "__main__":
    unittest.main()
