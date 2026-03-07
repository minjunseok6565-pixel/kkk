import random
import unittest
from datetime import date
from types import SimpleNamespace

from trades.generation.asset_catalog import IncomingPlayerRef, TeamOutgoingCatalog
from trades.generation.dealgen.skeletons import build_offer_skeletons_buy
from trades.generation.dealgen.targets import select_targets_buy
from trades.generation.dealgen.types import DealGeneratorBudget, DealGeneratorConfig, TargetCandidate


class _TickCtxStub:
    def __init__(self, *, trade_market=None):
        self.current_date = date(2026, 2, 10)
        self.team_situation_ctx = SimpleNamespace(trade_market=trade_market or {"listings": {}})

    def get_decision_context(self, team_id: str):
        return SimpleNamespace(need_map={"WING": 1.0})

    def get_team_situation(self, team_id: str):
        return SimpleNamespace(
            trade_posture="BUY",
            constraints=SimpleNamespace(cooldown_active=False, cap_space=30_000_000),
            needs=[],
            time_horizon="RE_TOOL",
        )


class BuyTargetListingInterestTests(unittest.TestCase):
    def _catalog(self, refs):
        out_lal = TeamOutgoingCatalog(
            team_id="LAL",
            player_ids_by_bucket={"SURPLUS_LOW_FIT": tuple(r.player_id for r in refs if r.from_team == "LAL"), "CORE": tuple()},
            pick_ids_by_bucket={"FIRST_SAFE": tuple(), "FIRST_SENSITIVE": tuple(), "SECOND": tuple()},
            swap_ids=tuple(),
            players={
                r.player_id: SimpleNamespace(buckets=("SURPLUS_LOW_FIT",))
                for r in refs if r.from_team == "LAL"
            },
            picks={},
            swaps={},
        )
        out_bos = TeamOutgoingCatalog(
            team_id="BOS",
            player_ids_by_bucket={"SURPLUS_LOW_FIT": tuple(r.player_id for r in refs if r.from_team == "BOS"), "CORE": tuple()},
            pick_ids_by_bucket={"FIRST_SAFE": tuple(), "FIRST_SENSITIVE": tuple(), "SECOND": tuple()},
            swap_ids=tuple(),
            players={
                r.player_id: SimpleNamespace(buckets=("SURPLUS_LOW_FIT",))
                for r in refs if r.from_team == "BOS"
            },
            picks={},
            swaps={},
        )
        return SimpleNamespace(
            incoming_by_need_tag={"WING": tuple(refs)},
            incoming_cheap_by_need_tag={"WING": tuple()},
            outgoing_by_team={"LAL": out_lal, "BOS": out_bos},
        )

    def _budget(self):
        return DealGeneratorBudget(
            max_targets=10,
            beam_width=4,
            max_attempts_per_target=4,
            max_validations=50,
            max_evaluations=30,
            max_repairs=1,
        )

    def test_listed_player_gets_buy_side_interest_priority(self):
        refs = [
            IncomingPlayerRef("p1", "LAL", "WING", 0.9, 10.0, 8.0, 2.0, 26.0),
            IncomingPlayerRef("p2", "LAL", "WING", 0.9, 10.0, 8.0, 2.0, 26.0),
        ]
        trade_market = {
            "listings": {
                "p2": {
                    "player_id": "p2",
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
            _TickCtxStub(trade_market=trade_market),
            self._catalog(refs),
            DealGeneratorConfig(),
            budget=self._budget(),
            rng=random.Random(7),
            banned_players=set(),
        )
        self.assertEqual(out[0].player_id, "p2")

    def test_listing_recency_decay_reduces_interest(self):
        refs = [
            IncomingPlayerRef("p1", "LAL", "WING", 0.9, 10.0, 8.0, 2.0, 26.0),
            IncomingPlayerRef("p2", "LAL", "WING", 0.9, 10.0, 8.0, 2.0, 26.0),
        ]
        trade_market = {
            "listings": {
                "p1": {
                    "player_id": "p1",
                    "team_id": "LAL",
                    "status": "ACTIVE",
                    "visibility": "PUBLIC",
                    "priority": 1.0,
                    "updated_at": "2026-01-01",
                },
                "p2": {
                    "player_id": "p2",
                    "team_id": "LAL",
                    "status": "ACTIVE",
                    "visibility": "PUBLIC",
                    "priority": 0.6,
                    "updated_at": "2026-02-10",
                },
            }
        }
        cfg = DealGeneratorConfig(buy_target_listing_interest_recency_half_life_days=7.0)
        out = select_targets_buy(
            "BOS",
            _TickCtxStub(trade_market=trade_market),
            self._catalog(refs),
            cfg,
            budget=self._budget(),
            rng=random.Random(7),
            banned_players=set(),
        )
        self.assertEqual(out[0].player_id, "p2")

    def test_public_listing_bypasses_seller_bucket_willingness_gate(self):
        refs = [
            IncomingPlayerRef("core1", "LAL", "WING", 0.8, 10.0, 8.0, 2.0, 26.0),
        ]
        out_lal = TeamOutgoingCatalog(
            team_id="LAL",
            player_ids_by_bucket={"SURPLUS_LOW_FIT": tuple(), "CORE": tuple()},
            pick_ids_by_bucket={"FIRST_SAFE": tuple(), "FIRST_SENSITIVE": tuple(), "SECOND": tuple()},
            swap_ids=tuple(),
            players={"core1": SimpleNamespace(buckets=tuple())},
            picks={},
            swaps={},
        )
        catalog = SimpleNamespace(
            incoming_by_need_tag={"WING": tuple(refs)},
            incoming_cheap_by_need_tag={"WING": tuple()},
            outgoing_by_team={"LAL": out_lal, "BOS": self._catalog(refs).outgoing_by_team["BOS"]},
        )
        trade_market = {
            "listings": {
                "core1": {
                    "player_id": "core1",
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
            _TickCtxStub(trade_market=trade_market),
            catalog,
            DealGeneratorConfig(),
            budget=self._budget(),
            rng=random.Random(7),
            banned_players=set(),
        )
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].player_id, "core1")


    def test_non_listed_target_can_be_selected_without_seller_outgoing_bucket_gate(self):
        refs = [IncomingPlayerRef("core2", "LAL", "WING", 0.8, 10.0, 8.0, 2.0, 26.0)]
        out_lal = TeamOutgoingCatalog(
            team_id="LAL",
            player_ids_by_bucket={"SURPLUS_LOW_FIT": tuple(), "CORE": tuple()},
            pick_ids_by_bucket={"FIRST_SAFE": tuple(), "FIRST_SENSITIVE": tuple(), "SECOND": tuple()},
            swap_ids=tuple(),
            players={"core2": SimpleNamespace(buckets=tuple())},
            picks={},
            swaps={},
        )
        catalog = SimpleNamespace(
            incoming_by_need_tag={"WING": tuple(refs)},
            incoming_cheap_by_need_tag={"WING": tuple()},
            outgoing_by_team={"LAL": out_lal, "BOS": self._catalog(refs).outgoing_by_team["BOS"]},
        )

        out = select_targets_buy(
            "BOS",
            _TickCtxStub(trade_market={"listings": {}}),
            catalog,
            DealGeneratorConfig(),
            budget=self._budget(),
            rng=random.Random(7),
            banned_players=set(),
        )

        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].player_id, "core2")

    def test_buy_skeleton_builds_even_when_target_not_in_seller_outgoing_bucket(self):
        refs = [IncomingPlayerRef("core1", "LAL", "WING", 0.8, 10.0, 8.0, 2.0, 26.0)]
        out_lal = TeamOutgoingCatalog(
            team_id="LAL",
            player_ids_by_bucket={"SURPLUS_LOW_FIT": tuple(), "CORE": tuple()},
            pick_ids_by_bucket={"FIRST_SAFE": tuple(), "FIRST_SENSITIVE": tuple(), "SECOND": tuple()},
            swap_ids=tuple(),
            players={"core1": SimpleNamespace(buckets=tuple(), return_ban_teams=tuple())},
            picks={},
            swaps={},
        )
        out_bos = TeamOutgoingCatalog(
            team_id="BOS",
            player_ids_by_bucket={"SURPLUS_LOW_FIT": tuple(), "CORE": tuple()},
            pick_ids_by_bucket={"FIRST_SAFE": tuple(), "FIRST_SENSITIVE": tuple(), "SECOND": tuple()},
            swap_ids=tuple(),
            players={},
            picks={},
            swaps={},
        )
        catalog = SimpleNamespace(
            incoming_by_need_tag={"WING": tuple(refs)},
            incoming_cheap_by_need_tag={"WING": tuple()},
            outgoing_by_team={"LAL": out_lal, "BOS": out_bos},
        )
        listed_target = TargetCandidate(
            player_id="core1",
            from_team="LAL",
            need_tag="WING",
            tag_strength=1.0,
            market_total=10.0,
            salary_m=8.0,
            remaining_years=2.0,
            age=26.0,
        )

        candidates = build_offer_skeletons_buy(
            "BOS",
            "LAL",
            listed_target,
            _TickCtxStub(),
            catalog,
            config=DealGeneratorConfig(),
            budget=self._budget(),
            rng=random.Random(7),
            banned_asset_keys=set(),
            banned_players=set(),
            banned_receivers_by_player={},
        )

        self.assertGreaterEqual(len(candidates), 1)


if __name__ == "__main__":
    unittest.main()
