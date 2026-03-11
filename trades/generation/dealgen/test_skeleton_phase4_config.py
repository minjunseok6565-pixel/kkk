import unittest

from trades.generation.dealgen.types import DealGeneratorConfig, DealGeneratorStats
from trades.generation.dealgen.types import SellAssetCandidate, TargetCandidate, PickOnlyContext, TierContext
from trades.generation.dealgen.utils import classify_target_tier


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
        self.assertTrue(hasattr(st, "arch_compat_counts"))
        self.assertTrue(hasattr(st, "modifier_trace_counts"))

    def test_market_percentile_changes_tier_at_same_market(self):
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
        low_ctx = TierContext(market_percentile_league=0.2)
        high_ctx = TierContext(market_percentile_league=0.9)

        self.assertEqual(classify_target_tier(target=cand, config=DealGeneratorConfig(), tier_ctx=low_ctx), "ROLE")
        self.assertEqual(classify_target_tier(target=cand, config=DealGeneratorConfig(), tier_ctx=high_ctx), "STARTER")

    def test_strictness_shifts_cuts(self):
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
        ctx = TierContext(market_percentile_league=0.72)

        relaxed = classify_target_tier(target=cand, config=DealGeneratorConfig(tier_strictness=-1.0), tier_ctx=ctx)
        strict = classify_target_tier(target=cand, config=DealGeneratorConfig(tier_strictness=1.0), tier_ctx=ctx)

        order = {"ROLE": 0, "STARTER": 1, "HIGH_STARTER": 2, "STAR": 3}
        self.assertGreaterEqual(order[relaxed], order[strict])

    def test_strategy_and_contract_weights_affect_tier(self):
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

        contender_ctx = TierContext(
            market_percentile_league=0.65,
            buyer_competitive_tier="CONTENDER",
            buyer_trade_posture="AGGRESSIVE_BUY",
            buyer_time_horizon="WIN_NOW",
            buyer_urgency=0.9,
            buyer_deadline_pressure=0.9,
        )
        rebuild_ctx = TierContext(
            market_percentile_league=0.65,
            buyer_competitive_tier="REBUILD",
            buyer_trade_posture="SELL",
            buyer_time_horizon="REBUILD",
            buyer_urgency=0.1,
            buyer_deadline_pressure=0.1,
        )
        good_contract_ctx = TierContext(
            market_percentile_league=0.65,
            contract_control_direction=1.0,
            contract_matching_utility=1.0,
            contract_toxic_risk=0.0,
            contract_trigger_risk=0.0,
        )

        t_cont = classify_target_tier(target=cand, config=DealGeneratorConfig(tier_strategy_weight=1.0), tier_ctx=contender_ctx)
        t_reb = classify_target_tier(target=cand, config=DealGeneratorConfig(tier_strategy_weight=1.0), tier_ctx=rebuild_ctx)
        t_good = classify_target_tier(target=cand, config=DealGeneratorConfig(tier_contract_weight=1.0), tier_ctx=good_contract_ctx)

        order = {"ROLE": 0, "STARTER": 1, "HIGH_STARTER": 2, "STAR": 3}
        self.assertGreaterEqual(order[t_cont], order[t_reb])
        self.assertGreaterEqual(order[t_good], order[t_reb])

    def test_prev_tier_hysteresis_and_pick_only_score_gate(self):
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
        hyst_ctx = TierContext(market_percentile_league=0.73, prev_tier="STARTER")
        self.assertEqual(
            classify_target_tier(target=cand, config=DealGeneratorConfig(tier_hysteresis_band=0.2), tier_ctx=hyst_ctx),
            "STARTER",
        )

        sale = SellAssetCandidate(
            player_id="p3",
            market_total=60.0,
            salary_m=18.0,
            remaining_years=2.0,
            is_expiring=False,
            top_tags=("PICK_BRIDGE",),
        )
        # keyword-only: no inventory gate => not PICK_ONLY
        self.assertNotEqual(
            classify_target_tier(sale_asset=sale, match_tag="pick_bridge", config=DealGeneratorConfig()),
            "PICK_ONLY",
        )

        pick_ctx = PickOnlyContext(
            pick_supply_safe=3,
            pick_supply_sensitive=0,
            pick_supply_second=3,
            stepien_sensitive_ratio=0.0,
            has_pick_inventory=True,
        )
        self.assertEqual(
            classify_target_tier(
                sale_asset=sale,
                match_tag="pick_bridge",
                config=DealGeneratorConfig(),
                pick_ctx=pick_ctx,
            ),
            "PICK_ONLY",
        )

    def test_route_tables_include_new_depth_skeleton(self):
        cfg = DealGeneratorConfig()
        self.assertIn("player_swap.one_for_two_depth", cfg.skeleton_route_role)
        self.assertIn("player_swap.one_for_two_depth", cfg.skeleton_route_starter)
        self.assertIn("player_swap.one_for_two_depth", cfg.skeleton_route_high_starter)


if __name__ == "__main__":
    unittest.main()
