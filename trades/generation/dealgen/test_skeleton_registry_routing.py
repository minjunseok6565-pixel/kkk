import unittest
from types import SimpleNamespace

from trades.generation.dealgen.skeleton_registry import build_default_registry
from trades.generation.dealgen.types import DealGeneratorConfig


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

    def test_tier_route_filters_by_config_allowlist(self):
        reg = build_default_registry()
        cfg = DealGeneratorConfig(
            skeleton_route_pick_only=(
                "compat.picks_only",
                "pick_engineering.first_split",
            )
        )

        specs = reg.get_specs_for_mode_and_tier("BUY", "PICK_ONLY", cfg)
        ids = [s.skeleton_id for s in specs]

        self.assertEqual(ids, ["compat.picks_only", "pick_engineering.first_split"])

    def test_tier_filter_respects_target_tiers(self):
        reg = build_default_registry()
        cfg = DealGeneratorConfig()

        role_specs = reg.get_specs_for_mode_and_tier("BUY", "ROLE", cfg)
        role_ids = {s.skeleton_id for s in role_specs}
        starter_specs = reg.get_specs_for_mode_and_tier("BUY", "STARTER", cfg)
        starter_ids = {s.skeleton_id for s in starter_specs}

        self.assertIn("player_swap.bench_bundle_for_role", role_ids)
        self.assertNotIn("timeline.bluechip_plus_first_plus_swap", role_ids)
        self.assertIn("player_swap.one_for_two_depth", starter_ids)

    def test_star_uses_high_starter_route_table(self):
        reg = build_default_registry()
        cfg = DealGeneratorConfig(
            skeleton_route_high_starter=(
                "player_swap.star_lateral_plus_delta",
                "timeline.bluechip_plus_first_plus_swap",
            )
        )
        specs = reg.get_specs_for_mode_and_tier("BUY", "STAR", cfg)
        ids = [s.skeleton_id for s in specs]
        self.assertEqual(ids, ["player_swap.star_lateral_plus_delta", "timeline.bluechip_plus_first_plus_swap"])

    def test_gate_fn_filters_spec_when_context_fails(self):
        reg = build_default_registry()
        cfg = DealGeneratorConfig()
        # one_for_two_depth gate: requires target or sale_asset in ctx
        ctx = SimpleNamespace(target=None, sale_asset=None)
        specs = reg.get_specs_for_mode_and_tier("BUY", "STARTER", cfg, ctx=ctx)
        ids = {s.skeleton_id for s in specs}
        self.assertNotIn("player_swap.one_for_two_depth", ids)


if __name__ == "__main__":
    unittest.main()
