from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Dict, Iterable, Mapping, Tuple

# Validation reason codes (for diagnostics / telemetry)
INVALID_TEAM_COUNT = "INVALID_TEAM_COUNT"
INVALID_LOTTERY_SHAPE = "INVALID_LOTTERY_SHAPE"
INVALID_STANDING_KEYS = "INVALID_STANDING_KEYS"
INVALID_PMF_KEY_RANGE = "INVALID_PMF_KEY_RANGE"
INVALID_PMF_PROBABILITY = "INVALID_PMF_PROBABILITY"
INVALID_PMF_SUM = "INVALID_PMF_SUM"
INVALID_NON_LOTTERY_TOP_SLOT = "INVALID_NON_LOTTERY_TOP_SLOT"

EPS = 1e-9


@dataclass(frozen=True)
class DraftLotteryRules:
    """Season-aware draft lottery rule table.

    `first_round_pmf_by_standing` is a full first-round PMF (pick 1..team_count)
    keyed by standings index (1 = worst, team_count = best).
    """

    season_year: int
    team_count: int
    lottery_team_count: int
    lottery_pick_count: int
    first_round_pmf_by_standing: Mapping[int, Mapping[int, float]]

    def validate(self) -> tuple[bool, tuple[str, ...]]:
        reasons: list[str] = []

        tc = int(self.team_count)
        ltc = int(self.lottery_team_count)
        lpc = int(self.lottery_pick_count)

        if tc < 2:
            reasons.append(INVALID_TEAM_COUNT)

        if not (1 <= lpc <= ltc <= tc):
            reasons.append(INVALID_LOTTERY_SHAPE)

        expected_standings = set(range(1, tc + 1))
        actual_standings = set(int(k) for k in self.first_round_pmf_by_standing.keys())
        if actual_standings != expected_standings:
            reasons.append(INVALID_STANDING_KEYS)

        for standing in range(1, tc + 1):
            pmf = self.first_round_pmf_by_standing.get(standing) or {}
            total = 0.0
            for pick_num, prob in pmf.items():
                try:
                    pnum = int(pick_num)
                    pval = float(prob)
                except Exception:
                    reasons.append(INVALID_PMF_PROBABILITY)
                    continue

                if pnum < 1 or pnum > tc:
                    reasons.append(INVALID_PMF_KEY_RANGE)
                if pval < -EPS or pval > 1.0 + EPS:
                    reasons.append(INVALID_PMF_PROBABILITY)

                total += max(0.0, pval)

                if standing > ltc and pnum <= lpc and pval > EPS:
                    reasons.append(INVALID_NON_LOTTERY_TOP_SLOT)

            if abs(total - 1.0) > EPS:
                reasons.append(INVALID_PMF_SUM)

        uniq = tuple(dict.fromkeys(reasons))
        return (len(uniq) == 0, uniq)


@dataclass(frozen=True)
class DraftLotteryRulesRegistry:
    rules_by_season: Mapping[int, DraftLotteryRules]

    def get(self, season_year: int) -> DraftLotteryRules | None:
        try:
            return self.rules_by_season.get(int(season_year))
        except Exception:
            return None


def _normalize_pmf(pmf: Mapping[int, float], *, team_count: int) -> Dict[int, float]:
    out = {pick: 0.0 for pick in range(1, int(team_count) + 1)}
    total = 0.0
    for pick, prob in pmf.items():
        pnum = int(pick)
        pval = float(prob)
        if 1 <= pnum <= team_count and pval > 0.0:
            out[pnum] += pval
            total += pval

    if total <= 0.0:
        return out

    for pick in list(out.keys()):
        out[pick] = out[pick] / total
    return out


def _enumerate_weighted_winner_sequences(
    weights: Mapping[int, int],
    picks_to_draw: int,
) -> Iterable[tuple[Tuple[int, ...], float]]:
    """Enumerate ordered winner sequences and exact probability.

    This models NBA-style lottery winner draws as weighted draws without replacement
    from lottery teams (weights are lottery combo counts).
    """

    pool = {int(k): int(v) for k, v in weights.items() if int(v) > 0}

    def rec(remaining: Dict[int, int], drawn: Tuple[int, ...], prob: float):
        if len(drawn) >= picks_to_draw:
            yield (drawn, prob)
            return

        total = sum(remaining.values())
        if total <= 0:
            return

        for team in sorted(remaining.keys()):
            w = remaining[team]
            if w <= 0:
                continue
            p = prob * (w / total)
            nxt = dict(remaining)
            nxt.pop(team, None)
            yield from rec(nxt, drawn + (team,), p)

    yield from rec(pool, tuple(), 1.0)


def _build_first_round_pmf_from_combo_weights(
    *,
    team_count: int,
    lottery_team_count: int,
    lottery_pick_count: int,
    combo_weights_by_standing: Mapping[int, int],
) -> Dict[int, Dict[int, float]]:
    """Build full first-round PMF by standings from combo weights.

    - Standings index: 1(worst) .. team_count(best)
    - Lottery winners are drawn among 1..lottery_team_count.
    - Non-winning lottery teams are ordered by standings after top lottery picks.
    - Non-lottery teams (lottery_team_count+1..team_count) keep deterministic order.
    """

    tc = int(team_count)
    ltc = int(lottery_team_count)
    lpc = int(lottery_pick_count)

    pmf_by_standing: Dict[int, Dict[int, float]] = {
        standing: {pick: 0.0 for pick in range(1, tc + 1)} for standing in range(1, tc + 1)
    }

    winner_sequences = _enumerate_weighted_winner_sequences(
        combo_weights_by_standing,
        lpc,
    )

    for winners, seq_prob in winner_sequences:
        if seq_prob <= 0.0:
            continue

        winner_pos = {team: idx + 1 for idx, team in enumerate(winners)}
        non_winners = [s for s in range(1, ltc + 1) if s not in winner_pos]

        rank_map_non_winner: Dict[int, int] = {}
        for idx, standing in enumerate(non_winners):
            rank_map_non_winner[standing] = lpc + idx + 1

        for standing in range(1, tc + 1):
            if standing <= ltc:
                if standing in winner_pos:
                    pick_num = winner_pos[standing]
                else:
                    pick_num = rank_map_non_winner[standing]
            else:
                pick_num = standing

            pmf_by_standing[standing][pick_num] += seq_prob

    # Normalize each standing PMF and clean tiny numeric artifacts.
    for standing in range(1, tc + 1):
        norm = _normalize_pmf(pmf_by_standing[standing], team_count=tc)
        pmf_by_standing[standing] = {pick: (0.0 if abs(prob) < EPS else prob) for pick, prob in norm.items()}

    return pmf_by_standing


def _make_rules(
    *,
    season_year: int,
    team_count: int,
    lottery_team_count: int,
    lottery_pick_count: int,
    combo_weights_by_standing: Mapping[int, int],
) -> DraftLotteryRules:
    return DraftLotteryRules(
        season_year=int(season_year),
        team_count=int(team_count),
        lottery_team_count=int(lottery_team_count),
        lottery_pick_count=int(lottery_pick_count),
        first_round_pmf_by_standing=_build_first_round_pmf_from_combo_weights(
            team_count=int(team_count),
            lottery_team_count=int(lottery_team_count),
            lottery_pick_count=int(lottery_pick_count),
            combo_weights_by_standing=combo_weights_by_standing,
        ),
    )


# Modern flattened-odds NBA era (2019+): 14 lottery teams, top 4 drawn.
# Combo counts total = 1000.
_FLATTENED_14_COMBO_WEIGHTS: Mapping[int, int] = {
    1: 140,
    2: 140,
    3: 140,
    4: 125,
    5: 105,
    6: 90,
    7: 75,
    8: 60,
    9: 45,
    10: 30,
    11: 20,
    12: 15,
    13: 10,
    14: 5,
}

# Pre-flattened era (legacy): 14 lottery teams, top 3 drawn.
# Combo counts total = 1000.
_PRE_FLATTENED_COMBO_WEIGHTS: Mapping[int, int] = {
    1: 250,
    2: 199,
    3: 156,
    4: 119,
    5: 88,
    6: 63,
    7: 43,
    8: 28,
    9: 17,
    10: 11,
    11: 8,
    12: 7,
    13: 6,
    14: 5,
}


DEFAULT_RULES_FLATTENED_14 = _make_rules(
    season_year=2019,
    team_count=30,
    lottery_team_count=14,
    lottery_pick_count=4,
    combo_weights_by_standing=_FLATTENED_14_COMBO_WEIGHTS,
)

DEFAULT_RULES_PRE_FLATTENED = _make_rules(
    season_year=2018,
    team_count=30,
    lottery_team_count=14,
    lottery_pick_count=3,
    combo_weights_by_standing=_PRE_FLATTENED_COMBO_WEIGHTS,
)


def _build_default_rules_by_season() -> Dict[int, DraftLotteryRules]:
    rules: Dict[int, DraftLotteryRules] = {}

    # Pre-2019 era fallback rule family.
    for season_year in range(1985, 2019):
        rules[season_year] = DraftLotteryRules(
            season_year=season_year,
            team_count=DEFAULT_RULES_PRE_FLATTENED.team_count,
            lottery_team_count=DEFAULT_RULES_PRE_FLATTENED.lottery_team_count,
            lottery_pick_count=DEFAULT_RULES_PRE_FLATTENED.lottery_pick_count,
            first_round_pmf_by_standing=DEFAULT_RULES_PRE_FLATTENED.first_round_pmf_by_standing,
        )

    # 2019+ flattened era.
    for season_year in range(2019, 2101):
        rules[season_year] = DraftLotteryRules(
            season_year=season_year,
            team_count=DEFAULT_RULES_FLATTENED_14.team_count,
            lottery_team_count=DEFAULT_RULES_FLATTENED_14.lottery_team_count,
            lottery_pick_count=DEFAULT_RULES_FLATTENED_14.lottery_pick_count,
            first_round_pmf_by_standing=DEFAULT_RULES_FLATTENED_14.first_round_pmf_by_standing,
        )

    return rules


@lru_cache(maxsize=1)
def default_registry() -> DraftLotteryRulesRegistry:
    return DraftLotteryRulesRegistry(rules_by_season=_build_default_rules_by_season())


def get_draft_lottery_rules(season_year: int) -> DraftLotteryRules | None:
    try:
        year = int(season_year)
    except Exception:
        return None

    return default_registry().get(year)
