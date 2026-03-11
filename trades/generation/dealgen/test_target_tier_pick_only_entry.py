import unittest

from trades.generation.dealgen.types import DealGeneratorConfig, PickOnlyContext, SellAssetCandidate
from trades.generation.dealgen.utils import classify_target_tier


class TargetTierPickOnlyEntryTests(unittest.TestCase):
    def setUp(self):
        self.cfg = DealGeneratorConfig()
        self.sale = SellAssetCandidate(
            player_id="p3",
            market_total=60.0,
            salary_m=18.0,
            remaining_years=2.0,
            is_expiring=False,
            top_tags=("PICK_BRIDGE",),
        )

    def test_keyword_only_does_not_enter_pick_only(self):
        tier = classify_target_tier(
            sale_asset=self.sale,
            match_tag="pick_bridge",
            config=self.cfg,
        )
        self.assertNotEqual(tier, "PICK_ONLY")

    def test_inventory_gate_allows_pick_only(self):
        pick_ctx = PickOnlyContext(
            pick_supply_safe=3,
            pick_supply_sensitive=0,
            pick_supply_second=3,
            stepien_sensitive_ratio=0.0,
            has_pick_inventory=True,
        )
        tier = classify_target_tier(
            sale_asset=self.sale,
            match_tag="pick_bridge",
            config=self.cfg,
            pick_ctx=pick_ctx,
        )
        self.assertEqual(tier, "PICK_ONLY")

    def test_stepien_sensitive_ratio_penalizes_entry(self):
        low_risk_ctx = PickOnlyContext(
            pick_supply_safe=0,
            pick_supply_sensitive=6,
            pick_supply_second=0,
            stepien_sensitive_ratio=0.0,
            has_pick_inventory=True,
        )
        high_risk_ctx = PickOnlyContext(
            pick_supply_safe=0,
            pick_supply_sensitive=6,
            pick_supply_second=0,
            stepien_sensitive_ratio=1.0,
            has_pick_inventory=True,
        )

        low_risk = classify_target_tier(
            sale_asset=self.sale,
            match_tag="pick_bridge",
            config=self.cfg,
            pick_ctx=low_risk_ctx,
        )
        high_risk = classify_target_tier(
            sale_asset=self.sale,
            match_tag="pick_bridge",
            config=self.cfg,
            pick_ctx=high_risk_ctx,
        )

        self.assertEqual(low_risk, "PICK_ONLY")
        self.assertNotEqual(high_risk, "PICK_ONLY")


if __name__ == "__main__":
    unittest.main()
