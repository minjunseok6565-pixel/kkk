import unittest

from trades.generation.dealgen.skeleton_registry import build_default_registry


class SkeletonBuildersCoreShapesTests(unittest.TestCase):
    """Step-6 contract tests: core skeleton catalog/shape coverage invariants.

    환경 의존도가 큰 fixture-less unit layer에서는 실제 딜 생성 대신,
    핵심 스켈레톤들이 registry에서 누락되지 않고 빌더 메타 계약을 만족하는지 고정한다.
    """

    CORE_SKELETON_IDS = {
        "player_swap.role_swap_small_delta",
        "player_swap.fit_swap_2_for_2",
        "player_swap.starter_for_two_rotation",
        "player_swap.one_for_two_depth",
        "player_swap.three_for_one_upgrade",
        "player_swap.bench_bundle_for_role",
        "timeline.veteran_for_young",
        "timeline.veteran_for_young_plus_protected_first",
        "timeline.bluechip_plus_first_plus_swap",
        "salary_cleanup.rental_expiring_plus_second",
        "salary_cleanup.pure_absorb_for_asset",
        "salary_cleanup.bad_money_swap",
        "pick_engineering.first_split",
        "pick_engineering.swap_substitute_for_first",
    }

    def test_core_skeletons_registered_at_least_14(self):
        reg = build_default_registry()
        ids = {s.skeleton_id for s in reg.specs}
        self.assertGreaterEqual(len(self.CORE_SKELETON_IDS), 14)
        self.assertTrue(self.CORE_SKELETON_IDS.issubset(ids))

    def test_core_skeletons_have_builder_and_tier_contract(self):
        reg = build_default_registry()
        spec_map = {s.skeleton_id: s for s in reg.specs}
        for sid in self.CORE_SKELETON_IDS:
            spec = spec_map[sid]
            self.assertTrue(callable(spec.build_fn), sid)
            self.assertTrue(bool(spec.target_tiers), sid)
            self.assertTrue(bool(spec.mode_allow), sid)
            self.assertTrue(bool(spec.compat_archetype), sid)


if __name__ == "__main__":
    unittest.main()
