import unittest

from trades.generation.dealgen.pick_protection_decorator import choose_top_n_options
from trades.generation.dealgen.types import DealGeneratorConfig


class SkeletonModifiersConfigTests(unittest.TestCase):
    def test_modifier_flags_default_enabled(self):
        cfg = DealGeneratorConfig()
        self.assertTrue(cfg.skeleton_modifiers_enabled)
        self.assertTrue(cfg.modifier_protection_enabled)
        self.assertTrue(cfg.modifier_swap_substitute_enabled)
        self.assertGreaterEqual(cfg.modifier_max_variants_per_candidate, 1)

    def test_protection_intent_prioritizes_ladder_options(self):
        cfg = DealGeneratorConfig(modifier_protection_default_ladder=("prot_heavy", "prot_mid", "prot_light"))
        opts = choose_top_n_options(None, pick_bucket="FIRST_SENSITIVE", config=cfg, protection_intent="heavy", max_variants=2)
        self.assertEqual(opts, [20, 24])

    def test_protection_intent_mid_uses_mid_first(self):
        cfg = DealGeneratorConfig(modifier_protection_default_ladder=("prot_mid", "prot_light", "prot_heavy"))
        opts = choose_top_n_options(None, pick_bucket="FIRST_SAFE", config=cfg, protection_intent="mid", max_variants=2)
        self.assertEqual(opts[0], 14)

    def test_without_intent_uses_fallback_strength_logic(self):
        cfg = DealGeneratorConfig()
        opts = choose_top_n_options(None, pick_bucket="FIRST_SENSITIVE", config=cfg, protection_intent=None, max_variants=2)
        self.assertGreaterEqual(len(opts), 1)


if __name__ == "__main__":
    unittest.main()
