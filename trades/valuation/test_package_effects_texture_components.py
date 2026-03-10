import unittest
from types import SimpleNamespace
from unittest.mock import patch

from trades.valuation.env import ValuationEnv
from trades.valuation.contract_texture import ContractTexture
from trades.valuation.package_effects import PackageEffects, PackageEffectsConfig
from trades.valuation.role_texture import RoleTexture
from trades.valuation.types import (
    AssetKind,
    ContractOptionSnapshot,
    ContractSnapshot,
    PlayerSnapshot,
    TeamValuation,
    ValueComponents,
)


class PackageEffectsTextureComponentTests(unittest.TestCase):
    def _ctx(self, *, debug_override: dict | None = None):
        knobs = SimpleNamespace(
            consolidation_bias=0.5,
            star_premium_exponent=1.0,
            w_now=1.0,
            w_future=1.0,
        )
        policies = SimpleNamespace(fit=SimpleNamespace(need_map={"CAP_FLEX": 1.0}))
        debug = {"cap_space": 0.0}
        if debug_override:
            debug.update(debug_override)
        return SimpleNamespace(team_id="LAL", posture="STAND_PAT", knobs=knobs, policies=policies, need_map={"CAP_FLEX": 1.0}, debug=debug)

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
        base = dict(
            consolidation_scale=0.0,
            roster_excess_waste_rate=0.0,
            roster_excess_cap_ratio=0.0,
            hole_penalty_scale=0.0,
            depth_need_scale=0.0,
            upgrade_scale=0.0,
            cap_room_weight_base=0.0,
            cap_room_value_per_cap_fraction=0.0,
            cap_room_abs_cap=0.0,
        )
        base.update(overrides)
        cfg = PackageEffectsConfig(**base)
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
        self.assertIn("v2_texture_diff", meta)
        self.assertIn("contract_component_delta", meta["v2_texture_diff"])
        self.assertEqual(meta["v2_texture_diff"]["contract_component_delta"]["selected_source"], "cap_ledger")

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

    def test_cap_flex_prefers_cap_ledger_when_payroll_available(self):
        eng = self._engine(dual_read_v2_components=True)
        player = self._player("lg", role_fit={"Engine_Primary": 0.6}, years=3, salary=20_000_000, option_type="PO")

        _, _, meta = eng.apply(
            team_id="LAL",
            incoming=[(self._tv("lg", 7.5), player)],
            outgoing=[],
            ctx=self._ctx(debug_override={"payroll": 165_000_000, "cap_space": 5_000_000}),
            env=ValuationEnv.from_trade_rules({"salary_cap": 140_000_000}, current_season_year=2026),
        )

        self.assertEqual(meta["v2_texture_diff"]["contract_component_delta"]["selected_source"], "cap_ledger")

    def test_rotation_context_reduces_waste_for_handler_need(self):
        eng = self._engine(
            cap_flex_scale=0.0,
            cap_flex_use_ledger_delta=False,
            dual_read_v2_components=False,
            roster_excess_waste_rate=0.85,
            roster_excess_cap_ratio=1.0,
        )

        p1 = self._player("h1", role_fit={"Engine_Primary": 0.95, "SpotUp_Spacer": 0.2}, years=2, salary=8_000_000)
        p2 = self._player("h2", role_fit={"Engine_Secondary": 0.9, "SpotUp_Spacer": 0.3}, years=2, salary=7_000_000)
        out = self._player("out", role_fit={"Roll_Man": 0.3}, years=1, salary=3_000_000)

        # excess incoming = 1 (incoming 2 / outgoing 1), compare no-rotation-context vs handler-need context
        _, steps_base, _ = eng.apply(
            team_id="LAL",
            incoming=[(self._tv("h1", 6.0), p1), (self._tv("h2", 5.0), p2)],
            outgoing=[(self._tv("out", 4.0), out)],
            ctx=self._ctx(),
            env=ValuationEnv.from_trade_rules({"salary_cap": 140_000_000}, current_season_year=2026),
        )
        _, steps_rot, _ = eng.apply(
            team_id="LAL",
            incoming=[(self._tv("h1", 6.0), p1), (self._tv("h2", 5.0), p2)],
            outgoing=[(self._tv("out", 4.0), out)],
            ctx=self._ctx(debug_override={"rotation_context": {"primary_handler_need": 1.0, "big_wing_balance_pressure": 0.0}}),
            env=ValuationEnv.from_trade_rules({"salary_cap": 140_000_000}, current_season_year=2026),
        )

        base_waste = [s for s in steps_base if s.code == "ROSTER_EXCESS_WASTE"][0].delta.total
        rot_waste = [s for s in steps_rot if s.code == "ROSTER_EXCESS_WASTE"][0].delta.total
        self.assertGreater(rot_waste, base_waste)

    def test_depth_need_emits_texture_parallel_metadata(self):
        eng = self._engine(
            depth_need_scale=1.0,
            hole_penalty_scale=0.0,
            roster_excess_waste_rate=0.0,
            cap_flex_scale=0.0,
            cap_flex_use_ledger_delta=False,
            dual_read_v2_components=False,
        )

        p_in = self._player("din", role_fit={"Engine_Primary": 0.9, "Rim_Pressure": 0.8, "HELP_RIM": 0.6}, years=2, salary=9_000_000)
        p_out = self._player("dout", role_fit={"Engine_Primary": 0.1, "Rim_Pressure": 0.1, "HELP_RIM": 0.2}, years=2, salary=9_000_000)
        ctx = self._ctx()
        ctx.need_map.update({"GUARD_DEPTH": 1.0, "BIG_DEPTH": 1.0, "WING_DEPTH": 0.6})

        _, steps, _ = eng.apply(
            team_id="LAL",
            incoming=[(self._tv("din", 8.0), p_in)],
            outgoing=[(self._tv("dout", 6.0), p_out)],
            ctx=ctx,
            env=ValuationEnv.from_trade_rules({"salary_cap": 140_000_000}, current_season_year=2026),
        )

        depth = [s for s in steps if s.code == "DEPTH_NEED_DELTA"]
        self.assertTrue(depth)
        self.assertIn("delta_texture", depth[0].meta)
        self.assertIn("texture_bonus_total", depth[0].meta)

    def test_outgoing_hole_includes_texture_penalties(self):
        eng = self._engine(
            hole_penalty_scale=0.22,
            hole_penalty_cap_ratio=1.0,
            depth_need_scale=0.0,
            roster_excess_waste_rate=0.0,
            cap_flex_scale=0.0,
            cap_flex_use_ledger_delta=False,
            dual_read_v2_components=False,
        )

        p_in = self._player("hin", role_fit={"Engine_Primary": 0.1, "Rim_Pressure": 0.1, "HELP_RIM": 0.2}, years=2, salary=6_000_000)
        p_out = self._player("hout", role_fit={"Engine_Primary": 0.9, "Rim_Pressure": 0.9, "HELP_RIM": 0.9}, years=2, salary=6_000_000)

        _, steps, _ = eng.apply(
            team_id="LAL",
            incoming=[(self._tv("hin", 4.0), p_in)],
            outgoing=[(self._tv("hout", 9.0), p_out)],
            ctx=self._ctx(),
            env=ValuationEnv.from_trade_rules({"salary_cap": 140_000_000}, current_season_year=2026),
        )

        hole = [s for s in steps if s.code == "OUTGOING_HOLE_PENALTY"]
        self.assertTrue(hole)
        self.assertIn("texture_penalties", hole[0].meta)
        self.assertTrue(isinstance(hole[0].meta["texture_penalties"], dict))

    def test_uses_prefetched_role_texture_from_context_v2(self):
        eng = self._engine(
            dual_read_v2_components=False,
            cap_flex_scale=0.0,
            cap_flex_use_ledger_delta=False,
            depth_need_scale=0.0,
            hole_penalty_scale=0.0,
            roster_excess_waste_rate=0.0,
        )
        p1 = self._player("r1", role_fit={}, years=2, salary=9_000_000)
        p2 = self._player("r2", role_fit={}, years=2, salary=8_000_000)

        v2_ctx = SimpleNamespace(
            role_textures={
                "r1": RoleTexture(0.9, 0.2, 0.3, 0.2, 0.4, {"role_fit": True, "supply_vector": True}),
                "r2": RoleTexture(0.88, 0.2, 0.28, 0.25, 0.4, {"role_fit": True, "supply_vector": True}),
            },
            contract_textures={},
        )

        with patch("trades.valuation.package_effects.build_role_textures", side_effect=AssertionError("must not rebuild role texture")):
            _, steps, _ = eng.apply(
                team_id="LAL",
                incoming=[(self._tv("r1", 9.0), p1), (self._tv("r2", 8.0), p2)],
                outgoing=[],
                ctx=self._ctx(),
                env=ValuationEnv.from_trade_rules({"salary_cap": 140_000_000}, current_season_year=2026),
                valuation_context_v2=v2_ctx,
            )

        self.assertTrue(any(s.code == "DIMINISHING_RETURNS" for s in steps))

    def test_uses_prefetched_contract_texture_from_context_v2(self):
        eng = self._engine(
            dual_read_v2_components=True,
            cap_flex_use_ledger_delta=False,
            depth_need_scale=0.0,
            hole_penalty_scale=0.0,
            roster_excess_waste_rate=0.0,
        )
        p = self._player("ct1", role_fit={"Engine_Primary": 0.2}, years=3, salary=14_000_000)

        v2_ctx = SimpleNamespace(
            role_textures={},
            contract_textures={
                "c_ct1": ContractTexture(
                    guaranteed_commitment=25_000_000,
                    control_direction=-1.0,
                    reversibility=0.2,
                    trigger_risk=0.6,
                    matching_utility=0.5,
                    toxic_risk=0.7,
                    notes=tuple(),
                    source_coverage={"contract_terms": True, "options": True, "salary_cap": True},
                )
            },
        )

        with patch("trades.valuation.package_effects.build_contract_textures", side_effect=AssertionError("must not rebuild contract texture")):
            _, _, meta = eng.apply(
                team_id="LAL",
                incoming=[(self._tv("ct1", 7.0), p)],
                outgoing=[],
                ctx=self._ctx(),
                env=ValuationEnv.from_trade_rules({"salary_cap": 140_000_000}, current_season_year=2026),
                valuation_context_v2=v2_ctx,
            )

        tex = meta["v2_texture_diff"]["contract_component_delta"]["texture"]
        self.assertAlmostEqual(tex["incoming_commit"], 33_250_000.0)


if __name__ == "__main__":
    unittest.main()
