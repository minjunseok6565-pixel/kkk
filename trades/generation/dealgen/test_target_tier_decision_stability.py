import unittest

from trades.generation.dealgen.types import DealGeneratorConfig, SellAssetCandidate, TargetCandidate, TierContext
from trades.generation.dealgen.utils import classify_target_tier


class TargetTierDecisionStabilityTests(unittest.TestCase):
    def test_hysteresis_holds_prev_tier_near_boundary(self):
        cand = TargetCandidate(
            player_id="p1",
            from_team="BOS",
            need_tag="WING",
            tag_strength=0.7,
            market_total=60.0,
            salary_m=18.0,
            remaining_years=2.0,
            age=27.0,
        )
        cfg = DealGeneratorConfig(tier_hysteresis_band=0.2)
        tier = classify_target_tier(
            target=cand,
            config=cfg,
            tier_ctx=TierContext(market_percentile_league=0.73, prev_tier="STARTER"),
        )
        self.assertEqual(tier, "STARTER")

    def test_tie_break_seed_is_reproducible(self):
        cand = TargetCandidate(
            player_id="p1",
            from_team="BOS",
            need_tag="WING",
            tag_strength=0.7,
            market_total=60.0,
            salary_m=18.0,
            remaining_years=2.0,
            age=27.0,
        )
        cfg = DealGeneratorConfig()
        ctx = TierContext(market_percentile_league=0.5, tie_break_seed="BOS:LAL:p1:BUY")

        t1 = classify_target_tier(target=cand, config=cfg, tier_ctx=ctx)
        t2 = classify_target_tier(target=cand, config=cfg, tier_ctx=ctx)
        self.assertEqual(t1, t2)

    def test_buy_sell_share_same_classifier_rules(self):
        cfg = DealGeneratorConfig()
        tier_ctx = TierContext(market_percentile_league=0.65)

        buy = TargetCandidate(
            player_id="p1",
            from_team="BOS",
            need_tag="WING",
            tag_strength=0.7,
            market_total=60.0,
            salary_m=18.0,
            remaining_years=2.0,
            age=27.0,
        )
        sell = SellAssetCandidate(
            player_id="p1",
            market_total=60.0,
            salary_m=18.0,
            remaining_years=2.0,
            is_expiring=False,
            top_tags=("WING",),
        )

        self.assertEqual(
            classify_target_tier(target=buy, config=cfg, tier_ctx=tier_ctx),
            classify_target_tier(sale_asset=sell, match_tag="WING", config=cfg, tier_ctx=tier_ctx),
        )


if __name__ == "__main__":
    unittest.main()
