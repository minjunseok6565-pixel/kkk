import unittest

from trades.generation.dealgen.skeleton_registry import build_default_registry
from trades.generation.dealgen.types import DealGeneratorConfig


class SkeletonRegistryRoutingTests(unittest.TestCase):
    def test_buy_mode_includes_tier_score_and_timeline_specs(self):
        reg = build_default_registry()
        specs = reg.get_specs_for_mode("BUY")
        ids = {s.skeleton_id for s in specs}
        self.assertIn("mvp.player_heavy", ids)
        self.assertIn("all_nba.mixed", ids)
        self.assertIn("rotation.pick_heavy", ids)
        self.assertIn("garbage.garbage", ids)
        self.assertIn("timeline.bluechip_plus_first_plus_swap", ids)
        self.assertGreaterEqual(len(specs), 25)

    def test_mvp_uses_lowercase_route_ids(self):
        reg = build_default_registry()
        cfg = DealGeneratorConfig(
            skeleton_route_mvp=(
                "mvp.player_heavy",
                "timeline.bluechip_plus_first_plus_swap",
            )
        )
        specs = reg.get_specs_for_mode_and_tier("BUY", "MVP", cfg)
        ids = [s.skeleton_id for s in specs]
        self.assertEqual(ids, ["mvp.player_heavy", "timeline.bluechip_plus_first_plus_swap"])

    def test_combined_phase_uses_tier_route_only(self):
        reg = build_default_registry()
        cfg = DealGeneratorConfig(
            skeleton_route_mvp=("mvp.player_heavy", "mvp.pick_heavy"),
        )
        specs = reg.get_specs_for_mode_and_tier("BUY", "MVP", cfg)
        ids = [s.skeleton_id for s in specs]
        self.assertEqual(ids, ["mvp.player_heavy", "mvp.pick_heavy"])


if __name__ == "__main__":
    unittest.main()
