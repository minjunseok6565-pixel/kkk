import unittest
from types import SimpleNamespace

from trades.valuation.env import ValuationEnv
from trades.valuation.package_effects import PackageEffects, PackageEffectsConfig
from trades.valuation.types import AssetKind, PlayerSnapshot, TeamValuation, ValueComponents


class PackageEffectsMetaPlayerNameTests(unittest.TestCase):
    def _ctx(self):
        knobs = SimpleNamespace(
            consolidation_bias=0.5,
            star_premium_exponent=1.0,
            w_now=1.0,
            w_future=1.0,
        )
        policies = SimpleNamespace(fit=SimpleNamespace(need_map={}))
        return SimpleNamespace(team_id="LAL", knobs=knobs, policies=policies, need_map={})

    def _tv(self, pid: str, total: float):
        return TeamValuation(
            asset_key=f"player:{pid}",
            kind=AssetKind.PLAYER,
            ref_id=pid,
            market_value=ValueComponents(now=total, future=0.0),
            team_value=ValueComponents(now=total, future=0.0),
        )

    def _snap(self, pid: str, *, name: str):
        return PlayerSnapshot(
            kind="player",
            player_id=pid,
            name=name,
            pos="SG",
            ovr=78,
            team_id="LAL",
        )

    def test_roster_excess_meta_uses_player_name_instead_of_player_id(self):
        eng = PackageEffects(
            config=PackageEffectsConfig(
                consolidation_scale=0.0,
                hole_penalty_scale=0.0,
                need_supply_excess_scale=0.0,
                slot_efficiency_enabled=False,
                agency_public_trade_request_discount=0.0,
                roster_excess_waste_rate=1.0,
                roster_excess_cap_ratio=1.0,
            )
        )

        incoming = [
            (self._tv("P000111", 4.0), self._snap("P000111", name="Alpha Guard")),
            (self._tv("P000222", 2.0), self._snap("P000222", name="Beta Wing")),
        ]
        outgoing = [(self._tv("P000333", 1.0), self._snap("P000333", name="Gamma Big"))]

        _, steps, _ = eng.apply(
            team_id="LAL",
            incoming=incoming,
            outgoing=outgoing,
            ctx=self._ctx(),
            env=ValuationEnv.from_trade_rules({}, current_season_year=2026),
        )

        waste_step = next(s for s in steps if s.code == "ROSTER_EXCESS_WASTE")
        candidates = waste_step.meta.get("waive_candidates") or []
        self.assertTrue(candidates)
        row = candidates[0]
        self.assertIn("player_name", row)
        self.assertNotIn("player_id", row)


if __name__ == "__main__":
    unittest.main()
