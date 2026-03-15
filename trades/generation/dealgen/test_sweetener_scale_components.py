import unittest

from trades.generation.dealgen.sweetener import _sweetener_scale_components, _sweetener_close_corridor
from trades.generation.dealgen.types import DealGeneratorConfig
from trades.valuation.types import SideTotals, TeamDealEvaluation, TeamSideValuation, ValueComponents


class SweetenerScaleComponentsTests(unittest.TestCase):
    def _evaluation(
        self,
        *,
        incoming_now: float,
        incoming_future: float,
        outgoing_now: float,
        outgoing_future: float,
    ) -> TeamDealEvaluation:
        incoming_total = float(incoming_now + incoming_future)
        outgoing_total = float(outgoing_now + outgoing_future)
        side = TeamSideValuation(
            team_id="LAL",
            incoming=tuple(),
            outgoing=tuple(),
            incoming_totals=SideTotals(value=ValueComponents(now=incoming_now, future=incoming_future), count=0),
            outgoing_totals=SideTotals(value=ValueComponents(now=outgoing_now, future=outgoing_future), count=0),
        )
        return TeamDealEvaluation(
            team_id="LAL",
            incoming_total=incoming_total,
            outgoing_total=outgoing_total,
            net_surplus=0.0,
            surplus_ratio=0.0,
            side=side,
            meta={},
        )

    def test_scale_uses_incoming_axis_when_larger(self):
        e = self._evaluation(incoming_now=9.0, incoming_future=0.0, outgoing_now=2.0, outgoing_future=0.0)
        scale, out, inc, mass = _sweetener_scale_components(e)
        self.assertAlmostEqual(scale, 9.0, places=6)
        self.assertAlmostEqual(out, 2.0, places=6)
        self.assertAlmostEqual(inc, 9.0, places=6)
        self.assertAlmostEqual(mass, 9.0, places=6)

    def test_scale_uses_mass_axis_when_totals_cancel(self):
        e = self._evaluation(incoming_now=5.0, incoming_future=-5.0, outgoing_now=4.0, outgoing_future=-4.0)
        scale, out, inc, mass = _sweetener_scale_components(e)
        self.assertAlmostEqual(out, 0.0, places=6)
        self.assertAlmostEqual(inc, 0.0, places=6)
        self.assertAlmostEqual(mass, 10.0, places=6)
        self.assertAlmostEqual(scale, 10.0, places=6)

    def test_close_corridor_uses_multi_axis_scale(self):
        cfg = DealGeneratorConfig(
            sweetener_close_corridor_ratio=0.1,
            sweetener_close_floor=0.0,
            sweetener_close_cap=0.0,
            sweetener_max_deficit=0.0,
        )
        e = self._evaluation(incoming_now=9.0, incoming_future=0.0, outgoing_now=2.0, outgoing_future=0.0)
        close = _sweetener_close_corridor(e, cfg)
        self.assertAlmostEqual(close, 0.9, places=6)


if __name__ == "__main__":
    unittest.main()
