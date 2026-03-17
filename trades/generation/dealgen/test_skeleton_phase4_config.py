import unittest

from trades.generation.dealgen.types import DealGeneratorConfig, DealGeneratorStats
from trades.generation.dealgen.types import SellAssetCandidate, TargetCandidate
from trades.generation.dealgen.utils import classify_target_profile


class SkeletonPhase4ConfigTests(unittest.TestCase):
    def test_shape_limits_relaxed_defaults(self):
        cfg = DealGeneratorConfig()
        self.assertEqual(cfg.base_beam_width, 12)
        self.assertEqual(cfg.max_assets_per_side, 9)
        self.assertEqual(cfg.max_players_moved_total, 7)
        self.assertEqual(cfg.max_players_per_side, 4)
        self.assertEqual(cfg.max_picks_per_side, 4)
        self.assertEqual(cfg.max_seconds_per_side, 4)
        self.assertAlmostEqual(cfg.skeleton_gate_strictness, 0.35, places=6)
        self.assertAlmostEqual(cfg.skeleton_false_negative_bias, 0.75, places=6)

    def test_stats_include_hard_cap_monitor_fields(self):
        st = DealGeneratorStats()
        self.assertTrue(hasattr(st, "budget_validation_cap_hits"))
        self.assertTrue(hasattr(st, "budget_evaluation_cap_hits"))
        self.assertTrue(hasattr(st, "hard_validation_cap_hits"))
        self.assertTrue(hasattr(st, "hard_evaluation_cap_hits"))

    def test_stats_include_skeleton_observability_fields(self):
        st = DealGeneratorStats()
        self.assertTrue(hasattr(st, "unique_skeleton_count"))
        self.assertTrue(hasattr(st, "skeleton_id_counts"))
        self.assertTrue(hasattr(st, "skeleton_domain_counts"))
        self.assertTrue(hasattr(st, "target_tier_counts"))
        self.assertTrue(hasattr(st, "contract_tag_counts"))
        self.assertTrue(hasattr(st, "arch_compat_counts"))
        self.assertTrue(hasattr(st, "modifier_trace_counts"))

    def test_classify_target_profile_buy_target(self):
        high = TargetCandidate(
            player_id="p1",
            from_team="BOS",
            need_tag="WING",
            tag_strength=0.7,
            market_total=78.0,
            salary_m=28.0,
            remaining_years=3.0,
            age=27.0,
        )
        role = TargetCandidate(
            player_id="p2",
            from_team="LAL",
            need_tag="GUARD",
            tag_strength=0.5,
            market_total=48.0,
            salary_m=12.0,
            remaining_years=1.0,
            age=31.0,
        )
        high_profile = classify_target_profile(target=high, config=DealGeneratorConfig())
        role_profile = classify_target_profile(target=role, config=DealGeneratorConfig())

        self.assertEqual(high_profile.get("tier"), "GARBAGE")
        self.assertEqual(role_profile.get("tier"), "GARBAGE")
        self.assertEqual(high_profile.get("contract_tag"), "fair")
        self.assertEqual(role_profile.get("contract_tag"), "fair")

    def test_classify_target_profile_sell_asset(self):
        sale = SellAssetCandidate(
            player_id="p3",
            market_total=60.0,
            salary_m=18.0,
            remaining_years=2.0,
            is_expiring=False,
            top_tags=("VETERAN_SALE",),
        )
        sale_profile = classify_target_profile(sale_asset=sale, match_tag="pick_bridge", config=DealGeneratorConfig())
        self.assertEqual(sale_profile.get("tier"), "GARBAGE")
        self.assertEqual(sale_profile.get("contract_tag"), "fair")

    def test_classify_target_profile_ovr_hardcut_boundaries(self):
        class Probe:
            def __init__(self, ovr: float):
                self.ovr = ovr

        self.assertEqual(classify_target_profile(target=Probe(97.0)).get("tier"), "MVP")
        self.assertEqual(classify_target_profile(target=Probe(93.0)).get("tier"), "ALL_NBA")
        self.assertEqual(classify_target_profile(target=Probe(90.0)).get("tier"), "ALL_STAR")
        self.assertEqual(classify_target_profile(target=Probe(85.0)).get("tier"), "HIGH_STARTER")
        self.assertEqual(classify_target_profile(target=Probe(80.0)).get("tier"), "STARTER")
        self.assertEqual(classify_target_profile(target=Probe(77.0)).get("tier"), "HIGH_ROTATION")
        self.assertEqual(classify_target_profile(target=Probe(75.0)).get("tier"), "ROTATION")
        self.assertEqual(classify_target_profile(target=Probe(74.0)).get("tier"), "GARBAGE")

    def test_route_tables_include_tier_score_skeletons(self):
        cfg = DealGeneratorConfig()
        self.assertIn("high_rotation.mixed", cfg.skeleton_route_high_rotation)
        self.assertIn("starter.mixed", cfg.skeleton_route_starter)
        self.assertIn("high_starter.mixed", cfg.skeleton_route_high_starter)

    def test_route_tables_expose_garbage_tier(self):
        cfg = DealGeneratorConfig()
        self.assertTrue(hasattr(cfg, "skeleton_route_garbage"))
        self.assertIn("garbage.garbage", cfg.skeleton_route_garbage)


if __name__ == "__main__":
    unittest.main()
