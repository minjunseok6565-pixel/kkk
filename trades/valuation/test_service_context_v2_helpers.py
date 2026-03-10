import os
import unittest

from trades.models import Deal, PickAsset, PlayerAsset, SwapAsset
from trades.valuation.service import _build_context_v2_asset_ids, _resolve_bool_flag
from trades.valuation import service as service_mod


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


    def test_resolve_bool_flag_default_on_when_unset(self):
        prev = os.environ.get("TRADE_VALUATION_CONTEXT_V2")
        try:
            os.environ.pop("TRADE_VALUATION_CONTEXT_V2", None)
            self.assertTrue(_resolve_bool_flag(None, env_name="TRADE_VALUATION_CONTEXT_V2", default=True))
        finally:
            if prev is None:
                os.environ.pop("TRADE_VALUATION_CONTEXT_V2", None)
            else:
                os.environ["TRADE_VALUATION_CONTEXT_V2"] = prev


    def test_stage_default_literal_is_full(self):
        kw = service_mod.evaluate_deal_for_team.__kwdefaults__ or {}
        self.assertEqual(kw.get("valuation_context_v2_stage"), "full")

    def test_resolve_bool_flag_prefers_explicit_and_env_fallback(self):
        prev = os.environ.get("TRADE_VALUATION_CONTEXT_V2")
        try:
            os.environ["TRADE_VALUATION_CONTEXT_V2"] = "true"
            self.assertTrue(_resolve_bool_flag(None, env_name="TRADE_VALUATION_CONTEXT_V2", default=False))
            self.assertFalse(_resolve_bool_flag(False, env_name="TRADE_VALUATION_CONTEXT_V2", default=True))

            os.environ["TRADE_VALUATION_CONTEXT_V2"] = "0"
            self.assertFalse(_resolve_bool_flag(None, env_name="TRADE_VALUATION_CONTEXT_V2", default=True))
        finally:
            if prev is None:
                os.environ.pop("TRADE_VALUATION_CONTEXT_V2", None)
            else:
                os.environ["TRADE_VALUATION_CONTEXT_V2"] = prev


if __name__ == "__main__":
    unittest.main()
