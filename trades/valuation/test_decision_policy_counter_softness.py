import unittest
from types import SimpleNamespace

from trades.valuation.decision_policy import DecisionPolicy, DecisionPolicyConfig
from trades.valuation.types import (
    DealVerdict,
    SideTotals,
    TeamDealEvaluation,
    TeamSideValuation,
    ValueComponents,
)


class DecisionPolicyCounterSoftnessTests(unittest.TestCase):
    def _ctx(self, *, counter_rate: float) -> SimpleNamespace:
        knobs = SimpleNamespace(
            min_surplus_required=0.1,
            overpay_budget=0.2,
            counter_rate=counter_rate,
        )
        return SimpleNamespace(knobs=knobs)

    def _evaluation(self, *, outgoing: float, net: float) -> TeamDealEvaluation:
        incoming = outgoing + net
        side = TeamSideValuation(
            team_id="LAL",
            incoming=tuple(),
            outgoing=tuple(),
            incoming_totals=SideTotals(value=ValueComponents(now=incoming, future=0.0), count=0),
            outgoing_totals=SideTotals(value=ValueComponents(now=outgoing, future=0.0), count=0),
        )
        return TeamDealEvaluation(
            team_id="LAL",
            incoming_total=incoming,
            outgoing_total=outgoing,
            net_surplus=net,
            surplus_ratio=0.0,
            side=side,
            meta={},
        )

    def _evaluation_components(
        self,
        *,
        incoming_now: float,
        incoming_future: float,
        outgoing_now: float,
        outgoing_future: float,
        net_surplus: float,
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
            net_surplus=float(net_surplus),
            surplus_ratio=0.0,
            side=side,
            meta={},
        )

    def test_near_threshold_and_small_overpay_prefers_counter(self):
        policy = DecisionPolicy(config=DecisionPolicyConfig())
        # required=+1.0, net=+0.9 -> gray zone near acceptance threshold
        evaluation = self._evaluation(outgoing=10.0, net=0.9)
        decision = policy.decide(evaluation=evaluation, ctx=self._ctx(counter_rate=0.95), allow_counter=True)

        self.assertEqual(decision.verdict, DealVerdict.COUNTER)
        self.assertIsNotNone(decision.meta.get("counter_score"))

    def test_same_net_keeps_counter_regardless_of_counter_rate(self):
        policy = DecisionPolicy(config=DecisionPolicyConfig())
        evaluation = self._evaluation(outgoing=10.0, net=0.9)

        low = policy.decide(evaluation=evaluation, ctx=self._ctx(counter_rate=0.05), allow_counter=True)
        high = policy.decide(evaluation=evaluation, ctx=self._ctx(counter_rate=0.95), allow_counter=True)

        self.assertEqual(low.verdict, DealVerdict.COUNTER)
        self.assertEqual(high.verdict, DealVerdict.COUNTER)

    def test_counter_rate_sweep_always_counter_in_gray_zone(self):
        policy = DecisionPolicy(config=DecisionPolicyConfig())
        rates = [0.05, 0.2, 0.5, 0.8, 0.95]
        nets = [0.96, 0.93, 0.90, 0.87, 0.84]

        for r in rates:
            for net in nets:
                dec = policy.decide(
                    evaluation=self._evaluation(outgoing=10.0, net=net),
                    ctx=self._ctx(counter_rate=r),
                    allow_counter=True,
                )
                self.assertEqual(dec.verdict, DealVerdict.COUNTER)

    def test_clear_reject_zone_unchanged(self):
        policy = DecisionPolicy(config=DecisionPolicyConfig())
        # required=+1.0 and overpay_floor=-2.0. net=-3.5 is clear reject.
        evaluation = self._evaluation(outgoing=10.0, net=-3.5)
        decision = policy.decide(evaluation=evaluation, ctx=self._ctx(counter_rate=0.95), allow_counter=True)
        self.assertEqual(decision.verdict, DealVerdict.REJECT)

    def test_threshold_scale_uses_incoming_when_larger_than_outgoing(self):
        policy = DecisionPolicy(config=DecisionPolicyConfig())
        # outgoing=2, incoming=9 -> scale should follow incoming axis.
        evaluation = self._evaluation(outgoing=2.0, net=7.0)
        decision = policy.decide(evaluation=evaluation, ctx=self._ctx(counter_rate=0.5), allow_counter=True)
        self.assertAlmostEqual(decision.required_surplus, 0.9, places=6)
        th_meta = next(r.meta for r in decision.reasons if r.code == "THRESHOLDS")
        self.assertAlmostEqual(float(th_meta["scale"]), 9.0, places=6)
        self.assertAlmostEqual(float(th_meta["scale_outgoing"]), 2.0, places=6)
        self.assertAlmostEqual(float(th_meta["scale_incoming"]), 9.0, places=6)

    def test_threshold_scale_can_be_driven_by_mass_axis(self):
        policy = DecisionPolicy(config=DecisionPolicyConfig())
        # Totals cancel to zero, but abs mass is large: in_mass=10, out_mass=10.
        evaluation = self._evaluation_components(
            incoming_now=5.0,
            incoming_future=-5.0,
            outgoing_now=4.0,
            outgoing_future=-4.0,
            net_surplus=0.0,
        )
        decision = policy.decide(evaluation=evaluation, ctx=self._ctx(counter_rate=0.5), allow_counter=True)
        self.assertAlmostEqual(decision.required_surplus, 1.0, places=6)
        th_meta = next(r.meta for r in decision.reasons if r.code == "THRESHOLDS")
        self.assertAlmostEqual(float(th_meta["scale"]), 10.0, places=6)
        self.assertAlmostEqual(float(th_meta["scale_mass"]), 10.0, places=6)

    def test_threshold_scale_has_eps_guard_when_all_axes_zero(self):
        policy = DecisionPolicy(config=DecisionPolicyConfig(eps=1e-9))
        evaluation = self._evaluation_components(
            incoming_now=0.0,
            incoming_future=0.0,
            outgoing_now=0.0,
            outgoing_future=0.0,
            net_surplus=0.0,
        )
        decision = policy.decide(evaluation=evaluation, ctx=self._ctx(counter_rate=0.5), allow_counter=True)
        th_meta = next(r.meta for r in decision.reasons if r.code == "THRESHOLDS")
        self.assertGreaterEqual(float(th_meta["scale"]), policy.config.eps)


if __name__ == "__main__":
    unittest.main()
