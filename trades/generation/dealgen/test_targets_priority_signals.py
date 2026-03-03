import random
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from trades.generation.asset_catalog import TeamOutgoingCatalog
from trades.generation.dealgen.targets import select_targets_sell
from trades.generation.dealgen.types import DealGeneratorBudget, DealGeneratorConfig

class _DeterministicRng:
    def random(self):
        return 0.0

    def shuffle(self, seq):
        return None


class _TickCtxStub:
    def __init__(self, *, agency_state_by_player=None):
        self.current_date = date(2026, 2, 1)
        self.provider = SimpleNamespace(agency_state_by_player=dict(agency_state_by_player or {}))

    def get_team_situation(self, team_id: str):
        return SimpleNamespace(trade_posture="SELL")


class SellTargetPrioritySignalTests(unittest.TestCase):
    def _catalog(self, players):
        out_cat = TeamOutgoingCatalog(
            team_id="LAL",
            player_ids_by_bucket={"SURPLUS_LOW_FIT": tuple(players.keys()), "CORE": tuple()},
            pick_ids_by_bucket={"FIRST_SAFE": tuple(), "FIRST_SENSITIVE": tuple(), "SECOND": tuple()},
            swap_ids=tuple(),
            players=players,
            picks={},
            swaps={},
        )
        return SimpleNamespace(outgoing_by_team={"LAL": out_cat})

    def _player(self, pid: str, *, surplus: float = 0.4):
        return SimpleNamespace(
            player_id=pid,
            lock=None,
            recent_signing_banned_until=None,
            buckets=("SURPLUS_LOW_FIT",),
            surplus_score=float(surplus),
            is_expiring=False,
            market=SimpleNamespace(total=10.0),
            salary_m=8.0,
            remaining_years=2.0,
            top_tags=tuple(),
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

    def test_listed_player_gets_priority_boost(self):
        cfg = DealGeneratorConfig()
        tick_ctx = _TickCtxStub()
        catalog = self._catalog({"p1": self._player("p1"), "p2": self._player("p2")})

        with patch(
            "trades.generation.dealgen.targets._active_public_listing_priority_by_player",
            return_value={"p2": 1.0},
        ):
            out = select_targets_sell(
                "LAL",
                tick_ctx,
                catalog,
                cfg,
                budget=self._budget(),
                rng=random.Random(7),
                banned_players=set(),
            )

        self.assertEqual(out[0].player_id, "p2")

    def test_public_trade_request_boost_applies_without_listing(self):
        cfg = DealGeneratorConfig()
        tick_ctx = _TickCtxStub(agency_state_by_player={"p2": {"trade_request_level": 2}})
        catalog = self._catalog({"p1": self._player("p1"), "p2": self._player("p2")})

        with patch(
            "trades.generation.dealgen.targets._active_public_listing_priority_by_player",
            return_value={},
        ):
            out = select_targets_sell(
                "LAL",
                tick_ctx,
                catalog,
                cfg,
                budget=self._budget(),
                rng=random.Random(11),
                banned_players=set(),
            )

        self.assertEqual(out[0].player_id, "p2")

    def test_signal_boost_capped_to_preserve_sort_stability(self):
        cfg = DealGeneratorConfig(
            listed_player_priority_boost=10.0,
            public_request_priority_boost=10.0,
            listed_public_request_synergy_boost=10.0,
            priority_signal_boost_cap=0.5,
        )
        tick_ctx = _TickCtxStub(agency_state_by_player={"p1": {"trade_request_level": 2}, "p2": {"trade_request_level": 2}})
        catalog = self._catalog({"p1": self._player("p1", surplus=0.6), "p2": self._player("p2", surplus=0.2)})

        with patch(
            "trades.generation.dealgen.targets._active_public_listing_priority_by_player",
            return_value={"p1": 1.0, "p2": 1.0},
        ):
            out = select_targets_sell(
                "LAL",
                tick_ctx,
                catalog,
                cfg,
                budget=self._budget(),
                rng=_DeterministicRng(),
                banned_players=set(),
            )

        # Both receive the same capped signal boost, so existing quality key (surplus) keeps order.
        self.assertEqual(out[0].player_id, "p1")

    def test_uses_tick_trade_market_snapshot_before_global_state(self):
        cfg = DealGeneratorConfig()
        tick_ctx = _TickCtxStub()
        tick_ctx.team_situation_ctx = SimpleNamespace(
            trade_market={
                "listings": {
                    "p2": {
                        "player_id": "p2",
                        "team_id": "LAL",
                        "status": "ACTIVE",
                        "visibility": "PUBLIC",
                        "listed_by": "USER",
                        "priority": 1.0,
                        "reason_code": "MANUAL",
                        "created_at": "2026-02-01",
                        "updated_at": "2026-02-01",
                        "expires_on": None,
                        "source": {},
                        "meta": {},
                    }
                }
            }
        )
        catalog = self._catalog({"p1": self._player("p1"), "p2": self._player("p2")})

        with patch("trades.orchestration.market_state.load_trade_market", side_effect=RuntimeError("must not be called")):
            out = select_targets_sell(
                "LAL",
                tick_ctx,
                catalog,
                cfg,
                budget=self._budget(),
                rng=random.Random(13),
                banned_players=set(),
            )

        self.assertEqual(out[0].player_id, "p2")


if __name__ == "__main__":
    unittest.main()
