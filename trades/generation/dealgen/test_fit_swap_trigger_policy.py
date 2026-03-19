import unittest

from trades.generation.dealgen.fit_swap import (
    _allowed_verdicts_for_receiver,
    _is_triggerable_for_receiver,
)
from trades.valuation.types import DealDecision, DealVerdict, DecisionReason


class FitSwapTriggerPolicyTests(unittest.TestCase):
    def _decision(self, verdict: DealVerdict, *, fit_fails: bool) -> DealDecision:
        reasons = tuple()
        if fit_fails:
            reasons = (
                DecisionReason(code="FIT_FAILS", message="fit failure"),
            )
        return DealDecision(
            verdict=verdict,
            required_surplus=0.0,
            overpay_allowed=0.0,
            reasons=reasons,
        )

    def test_allowed_verdicts_for_initiator_receiver(self):
        allowed = _allowed_verdicts_for_receiver(receiver_id="LAL", initiator_team_id="LAL")
        self.assertEqual(allowed, (DealVerdict.REJECT, DealVerdict.COUNTER))

    def test_allowed_verdicts_for_acceptor_receiver(self):
        allowed = _allowed_verdicts_for_receiver(receiver_id="BOS", initiator_team_id="LAL")
        self.assertEqual(allowed, (DealVerdict.REJECT,))

    def test_initiator_counter_with_fit_fails_triggers(self):
        dec = self._decision(DealVerdict.COUNTER, fit_fails=True)
        self.assertTrue(
            _is_triggerable_for_receiver(
                dec=dec,
                receiver_id="LAL",
                initiator_team_id="LAL",
            )
        )

    def test_acceptor_counter_with_fit_fails_does_not_trigger(self):
        dec = self._decision(DealVerdict.COUNTER, fit_fails=True)
        self.assertFalse(
            _is_triggerable_for_receiver(
                dec=dec,
                receiver_id="BOS",
                initiator_team_id="LAL",
            )
        )

    def test_acceptor_reject_with_fit_fails_triggers(self):
        dec = self._decision(DealVerdict.REJECT, fit_fails=True)
        self.assertTrue(
            _is_triggerable_for_receiver(
                dec=dec,
                receiver_id="BOS",
                initiator_team_id="LAL",
            )
        )


if __name__ == "__main__":
    unittest.main()
