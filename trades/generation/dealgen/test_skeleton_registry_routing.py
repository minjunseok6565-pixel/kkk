import unittest

from trades.generation.dealgen.skeleton_registry import build_default_registry


class SkeletonRegistryRoutingTests(unittest.TestCase):
    def test_buy_mode_includes_phase2_specs(self):
        reg = build_default_registry()
        specs = reg.get_specs_for_mode("BUY")
        ids = [s.skeleton_id for s in specs]
        self.assertIn("compat.picks_only", ids)
        self.assertIn("player_swap.role_swap_small_delta", ids)
        self.assertIn("timeline.veteran_for_young_plus_protected_first", ids)
        self.assertIn("salary_cleanup.bad_money_swap", ids)
        self.assertIn("pick_engineering.swap_purchase", ids)
        self.assertGreaterEqual(len(specs), 20)

    def test_sell_mode_includes_phase2_specs(self):
        reg = build_default_registry()
        specs = reg.get_specs_for_mode("SELL")
        ids = [s.skeleton_id for s in specs]
        self.assertIn("compat.buyer_picks", ids)
        self.assertIn("player_swap.fit_swap_2_for_2", ids)
        self.assertIn("timeline.bluechip_plus_first_plus_swap", ids)
        self.assertIn("salary_cleanup.partial_dump_for_expiring", ids)
        self.assertIn("pick_engineering.first_split", ids)
        self.assertGreaterEqual(len(specs), 20)


if __name__ == "__main__":
    unittest.main()
