from __future__ import annotations

"""
draft.expert_bigboard
---------------------

Generate "draft expert" big boards (Sam Vecenie-style) using incomplete information.

Design goals:
- Experts are *more accurate than the user* (they can indirectly leverage hidden ratings),
  but *less accurate than AI/true values* (they observe with noise + biases).
- Experts differ by emphasis (weights), what they're good at (axis-level accuracy multipliers),
  and systematic blind spots (bias rules).
- Big boards should not diverge wildly at the top (anchor-mixing with a public-ish base score).

This module intentionally:
- Uses only values that exist in this project (Prospect.bio/meta/stats/combine + attrs_json).
- Avoids leaking hidden "ovr/attrs/potential_*" values directly in the output.

Primary entrypoints:
- list_experts() -> list of expert descriptors for UI.
- generate_expert_bigboard(db_path, draft_year, expert_id, phase="auto") -> dict for API/UI.
"""

from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import math
import random
import zlib

from derived_formulas import compute_derived
from draft.pool import Prospect, load_pool_from_db, load_watch_pool_from_db


# -----------------------------------------------------------------------------
# Public constants
# -----------------------------------------------------------------------------

PHASE_PRE_COMBINE = "pre_combine"
PHASE_POST_COMBINE = "post_combine"
PHASE_AUTO = "auto"

_ALLOWED_PHASES = {PHASE_PRE_COMBINE, PHASE_POST_COMBINE, PHASE_AUTO}

# Default set used for "public consensus" (median of 6 expert boards)
DEFAULT_EXPERT_IDS: Tuple[str, ...] = (
    "balanced",
    "analytics",
    "defense_film",
    "tools_upside",
    "skill_dev",
    "risk_averse",
)


# -----------------------------------------------------------------------------
# Small utilities (stdlib-only)
# -----------------------------------------------------------------------------

def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x


def _f(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return float(default)
        return float(x)
    except Exception:
        return float(default)


def _i(x: Any, default: int = 0) -> int:
    try:
        if x is None:
            return int(default)
        return int(x)
    except Exception:
        return int(default)


def _stable_u32(*parts: Any) -> int:
    """Deterministic 32-bit hash (stable across Python runs)."""
    s = "|".join(str(p) for p in parts)
    return int(zlib.crc32(s.encode("utf-8")) & 0xFFFFFFFF)


def _pos_bucket(pos: Any) -> str:
    """G/W/B bucket for coarse positional heuristics (avoid importing heavy deps)."""
    p = str(pos or "").upper()
    if p in ("PG", "SG", "G"):
        return "G"
    if p in ("SF", "PF", "F", "W"):
        return "W"
    return "B"


def _get_nested(d: Any, *keys: str) -> Any:
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


# -----------------------------------------------------------------------------
# Axis construction: "true" skill axes (hidden) derived from attrs + bio
# -----------------------------------------------------------------------------

# Base SD per axis for expert observation noise.
# (Higher = harder to evaluate / higher translation uncertainty)
_BASE_SIGMA: Dict[str, float] = {
    "TOOLS": 2.0,       # mostly measured (height/weight) + broad athletic hints
    "DUR": 9.0,         # medical/durability uncertainty
    "S_SPOT": 6.0,
    "S_TOUCH": 5.0,
    "S_OD": 10.0,
    "FIN_RIM": 7.0,
    "FIN_BURST": 6.0,
    "FIN_PHYS": 10.0,
    "CREATION": 8.0,
    "BALL_SAFE": 7.0,
    "PLAY": 7.0,
    "DEF_POA": 10.0,
    "DEF_HELP": 12.0,   # hardest to evaluate reliably
    "DEF_RIM": 10.0,
    "REB": 6.0,
    "MOTOR": 8.0,
    "UPSIDE": 11.0,     # upside projection is noisy
    "AGE": 3.0,
}

_AXIS_ORDER: Tuple[str, ...] = (
    "S_SPOT", "S_OD", "S_TOUCH",
    "FIN_RIM", "FIN_BURST", "FIN_PHYS",
    "CREATION", "BALL_SAFE", "PLAY",
    "DEF_POA", "DEF_HELP", "DEF_RIM",
    "REB", "MOTOR",
    "TOOLS", "DUR", "UPSIDE", "AGE",
)


def build_true_axes_from_prospect(p: Prospect) -> Dict[str, float]:
    """Build hidden/true axes from Prospect.attrs/meta/bio.

    Uses:
      - derived_formulas.compute_derived(attrs) for skill composites
      - bio (height/weight/age) for tools/age axis
      - "Overall Durability" from attrs for durability risk axis
      - potential_points from meta (already computed in draft.pool) for upside axis
    """
    attrs = p.attrs if isinstance(p.attrs, dict) else {}
    D = compute_derived(attrs)

    H = float(getattr(p, "height_in", 78))
    W = float(getattr(p, "weight_lb", 215))
    age = float(getattr(p, "age", 20))

    # Bio-only frame proxy (public-ish / stable)
    frame = _clamp(50.0 + 2.2 * (H - 78.0) + 0.15 * (W - 215.0), 0.0, 100.0)

    # Tools blends frame with hidden athletic-ish derived components (still "true")
    tools = _clamp(
        0.55 * frame
        + 0.45 * (0.5 * _f(D.get("FIRST_STEP"), 50.0) + 0.5 * _f(D.get("PHYSICAL"), 50.0)),
        0.0,
        100.0,
    )

    durability = _clamp(_f(attrs.get("Overall Durability"), 70.0), 0.0, 100.0)

    pot_points = _f((p.meta or {}).get("potential_points"), 75.0)
    upside = _clamp(pot_points, 0.0, 100.0)

    # Age axis: younger == higher (crude readiness/upside proxy)
    # 19 -> 100, 23 -> ~68, 26 -> ~44 (clamped)
    age_axis = _clamp(100.0 - (age - 19.0) * 8.0, 0.0, 100.0)

    axes: Dict[str, float] = {
        # Shooting
        "S_SPOT": _clamp(0.70 * _f(D.get("SHOT_3_CS"), 50.0) + 0.30 * _f(D.get("SHOT_MID_CS"), 50.0)),
        "S_OD": _clamp(0.65 * _f(D.get("SHOT_3_OD"), 50.0) + 0.35 * _f(D.get("SHOT_MID_PU"), 50.0)),
        "S_TOUCH": _clamp(0.55 * _f(D.get("SHOT_FT"), 50.0) + 0.45 * _f(D.get("SHOT_TOUCH"), 50.0)),

        # Finishing
        "FIN_RIM": _clamp(_f(D.get("FIN_RIM"), 50.0)),
        "FIN_BURST": _clamp(0.60 * _f(D.get("FIN_DUNK"), 50.0) + 0.40 * _f(D.get("FIRST_STEP"), 50.0)),
        "FIN_PHYS": _clamp(0.60 * _f(D.get("FIN_CONTACT"), 50.0) + 0.40 * _f(D.get("PHYSICAL"), 50.0)),

        # Creation / handling / passing
        "CREATION": _clamp(0.60 * _f(D.get("DRIVE_CREATE"), 50.0) + 0.40 * _f(D.get("FIRST_STEP"), 50.0)),
        "BALL_SAFE": _clamp(_f(D.get("HANDLE_SAFE"), 50.0)),
        "PLAY": _clamp(
            0.40 * _f(D.get("PASS_CREATE"), 50.0)
            + 0.25 * _f(D.get("PASS_SAFE"), 50.0)
            + 0.25 * _f(D.get("PNR_READ"), 50.0)
            + 0.10 * _f(D.get("SHORTROLL_PLAY"), 50.0)
        ),

        # Defense / rebounding / motor
        "DEF_POA": _clamp(_f(D.get("DEF_POA"), 50.0)),
        "DEF_HELP": _clamp(_f(D.get("DEF_HELP"), 50.0)),
        "DEF_RIM": _clamp(0.75 * _f(D.get("DEF_RIM"), 50.0) + 0.25 * _f(D.get("DEF_POST"), 50.0)),
        "REB": _clamp(0.60 * _f(D.get("REB_DR"), 50.0) + 0.40 * _f(D.get("REB_OR"), 50.0)),
        "MOTOR": _clamp(0.60 * _f(D.get("ENDURANCE"), 50.0) + 0.40 * _f(D.get("PHYSICAL"), 50.0)),

        # Tools / risk / upside
        "TOOLS": tools,
        "DUR": durability,
        "UPSIDE": upside,
        "AGE": age_axis,
    }

    # Ensure fixed keys exist
    for k in _AXIS_ORDER:
        if k not in axes:
            axes[k] = 50.0

    return axes


# -----------------------------------------------------------------------------
# Expert profile definition
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class BiasRule:
    """Conditional axis/score adjustments (systematic biases)."""
    name: str
    predicate: Callable[[Dict[str, Any]], bool]
    axis_delta: Dict[str, float]
    score_delta: float = 0.0


@dataclass(frozen=True)
class ExpertProfile:
    """Expert configuration.

    weights:
      Axis importance. We compute a weighted average -> 0..100 score.
    acc_mult:
      Axis observation noise multiplier (0.7 = more accurate, 1.2 = less accurate).
    bias_rules:
      Conditional adjustments, based on ctx (bio/stats/combine/axes_obs/axes_true).
    anchor_alphas:
      Final mixing coefficient between base_public and expert_score, by base_rank bucket.
      Lower alpha -> stronger convergence to base_public at the top.
    """
    expert_id: str
    display_name: str
    short_tag: str
    weights: Dict[str, float]
    acc_mult: Dict[str, float]
    bias_rules: Sequence[BiasRule]
    anchor_alpha_top: float = 0.45   # base_rank 1..8
    anchor_alpha_mid: float = 0.65   # 9..25
    anchor_alpha_late: float = 0.80  # 26+
    global_noise_sd: float = 1.5     # tiny "opinion wiggle" on final expert score
    base_influence: float = 0.15     # experts still respect public-ish signals a bit


def _axis_weighted_avg(weights: Mapping[str, float], axes: Mapping[str, float]) -> float:
    num = 0.0
    den = 0.0
    for k, w in weights.items():
        if w <= 0:
            continue
        den += float(w)
        num += float(w) * _f(axes.get(k), 50.0)
    if den <= 1e-9:
        return 50.0
    return _clamp(num / den, 0.0, 100.0)


def _sigma_multiplier_from_phase(ctx: Dict[str, Any], axis: str, phase: str) -> float:
    """Dynamic SD multiplier based on public info availability.

    - After combine: TOOLS/FIN_BURST/MOTOR are more observable (tests + meas).
    - With larger college sample: shooting/handling/playmaking slightly more stable.
    """
    mult = 1.0

    combine = ctx.get("combine") if phase == PHASE_POST_COMBINE else None
    stats = ctx.get("stats") or {}

    has_combine = isinstance(combine, dict) and bool(combine)

    # Combine reduces uncertainty on select axes
    if has_combine and axis in ("TOOLS", "FIN_BURST", "MOTOR"):
        mult *= 0.65

    # College sample size confidence (0.75..1.0)
    games = _f(stats.get("games"), 0.0)
    mpg = _f(stats.get("mpg"), 0.0)
    sample_mult = 1.0
    if games > 0.0 or mpg > 0.0:
        sample_mult = max(0.75, 1.0 - 0.15 * min(games / 35.0, 1.0) - 0.10 * min(mpg / 30.0, 1.0))

    if axis in ("S_SPOT", "S_TOUCH", "S_OD", "PLAY", "BALL_SAFE"):
        mult *= sample_mult

    return mult


# -----------------------------------------------------------------------------
# Public-ish base score (to prevent top-end divergence)
# -----------------------------------------------------------------------------

def _public_prod_score(stats: Any) -> float:
    """Coarse productivity proxy using ONLY college season stats."""
    if not isinstance(stats, dict):
        return 0.0
    pts = _f(stats.get("pts"), 0.0)
    ast = _f(stats.get("ast"), 0.0)
    reb = _f(stats.get("reb"), 0.0)
    stl = _f(stats.get("stl"), 0.0)
    blk = _f(stats.get("blk"), 0.0)
    ts = _f(stats.get("ts_pct"), 0.0)
    tov = _f(stats.get("tov"), 0.0)

    base = pts * 1.0 + ast * 0.7 + reb * 0.5 + stl * 0.6 + blk * 0.6 - tov * 0.8
    if ts > 0.0:
        base *= (0.75 + ts)  # gentle efficiency boost
    return float(base)


def _make_market_profile() -> ExpertProfile:
    """Market consensus observation profile.

    This is NOT one of the 6 experts. It represents a noisy, biased "industry buzz" signal
    used only as a top-end anchor inside base_public.

    Key intent:
      - more stable than any single expert (slightly lower axis noise)
      - still imperfect (keeps meaningful uncertainty on hard-to-scout axes)
    """

    # Default (1.0) means "as hard as the base sigma says".
    # <1.0 means more stable; >1.0 means more uncertain.
    acc = {
        # Observable / measurable-ish
        "TOOLS": 0.80,
        "FIN_BURST": 0.80,
        "MOTOR": 0.90,
        "S_SPOT": 0.90,
        "S_TOUCH": 0.95,
        "BALL_SAFE": 0.95,
        "PLAY": 0.95,
        "REB": 0.95,

        # Still noisy (translation / role / scheme)
        "S_OD": 1.00,
        "CREATION": 1.00,
        "FIN_RIM": 1.00,
        "FIN_PHYS": 1.05,
        "DEF_POA": 1.05,
        "DEF_RIM": 1.05,
        "DEF_HELP": 1.10,

        # Hardest / most rumor-driven
        "DUR": 1.10,
        "UPSIDE": 1.10,
        "AGE": 1.00,
    }

    return ExpertProfile(
        expert_id="market_buzz",
        display_name="Market Buzz",
        short_tag="Market",
        weights={},               # unused
        acc_mult=acc,
        bias_rules=(),            # narrative bias handled at score level
        anchor_alpha_top=1.0,
        anchor_alpha_mid=1.0,
        anchor_alpha_late=1.0,
        global_noise_sd=0.0,      # unused
        base_influence=0.0,       # unused
    )


_MARKET_PROFILE: ExpertProfile = _make_market_profile()


# Market STAR/FLOOR weights (axis names are the same internal axes used by experts).
_MARKET_STAR_WEIGHTS_G: Dict[str, float] = {
    "UPSIDE": 1.25,
    "CREATION": 1.25,
    "S_OD": 0.95,
    "PLAY": 0.80,
    "FIN_BURST": 0.65,
    "TOOLS": 0.85,
    "S_SPOT": 0.70,
    "DEF_POA": 0.55,
    "MOTOR": 0.45,
}

_MARKET_STAR_WEIGHTS_W: Dict[str, float] = {
    "TOOLS": 1.25,
    "UPSIDE": 1.20,
    "CREATION": 0.95,
    "S_OD": 0.80,
    "S_SPOT": 0.75,
    "DEF_POA": 0.60,
    "DEF_HELP": 0.55,
    "FIN_BURST": 0.55,
    "MOTOR": 0.50,
}

_MARKET_STAR_WEIGHTS_B: Dict[str, float] = {
    "TOOLS": 1.30,
    "UPSIDE": 1.15,
    "DEF_RIM": 0.90,
    "FIN_PHYS": 0.75,
    "FIN_RIM": 0.65,
    "MOTOR": 0.55,
    "S_SPOT": 0.55,
    "FIN_BURST": 0.35,
    "PLAY": 0.35,
    "CREATION": 0.25,
}


_MARKET_FLOOR_WEIGHTS_G: Dict[str, float] = {
    "S_SPOT": 1.10,
    "S_TOUCH": 0.95,
    "BALL_SAFE": 0.90,
    "PLAY": 0.85,
    "DEF_POA": 0.65,
    "MOTOR": 0.55,
    "DUR": 0.75,
}

_MARKET_FLOOR_WEIGHTS_W: Dict[str, float] = {
    "S_SPOT": 1.05,
    "DEF_HELP": 0.75,
    "DEF_POA": 0.65,
    "MOTOR": 0.60,
    "DUR": 0.75,
    "BALL_SAFE": 0.55,
    "PLAY": 0.55,
    "FIN_RIM": 0.50,
}

_MARKET_FLOOR_WEIGHTS_B: Dict[str, float] = {
    "DEF_RIM": 1.00,
    "REB": 0.90,
    "DUR": 0.85,
    "MOTOR": 0.70,
    "FIN_PHYS": 0.60,
    "FIN_RIM": 0.55,
    "S_TOUCH": 0.45,
    "S_SPOT": 0.35,
}


def _market_star_floor_weights(pos_bucket: str) -> Tuple[Dict[str, float], Dict[str, float]]:
    b = str(pos_bucket or "B").upper()
    if b == "G":
        return _MARKET_STAR_WEIGHTS_G, _MARKET_FLOOR_WEIGHTS_G
    if b == "W":
        return _MARKET_STAR_WEIGHTS_W, _MARKET_FLOOR_WEIGHTS_W
    return _MARKET_STAR_WEIGHTS_B, _MARKET_FLOOR_WEIGHTS_B


def _market_buzz_component(*, p: Prospect, draft_year: int, phase: str) -> float:
    """A noisy, biased "industry/market buzz" anchor score in 0..100.

    Goal:
      - keep generational prospects from drifting out of the top 10
      - remain imperfect (not a hidden true-rank oracle)

    Implementation summary:
      1) build true axes (hidden)
      2) observe them with a special market profile (reduced axis noise)
      3) compute STAR and FLOOR composites by position bucket
      4) apply small narrative biases + durability red flags
      5) add controlled noise that scales with uncertainty + polarizing profiles
    """

    tid = str(getattr(p, "temp_id", "") or "")
    dy = int(draft_year)
    ph = str(phase or PHASE_PRE_COMBINE)
    seed = _stable_u32("market_buzz_v1", dy, ph, tid)
    rng = random.Random(seed)

    true_axes = build_true_axes_from_prospect(p)

    ctx = {
        "temp_id": tid,
        "name": p.name,
        "pos": p.pos,
        "bio": {
            "pos": p.pos,
            "age": int(getattr(p, "age", 20)),
            "height_in": int(getattr(p, "height_in", 78)),
            "weight_lb": int(getattr(p, "weight_lb", 215)),
        },
        "stats": (p.meta or {}).get("season_stats") if isinstance((p.meta or {}).get("season_stats"), dict) else {},
        "combine": (p.meta or {}).get("combine") if (ph == PHASE_POST_COMBINE and isinstance((p.meta or {}).get("combine"), dict)) else None,
    }

    # 1) market-observed axes (independent RNG from experts)
    obs_axes, _rule_delta = observe_axes(expert=_MARKET_PROFILE, true_axes=true_axes, ctx=ctx, phase=ph, rng=rng)

    # 2) STAR / FLOOR composites
    pb = _pos_bucket(p.pos)
    w_star, w_floor = _market_star_floor_weights(pb)
    star = _axis_weighted_avg(w_star, obs_axes)
    floor = _axis_weighted_avg(w_floor, obs_axes)

    if pb == "B":
        core = 0.60 * star + 0.40 * floor
    else:
        core = 0.66 * star + 0.34 * floor

    # 3) Narrative bias (small but meaningful)
    narrative = 0.0

    h_in = float(getattr(p, "height_in", 78))
    tools = _f(obs_axes.get("TOOLS"), 50.0)
    s_spot = _f(obs_axes.get("S_SPOT"), 50.0)
    s_od = _f(obs_axes.get("S_OD"), 50.0)
    creation = _f(obs_axes.get("CREATION"), 50.0)
    def_poa = _f(obs_axes.get("DEF_POA"), 50.0)
    def_rim = _f(obs_axes.get("DEF_RIM"), 50.0)

    # Small guard skepticism
    if pb == "G" and h_in < 74.0:
        narrative -= 2.0
        if tools < 60.0:
            narrative -= 1.0

    # Bigs who can't shoot get pushed down; stretch bigs get a bump
    if pb == "B":
        if s_spot < 45.0:
            narrative -= 4.0
        elif s_spot < 55.0:
            narrative -= 2.5
        elif s_spot >= 75.0:
            narrative += 2.0

    # Plus wingspan (only if combine is available in this phase)
    combine = ctx.get("combine")
    if isinstance(combine, dict):
        ws = _f(_get_nested(combine, "measurements", "wingspan_in"), 0.0)
        hn = _f(_get_nested(combine, "measurements", "height_noshoes_in"), 0.0)
        if ws > 0.0 and hn > 0.0:
            diff = ws - hn
            if diff >= 8.0:
                narrative += 1.6
            elif diff >= 6.0:
                narrative += 1.0

    # Elite shot creator signal
    if creation >= 88.0 and s_od >= 84.0:
        narrative += 1.5

    # Two-way wing premium
    if pb == "W" and def_poa >= 82.0 and s_spot >= 72.0:
        narrative += 1.0

    # Rim protector premium
    if pb == "B" and def_rim >= 85.0:
        narrative += 1.2
        if tools >= 80.0:
            narrative += 0.5

    # 4) Medical / durability red flags (rumor-driven)
    dur = _f(obs_axes.get("DUR"), 70.0)
    medical = 0.0
    if dur <= 35.0:
        medical -= 9.0
    elif dur <= 45.0:
        medical -= 6.0
    elif dur <= 55.0:
        medical -= 3.0

    # 5) Rare-trait separation (cap to avoid over-shooting)
    rare = 0.35 * max(0.0, star - 85.0)
    if rare > 4.5:
        rare = 4.5

    # 6) Controlled noise: more uncertainty on lower cores and polarizing profiles
    base_sd = 1.6 + 3.0 * (1.0 - core / 100.0)
    polar = max(0.0, star - floor)
    sd = base_sd + 0.05 * polar

    stats = ctx.get("stats") or {}
    games = _f(stats.get("games"), 0.0)
    mpg = _f(stats.get("mpg"), 0.0)
    coverage = 0.0
    if games > 0.0 or mpg > 0.0:
        coverage = 0.5 * min(games / 35.0, 1.0) + 0.5 * min(mpg / 30.0, 1.0)

    # Up to 25% less variance for well-scouted college samples
    sd *= (1.0 - 0.25 * coverage)

    # Combine reduces overall uncertainty only if we actually have combine payload
    has_combine = isinstance(combine, dict) and bool(combine)
    if ph == PHASE_POST_COMBINE and has_combine:
        sd *= 0.85

    # Final market buzz score
    score = float(core + rare + narrative + medical + rng.gauss(0.0, float(sd)))
    return _clamp(score, 0.0, 100.0)


def _public_frame_component(p: Prospect) -> float:
    H = float(getattr(p, "height_in", 78))
    W = float(getattr(p, "weight_lb", 215))
    return _clamp(50.0 + 2.2 * (H - 78.0) + 0.15 * (W - 215.0), 0.0, 100.0)


def _public_combine_component(p: Prospect, phase: str) -> float:
    """Use combine athletic_score if phase permits and combine exists."""
    if phase != PHASE_POST_COMBINE:
        return 0.0
    combine = (p.meta or {}).get("combine")
    if not isinstance(combine, dict):
        return 0.0
    a = _f(_get_nested(combine, "drills", "athletic_score"), 0.0)
    # athletic_score already ~0..100 in this project
    return _clamp(a, 0.0, 100.0)


def compute_base_public_score(p: Prospect, draft_year: int, phase: str, *, prod_norm: Optional[Tuple[float, float]] = None) -> float:
    """Base score from public-ish signals.

    Components:
      - market/industry buzz (public-ish; noisy, biased; does NOT require projected_pick)
      - productivity proxy (public)
      - frame (height/weight, public)
      - combine athletic_score (public, post-combine only)
      - age mild adjustment (public)
    """
    buzz = _market_buzz_component(p=p, draft_year=int(draft_year), phase=str(phase))
    prod = _public_prod_score((p.meta or {}).get("season_stats"))
    if prod_norm:
        mean, sd = prod_norm
        if sd > 1e-6:
            prod_z = (prod - mean) / sd
        else:
            prod_z = 0.0
    else:
        prod_z = 0.0

    frame = _public_frame_component(p)
    comb = _public_combine_component(p, phase)
    age = float(getattr(p, "age", 20))

    # weights tuned for stability: market buzz anchors the top
    base = 0.62 * buzz + 0.18 * _clamp(50.0 + 10.0 * prod_z, 0.0, 100.0) + 0.14 * frame + 0.06 * comb
    # older players: tiny penalty (not huge; expert #6 handles this more)
    base += _clamp((20.0 - age) * 0.6, -4.0, 4.0)
    return _clamp(base, 0.0, 100.0)


# -----------------------------------------------------------------------------
# Observation: turn true axes into expert-observed axes (noise + biases)
# -----------------------------------------------------------------------------

def observe_axes(
    *,
    expert: ExpertProfile,
    true_axes: Mapping[str, float],
    ctx: Dict[str, Any],
    phase: str,
    rng: random.Random,
) -> Tuple[Dict[str, float], float]:
    """Return (observed_axes, rule_score_delta)."""
    obs: Dict[str, float] = {}
    for axis in _AXIS_ORDER:
        t = _f(true_axes.get(axis), 50.0)
        base_sd = _BASE_SIGMA.get(axis, 8.0)
        acc = _f(expert.acc_mult.get(axis), 1.0)
        phase_mult = _sigma_multiplier_from_phase(ctx, axis, phase)
        sd = float(base_sd) * float(acc) * float(phase_mult)
        obs[axis] = _clamp(t + rng.gauss(0.0, sd), 0.0, 100.0)

    # Apply bias rules
    score_delta = 0.0
    rule_ctx = dict(ctx)
    rule_ctx["axes_true"] = dict(true_axes)
    rule_ctx["axes_obs"] = obs

    for rule in expert.bias_rules:
        ok = False
        try:
            ok = bool(rule.predicate(rule_ctx))
        except Exception:
            ok = False
        if not ok:
            continue

        for axis, dv in rule.axis_delta.items():
            obs[axis] = _clamp(_f(obs.get(axis), 50.0) + float(dv), 0.0, 100.0)
        score_delta += float(rule.score_delta)

    return obs, score_delta


def _alpha_by_base_rank(expert: ExpertProfile, base_rank: int) -> float:
    if base_rank <= 8:
        return float(expert.anchor_alpha_top)
    if base_rank <= 25:
        return float(expert.anchor_alpha_mid)
    return float(expert.anchor_alpha_late)


def _make_expert_score(
    *,
    expert: ExpertProfile,
    obs_axes: Mapping[str, float],
    base_public: float,
    rule_score_delta: float,
    rng: random.Random,
) -> float:
    # weighted average 0..100
    core = _axis_weighted_avg(expert.weights, obs_axes)
    s = (1.0 - float(expert.base_influence)) * core + float(expert.base_influence) * float(base_public)
    s += float(rule_score_delta)
    s += rng.gauss(0.0, float(expert.global_noise_sd))
    return _clamp(s, 0.0, 100.0)


# -----------------------------------------------------------------------------
# Expert profiles (6)
# -----------------------------------------------------------------------------

def _make_profiles() -> Dict[str, ExpertProfile]:
    def _ft_low(ctx: Dict[str, Any]) -> bool:
        ft = _f(_get_nested(ctx, "stats", "ft_pct"), 0.0)
        return ft > 0.0 and ft < 0.68

    def _young(ctx: Dict[str, Any]) -> bool:
        age = _f(_get_nested(ctx, "bio", "age"), 20.0)
        return age <= 19.5

    def _small_guard(ctx: Dict[str, Any]) -> bool:
        pos = str(_get_nested(ctx, "bio", "pos") or _get_nested(ctx, "pos") or "").upper()
        h = _f(_get_nested(ctx, "bio", "height_in"), 78.0)
        return (pos in ("PG", "SG", "G")) and (h < 74.0)

    def _long_arms(ctx: Dict[str, Any]) -> bool:
        combine = ctx.get("combine")
        if not isinstance(combine, dict):
            return False
        ws = _f(_get_nested(combine, "measurements", "wingspan_in"), 0.0)
        hn = _f(_get_nested(combine, "measurements", "height_noshoes_in"), 0.0)
        return ws > 0.0 and hn > 0.0 and (ws - hn) >= 6.0

    # 1) Balanced (Vecenie-ish)
    balanced = ExpertProfile(
        expert_id="balanced",
        display_name="밸런스형 분석가",
        short_tag="Balanced",
        weights={
            "UPSIDE": 1.20, "TOOLS": 1.00,
            "S_SPOT": 0.90, "PLAY": 0.90, "CREATION": 0.80,
            "DEF_POA": 0.80, "DEF_HELP": 0.70,
            "FIN_RIM": 0.60, "S_TOUCH": 0.60, "MOTOR": 0.60,
            "DUR": 0.40, "AGE": 0.30,
        },
        acc_mult={},
        bias_rules=(),
    )

    # 2) Analytics / efficiency
    analytics = ExpertProfile(
        expert_id="analytics",
        display_name="효율/데이터형 분석가",
        short_tag="Analytics",
        weights={
            "S_TOUCH": 1.20, "S_SPOT": 1.00, "PLAY": 1.00, "BALL_SAFE": 0.90,
            "CREATION": 0.70, "TOOLS": 0.60,
            "DEF_POA": 0.50, "DEF_HELP": 0.40, "UPSIDE": 0.50,
            "DUR": 0.40, "AGE": 0.30,
        },
        acc_mult={"S_TOUCH": 0.85, "S_SPOT": 0.90, "PLAY": 0.90, "DEF_HELP": 1.15},
        bias_rules=(
            BiasRule(
                name="low_ft_shooting_skeptic",
                predicate=lambda c: _ft_low(c),
                axis_delta={"S_SPOT": -2.0, "S_OD": -2.0},
            ),
        ),
    )

    # 3) Defense / film scout
    defense_film = ExpertProfile(
        expert_id="defense_film",
        display_name="필름/수비형 스카우트",
        short_tag="Defense",
        weights={
            "DEF_POA": 1.20, "DEF_HELP": 1.10, "DEF_RIM": 1.00,
            "MOTOR": 0.90, "TOOLS": 0.80, "REB": 0.60,
            "S_SPOT": 0.50, "PLAY": 0.40, "UPSIDE": 0.60,
            "DUR": 0.40, "AGE": 0.30,
        },
        acc_mult={"DEF_POA": 0.75, "DEF_HELP": 0.75, "MOTOR": 0.80, "S_OD": 1.10},
        bias_rules=(
            BiasRule(
                name="small_guard_def_skeptic",
                predicate=lambda c: _small_guard(c),
                axis_delta={"DEF_POA": -2.0, "DEF_HELP": -1.0},
            ),
        ),
    )

    # 4) Tools / upside
    tools_upside = ExpertProfile(
        expert_id="tools_upside",
        display_name="툴/업사이드형 분석가",
        short_tag="Tools+Upside",
        weights={
            "TOOLS": 1.30, "UPSIDE": 1.20, "FIN_BURST": 1.00, "CREATION": 0.90,
            "DEF_RIM": 0.60, "DEF_POA": 0.60,
            "S_SPOT": 0.50, "S_TOUCH": 0.50,
            "DUR": 0.40, "AGE": 0.40, "MOTOR": 0.50,
        },
        acc_mult={"TOOLS": 0.80, "FIN_BURST": 0.70, "DEF_HELP": 1.10},
        bias_rules=(
            BiasRule(
                name="young_upside_bonus",
                predicate=lambda c: _young(c),
                axis_delta={"UPSIDE": +2.0},
            ),
            BiasRule(
                name="plus_wingspan_rim_signal",
                predicate=lambda c: _long_arms(c),
                axis_delta={"DEF_RIM": +1.0, "TOOLS": +0.5},
            ),
        ),
    )

    # 5) Skill development / advantage creation
    skill_dev = ExpertProfile(
        expert_id="skill_dev",
        display_name="스킬/디벨롭 코치형",
        short_tag="Skill Dev",
        weights={
            "S_OD": 1.20, "CREATION": 1.10, "BALL_SAFE": 1.00, "PLAY": 0.90,
            "S_TOUCH": 0.80, "S_SPOT": 0.70,
            "TOOLS": 0.50, "UPSIDE": 0.60,
            "DEF_POA": 0.40, "DEF_RIM": 0.30, "DEF_HELP": 0.30,
            "DUR": 0.30, "AGE": 0.30,
        },
        acc_mult={"S_OD": 0.80, "CREATION": 0.80, "DEF_RIM": 1.05},
        bias_rules=(
            BiasRule(
                name="pullup_optimist",
                predicate=lambda c: True,
                axis_delta={"S_OD": +2.0},
            ),
        ),
    )

    # 6) Risk-averse front office
    risk_averse = ExpertProfile(
        expert_id="risk_averse",
        display_name="리스크 회피형 프런트",
        short_tag="Safe Pick",
        weights={
            "DUR": 1.20, "BALL_SAFE": 1.00, "MOTOR": 0.90,
            "DEF_POA": 0.80, "DEF_HELP": 0.80,
            "S_TOUCH": 0.70,
            "TOOLS": 0.60, "REB": 0.50,
            "UPSIDE": 0.40, "AGE": 0.50, "S_SPOT": 0.40,
        },
        acc_mult={"DUR": 0.85, "MOTOR": 0.85, "UPSIDE": 1.20},
        bias_rules=(
            BiasRule(
                name="low_durability_penalty",
                predicate=lambda c: _f(_get_nested(c, "axes_obs", "DUR"), 70.0) < 60.0,
                axis_delta={},
                score_delta=-3.5,
            ),
            BiasRule(
                name="older_upside_penalty",
                predicate=lambda c: _f(_get_nested(c, "bio", "age"), 20.0) >= 22.0,
                axis_delta={"UPSIDE": -2.0},
            ),
        ),
    )

    return {
        balanced.expert_id: balanced,
        analytics.expert_id: analytics,
        defense_film.expert_id: defense_film,
        tools_upside.expert_id: tools_upside,
        skill_dev.expert_id: skill_dev,
        risk_averse.expert_id: risk_averse,
    }


_PROFILES: Dict[str, ExpertProfile] = _make_profiles()


def get_expert_profile(expert_id: str) -> ExpertProfile:
    eid = str(expert_id or "").strip()
    if not eid:
        raise ValueError("expert_id is required")
    if eid not in _PROFILES:
        raise KeyError(f"Unknown expert_id: {eid}")
    return _PROFILES[eid]


def list_experts() -> List[Dict[str, Any]]:
    """Return expert descriptors suitable for UI."""
    out: List[Dict[str, Any]] = []
    for eid, p in sorted(_PROFILES.items(), key=lambda kv: kv[0]):
        out.append(
            {
                "expert_id": p.expert_id,
                "display_name": p.display_name,
                "tag": p.short_tag,
            }
        )
    return out


# -----------------------------------------------------------------------------
# Output shaping (no hidden values)
# -----------------------------------------------------------------------------

def _tier_label(rank: int) -> str:
    if rank <= 5:
        return "Tier 1"
    if rank <= 14:
        return "Lottery"
    if rank <= 30:
        return "1st Round"
    if rank <= 45:
        return "2nd Round"
    return "UDFA"


def _pick_tags(obs_axes: Mapping[str, float], *, pos: str = "") -> List[str]:
    """Generate small, non-spoilery tags for UI."""
    tags: List[str] = []

    s_spot = _f(obs_axes.get("S_SPOT"), 50.0)
    s_od = _f(obs_axes.get("S_OD"), 50.0)
    s_touch = _f(obs_axes.get("S_TOUCH"), 50.0)
    def_poa = _f(obs_axes.get("DEF_POA"), 50.0)
    def_help = _f(obs_axes.get("DEF_HELP"), 50.0)
    def_rim = _f(obs_axes.get("DEF_RIM"), 50.0)
    fin_rim = _f(obs_axes.get("FIN_RIM"), 50.0)
    creation = _f(obs_axes.get("CREATION"), 50.0)
    play = _f(obs_axes.get("PLAY"), 50.0)
    tools = _f(obs_axes.get("TOOLS"), 50.0)

    # Shooting
    if s_spot >= 85:
        tags.append("elite spot-up")
    elif s_spot >= 75:
        tags.append("plus shooter")
    elif s_spot <= 45 and (s_touch <= 45):
        tags.append("shooting question")

    if s_od >= 82:
        tags.append("pull-up threat")

    # Defense
    if def_poa >= 82 and pos.upper() in ("PG", "SG", "SF"):
        tags.append("POA defender")
    if def_help >= 84:
        tags.append("team D IQ")
    if def_rim >= 84 and _pos_bucket(pos) == "B":
        tags.append("rim protector")

    # Creation / playmaking
    if creation >= 82:
        tags.append("advantage creator")
    if play >= 82:
        tags.append("playmaker")

    # Finishing
    if fin_rim >= 82:
        tags.append("rim finisher")

    # Tools
    if tools >= 82:
        tags.append("great tools")

    # Keep small
    return tags[:4]


def _short_summary(expert: ExpertProfile, obs_axes: Mapping[str, float]) -> str:
    # Highlight 2 strengths and 1 concern
    # strengths from expert-weighted axes
    axes_list = [(k, _f(obs_axes.get(k), 50.0), _f(expert.weights.get(k), 0.0)) for k in _AXIS_ORDER]
    strengths = sorted([x for x in axes_list if x[2] > 0], key=lambda t: (t[2] * t[1]), reverse=True)[:2]
    concerns = sorted(axes_list, key=lambda t: t[1])[:1]

    def fmt(k: str, v: float) -> str:
        # friendlier labels
        label = {
            "S_SPOT": "Spot-up",
            "S_OD": "Pull-up",
            "S_TOUCH": "Touch",
            "FIN_RIM": "Finish",
            "FIN_BURST": "Burst",
            "FIN_PHYS": "Contact",
            "CREATION": "Creation",
            "BALL_SAFE": "Handle",
            "PLAY": "Playmaking",
            "DEF_POA": "POA D",
            "DEF_HELP": "Team D",
            "DEF_RIM": "Rim D",
            "REB": "Reb",
            "MOTOR": "Motor",
            "TOOLS": "Tools",
            "DUR": "Durability",
            "UPSIDE": "Upside",
            "AGE": "Age",
        }.get(k, k)
        if v >= 84:
            q = "A"
        elif v >= 74:
            q = "B"
        elif v >= 64:
            q = "C"
        elif v >= 54:
            q = "D"
        else:
            q = "F"
        return f"{label} {q}"

    s_txt = ", ".join(fmt(k, v) for k, v, _w in strengths)
    c_txt = ", ".join(fmt(k, v) for k, v, _w in concerns)
    return f"Strengths: {s_txt}. Concern: {c_txt}."


# -----------------------------------------------------------------------------
# Main API: generate expert big board
# -----------------------------------------------------------------------------


def compute_expert_ranks_from_prospects(
    *,
    prospects: Sequence[Prospect],
    draft_year: int,
    expert_id: str,
    phase: str = PHASE_AUTO,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    """Compute expert ranks from already-loaded prospects (no DB I/O).

    This is intended for draft.pool to build a public-facing consensus projected_pick
    without re-loading the pool from DB (reuse the already loaded Prospect objects).
    """
    ph = str(phase or PHASE_AUTO).strip()
    if ph not in _ALLOWED_PHASES:
        raise ValueError(f"Invalid phase: {ph}. Allowed: {sorted(_ALLOWED_PHASES)}")

    expert = get_expert_profile(expert_id)

    # Clean + stable list
    ps: List[Prospect] = []
    for p in prospects:
        if isinstance(p, Prospect) and str(getattr(p, "temp_id", "") or "").strip():
            ps.append(p)

    if limit is not None:
        try:
            lim = max(0, int(limit))
        except Exception:
            lim = 0
        if lim:
            ps = ps[:lim]

    # Determine phase automatically (same rule as generate_expert_bigboard)
    if ph == PHASE_AUTO:
        has_any_combine = any(isinstance((p.meta or {}).get("combine"), dict) for p in ps)
        ph = PHASE_POST_COMBINE if has_any_combine else PHASE_PRE_COMBINE

    # Public productivity normalization (for stable base scores)
    prods = [_public_prod_score((p.meta or {}).get("season_stats")) for p in ps]
    if prods:
        mean = sum(prods) / float(len(prods))
        var = sum((x - mean) ** 2 for x in prods) / float(len(prods))
        sd = math.sqrt(var) if var > 1e-9 else 1.0
        prod_norm = (mean, sd)
    else:
        prod_norm = None

    # Base public scores (independent of input ordering)
    base_rows: List[Tuple[str, float]] = []
    base_score_by_id: Dict[str, float] = {}
    for p in ps:
        tid = str(p.temp_id)
        b = compute_base_public_score(p, int(draft_year), ph, prod_norm=prod_norm)
        base_score_by_id[tid] = b
        base_rows.append((tid, b))

    base_rows.sort(key=lambda t: (-float(t[1]), str(t[0])))
    base_rank: Dict[str, int] = {tid: i + 1 for i, (tid, _b) in enumerate(base_rows)}

    # Deterministic global seed per (draft_year, expert_id, phase)
    global_seed = _stable_u32("expert_bigboard_v1", int(draft_year), expert.expert_id, ph)

    scored: List[Tuple[float, str]] = []
    for p in ps:
        tid = str(p.temp_id)
        prng = random.Random(_stable_u32(global_seed, tid))

        true_axes = build_true_axes_from_prospect(p)
        ctx = {
            "temp_id": tid,
            "name": p.name,
            "pos": p.pos,
            "bio": {
                "pos": p.pos,
                "age": int(p.age),
                "height_in": int(p.height_in),
                "weight_lb": int(p.weight_lb),
            },
            "stats": (p.meta or {}).get("season_stats") if isinstance((p.meta or {}).get("season_stats"), dict) else {},
            "combine": (p.meta or {}).get("combine") if (ph == PHASE_POST_COMBINE and isinstance((p.meta or {}).get("combine"), dict)) else None,
        }

        obs_axes, rule_delta = observe_axes(expert=expert, true_axes=true_axes, ctx=ctx, phase=ph, rng=prng)
        base_public = float(base_score_by_id.get(tid, 50.0))
        e_raw = _make_expert_score(expert=expert, obs_axes=obs_axes, base_public=base_public, rule_score_delta=rule_delta, rng=prng)
        a = _alpha_by_base_rank(expert, base_rank.get(tid, 9999))
        final = (1.0 - a) * base_public + a * e_raw

        scored.append((float(final), tid))

    scored.sort(key=lambda t: (-float(t[0]), str(t[1])))
    ranks: Dict[str, int] = {tid: i + 1 for i, (_s, tid) in enumerate(scored)}

    return {
        "ok": True,
        "draft_year": int(draft_year),
        "phase": str(ph),
        "expert_id": expert.expert_id,
        "ranks": ranks,
        "ordered_temp_ids": [tid for _s, tid in scored],
    }


def generate_expert_bigboard(
    *,
    db_path: str,
    draft_year: int,
    expert_id: str,
    phase: str = PHASE_AUTO,
    limit: Optional[int] = None,
    include_debug_axes: bool = False,
    pool_mode: str = "declared",          # "declared" | "watch" | "auto"
    watch_run_id: Optional[str] = None,
    watch_min_prob: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Generate a deterministic expert big board for a given draft year.

    Args:
      db_path: sqlite DB path.
      draft_year: target draft year (e.g. 2026).
      expert_id: from list_experts().
      phase:
        - "pre_combine": experts haven't seen combine measurements/drills.
        - "post_combine": experts incorporate combine info (and become a bit more accurate on tools/burst/motor).
        - "auto": if pool has combine data -> post_combine else pre_combine
      limit: optional number of prospects in output.
      include_debug_axes: if True, include axes_obs (and axes_true) in output for dev/debug.
                          DO NOT enable for user-facing builds.
      pool_mode:
        - "declared": use declared pool (college_draft_entries) only.
        - "watch": use pre-declaration watch pool (draft_watch_runs/probs).
        - "auto": try declared first; if none exist, fallback to watch.
      watch_run_id: optional explicit watch run id (DY{draft_year}@YYYY-MM).
      watch_min_prob: optional inclusion threshold for watch pool (declare_prob >= threshold).

    Returns:
      {
        "ok": True,
        "draft_year": ...,
        "phase": ...,
        "expert": {...},
        "pool_mode_used": "declared"|"watch",
        "watch_run_id_used": "..." (only when watch is used),
        "board": [{"rank":1, "temp_id":..., "name":..., "pos":..., "tier":..., "score":..., "tags":[...], "summary":...}, ...]
      }
    """
    ph = str(phase or PHASE_AUTO).strip()
    if ph not in _ALLOWED_PHASES:
        raise ValueError(f"Invalid phase: {ph}. Allowed: {sorted(_ALLOWED_PHASES)}")

    expert = get_expert_profile(expert_id)

    pm = str(pool_mode or "declared").strip().lower()
    if pm not in ("declared", "watch", "auto"):
        raise ValueError("pool_mode must be one of: declared, watch, auto")

    pool_mode_used = "declared"
    watch_run_id_used: Optional[str] = None
    try:
        if pm == "watch":
            pool = load_watch_pool_from_db(
                db_path=str(db_path),
                draft_year=int(draft_year),
                run_id=watch_run_id,
                min_prob=watch_min_prob,
                limit=limit,
            )
            pool_mode_used = "watch"
            watch_run_id_used = str(watch_run_id or "") or None
        else:
            # declared or auto: try declared first
            pool = load_pool_from_db(db_path=str(db_path), draft_year=int(draft_year))
            pool_mode_used = "declared"
    except ValueError as e:
        # auto mode: if declared pool isn't present yet, fallback to watch snapshot
        if pm == "auto" and "No declared prospects found" in str(e):
            pool = load_watch_pool_from_db(
                db_path=str(db_path),
                draft_year=int(draft_year),
                run_id=watch_run_id,
                min_prob=watch_min_prob,
                limit=limit,
            )
            pool_mode_used = "watch"
            watch_run_id_used = str(watch_run_id or "") or None
        else:
            raise

    prospects: List[Prospect] = []
    for tid in list(pool.ranked_temp_ids or []):
        p = pool.prospects_by_temp_id.get(str(tid))
        if isinstance(p, Prospect):
            prospects.append(p)

    if limit is not None:
        prospects = prospects[: max(0, int(limit))]

    # Determine phase automatically
    if ph == PHASE_AUTO:
        has_any_combine = any(isinstance((p.meta or {}).get("combine"), dict) for p in prospects)
        ph = PHASE_POST_COMBINE if has_any_combine else PHASE_PRE_COMBINE

    # Public productivity normalization (for stable base scores)
    prods = [_public_prod_score((p.meta or {}).get("season_stats")) for p in prospects]
    if prods:
        mean = sum(prods) / float(len(prods))
        var = sum((x - mean) ** 2 for x in prods) / float(len(prods))
        sd = math.sqrt(var) if var > 1e-9 else 1.0
        prod_norm = (mean, sd)
    else:
        prod_norm = None

    # Base public scores
    base_rows: List[Tuple[str, float]] = []
    base_score_by_id: Dict[str, float] = {}
    for p in prospects:
        tid = str(p.temp_id)
        b = compute_base_public_score(p, int(draft_year), ph, prod_norm=prod_norm)
        base_score_by_id[tid] = b
        base_rows.append((tid, b))

    base_rows.sort(key=lambda t: t[1], reverse=True)
    base_rank: Dict[str, int] = {tid: i + 1 for i, (tid, _b) in enumerate(base_rows)}

    # Deterministic global seed per (draft_year, expert_id, phase)
    global_seed = _stable_u32("expert_bigboard_v1", int(draft_year), expert.expert_id, ph)

    board_rows: List[Dict[str, Any]] = []
    for p in prospects:
        tid = str(p.temp_id)
        # Per-prospect RNG: stable and independent of iteration order
        prng = random.Random(_stable_u32(global_seed, tid))

        true_axes = build_true_axes_from_prospect(p)

        ctx = {
            "temp_id": tid,
            "name": p.name,
            "pos": p.pos,
            "bio": {
                "pos": p.pos,
                "age": int(p.age),
                "height_in": int(p.height_in),
                "weight_lb": int(p.weight_lb),
            },
            "stats": (p.meta or {}).get("season_stats") if isinstance((p.meta or {}).get("season_stats"), dict) else {},
            # combine payload might exist in DB; we only expose to model if phase permits
            "combine": (p.meta or {}).get("combine") if (ph == PHASE_POST_COMBINE and isinstance((p.meta or {}).get("combine"), dict)) else None,
        }

        obs_axes, rule_delta = observe_axes(expert=expert, true_axes=true_axes, ctx=ctx, phase=ph, rng=prng)
        base_public = float(base_score_by_id.get(tid, 50.0))
        e_raw = _make_expert_score(expert=expert, obs_axes=obs_axes, base_public=base_public, rule_score_delta=rule_delta, rng=prng)

        a = _alpha_by_base_rank(expert, base_rank.get(tid, 9999))
        final = (1.0 - a) * base_public + a * e_raw

        row: Dict[str, Any] = {
            "temp_id": tid,
            "name": p.name,
            "pos": p.pos,
            "age": int(p.age),
            "height_in": int(p.height_in),
            "weight_lb": int(p.weight_lb),
            "score": round(float(final), 3),
            "score_components": {
                "base_public": round(float(base_public), 3),
                "expert": round(float(e_raw), 3),
                "alpha": round(float(a), 3),
            },
            "tags": _pick_tags(obs_axes, pos=p.pos),
            "summary": _short_summary(expert, obs_axes),
        }
        if include_debug_axes:
            row["axes_obs"] = {k: round(float(obs_axes.get(k, 50.0)), 3) for k in _AXIS_ORDER}
            row["axes_true"] = {k: round(float(true_axes.get(k, 50.0)), 3) for k in _AXIS_ORDER}

        board_rows.append(row)

    board_rows.sort(key=lambda r: (-float(r.get("score", 0.0)), str(r.get("temp_id", ""))))

    board_out: List[Dict[str, Any]] = []
    for i, r in enumerate(board_rows, start=1):
        out = dict(r)
        out["rank"] = int(i)
        out["tier"] = _tier_label(i)
        board_out.append(out)

    return {
        "ok": True,
        "draft_year": int(draft_year),
        "phase": str(ph),
        "expert": {
            "expert_id": expert.expert_id,
            "display_name": expert.display_name,
            "tag": expert.short_tag,
        },
        "pool_mode_used": pool_mode_used,
        "watch_run_id_used": watch_run_id_used,
        "board": board_out,
    }
