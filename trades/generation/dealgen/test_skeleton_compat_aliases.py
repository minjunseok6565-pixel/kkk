import unittest

from trades.generation.dealgen.skeleton_registry import build_default_registry


class SkeletonCompatAliasTests(unittest.TestCase):
    def test_all_specs_expose_compat_archetype(self):
        reg = build_default_registry()
        for spec in reg.specs:
            self.assertTrue(spec.compat_archetype)

    def test_phase2_domains_are_registered(self):
        reg = build_default_registry()
        domains = {s.domain for s in reg.specs}
        self.assertIn("player_swap", domains)
        self.assertIn("timeline", domains)
        self.assertIn("salary_cleanup", domains)
        self.assertIn("pick_engineering", domains)


if __name__ == "__main__":
    unittest.main()
