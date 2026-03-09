import unittest

from trades.generation.dealgen.types import DealGeneratorConfig


class SkeletonModifiersConfigTests(unittest.TestCase):
    def test_modifier_flags_default_enabled(self):
        cfg = DealGeneratorConfig()
        self.assertTrue(cfg.skeleton_modifiers_enabled)
        self.assertTrue(cfg.modifier_protection_enabled)
        self.assertTrue(cfg.modifier_swap_substitute_enabled)
        self.assertGreaterEqual(cfg.modifier_max_variants_per_candidate, 1)


if __name__ == "__main__":
    unittest.main()
