import unittest

from trades.generation.dealgen.core import _derive_buy_retrieval_budget_guard_config
from trades.generation.dealgen.types import DealGeneratorBudget, DealGeneratorConfig


class CoreBudgetGuardTests(unittest.TestCase):
    def _budget(self, *, evals: int, vals: int) -> DealGeneratorBudget:
        return DealGeneratorBudget(
            max_targets=12,
            beam_width=8,
            max_attempts_per_target=40,
            max_validations=vals,
            max_evaluations=evals,
            max_repairs=2,
        )

    def test_low_budget_disables_tier2(self):
        cfg = DealGeneratorConfig(
            buy_target_expand_tier2_enabled=True,
            buy_target_expand_tier2_budget_share=0.35,
            buy_target_retrieval_iteration_cap=400,
            buy_target_non_listed_deadline_bonus_max=12,
        )
        out = _derive_buy_retrieval_budget_guard_config(cfg, self._budget(evals=40, vals=80))
        self.assertFalse(out.buy_target_expand_tier2_enabled)
        self.assertEqual(out.buy_target_expand_tier2_budget_share, 0.0)

    def test_healthy_budget_keeps_tier2_and_scales_limits(self):
        cfg = DealGeneratorConfig(
            buy_target_expand_tier2_enabled=True,
            buy_target_expand_tier2_budget_share=0.40,
            buy_target_retrieval_iteration_cap=400,
            buy_target_non_listed_deadline_bonus_max=12,
        )
        out = _derive_buy_retrieval_budget_guard_config(cfg, self._budget(evals=180, vals=360))
        self.assertTrue(out.buy_target_expand_tier2_enabled)
        self.assertAlmostEqual(out.buy_target_expand_tier2_budget_share, 0.40, places=6)
        self.assertGreaterEqual(out.buy_target_retrieval_iteration_cap, 48)
        self.assertGreaterEqual(out.buy_target_non_listed_deadline_bonus_max, 1)


if __name__ == "__main__":
    unittest.main()
