import unittest
from datetime import date
from types import SimpleNamespace

from trades.orchestration.listing_policy import apply_ai_proactive_listings


class ProactiveListingTests(unittest.TestCase):
    def _tick_ctx(
        self,
        players,
        posture="SELL",
        horizon="RE_TOOL",
        urgency=0.2,
        cooldown_active=False,
        agency=None,
        player_ids_by_bucket=None,
    ):
        out = SimpleNamespace(players=players, player_ids_by_bucket=dict(player_ids_by_bucket or {}))
        ts = SimpleNamespace(
            trade_posture=posture,
            time_horizon=horizon,
            urgency=urgency,
            constraints=SimpleNamespace(cooldown_active=bool(cooldown_active)),
        )
        return SimpleNamespace(
            asset_catalog=SimpleNamespace(outgoing_by_team={"LAL": out}),
            provider=SimpleNamespace(agency_state_by_player=dict(agency or {})),
            get_team_situation=lambda team_id: ts,
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
            ai_proactive_listing_priority_base=0.45,
            ai_proactive_listing_priority_span=0.35,
            ai_proactive_listing_cadence="DAILY",
            ai_proactive_listing_anchor_weekday=0,
            ai_proactive_listing_threshold_enabled=True,
            ai_proactive_listing_threshold_default=0.55,
            ai_proactive_listing_bucket_thresholds={
                "AGGRESSIVE_BUY": {
                    "SURPLUS_LOW_FIT": 0.30,
                    "SURPLUS_REDUNDANT": 0.35,
                    "CONSOLIDATE": 0.55,
                    "FILLER_CHEAP": 0.65,
                    "FILLER_BAD_CONTRACT": 0.80,
                    "VETERAN_SALE": 0.90,
                },
                "SOFT_BUY": {
                    "SURPLUS_LOW_FIT": 0.38,
                    "SURPLUS_REDUNDANT": 0.42,
                    "CONSOLIDATE": 0.60,
                    "FILLER_CHEAP": 0.68,
                    "FILLER_BAD_CONTRACT": 0.82,
                    "VETERAN_SALE": 0.92,
                },
                "STAND_PAT": {
                    "SURPLUS_LOW_FIT": 0.50,
                    "SURPLUS_REDUNDANT": 0.55,
                    "CONSOLIDATE": 0.70,
                    "FILLER_CHEAP": 0.72,
                    "FILLER_BAD_CONTRACT": 0.86,
                    "VETERAN_SALE": 0.95,
                },
                "SOFT_SELL": {
                    "SURPLUS_LOW_FIT": 0.40,
                    "SURPLUS_REDUNDANT": 0.45,
                    "CONSOLIDATE": 0.85,
                    "FILLER_CHEAP": 0.62,
                    "FILLER_BAD_CONTRACT": 0.70,
                    "VETERAN_SALE": 0.45,
                },
                "SELL": {
                    "SURPLUS_LOW_FIT": 0.32,
                    "SURPLUS_REDUNDANT": 0.38,
                    "CONSOLIDATE": 0.90,
                    "FILLER_CHEAP": 0.58,
                    "FILLER_BAD_CONTRACT": 0.62,
                    "VETERAN_SALE": 0.35,
                },
            },
            ai_proactive_listing_threshold_horizon_win_now_delta=-0.03,
            ai_proactive_listing_threshold_horizon_rebuild_delta=-0.05,
            ai_proactive_listing_threshold_urgency_cut=0.75,
            ai_proactive_listing_threshold_urgency_delta=-0.03,
            ai_proactive_listing_threshold_cooldown_active_delta=0.05,
            ai_proactive_listing_threshold_min=0.10,
            ai_proactive_listing_threshold_max=0.95,
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
            tick_ctx=self._tick_ctx(players, player_ids_by_bucket={"SURPLUS_LOW_FIT": ("p1",)}),
            trade_market=trade_market,
            today=date(2026, 2, 1),
            config=self._cfg(),
        )
        self.assertEqual(listed, ["p1"])
        self.assertIn("p1", trade_market["listings"])
        self.assertEqual(trade_market["events"][-1]["payload"].get("origin"), "PROACTIVE")

    def test_proactive_listing_excludes_non_allowed_and_blocks_locked(self):
        players = {
            "not_allowed": SimpleNamespace(
                buckets=("UNKNOWN_BUCKET",),
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
            tick_ctx=self._tick_ctx(
                players,
                player_ids_by_bucket={"UNKNOWN_BUCKET": ("not_allowed",), "SURPLUS_LOW_FIT": ("locked",)},
            ),
            trade_market=trade_market,
            today=date(2026, 2, 1),
            config=self._cfg(ai_proactive_listing_team_daily_cap=2),
        )
        self.assertEqual(listed, [])
        self.assertNotIn("not_allowed", listed)
        self.assertNotIn("locked", listed)


    def test_proactive_listing_skips_filler_cheap_bucket(self):
        players = {
            "cheap": SimpleNamespace(
                buckets=("FILLER_CHEAP",),
                lock=SimpleNamespace(is_locked=False),
                recent_signing_banned_until=None,
                surplus_score=1.0,
                is_expiring=False,
            )
        }
        trade_market = {"listings": {}, "events": []}
        listed = apply_ai_proactive_listings(
            team_id="LAL",
            tick_ctx=self._tick_ctx(players, player_ids_by_bucket={"FILLER_CHEAP": ("cheap",)}),
            trade_market=trade_market,
            today=date(2026, 2, 1),
            config=self._cfg(),
        )
        self.assertEqual(listed, [])
        self.assertNotIn("cheap", trade_market.get("listings", {}))

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
            tick_ctx=self._tick_ctx(players, player_ids_by_bucket={"SURPLUS_LOW_FIT": ("p1",)}),
            trade_market=trade_market,
            today=date(2026, 2, 3),
            config=self._cfg(ai_proactive_listing_player_cooldown_days=7),
        )
        self.assertEqual(listed, [])

    def test_threshold_excludes_below_cutoff(self):
        players = {
            "p1": SimpleNamespace(
                buckets=("SURPLUS_LOW_FIT",),
                lock=SimpleNamespace(is_locked=False),
                recent_signing_banned_until=None,
                surplus_score=0.40,
                is_expiring=False,
            )
        }
        trade_market = {"listings": {}, "events": []}
        listed = apply_ai_proactive_listings(
            team_id="LAL",
            tick_ctx=self._tick_ctx(players, posture="STAND_PAT", player_ids_by_bucket={"SURPLUS_LOW_FIT": ("p1",)}),
            trade_market=trade_market,
            today=date(2026, 2, 2),
            config=self._cfg(),
        )
        self.assertEqual(listed, [])

    def test_weekly_cadence_skips_non_anchor_day(self):
        players = {
            "p1": SimpleNamespace(
                buckets=("SURPLUS_LOW_FIT",),
                lock=SimpleNamespace(is_locked=False),
                recent_signing_banned_until=None,
                surplus_score=0.9,
                is_expiring=False,
            )
        }
        trade_market = {"listings": {}, "events": [], "proactive_listing_meta": {}}
        listed = apply_ai_proactive_listings(
            team_id="LAL",
            tick_ctx=self._tick_ctx(players, player_ids_by_bucket={"SURPLUS_LOW_FIT": ("p1",)}),
            trade_market=trade_market,
            today=date(2026, 2, 3),  # Tuesday
            config=self._cfg(ai_proactive_listing_cadence="WEEKLY", ai_proactive_listing_anchor_weekday=0),
        )
        self.assertEqual(listed, [])
        self.assertEqual(trade_market["proactive_listing_meta"], {})

    def test_threshold_allows_above_cutoff(self):
        players = {
            "p1": SimpleNamespace(
                buckets=("SURPLUS_LOW_FIT",),
                lock=SimpleNamespace(is_locked=False),
                recent_signing_banned_until=None,
                surplus_score=0.80,
                is_expiring=False,
            )
        }
        trade_market = {"listings": {}, "events": []}
        listed = apply_ai_proactive_listings(
            team_id="LAL",
            tick_ctx=self._tick_ctx(players, posture="STAND_PAT", player_ids_by_bucket={"SURPLUS_LOW_FIT": ("p1",)}),
            trade_market=trade_market,
            today=date(2026, 2, 2),
            config=self._cfg(),
        )
        self.assertEqual(listed, ["p1"])

    def test_posture_specific_threshold_diff(self):
        players = {
            "p1": SimpleNamespace(
                buckets=("SURPLUS_LOW_FIT",),
                lock=SimpleNamespace(is_locked=False),
                recent_signing_banned_until=None,
                surplus_score=0.45,
                is_expiring=False,
            )
        }
        buy_listed = apply_ai_proactive_listings(
            team_id="LAL",
            tick_ctx=self._tick_ctx(players, posture="AGGRESSIVE_BUY", player_ids_by_bucket={"SURPLUS_LOW_FIT": ("p1",)}),
            trade_market={"listings": {}, "events": []},
            today=date(2026, 2, 2),
            config=self._cfg(),
        )
        pat_listed = apply_ai_proactive_listings(
            team_id="LAL",
            tick_ctx=self._tick_ctx(players, posture="STAND_PAT", player_ids_by_bucket={"SURPLUS_LOW_FIT": ("p1",)}),
            trade_market={"listings": {}, "events": []},
            today=date(2026, 2, 2),
            config=self._cfg(),
        )
        self.assertEqual(buy_listed, ["p1"])
        self.assertEqual(pat_listed, [])

    def test_weekly_cadence_stamps_last_eval_even_when_no_rows(self):
        players = {
            "p1": SimpleNamespace(
                buckets=("SURPLUS_LOW_FIT",),
                lock=SimpleNamespace(is_locked=True),
                recent_signing_banned_until=None,
                surplus_score=0.90,
                is_expiring=False,
            )
        }
        trade_market = {"listings": {}, "events": [], "proactive_listing_meta": {}}
        today = date(2026, 2, 2)  # Monday
        listed = apply_ai_proactive_listings(
            team_id="LAL",
            tick_ctx=self._tick_ctx(players, player_ids_by_bucket={"SURPLUS_LOW_FIT": ("p1",)}),
            trade_market=trade_market,
            today=today,
            config=self._cfg(ai_proactive_listing_cadence="WEEKLY", ai_proactive_listing_anchor_weekday=0),
        )
        self.assertEqual(listed, [])
        self.assertEqual(trade_market["proactive_listing_meta"]["LAL"]["last_eval_at"], today.isoformat())

    def test_weekly_cadence_skips_within_7_days_since_last_eval(self):
        players = {
            "p1": SimpleNamespace(
                buckets=("SURPLUS_LOW_FIT",),
                lock=SimpleNamespace(is_locked=False),
                recent_signing_banned_until=None,
                surplus_score=0.90,
                is_expiring=False,
            )
        }
        trade_market = {
            "listings": {},
            "events": [],
            "proactive_listing_meta": {"LAL": {"last_eval_at": "2026-02-01"}},
        }
        listed = apply_ai_proactive_listings(
            team_id="LAL",
            tick_ctx=self._tick_ctx(players, player_ids_by_bucket={"SURPLUS_LOW_FIT": ("p1",)}),
            trade_market=trade_market,
            today=date(2026, 2, 2),  # Monday
            config=self._cfg(ai_proactive_listing_cadence="WEEKLY", ai_proactive_listing_anchor_weekday=0),
        )
        self.assertEqual(listed, [])
        self.assertEqual(trade_market["proactive_listing_meta"]["LAL"]["last_eval_at"], "2026-02-01")

    def test_daily_cadence_ignores_weekly_meta(self):
        players = {
            "p1": SimpleNamespace(
                buckets=("SURPLUS_LOW_FIT",),
                lock=SimpleNamespace(is_locked=False),
                recent_signing_banned_until=None,
                surplus_score=0.90,
                is_expiring=False,
            )
        }
        trade_market = {
            "listings": {},
            "events": [],
            "proactive_listing_meta": {"LAL": {"last_eval_at": "2026-02-01"}},
        }
        listed = apply_ai_proactive_listings(
            team_id="LAL",
            tick_ctx=self._tick_ctx(players, player_ids_by_bucket={"SURPLUS_LOW_FIT": ("p1",)}),
            trade_market=trade_market,
            today=date(2026, 2, 2),
            config=self._cfg(ai_proactive_listing_cadence="DAILY"),
        )
        self.assertEqual(listed, ["p1"])


if __name__ == "__main__":
    unittest.main()
