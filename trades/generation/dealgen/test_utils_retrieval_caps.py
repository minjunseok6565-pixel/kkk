import unittest
from types import SimpleNamespace

from trades.generation.dealgen.config import _scale_buy_retrieval_limits
from trades.generation.dealgen.types import DealGeneratorConfig
from trades.generation.dealgen.utils import _safe_norm, _smoothstep01, compute_buy_retrieval_caps


class RetrievalCapsUtilsTests(unittest.TestCase):
    def _ts(self, *, deadline: float, urgency: float):
        return SimpleNamespace(
            urgency=urgency,
            constraints=SimpleNamespace(deadline_pressure=deadline),
        )

    def test_smoothstep01_clamps_and_shapes(self):
        self.assertEqual(_smoothstep01(-1.0), 0.0)
        self.assertEqual(_smoothstep01(2.0), 1.0)
        self.assertAlmostEqual(_smoothstep01(0.5), 0.5, places=6)

    def test_safe_norm_handles_range_and_degenerate(self):
        self.assertEqual(_safe_norm(0.0, 0.0, 10.0), 0.0)
        self.assertEqual(_safe_norm(10.0, 0.0, 10.0), 1.0)
        self.assertAlmostEqual(_safe_norm(2.5, 0.0, 10.0), 0.25, places=6)
        self.assertEqual(_safe_norm(5.0, 7.0, 7.0), 0.0)

    def test_compute_buy_retrieval_caps_expands_with_deadline(self):
        cfg = DealGeneratorConfig()
        low = compute_buy_retrieval_caps(self._ts(deadline=0.0, urgency=0.0), cfg)
        high = compute_buy_retrieval_caps(self._ts(deadline=1.0, urgency=1.0), cfg)

        self.assertLess(low["intensity"], high["intensity"])
        self.assertLess(low["teams_cap"], high["teams_cap"])
        self.assertLess(low["players_cap"], high["players_cap"])
        self.assertLess(low["non_listed_quota"], high["non_listed_quota"])


    def test_compute_buy_retrieval_caps_monotonic_across_deadline_phases(self):
        cfg = DealGeneratorConfig()
        low = compute_buy_retrieval_caps(self._ts(deadline=0.0, urgency=0.0), cfg)
        mid = compute_buy_retrieval_caps(self._ts(deadline=0.5, urgency=0.5), cfg)
        high = compute_buy_retrieval_caps(self._ts(deadline=1.0, urgency=1.0), cfg)

        self.assertLessEqual(low["intensity"], mid["intensity"])
        self.assertLessEqual(mid["intensity"], high["intensity"])
        self.assertLessEqual(low["teams_cap"], mid["teams_cap"])
        self.assertLessEqual(mid["teams_cap"], high["teams_cap"])
        self.assertLessEqual(low["players_cap"], mid["players_cap"])
        self.assertLessEqual(mid["players_cap"], high["players_cap"])
        self.assertLessEqual(low["non_listed_quota"], mid["non_listed_quota"])
        self.assertLessEqual(mid["non_listed_quota"], high["non_listed_quota"])

    def test_scale_buy_retrieval_limits_clamps_quota_ratio_and_share(self):
        cfg = DealGeneratorConfig(
            buy_target_listed_max_share=5.0,
            buy_target_expand_tier2_budget_share=2.0,
            buy_target_retrieval_iteration_cap=0,
        )
        out = _scale_buy_retrieval_limits(cfg, self._ts(deadline=0.5, urgency=0.5))

        self.assertEqual(out["listed_max_share"], 1.0)
        self.assertEqual(out["tier2_budget_share"], 1.0)
        self.assertEqual(out["retrieval_iteration_cap"], 1.0)
        self.assertIn("listed_min_quota", out)
        self.assertIn("teams_cap", out)


if __name__ == "__main__":
    unittest.main()
