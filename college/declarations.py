from __future__ import annotations

import math
import random
from typing import Optional

from .types import CollegeSeasonStats, DraftEntryDecisionTrace
from ratings_2k import potential_grade_to_scalar


def _clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def _potential_points_from_grade(grade: str) -> int:
    s = float(potential_grade_to_scalar(grade))
    # Map scalar [0.40, 1.00] -> points [60, 97] (기존 로직의 70 기준 스케일 유지 목적)
    x = 60.0 + (s - 0.40) * (37.0 / 0.60)
    x = float(_clamp(x, 60.0, 97.0))
    return int(round(x))


def _sigmoid(x: float) -> float:
    """Numerically-stable sigmoid.

    NOTE: We clamp x because some declare-model helper computations use rank values that
    can be in the thousands (large eligible pool). Without clamping, math.exp() can
    overflow for very negative x.
    """
    x = float(_clamp(float(x), -20.0, 20.0))
    return 1.0 / (1.0 + math.exp(-x))


def estimate_draft_score(
    *,
    ovr: int,
    age: int,
    class_year: int,
    potential_grade: str,
    season_stats: Optional[CollegeSeasonStats],
    class_strength: float,
) -> float:
    """Internal draft stock score (higher is better).

    IMPORTANT:
    - This score is designed to be *ranked within the eligible pool*.
    - Treat this as a relative ordering signal (higher is better), not as a pick number.

    We keep this intentionally compact and stable so that:
    - OVR / upside (potential) are primary drivers
    - production (season stats) matters but is reliability-scaled in early snapshots
    - younger prospects get a bonus

    `class_strength` is included for backwards compatibility / tuning, but it is a
    constant for a given draft year and does NOT affect ranking.
    """

    prod = 0.0
    prod_rel = 1.0
    if season_stats:
        # A compact "production score" with diminishing returns
        prod = 0.35 * season_stats.pts + 0.18 * season_stats.reb + 0.22 * season_stats.ast
        prod += 6.0 * (season_stats.ts_pct - 0.52)
        prod += 3.0 * (season_stats.usg - 0.18)
        prod = float(_clamp(prod, -8.0, 22.0))

        # Reliability scaling for in-season snapshots:
        # early months (few games) should not swing *ranking* too hard.
        try:
            g = int(season_stats.games)
        except Exception:
            g = 0
        prod_rel = float(_clamp((float(g) / 20.0), 0.25, 1.0))
        prod = float(prod) * float(prod_rel)

    # NBA preference: younger + upside (potential) matters
    potential = _potential_points_from_grade(str(potential_grade))
    youth_bonus = float(_clamp(21 - age, -2.0, 3.0))  # younger => positive
    class_penalty = 0.45 * (class_year - 1)  # mild preference toward younger classes

    score = (
        1.00 * (ovr - 60)
        + 0.55 * (potential - 70)
        + 0.60 * prod
        + 1.10 * youth_bonus
        - 0.40 * class_penalty
        + 2.0 * class_strength
    )

    return float(score)


def declare_probability(
    rng: random.Random,
    *,
    player_id: str,
    draft_year: int,
    ovr: int,
    age: int,
    class_year: int,
    potential_grade: str,
    season_stats: Optional[CollegeSeasonStats],
    class_strength: float,
    projected_pick: int,
    eligible_pool_size: Optional[int] = None,
) -> DraftEntryDecisionTrace:
    """Compute declare probability with a transparent factor breakdown.

    IMPORTANT (P0-2 fix):
    - `projected_pick` should be the player's *rank within the eligible pool*.
      (1 = best prospect, 60 = fringe draft pick, 61+ = increasingly likely undrafted)
    """

    rank = int(projected_pick)

    pot_scalar = float(potential_grade_to_scalar(potential_grade))
    potential = _potential_points_from_grade(str(potential_grade))

    # Production scalar
    prod = 0.0
    prod_rel = 1.0
    if season_stats:
        prod = 0.25 * season_stats.pts + 0.12 * season_stats.reb + 0.18 * season_stats.ast
        prod += 8.0 * (season_stats.ts_pct - 0.52)
        prod = float(_clamp(prod, -6.0, 16.0))

        # Reliability scaling for in-season snapshots:
        # early months (few games) should not swing declare probability too hard.
        try:
            g = int(season_stats.games)
        except Exception:
            g = 0
        prod_rel = float(_clamp((float(g) / 20.0), 0.25, 1.0))
        prod_raw = float(prod)
        prod = float(prod) * float(prod_rel)
    else:
        prod_raw = float(prod)

    # Components (logit space)
    comp_ovr = 0.07 * (ovr - 60)
    comp_pot = 0.04 * (potential - 70)
    comp_prod = 0.10 * prod
    comp_age = 0.18 * (age - 19)
    comp_class = 0.35 * (class_year - 1)
    comp_strength = 0.35 * class_strength

    # ------------------------------------------------------------------
    # Continuous draft outcome expectation (soft bucket membership)
    # ------------------------------------------------------------------
    # We model two "soft" membership probabilities driven by *eligible rank*.
    # These are not meant to be the true probability of being drafted; they are
    # a smooth approximation used for declaration behavior.
    #
    # - p_drafted: membership in "top 60-ish" (draftable)
    # - p_first_round: membership in "top 30" (1st round)
    #
    # NOTE: We intentionally allow a small buffer beyond the hard cut (e.g. 60)
    # so that fringe prospects (61~70) can still rationally "test" the draft.
    DRAFT_SLOTS = 60
    FIRST_ROUND_SLOTS = 30

    # Softness / buffer knobs (tuning targets: stability + NBA-feel)
    # - drafted cutoff: smoother, modest tail past 60
    # - first round cutoff: sharper (top 30 is relatively distinct)
    drafted_buffer = 10.0
    drafted_k = 12.0
    first_round_k = 4.0

    p_drafted = float(_sigmoid(((DRAFT_SLOTS + drafted_buffer + 0.5) - float(rank)) / drafted_k))
    p_first_round = float(_sigmoid(((FIRST_ROUND_SLOTS + 0.5) - float(rank)) / first_round_k))
    # ensure invariant: first round implies drafted
    if p_first_round > p_drafted:
        p_first_round = float(p_drafted)

    # Soft bucket blending using legacy bucket values as anchors:
    # - top 30: +1.10
    # - 31-60: +0.25
    # - >60: -1.35
    # This preserves prior tuning intuition while making the signal continuous.
    risk_top30 = 1.10
    risk_31_60 = 0.25
    risk_undrafted = -1.35

    comp_risk = (
        risk_top30 * float(p_first_round)
        + risk_31_60 * float(p_drafted - p_first_round)
        + risk_undrafted * float(1.0 - p_drafted)
    )

    # Base bias: most players do NOT declare
    bias = -2.10

    logit = bias + comp_ovr + comp_pot + comp_prod + comp_age + comp_class + comp_strength + comp_risk

    # Small randomness in preference (kept tight for stability)
    logit += rng.gauss(0.0, 0.20)

    p = float(_clamp(_sigmoid(logit), 0.01, 0.99))
    declared = rng.random() < p

    return DraftEntryDecisionTrace(
        player_id=player_id,
        draft_year=int(draft_year),
        declared=bool(declared),
        declare_prob=float(p),
        projected_pick=int(rank),
        factors={
            "bias": bias,
            "ovr": comp_ovr,
            "potential": comp_pot,
            "potential_grade": str(potential_grade),
            "potential_scalar": float(pot_scalar),
            "potential_points": int(potential),
            "production": comp_prod,
            "age": comp_age,
            "class_year": comp_class,
            "class_strength": comp_strength,
            # Continuous draft expectation terms
            "rank_eligible": int(rank),
            "eligible_pool_size": int(eligible_pool_size) if eligible_pool_size is not None else None,
            "p_drafted": float(p_drafted),
            "p_first_round": float(p_first_round),
            # kept key name for backward compatibility with existing telemetry tooling
            "risk_bucket": float(comp_risk),
            # in-season reliability factor (games-based)
            "prod_reliability": float(prod_rel),
            "logit": logit,
        },
        notes={
            "draft_slots": int(DRAFT_SLOTS),
            "first_round_slots": int(FIRST_ROUND_SLOTS),
            "drafted_buffer": float(drafted_buffer),
            "drafted_k": float(drafted_k),
            "first_round_k": float(first_round_k),
            "risk_anchors": {
                "top30": float(risk_top30),
                "31_60": float(risk_31_60),
                "undrafted": float(risk_undrafted),
            },
            "prod_score": float(prod),
            "prod_score_raw": float(prod_raw),
            "prod_reliability": float(prod_rel),
        },
    )
