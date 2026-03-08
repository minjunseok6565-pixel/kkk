import random
import unittest
from datetime import date
from types import SimpleNamespace

from trades.generation.asset_catalog import IncomingPlayerRef, TeamOutgoingCatalog
from trades.generation.dealgen.targets import select_targets_buy
from trades.generation.dealgen.types import DealGeneratorBudget, DealGeneratorConfig


class _TickCtxContractStub:
    def __init__(self, *, apron_status: str, posture: str = "BUY", deadline_pressure: float = 0.0):
        self.current_date = date(2026, 2, 10)
        self.team_situation_ctx = SimpleNamespace(trade_market={"listings": {}})
        self._apron_status = str(apron_status)
        self._posture = str(posture)
        self._deadline_pressure = float(deadline_pressure)

    def get_decision_context(self, team_id: str):
        return SimpleNamespace(need_map={"WING": 1.0})

    def get_team_situation(self, team_id: str):
        return SimpleNamespace(
            trade_posture=self._posture,
            urgency=0.5,
            constraints=SimpleNamespace(
                cooldown_active=False,
                cap_space=10_000_000,
                apron_status=self._apron_status,
                deadline_pressure=self._deadline_pressure,
            ),
            needs=[],
            time_horizon="RE_TOOL",
        )


class BuyTargetContractValueTests(unittest.TestCase):
    def _budget(self, max_targets: int = 3):
        return DealGeneratorBudget(
            max_targets=max_targets,
            beam_width=4,
            max_attempts_per_target=4,
            max_validations=50,
            max_evaluations=30,
            max_repairs=1,
        )

    def _catalog(self, refs):
        teams = {str(r.from_team).upper() for r in refs} | {"BOS"}
        by_team = {}
        for team_id in teams:
            team_refs = [r for r in refs if str(r.from_team).upper() == team_id]
            by_team[team_id] = TeamOutgoingCatalog(
                team_id=team_id,
                player_ids_by_bucket={"SURPLUS_LOW_FIT": tuple(r.player_id for r in team_refs)},
                pick_ids_by_bucket={"FIRST_SAFE": tuple(), "FIRST_SENSITIVE": tuple(), "SECOND": tuple()},
                swap_ids=tuple(),
                players={r.player_id: SimpleNamespace(buckets=("SURPLUS_LOW_FIT",)) for r in team_refs},
                picks={},
                swaps={},
            )
        return SimpleNamespace(incoming_all_players=tuple(refs), outgoing_by_team=by_team)

    def test_underpaid_breakout_ranks_above_fair_and_overpaid(self):
        refs = [
            IncomingPlayerRef(
                "underpaid", "LAL", "WING", 0.80, 24.0, 10.0, 4.0, 24.0,
                basketball_total=24.0,
                contract_gap_cap_share=0.08,
            ),
            IncomingPlayerRef(
                "fair_max", "NYK", "WING", 0.80, 24.0, 30.0, 4.0, 27.0,
                basketball_total=24.0,
                contract_gap_cap_share=0.00,
            ),
            IncomingPlayerRef(
                "overpaid", "MIA", "WING", 0.80, 24.0, 32.0, 4.0, 29.0,
                basketball_total=24.0,
                contract_gap_cap_share=-0.10,
            ),
        ]

        out = select_targets_buy(
            "BOS",
            _TickCtxContractStub(apron_status="OVER_CAP", posture="BUY", deadline_pressure=0.3),
            self._catalog(refs),
            DealGeneratorConfig(buy_target_listed_min_quota=0, buy_target_non_listed_base_quota=3),
            budget=self._budget(max_targets=3),
            rng=random.Random(5),
            banned_players=set(),
        )
        got = [t.player_id for t in out]
        self.assertEqual(got[0], "underpaid")
        self.assertEqual(got[-1], "overpaid")

    def test_same_gap_has_stronger_effect_under_second_apron(self):
        refs = [
            IncomingPlayerRef(
                "plus_gap", "LAL", "WING", 0.78, 20.0, 20.0, 3.0, 27.0,
                basketball_total=21.0,
                contract_gap_cap_share=0.05,
            ),
            IncomingPlayerRef(
                "minus_gap", "NYK", "WING", 0.78, 20.0, 20.0, 3.0, 27.0,
                basketball_total=21.0,
                contract_gap_cap_share=-0.05,
            ),
        ]
        cfg = DealGeneratorConfig(buy_target_listed_min_quota=0, buy_target_non_listed_base_quota=2)

        out_below = select_targets_buy(
            "BOS",
            _TickCtxContractStub(apron_status="BELOW_CAP", posture="BUY", deadline_pressure=0.0),
            self._catalog(refs),
            cfg,
            budget=self._budget(max_targets=2),
            rng=random.Random(8),
            banned_players=set(),
        )
        out_second = select_targets_buy(
            "BOS",
            _TickCtxContractStub(apron_status="ABOVE_2ND_APRON", posture="BUY", deadline_pressure=0.0),
            self._catalog(refs),
            cfg,
            budget=self._budget(max_targets=2),
            rng=random.Random(8),
            banned_players=set(),
        )

        spread_below = float(out_below[0].tag_strength - out_below[1].tag_strength)
        spread_second = float(out_second[0].tag_strength - out_second[1].tag_strength)
        self.assertGreater(spread_second, spread_below)


if __name__ == "__main__":
    unittest.main()
