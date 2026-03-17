import unittest

from trades.generation.dealgen.skeleton_registry import build_default_registry


class SkeletonCompatAliasTests(unittest.TestCase):
    def test_all_specs_expose_compat_archetype(self):
        reg = build_default_registry()
        for spec in reg.specs:
            self.assertTrue(spec.compat_archetype)


if __name__ == "__main__":
    unittest.main()
