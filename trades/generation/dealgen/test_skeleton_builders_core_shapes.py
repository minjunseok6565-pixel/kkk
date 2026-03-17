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

if __name__ == "__main__":
    unittest.main()
