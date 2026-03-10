import unittest
from types import SimpleNamespace

from trades.valuation.env import ValuationEnv
from trades.valuation.package_effects import PackageEffects, PackageEffectsConfig
from trades.valuation.types import (
    AssetKind,
    ContractOptionSnapshot,
    ContractSnapshot,
    PlayerSnapshot,
    TeamValuation,
    ValueComponents,
)


class PackageEffectsTextureComponentTests(unittest.TestCase):
    def _ctx(self):
        knobs = SimpleNamespace(
            consolidation_bias=0.5,
            star_premium_exponent=1.0,
            w_now=1.0,
            w_future=1.0,
        )
        policies = SimpleNamespace(fit=SimpleNamespace(need_map={"CAP_FLEX": 1.0}))
        return SimpleNamespace(team_id="LAL", knobs=knobs, policies=policies, need_map={"CAP_FLEX": 1.0}, debug={"cap_space": 0.0})

    def _tv(self, pid: str, total: float):
        return TeamValuation(
            asset_key=f"player:{pid}",
            kind=AssetKind.PLAYER,
            ref_id=pid,
            market_value=ValueComponents(now=total, future=0.0),
            team_value=ValueComponents(now=total, future=0.0),
        )

    def _player(self, pid: str, *, role_fit: dict, years: int, salary: float, option_type: str | None = None):
        opts = []
        if option_type is not None:
            opts = [ContractOptionSnapshot(season_year=2027, type=option_type, status="PENDING")]
        return PlayerSnapshot(
            kind="player",
            player_id=pid,
            pos="SG/SF",
            team_id="LAL",
            salary_amount=salary,
            meta={"role_fit": role_fit},
            contract=ContractSnapshot(
                contract_id=f"c_{pid}",
                player_id=pid,
                team_id="LAL",
                status="ACTIVE",
                signed_date=None,
                start_season_year=2026,
                years=years,
                salary_by_year={2026: salary, 2027: salary, 2028: salary},
                options=opts,
            ),
        )

    def _engine(self, **overrides):
        cfg = PackageEffectsConfig(
            consolidation_scale=0.0,
            roster_excess_waste_rate=0.0,
            hole_penalty_scale=0.0,
            depth_need_scale=0.0,
            upgrade_scale=0.0,
            cap_room_weight_base=0.0,
            cap_room_value_per_cap_fraction=0.0,
            cap_room_abs_cap=0.0,
            **overrides,
        )
        return PackageEffects(config=cfg)

    def test_emits_texture_components_and_dual_meta(self):
        eng = self._engine(dual_read_v2_components=True)

        p1 = self._player(
            "p1",
            role_fit={"Engine_Primary": 0.9, "SpotUp_Spacer": 0.8, "Rim_Pressure": 0.2},
            years=3,
            salary=20_000_000,
            option_type="PO",
        )
        p2 = self._player(
            "p2",
            role_fit={"Engine_Secondary": 0.9, "Movement_Shooter": 0.7, "Rim_Pressure": 0.1},
            years=3,
            salary=18_000_000,
            option_type="PO",
        )

        delta, steps, meta = eng.apply(
            team_id="LAL",
            incoming=[(self._tv("p1", 10.0), p1), (self._tv("p2", 9.5), p2)],
            outgoing=[],
            ctx=self._ctx(),
            env=ValuationEnv.from_trade_rules({"salary_cap": 140_000_000}, current_season_year=2026),
        )

        self.assertNotEqual(delta.total, 0.0)
        codes = {s.code for s in steps}
        self.assertIn("BASKETBALL_COMPONENT", codes)
        self.assertIn("CONTRACT_COMPONENT", codes)
        self.assertIn("v2_texture_diff", meta)
        self.assertIn("contract_component_delta", meta["v2_texture_diff"])
        self.assertEqual(meta["v2_texture_diff"]["contract_component_delta"]["selected_source"], "contract_texture")

    def test_diminishing_returns_uses_texture_overlap_matrix(self):
        eng = self._engine(cap_flex_scale=0.0, dual_read_v2_components=False)

        p1 = self._player("a", role_fit={"Engine_Primary": 0.9, "SpotUp_Spacer": 0.8}, years=2, salary=10_000_000)
        p2 = self._player("b", role_fit={"Engine_Secondary": 0.9, "Movement_Shooter": 0.8}, years=2, salary=10_000_000)
        p3 = self._player("c", role_fit={"Roll_Man": 0.9, "Rim_Pressure": 0.9}, years=2, salary=10_000_000)

        _, steps, _ = eng.apply(
            team_id="LAL",
            incoming=[(self._tv("a", 10.0), p1), (self._tv("b", 9.0), p2), (self._tv("c", 8.0), p3)],
            outgoing=[],
            ctx=self._ctx(),
            env=ValuationEnv.from_trade_rules({"salary_cap": 140_000_000}, current_season_year=2026),
        )

        dim = [s for s in steps if s.code == "DIMINISHING_RETURNS"]
        self.assertTrue(dim)
        self.assertIn("pairwise_overlap", dim[0].meta)
        self.assertIn("a", dim[0].meta["pairwise_overlap"])

    def test_cap_flex_commitment_uses_contract_texture_inputs(self):
        eng = self._engine(dual_read_v2_components=True)

        po_player = self._player("po", role_fit={"Engine_Primary": 0.7}, years=3, salary=15_000_000, option_type="PO")
        to_player = self._player("to", role_fit={"Engine_Primary": 0.7}, years=3, salary=15_000_000, option_type="TO")

        _, _, meta_po = eng.apply(
            team_id="LAL",
            incoming=[(self._tv("po", 8.0), po_player)],
            outgoing=[],
            ctx=self._ctx(),
            env=ValuationEnv.from_trade_rules({"salary_cap": 140_000_000}, current_season_year=2026),
        )
        _, _, meta_to = eng.apply(
            team_id="LAL",
            incoming=[(self._tv("to", 8.0), to_player)],
            outgoing=[],
            ctx=self._ctx(),
            env=ValuationEnv.from_trade_rules({"salary_cap": 140_000_000}, current_season_year=2026),
        )

        commit_po = meta_po["v2_texture_diff"]["contract_component_delta"]["texture"]["incoming_commit"]
        commit_to = meta_to["v2_texture_diff"]["contract_component_delta"]["texture"]["incoming_commit"]
        self.assertGreater(commit_po, commit_to)


if __name__ == "__main__":
    unittest.main()
