import random
import unittest
from types import SimpleNamespace

from trades.generation.dealgen.targets import select_buyers_for_sale_asset
from trades.generation.dealgen.types import DealGeneratorBudget, DealGeneratorConfig, SellAssetCandidate
from trades.generation.dealgen.utils import classify_target_tier


class _TickCtxStub:
    def get_team_situation(self, team_id: str):
        return SimpleNamespace(
            trade_posture="AGGRESSIVE_BUY" if str(team_id).upper() == "BOS" else "STAND_PAT",
            urgency=0.7 if str(team_id).upper() == "BOS" else 0.2,
            constraints=SimpleNamespace(cooldown_active=False),
        )

    def get_decision_context(self, team_id: str):
        # BOS strongly needs PICK_BRIDGE so it should be selected as best_tag_hint.
        if str(team_id).upper() == "BOS":
            return SimpleNamespace(need_map={"PICK_BRIDGE": 1.0, "WING": 0.4})
        return SimpleNamespace(need_map={"WING": 0.3})


class SellMatchTagHintTests(unittest.TestCase):
    def test_match_tag_is_hint_not_pick_only_short_circuit(self):
        sale = SellAssetCandidate(
            player_id="p1",
            market_total=63.0,
            salary_m=17.0,
            remaining_years=2.0,
            is_expiring=False,
            top_tags=("PICK_BRIDGE", "WING"),
        )

        catalog = SimpleNamespace(
            outgoing_by_team={
                "BOS": SimpleNamespace(),
                "NYK": SimpleNamespace(),
                "LAL": SimpleNamespace(),
            }
        )

        out = select_buyers_for_sale_asset(
            seller_id="LAL",
            sale_asset=sale,
            tick_ctx=_TickCtxStub(),
            catalog=catalog,
            config=DealGeneratorConfig(),
            budget=DealGeneratorBudget(
                max_targets=10,
                beam_width=4,
                max_attempts_per_target=4,
                max_validations=40,
                max_evaluations=20,
                max_repairs=1,
            ),
            rng=random.Random(7),
        )

        self.assertTrue(out)
        buyer_id, match_tag_hint = out[0]
        self.assertEqual(buyer_id, "BOS")
        self.assertEqual(match_tag_hint, "PICK_BRIDGE")

        # PICK keyword hint alone must not force PICK_ONLY without pick_ctx inventory gate.
        self.assertNotEqual(
            classify_target_tier(
                sale_asset=sale,
                match_tag=match_tag_hint,
                config=DealGeneratorConfig(),
            ),
            "PICK_ONLY",
        )


if __name__ == "__main__":
    unittest.main()
