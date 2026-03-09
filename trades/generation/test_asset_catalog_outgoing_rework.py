import unittest
from types import SimpleNamespace

from trades.generation import asset_catalog as ac


class AssetCatalogOutgoingReworkTests(unittest.TestCase):
    def _candidate(
        self,
        pid: str,
        *,
        age: float,
        remaining_years: float,
        market_now: float,
        surplus_score: float = 0.5,
        is_expiring: bool = False,
    ) -> ac.PlayerTradeCandidate:
        market_now_f = float(market_now)
        return ac.PlayerTradeCandidate(
            player_id=pid,
            team_id="LAL",
            snap=SimpleNamespace(age=float(age)),
            market=ac.MarketValueSummary(now=market_now_f, future=1.0, total=market_now_f + 1.0),
            supply={},
            top_tags=tuple(),
            fit_vs_team=1.0 - float(surplus_score),
            surplus_score=float(surplus_score),
            salary_m=2.0,
            remaining_years=float(remaining_years),
            is_expiring=bool(is_expiring),
            lock=ac.LockInfo(is_locked=False),
            recent_signing_banned_until=None,
            aggregation_banned_until=None,
            aggregation_solo_only=False,
            return_ban_teams=tuple(),
        )

    def _team_state(self, *, posture: str, horizon: str, flexibility: float = 0.5, re_sign_pressure: float = 0.0):
        return SimpleNamespace(
            trade_posture=str(posture),
            time_horizon=str(horizon),
            signals=SimpleNamespace(
                flexibility=float(flexibility),
                re_sign_pressure=float(re_sign_pressure),
            ),
        )

    def test_bad_contract_excludes_low_value_cheap_bench_when_not_overpaid(self):
        c = self._candidate(
            "bench",
            age=25.0,
            remaining_years=1.0,
            market_now=1.0,
            surplus_score=0.3,
            is_expiring=True,
        )
        ts = self._team_state(posture="SELL", horizon="REBUILD", flexibility=0.2)

        ev = ac._eval_bad_contract_candidate(
            c=c,
            ts=ts,
            expected_cap_share_avg=0.020,
            actual_cap_share_avg=0.020,
        )

        self.assertFalse(ev.enter)
        self.assertEqual(ev.negative_money, 0.0)

    def test_bad_contract_prefers_negative_money_plus_long_term(self):
        ts = self._team_state(posture="SELL", horizon="REBUILD", flexibility=0.7)

        high = self._candidate("high", age=29.0, remaining_years=4.0, market_now=8.0, surplus_score=0.6)
        low = self._candidate("low", age=29.0, remaining_years=2.0, market_now=8.0, surplus_score=0.6)

        ev_high = ac._eval_bad_contract_candidate(
            c=high,
            ts=ts,
            expected_cap_share_avg=0.010,
            actual_cap_share_avg=0.035,
        )
        ev_low = ac._eval_bad_contract_candidate(
            c=low,
            ts=ts,
            expected_cap_share_avg=0.010,
            actual_cap_share_avg=0.026,
        )

        self.assertTrue(ev_high.enter)
        self.assertTrue(ev_low.enter)
        self.assertGreater(ev_high.score, ev_low.score)

    def test_veteran_sale_depends_on_timeline_not_age_cut_only(self):
        c = self._candidate(
            "vet",
            age=30.0,
            remaining_years=3.0,
            market_now=7.0,
            surplus_score=0.4,
            is_expiring=False,
        )

        win_now = self._team_state(posture="STAND_PAT", horizon="WIN_NOW", re_sign_pressure=0.1)
        rebuild = self._team_state(posture="STAND_PAT", horizon="REBUILD", re_sign_pressure=0.1)

        ev_win_now = ac._eval_veteran_sale_candidate(c=c, ts=win_now)
        ev_rebuild = ac._eval_veteran_sale_candidate(c=c, ts=rebuild)

        self.assertFalse(ev_win_now.enter)
        self.assertTrue(ev_rebuild.enter)

    def test_veteran_sale_requires_market_now_and_mismatch_gate(self):
        ts = self._team_state(posture="SELL", horizon="REBUILD", re_sign_pressure=0.0)

        low_market = self._candidate(
            "low_market",
            age=34.0,
            remaining_years=4.0,
            market_now=5.9,
            surplus_score=0.5,
            is_expiring=False,
        )
        low_mismatch = self._candidate(
            "low_mismatch",
            age=29.0,
            remaining_years=1.0,
            market_now=7.0,
            surplus_score=0.5,
            is_expiring=False,
        )

        ev_low_market = ac._eval_veteran_sale_candidate(c=low_market, ts=ts)
        ev_low_mismatch = ac._eval_veteran_sale_candidate(c=low_mismatch, ts=ts)

        self.assertFalse(ev_low_market.enter)
        self.assertFalse(ev_low_mismatch.enter)


if __name__ == "__main__":
    unittest.main()
