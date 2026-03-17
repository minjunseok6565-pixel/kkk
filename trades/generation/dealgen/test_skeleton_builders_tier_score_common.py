import unittest
from types import SimpleNamespace

from trades.generation.dealgen.skeleton_builders_all_nba import (
    build_all_nba_mixed,
    build_all_nba_pick_heavy,
    build_all_nba_player_heavy,
)
from trades.generation.dealgen.skeleton_builders_all_star import (
    build_all_star_mixed,
    build_all_star_pick_heavy,
    build_all_star_player_heavy,
)
from trades.generation.dealgen.skeleton_builders_garbage import build_garbage_garbage
from trades.generation.dealgen.skeleton_builders_high_rotation import (
    build_high_rotation_mixed,
    build_high_rotation_pick_heavy,
    build_high_rotation_player_heavy,
)
from trades.generation.dealgen.skeleton_builders_high_starter import (
    build_high_starter_mixed,
    build_high_starter_pick_heavy,
    build_high_starter_player_heavy,
)
from trades.generation.dealgen.skeleton_builders_mvp import (
    build_mvp_mixed,
    build_mvp_pick_heavy,
    build_mvp_player_heavy,
)
from trades.generation.dealgen.skeleton_builders_rotation import (
    build_rotation_mixed,
    build_rotation_pick_heavy,
    build_rotation_player_heavy,
)
from trades.generation.dealgen.skeleton_builders_starter import (
    build_starter_mixed,
    build_starter_pick_heavy,
    build_starter_player_heavy,
)
from trades.generation.dealgen.skeleton_builders_tier_score_common import (
    MAX_CANDIDATES_PER_BUILDER,
    MAX_SEARCH_ATTEMPTS,
)


class TierScoreSkeletonBuildersTests(unittest.TestCase):
    def test_performance_guards_match_requested_defaults(self):
        self.assertEqual(MAX_SEARCH_ATTEMPTS, 12)
        self.assertEqual(MAX_CANDIDATES_PER_BUILDER, 6)

    def test_all_builders_fail_fast_without_focal(self):
        ctx = SimpleNamespace(target=None, sale_asset=None)
        fns = [
            build_mvp_player_heavy,
            build_mvp_pick_heavy,
            build_mvp_mixed,
            build_all_nba_player_heavy,
            build_all_nba_pick_heavy,
            build_all_nba_mixed,
            build_all_star_player_heavy,
            build_all_star_pick_heavy,
            build_all_star_mixed,
            build_high_starter_player_heavy,
            build_high_starter_pick_heavy,
            build_high_starter_mixed,
            build_starter_player_heavy,
            build_starter_pick_heavy,
            build_starter_mixed,
            build_high_rotation_player_heavy,
            build_high_rotation_pick_heavy,
            build_high_rotation_mixed,
            build_rotation_player_heavy,
            build_rotation_pick_heavy,
            build_rotation_mixed,
            build_garbage_garbage,
        ]
        for fn in fns:
            self.assertEqual(fn(ctx), [], msg=fn.__name__)


if __name__ == "__main__":
    unittest.main()
