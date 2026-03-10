import unittest

from trades.models import Deal, PickAsset, PlayerAsset, SwapAsset
from trades.valuation.service import _build_context_v2_asset_ids


class ServiceContextV2HelperTests(unittest.TestCase):
    def test_build_context_v2_asset_ids_collects_unique_ids(self):
        deal = Deal(
            teams=["LAL", "BOS"],
            legs={
                "LAL": [
                    PlayerAsset(kind="player", player_id="p1", to_team="BOS"),
                    PlayerAsset(kind="player", player_id="p1", to_team="BOS"),
                    PickAsset(kind="pick", pick_id="pk1", to_team="BOS"),
                ],
                "BOS": [
                    SwapAsset(kind="swap", swap_id="sw1", pick_id_a="pk2", pick_id_b="pk3", to_team="LAL"),
                ],
            },
        )

        out = _build_context_v2_asset_ids(deal, standings_order_worst_to_best=["DET", "WAS"])

        self.assertEqual(out["players"], ("p1",))
        self.assertEqual(out["picks"], ("pk1",))
        self.assertEqual(out["swaps"], ("sw1",))
        self.assertEqual(out["standings_order_worst_to_best"], ("DET", "WAS"))

if __name__ == "__main__":
    unittest.main()
