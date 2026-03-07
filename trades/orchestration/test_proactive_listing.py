import unittest
from datetime import date
from types import SimpleNamespace

from trades.orchestration.listing_policy import apply_ai_proactive_listings


class ProactiveListingTests(unittest.TestCase):
    def _tick_ctx(self, players, posture="SELL", agency=None):
        out = SimpleNamespace(players=players)
        return SimpleNamespace(
            asset_catalog=SimpleNamespace(outgoing_by_team={"LAL": out}),
            provider=SimpleNamespace(agency_state_by_player=dict(agency or {})),
            get_team_situation=lambda team_id: SimpleNamespace(trade_posture=posture),
        )

    def _cfg(self, **kwargs):
        base = dict(
            ai_proactive_listing_enabled=True,
            ai_proactive_listing_team_daily_cap=2,
            ai_proactive_listing_team_active_cap=4,
            ai_proactive_listing_player_cooldown_days=7,
            ai_proactive_listing_ttl_days_sell=12,
            ai_proactive_listing_ttl_days_soft_sell=7,
            ai_proactive_listing_ttl_days_default=5,
            ai_proactive_listing_min_score=0.25,
            ai_proactive_listing_priority_base=0.45,
            ai_proactive_listing_priority_span=0.35,
        )
        base.update(kwargs)
        return SimpleNamespace(**base)

    def test_proactive_listing_adds_candidate_without_props(self):
        players = {
            "p1": SimpleNamespace(
                buckets=("SURPLUS_LOW_FIT",),
                lock=SimpleNamespace(is_locked=False),
                recent_signing_banned_until=None,
                surplus_score=0.9,
                is_expiring=False,
            )
        }
        trade_market = {"listings": {}, "events": []}
        listed = apply_ai_proactive_listings(
            team_id="LAL",
            tick_ctx=self._tick_ctx(players),
            trade_market=trade_market,
            today=date(2026, 2, 1),
            config=self._cfg(),
        )
        self.assertEqual(listed, ["p1"])
        self.assertIn("p1", trade_market["listings"])
        self.assertEqual(trade_market["events"][-1]["payload"].get("origin"), "PROACTIVE")

    def test_proactive_listing_allows_core_but_still_blocks_locked(self):
        players = {
            "core": SimpleNamespace(
                buckets=("CORE",),
                lock=SimpleNamespace(is_locked=False),
                recent_signing_banned_until=None,
                surplus_score=1.0,
                is_expiring=False,
            ),
            "locked": SimpleNamespace(
                buckets=("SURPLUS_LOW_FIT",),
                lock=SimpleNamespace(is_locked=True),
                recent_signing_banned_until=None,
                surplus_score=1.0,
                is_expiring=False,
            ),
        }
        trade_market = {"listings": {}, "events": []}
        listed = apply_ai_proactive_listings(
            team_id="LAL",
            tick_ctx=self._tick_ctx(players),
            trade_market=trade_market,
            today=date(2026, 2, 1),
            config=self._cfg(ai_proactive_listing_team_daily_cap=2),
        )
        self.assertEqual(listed, ["core"])
        self.assertNotIn("locked", listed)

    def test_proactive_listing_respects_player_cooldown(self):
        players = {
            "p1": SimpleNamespace(
                buckets=("SURPLUS_LOW_FIT",),
                lock=SimpleNamespace(is_locked=False),
                recent_signing_banned_until=None,
                surplus_score=0.9,
                is_expiring=False,
            )
        }
        trade_market = {
            "listings": {},
            "events": [
                {
                    "at": "2026-02-01",
                    "type": "TRADE_BLOCK_LISTED",
                    "payload": {"team_id": "LAL", "player_id": "p1", "origin": "PROACTIVE"},
                }
            ],
        }
        listed = apply_ai_proactive_listings(
            team_id="LAL",
            tick_ctx=self._tick_ctx(players),
            trade_market=trade_market,
            today=date(2026, 2, 3),
            config=self._cfg(ai_proactive_listing_player_cooldown_days=7),
        )
        self.assertEqual(listed, [])


if __name__ == "__main__":
    unittest.main()
