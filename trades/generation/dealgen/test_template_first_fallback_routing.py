import unittest

from trades.generation.dealgen.skeleton_registry import build_default_registry
from trades.generation.dealgen.types import DealGeneratorConfig


class TemplateFirstFallbackRoutingTests(unittest.TestCase):
    def test_template_only_returns_template_route_specs(self):
        reg = build_default_registry()
        cfg = DealGeneratorConfig(
            skeleton_route_template_mvp=(
                "template.mvp.placeholder_2",
                "template.mvp.placeholder_4",
            ),
            skeleton_route_fallback_mvp=("mvp.player_heavy",),
        )

        specs = reg.get_specs_for_mode_and_tier(
            "BUY",
            "MVP",
            cfg,
            contract_tag="FAIR",
            route_phase="template_only",
        )
        ids = [s.skeleton_id for s in specs]

        self.assertEqual(ids, ["template.mvp.placeholder_2", "template.mvp.placeholder_4"])
        self.assertTrue(all(s.domain == "template" for s in specs))

    def test_fallback_only_returns_tier_score_specs(self):
        reg = build_default_registry()
        cfg = DealGeneratorConfig(
            skeleton_route_template_mvp=("template.mvp.placeholder_1",),
            skeleton_route_fallback_mvp=("mvp.pick_heavy", "mvp.mixed"),
        )

        specs = reg.get_specs_for_mode_and_tier(
            "BUY",
            "MVP",
            cfg,
            contract_tag="FAIR",
            route_phase="fallback_only",
        )
        ids = [s.skeleton_id for s in specs]

        self.assertEqual(ids, ["mvp.pick_heavy", "mvp.mixed"])
        self.assertTrue(all(s.domain != "template" for s in specs))

    def test_combined_returns_union_of_template_and_fallback(self):
        reg = build_default_registry()
        cfg = DealGeneratorConfig(
            skeleton_route_mvp=("mvp.player_heavy",),
            skeleton_route_contract_fair=("template.mvp.placeholder_3",),
            skeleton_route_template_mvp=("template.mvp.placeholder_1",),
            skeleton_route_fallback_mvp=("mvp.player_heavy",),
        )

        specs = reg.get_specs_for_mode_and_tier(
            "BUY",
            "MVP",
            cfg,
            contract_tag="FAIR",
            route_phase="combined",
        )
        ids = {s.skeleton_id for s in specs}

        self.assertIn("mvp.player_heavy", ids)
        self.assertIn("template.mvp.placeholder_3", ids)


if __name__ == "__main__":
    unittest.main()
