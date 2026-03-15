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

    def test_counter_probability_changes_smoothly_around_half_rate(self):
        policy = DecisionPolicy(config=DecisionPolicyConfig(stochastic_counter=False))
        outgoing = 10.0
        scale = outgoing
        required = 0.1 * scale
        net = required - (0.01 * scale)
        corridor = policy.config.counter_corridor_ratio * scale

        p_49 = policy._counter_probability(
            counter_rate=0.49,
            net=net,
            accept_threshold=required,
            overpay_allowed=0.2 * scale,
            scale=scale,
            corridor=corridor,
        )
        p_51 = policy._counter_probability(
            counter_rate=0.51,
            net=net,
            accept_threshold=required,
            overpay_allowed=0.2 * scale,
            scale=scale,
            corridor=corridor,
        )

        self.assertGreater(p_49, 0.0)
        self.assertLess(p_51, 1.0)
        self.assertGreater(p_51, p_49)
        self.assertLess(p_51 - p_49, 0.05)

    def test_low_counter_rate_is_suppressed_in_same_near_threshold_condition(self):
        policy = DecisionPolicy(config=DecisionPolicyConfig(stochastic_counter=False))
        outgoing = 10.0
        scale = outgoing
        required = 0.1 * scale
        net = required - (0.01 * scale)
        corridor = policy.config.counter_corridor_ratio * scale

        p_low = policy._counter_probability(
            counter_rate=0.05,
            net=net,
            accept_threshold=required,
            overpay_allowed=0.2 * scale,
            scale=scale,
            corridor=corridor,
        )
        p_mid = policy._counter_probability(
            counter_rate=0.5,
            net=net,
            accept_threshold=required,
            overpay_allowed=0.2 * scale,
            scale=scale,
            corridor=corridor,
        )
        p_high = policy._counter_probability(
            counter_rate=0.95,
            net=net,
            accept_threshold=required,
            overpay_allowed=0.2 * scale,
            scale=scale,
            corridor=corridor,
        )

        self.assertLess(p_low, 0.5)
        self.assertGreater(p_mid, p_low)
        self.assertGreater(p_high, p_mid)

    def test_near_threshold_and_small_overpay_prefers_counter(self):
        policy = DecisionPolicy(config=DecisionPolicyConfig(stochastic_counter=False))
        # required=+1.0, net=+0.9 -> gray zone near acceptance threshold
        evaluation = self._evaluation(outgoing=10.0, net=0.9)
        decision = policy.decide(evaluation=evaluation, ctx=self._ctx(counter_rate=0.95), allow_counter=True)

        self.assertEqual(decision.verdict, DealVerdict.COUNTER)
        self.assertIsNotNone(decision.meta.get("counter_score"))

    def test_same_net_changes_verdict_with_counter_rate(self):
        policy = DecisionPolicy(config=DecisionPolicyConfig(stochastic_counter=False))
        evaluation = self._evaluation(outgoing=10.0, net=0.9)

        low = policy.decide(evaluation=evaluation, ctx=self._ctx(counter_rate=0.05), allow_counter=True)
        high = policy.decide(evaluation=evaluation, ctx=self._ctx(counter_rate=0.95), allow_counter=True)

        self.assertNotEqual(low.verdict, high.verdict)
        self.assertEqual(low.verdict, DealVerdict.ACCEPT)
        self.assertEqual(high.verdict, DealVerdict.COUNTER)

    def test_counter_rate_sweep_distribution_in_gray_zone(self):
        policy = DecisionPolicy(config=DecisionPolicyConfig(stochastic_counter=False))
        rates = [0.05, 0.2, 0.5, 0.8, 0.95]
        nets = [0.96, 0.93, 0.90, 0.87, 0.84]

        verdict_counts = {r: {DealVerdict.ACCEPT: 0, DealVerdict.COUNTER: 0, DealVerdict.REJECT: 0} for r in rates}
        for r in rates:
            for net in nets:
                dec = policy.decide(
                    evaluation=self._evaluation(outgoing=10.0, net=net),
                    ctx=self._ctx(counter_rate=r),
                    allow_counter=True,
                )
                verdict_counts[r][dec.verdict] += 1

        self.assertGreater(verdict_counts[0.05][DealVerdict.ACCEPT], 0)
        self.assertGreater(verdict_counts[0.95][DealVerdict.COUNTER], 0)
        self.assertGreater(
            verdict_counts[0.95][DealVerdict.COUNTER],
            verdict_counts[0.05][DealVerdict.COUNTER],
        )

    def test_clear_reject_zone_unchanged(self):
        policy = DecisionPolicy(config=DecisionPolicyConfig(stochastic_counter=False))
        # required=+1.0 and overpay_floor=-2.0. net=-3.5 is clear reject.
        evaluation = self._evaluation(outgoing=10.0, net=-3.5)
        decision = policy.decide(evaluation=evaluation, ctx=self._ctx(counter_rate=0.95), allow_counter=True)
        self.assertEqual(decision.verdict, DealVerdict.REJECT)


if __name__ == "__main__":
    unittest.main()
