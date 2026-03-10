from __future__ import annotations

import unittest
from dataclasses import dataclass
from types import SimpleNamespace

from trades.valuation.lineup_ecosystem import compute_ecosystem_fit_score


@dataclass(frozen=True)
class _C:
    player_id: str
    supply: dict
    top_tags: tuple[str, ...]
    fit_vs_team: float
    salary_m: float
    remaining_years: float
    market: SimpleNamespace
    snap: SimpleNamespace


def _cand(pid: str, market: float, fit: float, supply: dict) -> _C:
    return _C(
        player_id=pid,
        supply=dict(supply),
        top_tags=tuple(supply.keys()),
        fit_vs_team=fit,
        salary_m=10.0,
        remaining_years=2.0,
        market=SimpleNamespace(total=market),
        snap=SimpleNamespace(meta={"role_fit": {}}, attrs={}),
    )


class _Tick:
    def __init__(self, roster: list[_C], need_map: dict, ts: SimpleNamespace):
        self.asset_catalog = SimpleNamespace(
            outgoing_by_team={
                "LAL": SimpleNamespace(players={c.player_id: c for c in roster})
            }
        )
        self._need_map = need_map
        self._ts = ts

    def get_decision_context(self, team_id: str):
        return SimpleNamespace(need_map=dict(self._need_map))

    def get_team_situation(self, team_id: str):
        return self._ts


class LineupEcosystemTests(unittest.TestCase):
    def test_score_is_normalized_and_reasons_exist(self):
        roster = [
            _cand("1", 20, 0.7, {"PRIMARY_INITIATOR": 0.8, "SHOT_CREATION": 0.7, "SPACING": 0.2}),
            _cand("2", 18, 0.6, {"SPACING": 0.75, "DEFENSE": 0.5}),
            _cand("3", 14, 0.5, {"RIM_PRESSURE": 0.8, "DEFENSE": 0.45}),
            _cand("4", 12, 0.4, {"SPACING": 0.5}),
            _cand("5", 11, 0.4, {"DEFENSE": 0.6}),
        ]
        inc = [_cand("X", 13, 0.7, {"SPACING": 0.9, "DEFENSE": 0.6})]
        tick = _Tick(
            roster=roster,
            need_map={"SPACING": 1.0, "DEFENSE": 0.8, "RIM_PRESSURE": 0.4},
            ts=SimpleNamespace(time_horizon="CONTEND", trade_posture="SOFT_BUY", competitive_tier="CONTENDER"),
        )

        res = compute_ecosystem_fit_score(
            receiver_team_id="LAL",
            incoming_candidates=inc,
            outgoing_player_ids=tuple(),
            tick_ctx=tick,
            cfg=SimpleNamespace(),
        )

        self.assertGreaterEqual(res.total_score, 0.0)
        self.assertLessEqual(res.total_score, 1.0)
        self.assertEqual(len(res.components), 5)
        self.assertGreaterEqual(len(res.lineup_samples), 1)

    def test_better_spacing_candidate_scores_higher(self):
        roster = [
            _cand("1", 20, 0.7, {"PRIMARY_INITIATOR": 0.8, "SHOT_CREATION": 0.7, "SPACING": 0.2}),
            _cand("2", 18, 0.6, {"SPACING": 0.3, "DEFENSE": 0.4}),
            _cand("3", 14, 0.5, {"RIM_PRESSURE": 0.6, "DEFENSE": 0.4}),
            _cand("4", 12, 0.4, {"SPACING": 0.25}),
            _cand("5", 11, 0.4, {"DEFENSE": 0.5}),
        ]
        tick = _Tick(
            roster=roster,
            need_map={"SPACING": 1.0, "DEFENSE": 0.4},
            ts=SimpleNamespace(time_horizon="COMPETE", trade_posture="SOFT_BUY", competitive_tier="PLAYOFF_BUYER"),
        )

        strong = compute_ecosystem_fit_score(
            receiver_team_id="LAL",
            incoming_candidates=[_cand("S", 10, 0.6, {"SPACING": 0.9, "DEFENSE": 0.4})],
            outgoing_player_ids=tuple(),
            tick_ctx=tick,
            cfg=SimpleNamespace(),
        )
        weak = compute_ecosystem_fit_score(
            receiver_team_id="LAL",
            incoming_candidates=[_cand("W", 10, 0.6, {"SPACING": 0.1, "DEFENSE": 0.2})],
            outgoing_player_ids=tuple(),
            tick_ctx=tick,
            cfg=SimpleNamespace(),
        )

        self.assertGreater(strong.total_score, weak.total_score)


if __name__ == "__main__":
    unittest.main()
