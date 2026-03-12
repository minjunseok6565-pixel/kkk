import random
import unittest
from datetime import date
from types import SimpleNamespace

from trades.generation.asset_catalog import IncomingPlayerRef, TeamOutgoingCatalog
from trades.generation.dealgen.targets import select_targets_buy
from trades.generation.dealgen.types import DealGeneratorBudget, DealGeneratorConfig


class _TickCtxStub:
    def __init__(self, *, deadline_pressure: float = 0.0, urgency: float = 0.0, trade_market=None, need_map=None):
        self.current_date = date(2026, 2, 10)
        self._deadline_pressure = float(deadline_pressure)
        self._urgency = float(urgency)
        self.team_situation_ctx = SimpleNamespace(trade_market=trade_market or {"listings": {}})
        self._need_map = dict(need_map or {"WING": 1.0})

    def get_decision_context(self, team_id: str):
        return SimpleNamespace(need_map=dict(self._need_map))

    def get_team_situation(self, team_id: str):
        return SimpleNamespace(
            trade_posture="BUY",
            urgency=self._urgency,
            constraints=SimpleNamespace(
                cooldown_active=False,
                cap_space=30_000_000,
                deadline_pressure=self._deadline_pressure,
                apron_status="OVER_CAP",
            ),
            needs=[],
            time_horizon="RE_TOOL",
        )


class BuyTieredRetrievalTests(unittest.TestCase):
    def _budget(self, max_targets: int = 8):
        return DealGeneratorBudget(
            max_targets=max_targets,
            beam_width=4,
            max_attempts_per_target=4,
            max_validations=50,
            max_evaluations=30,
            max_repairs=1,
        )

    def _catalog(self, refs):
        by_team = {}
        for team_id in {str(r.from_team).upper() for r in refs} | {"BOS"}:
            team_refs = [r for r in refs if str(r.from_team).upper() == team_id]
            by_team[team_id] = TeamOutgoingCatalog(
                team_id=team_id,
                player_ids_by_bucket={"SURPLUS_EXPENDABLE": tuple(r.player_id for r in team_refs)},
                pick_ids_by_bucket={"FIRST_SAFE": tuple(), "FIRST_SENSITIVE": tuple(), "SECOND": tuple()},
                swap_ids=tuple(),
                players={r.player_id: SimpleNamespace(buckets=("SURPLUS_EXPENDABLE",)) for r in team_refs},
                picks={},
                swaps={},
            )

        return SimpleNamespace(
            incoming_all_players=tuple(refs),
            outgoing_by_team=by_team,
        )

    def test_listed_always_on_at_low_deadline(self):
        refs = [
            IncomingPlayerRef("listed1", "LAL", "WING", 0.1, 5.0, 6.0, 1.0, 25.0),
            IncomingPlayerRef("n1", "NYK", "BIG", 0.9, 12.0, 7.0, 2.0, 24.0),
        ]
        trade_market = {
            "listings": {
                "listed1": {
                    "player_id": "listed1",
                    "team_id": "LAL",
                    "status": "ACTIVE",
                    "visibility": "PUBLIC",
                    "priority": 1.0,
                    "updated_at": "2026-02-10",
                }
            }
        }

        out = select_targets_buy(
            "BOS",
            _TickCtxStub(deadline_pressure=0.0, urgency=0.0, trade_market=trade_market),
            self._catalog(refs),
            DealGeneratorConfig(buy_target_listed_min_quota=1),
            budget=self._budget(max_targets=2),
            rng=random.Random(7),
            banned_players=set(),
        )

        self.assertIn("listed1", {t.player_id for t in out})

    def test_non_listed_expands_with_deadline_pressure(self):
        refs = [
            IncomingPlayerRef(f"p{i}", f"T{i}", "WING", 0.6, 10.0 + i, 5.0, 2.0, 24.0)
            for i in range(20)
        ]
        cfg = DealGeneratorConfig(
            buy_target_listed_min_quota=0,
            buy_target_non_listed_base_quota=2,
            buy_target_non_listed_deadline_bonus_max=10,
            buy_target_expand_tier2_enabled=False,
        )

        low = select_targets_buy(
            "BOS",
            _TickCtxStub(deadline_pressure=0.0, urgency=0.0),
            self._catalog(refs),
            cfg,
            budget=self._budget(max_targets=20),
            rng=random.Random(7),
            banned_players=set(),
        )
        high = select_targets_buy(
            "BOS",
            _TickCtxStub(deadline_pressure=1.0, urgency=1.0),
            self._catalog(refs),
            cfg,
            budget=self._budget(max_targets=20),
            rng=random.Random(7),
            banned_players=set(),
        )

        self.assertLessEqual(len(low), len(high))


    def test_deadline_phase_monotonic_non_listed_counts(self):
        refs = [
            IncomingPlayerRef(f"p{i}", f"T{i}", "WING", 0.55 + (i % 3) * 0.05, 8.0 + i, 4.0, 2.0, 25.0)
            for i in range(30)
        ]
        cfg = DealGeneratorConfig(
            buy_target_listed_min_quota=0,
            buy_target_non_listed_base_quota=3,
            buy_target_non_listed_deadline_bonus_max=9,
            buy_target_expand_tier2_enabled=False,
        )

        low = select_targets_buy(
            "BOS",
            _TickCtxStub(deadline_pressure=0.0, urgency=0.0),
            self._catalog(refs),
            cfg,
            budget=self._budget(max_targets=30),
            rng=random.Random(13),
            banned_players=set(),
        )
        mid = select_targets_buy(
            "BOS",
            _TickCtxStub(deadline_pressure=0.5, urgency=0.5),
            self._catalog(refs),
            cfg,
            budget=self._budget(max_targets=30),
            rng=random.Random(13),
            banned_players=set(),
        )
        high = select_targets_buy(
            "BOS",
            _TickCtxStub(deadline_pressure=1.0, urgency=1.0),
            self._catalog(refs),
            cfg,
            budget=self._budget(max_targets=30),
            rng=random.Random(13),
            banned_players=set(),
        )

        self.assertLessEqual(len(low), len(mid))
        self.assertLessEqual(len(mid), len(high))

    def test_quota_separation_preserves_non_listed_slots(self):
        listed = [
            IncomingPlayerRef(f"l{i}", "LAL", "WING", 0.8, 12.0, 8.0, 2.0, 27.0)
            for i in range(10)
        ]
        non_listed = [IncomingPlayerRef("n1", "NYK", "WING", 0.7, 11.0, 7.0, 2.0, 26.0)]
        refs = listed + non_listed
        trade_market = {
            "listings": {
                r.player_id: {
                    "player_id": r.player_id,
                    "team_id": "LAL",
                    "status": "ACTIVE",
                    "visibility": "PUBLIC",
                    "priority": 0.8,
                    "updated_at": "2026-02-10",
                }
                for r in listed
            }
        }
        cfg = DealGeneratorConfig(
            buy_target_listed_min_quota=1,
            buy_target_non_listed_base_quota=1,
            buy_target_listed_max_share=0.75,
        )

        out = select_targets_buy(
            "BOS",
            _TickCtxStub(deadline_pressure=0.0, urgency=0.0, trade_market=trade_market),
            self._catalog(refs),
            cfg,
            budget=self._budget(max_targets=4),
            rng=random.Random(7),
            banned_players=set(),
        )

        self.assertIn("n1", {t.player_id for t in out})


    def test_listed_bypasses_non_listed_team_cap(self):
        refs = [
            IncomingPlayerRef("listed_far", "LAL", "WING", 0.2, 6.0, 5.0, 1.0, 27.0),
            IncomingPlayerRef("n_core", "NYK", "WING", 0.8, 15.0, 7.0, 2.0, 25.0),
            IncomingPlayerRef("n_other", "MIA", "WING", 0.75, 14.0, 7.0, 2.0, 25.0),
        ]
        trade_market = {
            "listings": {
                "listed_far": {
                    "player_id": "listed_far",
                    "team_id": "LAL",
                    "status": "ACTIVE",
                    "visibility": "PUBLIC",
                    "priority": 1.0,
                    "updated_at": "2026-02-10",
                }
            }
        }
        cfg = DealGeneratorConfig(
            buy_target_listed_min_quota=1,
            buy_target_non_listed_base_quota=1,
            buy_target_max_teams_scanned_base=1,
            buy_target_max_teams_scanned_deadline_bonus=0,
            buy_target_expand_tier2_enabled=False,
        )

        out = select_targets_buy(
            "BOS",
            _TickCtxStub(deadline_pressure=0.0, urgency=0.0, trade_market=trade_market),
            self._catalog(refs),
            cfg,
            budget=self._budget(max_targets=2),
            rng=random.Random(17),
            banned_players=set(),
        )

        self.assertIn("listed_far", {t.player_id for t in out})

    def test_late_high_value_candidate_not_missed(self):
        refs = [
            IncomingPlayerRef("a", "LAL", "WING", 0.2, 4.0, 2.0, 1.0, 28.0),
            IncomingPlayerRef("b", "NYK", "WING", 0.3, 5.0, 2.0, 1.0, 28.0),
            IncomingPlayerRef("star", "MIA", "WING", 1.0, 60.0, 20.0, 4.0, 25.0),
        ]

        out = select_targets_buy(
            "BOS",
            _TickCtxStub(deadline_pressure=0.0, urgency=0.0),
            self._catalog(refs),
            DealGeneratorConfig(buy_target_listed_min_quota=0, buy_target_non_listed_base_quota=3),
            budget=self._budget(max_targets=1),
            rng=random.Random(7),
            banned_players=set(),
        )

        self.assertEqual(out[0].player_id, "star")

    def test_need_similarity_uses_multi_tag_profile(self):
        refs = [
            IncomingPlayerRef(
                "fit_need", "LAL", "WING", 0.6, 10.0, 6.0, 2.0, 25.0,
                supply_items=(("WING", 0.7), ("BIG", 0.2)),
            ),
            IncomingPlayerRef(
                "fit_other", "NYK", "WING", 0.6, 10.0, 6.0, 2.0, 25.0,
                supply_items=(("WING", 0.1), ("BIG", 0.8)),
            ),
        ]

        out = select_targets_buy(
            "BOS",
            _TickCtxStub(deadline_pressure=0.0, urgency=0.0, need_map={"WING": 1.0, "BIG": 0.2}),
            self._catalog(refs),
            DealGeneratorConfig(
                buy_target_listed_min_quota=0,
                buy_target_non_listed_base_quota=2,
                buy_target_need_weight_scale=0.8,
                buy_target_need_mismatch_floor=-0.2,
                buy_target_market_weight=0.3,
                buy_target_fit_weight=0.45,
            ),
            budget=self._budget(max_targets=2),
            rng=random.Random(31),
            banned_players=set(),
        )

        self.assertEqual(out[0].player_id, "fit_need")


    def test_contract_gap_affects_rank_without_direct_salary_term(self):
        refs = [
            IncomingPlayerRef(
                "good_contract", "LAL", "WING", 0.8, 20.0, 20.0, 3.0, 26.0,
                basketball_total=22.0,
                contract_gap_cap_share=0.08,
            ),
            IncomingPlayerRef(
                "bad_contract", "NYK", "WING", 0.8, 20.0, 20.0, 3.0, 26.0,
                basketball_total=22.0,
                contract_gap_cap_share=-0.08,
            ),
        ]

        cfg = DealGeneratorConfig(
            buy_target_listed_min_quota=0,
            buy_target_non_listed_base_quota=2,
            buy_target_contract_base_weight=0.35,
            buy_target_pre_score_contract_weight=0.20,
        )

        out = select_targets_buy(
            "BOS",
            _TickCtxStub(deadline_pressure=0.5, urgency=0.5),
            self._catalog(refs),
            cfg,
            budget=self._budget(max_targets=2),
            rng=random.Random(9),
            banned_players=set(),
        )

        self.assertEqual(out[0].player_id, "good_contract")


if __name__ == "__main__":
    unittest.main()
