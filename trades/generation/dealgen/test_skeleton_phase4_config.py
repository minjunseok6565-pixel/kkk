import unittest

from trades.generation.dealgen.types import DealGeneratorConfig, DealGeneratorStats


class SkeletonPhase4ConfigTests(unittest.TestCase):
    def test_shape_limits_relaxed_defaults(self):
        cfg = DealGeneratorConfig()
        self.assertEqual(cfg.base_beam_width, 12)
        self.assertEqual(cfg.max_assets_per_side, 9)
        self.assertEqual(cfg.max_players_moved_total, 7)
        self.assertEqual(cfg.max_players_per_side, 4)
        self.assertEqual(cfg.max_picks_per_side, 4)
        self.assertEqual(cfg.max_seconds_per_side, 4)

    def test_stats_include_hard_cap_monitor_fields(self):
        st = DealGeneratorStats()
        self.assertTrue(hasattr(st, "budget_validation_cap_hits"))
        self.assertTrue(hasattr(st, "budget_evaluation_cap_hits"))
        self.assertTrue(hasattr(st, "hard_validation_cap_hits"))
        self.assertTrue(hasattr(st, "hard_evaluation_cap_hits"))


if __name__ == "__main__":
    unittest.main()
