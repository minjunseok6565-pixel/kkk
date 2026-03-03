import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from trades.orchestration.actor_selection import select_trade_actors
from trades.orchestration.types import OrchestrationConfig


class _GreedyRng:
    def choices(self, population, weights, k=1):
        if not population:
            return []
        idx = max(range(len(population)), key=lambda i: float(weights[i]))
        return [population[idx]]


class _ProviderStub:
    def __init__(self, agency_state_by_player, team_by_player, *, repo=None, fail_on_get_snapshot=False):
        self.agency_state_by_player = dict(agency_state_by_player)
        self._team_by_player = dict(team_by_player)
        self.repo = repo
        self._fail_on_get_snapshot = bool(fail_on_get_snapshot)

    def get_player_snapshot(self, player_id: str):
        if self._fail_on_get_snapshot:
            raise RuntimeError("get_player_snapshot should not be called")
        return SimpleNamespace(team_id=self._team_by_player.get(str(player_id), ""))


class ActorSelectionPriorityTuningTests(unittest.TestCase):
    def _tick_ctx(self, team_ids, *, agency_state_by_player=None, team_by_player=None, repo=None, fail_on_get_snapshot=False):
        ts_map = {}
        for tid in team_ids:
            ts_map[str(tid).upper()] = SimpleNamespace(constraints=SimpleNamespace(cooldown_active=False))
        provider = _ProviderStub(agency_state_by_player or {}, team_by_player or {}, repo=repo, fail_on_get_snapshot=fail_on_get_snapshot)
        return SimpleNamespace(
            team_situations=ts_map,
            provider=provider,
            current_date=date(2026, 2, 1),
        )

    def _config(self, **overrides):
        base = dict(
            min_active_teams=1,
            max_active_teams=1,
            enable_threads=False,
            enable_trade_block=True,
            trade_block_actor_weight_multiplier=1.2,
            trade_request_public_actor_weight_multiplier=1.2,
            trade_request_public_no_listing_weight_multiplier=1.08,
            trade_request_public_with_listing_weight_multiplier=1.06,
            actor_weight_multiplier_cap=3.0,
        )
        base.update(dict(overrides or {}))
        return OrchestrationConfig(**base)

    def test_listing_plus_public_request_team_gets_actor_priority(self):
        tick_ctx = self._tick_ctx(
            ["LAL", "NYK"],
            agency_state_by_player={"p1": {"trade_request_level": 2}},
            team_by_player={"p1": "LAL"},
        )

        with patch("trades.orchestration.actor_selection.policy.team_activity_breakdown", return_value={"activity_score": 1.0, "tags": []}), \
             patch("trades.orchestration.actor_selection.policy.market_day_rhythm", return_value=(1.0, "NORMAL", {})), \
             patch("trades.orchestration.actor_selection.policy.compute_active_team_budget", return_value=1), \
             patch("trades.orchestration.actor_selection.policy.apply_day_rhythm_to_budget", return_value=1), \
             patch("trades.orchestration.actor_selection.policy.assign_dynamic_max_results", side_effect=lambda picked, **_: picked), \
             patch("trades.orchestration.actor_selection.get_active_listing_team_ids", return_value={"LAL"}):
            picked = select_trade_actors(
                tick_ctx,
                config=self._config(),
                rng=_GreedyRng(),
                trade_market={"listings": {}},
                today=date(2026, 2, 1),
            )

        self.assertEqual(len(picked), 1)
        self.assertEqual(picked[0].team_id, "LAL")

    def test_actor_weight_cap_prevents_runaway_flip(self):
        tick_ctx = self._tick_ctx(
            ["BOS", "LAL"],
            agency_state_by_player={"p1": {"trade_request_level": 2}},
            team_by_player={"p1": "LAL"},
        )

        breakdown = {
            "BOS": {"activity_score": 1.00, "tags": []},
            "LAL": {"activity_score": 0.70, "tags": []},
        }

        def _bd(ts):
            # match by object identity via map scan
            for tid, obj in tick_ctx.team_situations.items():
                if obj is ts:
                    return breakdown[tid]
            return {"activity_score": 0.0, "tags": []}

        cfg = self._config(
            trade_block_actor_weight_multiplier=5.0,
            trade_request_public_actor_weight_multiplier=5.0,
            trade_request_public_with_listing_weight_multiplier=2.0,
            actor_weight_multiplier_cap=1.0,
        )

        with patch("trades.orchestration.actor_selection.policy.team_activity_breakdown", side_effect=_bd), \
             patch("trades.orchestration.actor_selection.policy.market_day_rhythm", return_value=(1.0, "NORMAL", {})), \
             patch("trades.orchestration.actor_selection.policy.compute_active_team_budget", return_value=1), \
             patch("trades.orchestration.actor_selection.policy.apply_day_rhythm_to_budget", return_value=1), \
             patch("trades.orchestration.actor_selection.policy.assign_dynamic_max_results", side_effect=lambda picked, **_: picked), \
             patch("trades.orchestration.actor_selection.get_active_listing_team_ids", return_value={"LAL"}):
            picked = select_trade_actors(
                tick_ctx,
                config=cfg,
                rng=_GreedyRng(),
                trade_market={"listings": {}},
                today=date(2026, 2, 1),
            )

        self.assertEqual(len(picked), 1)
        self.assertEqual(picked[0].team_id, "BOS")


    def test_public_request_team_count_uses_repo_batch_lookup(self):
        class _RepoStub:
            def get_team_ids_by_players(self, pids):
                return {str(pid): "LAL" for pid in (pids or [])}

        tick_ctx = self._tick_ctx(
            ["LAL", "NYK"],
            agency_state_by_player={"p1": {"trade_request_level": 2}, "p2": {"trade_request_level": 2}},
            team_by_player={},
            repo=_RepoStub(),
            fail_on_get_snapshot=True,
        )

        with patch("trades.orchestration.actor_selection.policy.team_activity_breakdown", return_value={"activity_score": 1.0, "tags": []}), \
             patch("trades.orchestration.actor_selection.policy.market_day_rhythm", return_value=(1.0, "NORMAL", {})), \
             patch("trades.orchestration.actor_selection.policy.compute_active_team_budget", return_value=1), \
             patch("trades.orchestration.actor_selection.policy.apply_day_rhythm_to_budget", return_value=1), \
             patch("trades.orchestration.actor_selection.policy.assign_dynamic_max_results", side_effect=lambda picked, **_: picked), \
             patch("trades.orchestration.actor_selection.get_active_listing_team_ids", return_value=set()):
            picked = select_trade_actors(
                tick_ctx,
                config=self._config(),
                rng=_GreedyRng(),
                trade_market={"listings": {}},
                today=date(2026, 2, 1),
            )

        self.assertEqual(len(picked), 1)
        self.assertEqual(picked[0].team_id, "LAL")


if __name__ == "__main__":
    unittest.main()
