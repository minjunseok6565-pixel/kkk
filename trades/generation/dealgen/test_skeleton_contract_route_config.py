import unittest

from trades.generation.dealgen.types import DealGeneratorConfig


class SkeletonContractRouteConfigTests(unittest.TestCase):
    def test_contract_route_fields_exist_with_empty_defaults(self):
        cfg = DealGeneratorConfig()
        self.assertEqual(cfg.skeleton_route_contract_overpay, tuple())
        self.assertEqual(cfg.skeleton_route_contract_fair, tuple())
        self.assertEqual(cfg.skeleton_route_contract_value, tuple())

    def test_contract_route_fields_are_configurable(self):
        cfg = DealGeneratorConfig(
            skeleton_route_contract_overpay=("mvp.pick_heavy",),
            skeleton_route_contract_fair=("all_star.mixed",),
            skeleton_route_contract_value=("mvp.player_heavy", "all_nba.player_heavy"),
        )
        self.assertEqual(cfg.skeleton_route_contract_overpay, ("mvp.pick_heavy",))
        self.assertEqual(cfg.skeleton_route_contract_fair, ("all_star.mixed",))
        self.assertEqual(
            cfg.skeleton_route_contract_value,
            ("mvp.player_heavy", "all_nba.player_heavy"),
        )


if __name__ == "__main__":
    unittest.main()
