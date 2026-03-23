import unittest

from trades.generation.dealgen.skeleton_score_ssot import (
    PICK_POINTS,
    TIER_POINTS,
    asset_points_for_pick,
    build_score_target,
    is_score_satisfied,
    normalize_tier,
    target_required_score,
)


class SkeletonScoreSSOTTests(unittest.TestCase):
    def test_tier_points_table(self):
        self.assertEqual(TIER_POINTS["MVP"], 26.0)
        self.assertEqual(TIER_POINTS["ALL_NBA"], 18.0)
        self.assertEqual(TIER_POINTS["ALL_STAR"], 12.0)
        self.assertEqual(TIER_POINTS["HIGH_STARTER"], 8.0)
        self.assertEqual(TIER_POINTS["STARTER"], 4.0)
        self.assertEqual(TIER_POINTS["HIGH_ROTATION"], 2.0)
        self.assertEqual(TIER_POINTS["ROTATION"], 1.0)
        self.assertEqual(TIER_POINTS["GARBAGE"], 0.0)

    def test_pick_points_table(self):
        self.assertEqual(PICK_POINTS["FIRST"], 4.0)
        self.assertEqual(PICK_POINTS["SECOND"], 0.5)

    def test_required_score(self):
        self.assertEqual(target_required_score("MVP"), 26.0)

    def test_normalize_helpers(self):
        self.assertEqual(normalize_tier("mvp"), "MVP")

    def test_pick_round_points(self):
        self.assertEqual(asset_points_for_pick(1), 4.0)
        self.assertEqual(asset_points_for_pick(2), 0.5)
        self.assertEqual(asset_points_for_pick(3), 0.0)

    def test_is_score_satisfied(self):
        self.assertTrue(is_score_satisfied(25.5, 26.0, tolerance=0.5))
        self.assertFalse(is_score_satisfied(25.4, 26.0, tolerance=0.5))

    def test_build_score_target(self):
        st = build_score_target("all_nba")
        self.assertEqual(st.tier, "ALL_NBA")
        self.assertEqual(st.required_score, 18.0)


if __name__ == "__main__":
    unittest.main()
