import statistics
import unittest
from types import SimpleNamespace

from trades.generation import asset_catalog as ac


class _FitEngineStub:
    def score_fit(self, need_map, supply):
        num = 0.0
        den = 0.0
        for tag, w in (need_map or {}).items():
            ww = float(w or 0.0)
            if ww <= 0:
                continue
            den += ww
            num += ww * float((supply or {}).get(tag, 0.0) or 0.0)
        if den <= 0.0:
            return 0.0, {}, {}
        return max(0.0, min(1.0, num / den)), {}, {}


class AssetCatalogExpendableTests(unittest.TestCase):
    def _candidate(self, pid: str, *, fit_vs_team: float, supply=None, top_tags=(), is_expiring=False, market_now=10.0):
        return ac.PlayerTradeCandidate(
            player_id=pid,
            team_id="LAL",
            snap=SimpleNamespace(age=26.0),
            market=ac.MarketValueSummary(now=float(market_now), future=3.0, total=float(market_now) + 3.0),
            supply=dict(supply or {}),
            top_tags=tuple(top_tags),
            fit_vs_team=float(fit_vs_team),
            surplus_score=1.0 - float(fit_vs_team),
            salary_m=10.0,
            remaining_years=2.0,
            is_expiring=bool(is_expiring),
            recent_signing_banned_until=None,
            aggregation_banned_until=None,
            aggregation_solo_only=False,
            return_ban_teams=tuple(),
        )

    def _enter_expendable(self, *, posture: str, peer, protection) -> bool:
        raw = (
            0.40 * float(peer["redundancy_peer_norm"])
            + 0.20 * float(peer["misfit_peer"])
            + 0.20 * float(peer["peer_cover"])
            + 0.10 * float(protection["contract_pressure"])
            + 0.10 * float(protection["minutes_squeeze_proxy"])
            - ac._PROTECTION_WEIGHT_BY_POSTURE[posture] * (
                0.40 * float(protection["core_proxy"])
                + 0.35 * float(peer["dependence_risk"])
                + 0.25 * float(protection["identity_risk_proxy"])
            )
            + 0.05
        )
        score = ac._clamp01(raw)
        hard = (
            float(protection["core_proxy"]) >= 0.82
            or float(protection["identity_risk_proxy"]) >= 0.78
            or float(peer["dependence_risk"]) >= 0.68
        )
        gate = (
            float(peer["redundancy_peer_norm"]) >= ac._REDUNDANCY_GATE
            or float(peer["peer_cover"]) >= ac._REPLACEABLE_GATE
            or float(protection["minutes_squeeze_proxy"]) >= ac._SQUEEZE_GATE
            or float(protection["contract_pressure"]) >= ac._CONTRACT_GATE
            or (float(protection["timing_liquidity"]) >= 1.0 and posture in {"SELL", "SOFT_SELL"})
        )
        return (not hard) and score >= ac._TRADE_BLOCK_SCORE_GATE_BY_POSTURE[posture] and gate

    def test_low_fit_alone_does_not_enter_expendable(self):
        fit = _FitEngineStub()
        c = self._candidate("p1", fit_vs_team=0.20, supply={"TAG1": 0.2}, top_tags=("TAG1",))
        peer = ac._compute_peer_signals(
            candidate=c,
            need_map={"TAG1": 1.0},
            team_supply_total={"TAG1": 0.2},
            fit_engine=fit,
        )
        protection = ac._compute_protection_signals(
            candidate=c,
            team_candidates=[c],
            team_supply_total={"TAG1": 0.2},
            value_breakdown={"contract_gap_cap_share": 0.02},
        )
        self.assertGreaterEqual(peer["misfit_peer"], 0.55)
        self.assertFalse(self._enter_expendable(posture="SELL", peer=peer, protection=protection))

    def test_core_identity_player_is_protected(self):
        fit = _FitEngineStub()
        c1 = self._candidate("core", fit_vs_team=0.70, supply={"A": 1.0}, top_tags=("A",), market_now=50.0)
        c2 = self._candidate("role", fit_vs_team=0.50, supply={"A": 0.2}, top_tags=("A",), market_now=10.0)

        peer = ac._compute_peer_signals(
            candidate=c1,
            need_map={"A": 0.7},
            team_supply_total={"A": 1.2},
            fit_engine=fit,
        )
        protection = ac._compute_protection_signals(
            candidate=c1,
            team_candidates=[c1, c2],
            team_supply_total={"A": 1.2},
            value_breakdown={"basketball_total": 25.0, "contract_gap_cap_share": 0.01},
        )
        self.assertTrue(
            protection["core_proxy"] >= 0.82
            or protection["identity_risk_proxy"] >= 0.78
            or peer["dependence_risk"] >= 0.68
        )
        self.assertFalse(self._enter_expendable(posture="SELL", peer=peer, protection=protection))

    def test_redundant_replaceable_enters_expendable(self):
        fit = _FitEngineStub()
        c = self._candidate("depth", fit_vs_team=0.95, supply={"A": 0.1}, top_tags=("A",), is_expiring=True, market_now=2.0)
        peer = ac._compute_peer_signals(
            candidate=c,
            need_map={"A": 0.1},
            team_supply_total={"A": 1.0},
            fit_engine=fit,
        )
        protection = ac._compute_protection_signals(
            candidate=c,
            team_candidates=[c, self._candidate("other", fit_vs_team=0.5, supply={"A": 0.9}, top_tags=("A",), market_now=20.0)],
            team_supply_total={"A": 1.0},
            value_breakdown={"contract_gap_cap_share": -0.06},
        )
        self.assertGreaterEqual(peer["redundancy_peer_norm"], ac._REDUNDANCY_GATE)
        self.assertTrue(self._enter_expendable(posture="SELL", peer=peer, protection=protection))

    def test_kpi_distribution_fields_are_collectable(self):
        raw_scores = [-0.2, 0.1, 0.4, 0.8, 1.1]
        mean_v = statistics.fmean(raw_scores)
        p10, p50, p90 = statistics.quantiles(raw_scores, n=10)[0], statistics.median(raw_scores), statistics.quantiles(raw_scores, n=10)[8]
        self.assertIsInstance(mean_v, float)
        self.assertLessEqual(p10, p50)
        self.assertLessEqual(p50, p90)


if __name__ == "__main__":
    unittest.main()
