import unittest

from trades.generation.dealgen.core import (
    _derive_buy_retrieval_budget_guard_config,
    _record_candidate_observability,
    _finalize_observability_stats,
)
from trades.generation.dealgen.types import DealCandidate, DealGeneratorBudget, DealGeneratorConfig, DealGeneratorStats
from trades.models import Deal


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

    def test_observability_stats_collect_and_finalize(self):
        stats = DealGeneratorStats()
        cand = DealCandidate(
            deal=Deal(teams=["A", "B"], legs={"A": [], "B": []}),
            buyer_id="A",
            seller_id="B",
            focal_player_id="p1",
            archetype="p4p_salary",
            skeleton_id="player_swap.role_swap_small_delta",
            skeleton_domain="player_swap",
            target_tier="STARTER",
            compat_archetype="p4p_salary",
            modifier_trace=["protection_step_up_down"],
        )
        _record_candidate_observability(stats, cand)
        _finalize_observability_stats(stats)

        self.assertEqual(stats.unique_skeleton_count, 1)
        self.assertEqual(stats.skeleton_domain_counts.get("player_swap"), 1)
        self.assertEqual(stats.target_tier_counts.get("STARTER"), 1)
        self.assertEqual(stats.arch_compat_counts.get("p4p_salary"), 1)
        self.assertEqual(stats.modifier_trace_counts.get("protection_step_up_down"), 1)
        self.assertGreater(stats.modifier_success_rate, 0.0)


if __name__ == "__main__":
    unittest.main()
