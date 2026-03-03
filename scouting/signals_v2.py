from __future__ import annotations

"""Signal-based scouting model (v2).

This module defines:
  - Signal axes that feel like real scouting "translation" signals
  - True-value computation mixing SSOT attrs_json (+ derived metrics) and
    college production/stats
  - Tier/confidence helpers (user-facing; no raw 0..100 score exposure)
  - Evidence tag generation rules (PLUS/MINUS/QUESTION/META)

Design goals:
  - Keep the existing Kalman/Bayesian backbone in scouting.service (mu/sigma)
  - Replace the legacy "overall/shooting/defense" style axes with a more
    scouting-flavored set of signals.
  - Provide structured evidence tags so LLM reports can be grounded and
    consistent (and avoid hallucinations).

All numeric computations remain 0..100 internally.
UI/LLM should rely on tier/confidence + evidence tags, not raw scores.
"""

import math
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from ratings_2k import potential_grade_to_scalar


# -----------------------------------------------------------------------------
# Types
# -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SignalDef:
    key: str
    label: str
    group: str  # offense | defense | physical

    # Measurement model (used by scouting.service)
    base_meas_std: float
    init_sigma: float
    sigma_floor: float
    meas_floor: float


# Canonical signals (0..100). Higher is better.
SIGNALS: Dict[str, SignalDef] = {
    # Offense
    "space_bending": SignalDef(
        key="space_bending",
        label="스페이싱 중력",
        group="offense",
        base_meas_std=15.0,
        init_sigma=18.0,
        sigma_floor=5.0,
        meas_floor=3.0,
    ),
    "downhill_pressure": SignalDef(
        key="downhill_pressure",
        label="림 압박",
        group="offense",
        base_meas_std=14.0,
        init_sigma=18.0,
        sigma_floor=5.0,
        meas_floor=3.0,
    ),
    "shotmaking_complexity": SignalDef(
        key="shotmaking_complexity",
        label="어려운 슛 해결",
        group="offense",
        base_meas_std=18.0,
        init_sigma=21.0,
        sigma_floor=6.0,
        meas_floor=3.5,
    ),
    "playmaking_engine": SignalDef(
        key="playmaking_engine",
        label="창출형 플레이메이킹",
        group="offense",
        base_meas_std=16.0,
        init_sigma=20.0,
        sigma_floor=5.5,
        meas_floor=3.2,
    ),
    "processing_safety": SignalDef(
        key="processing_safety",
        label="처리/안전성",
        group="offense",
        base_meas_std=17.0,
        init_sigma=20.0,
        sigma_floor=6.0,
        meas_floor=3.5,
    ),

    # Defense
    "perimeter_containment": SignalDef(
        key="perimeter_containment",
        label="외곽 억제",
        group="defense",
        base_meas_std=17.0,
        init_sigma=20.0,
        sigma_floor=6.0,
        meas_floor=3.5,
    ),
    "team_defense_rim_support": SignalDef(
        key="team_defense_rim_support",
        label="팀수비/림 서포트",
        group="defense",
        base_meas_std=18.0,
        init_sigma=22.0,
        sigma_floor=6.5,
        meas_floor=3.8,
    ),
    "defensive_playmaking": SignalDef(
        key="defensive_playmaking",
        label="수비 이벤트",
        group="defense",
        base_meas_std=16.0,
        init_sigma=20.0,
        sigma_floor=6.0,
        meas_floor=3.4,
    ),

    # Physical / context
    "glass_physicality": SignalDef(
        key="glass_physicality",
        label="리바운드/컨택",
        group="physical",
        base_meas_std=13.0,
        init_sigma=17.0,
        sigma_floor=5.0,
        meas_floor=2.8,
    ),
    "motor_compete": SignalDef(
        key="motor_compete",
        label="모터/지속성",
        group="physical",
        base_meas_std=20.0,
        init_sigma=24.0,
        sigma_floor=7.0,
        meas_floor=4.5,
    ),
    "runway": SignalDef(
        key="runway",
        label="성장 여지(Runway)",
        group="physical",
        base_meas_std=22.0,
        init_sigma=26.0,
        sigma_floor=8.0,
        meas_floor=5.0,
    ),
}


# -----------------------------------------------------------------------------
# Small numeric helpers
# -----------------------------------------------------------------------------


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return float(default)
        return float(v)
    except Exception:
        return float(default)


def clamp01(x: float) -> float:
    return float(max(0.0, min(1.0, float(x))))


def clamp100(x: float) -> float:
    return float(max(0.0, min(100.0, float(x))))


def N(x: float, lo: float, hi: float) -> float:
    """Normalize x from [lo..hi] to [0..100], clamped."""
    x0 = float(x)
    lo0 = float(lo)
    hi0 = float(hi)
    if hi0 <= lo0:
        return 50.0
    return clamp100(100.0 * (x0 - lo0) / (hi0 - lo0))


def INV(x: float, lo: float, hi: float) -> float:
    """Inverse normalize (lower is better) to [0..100], clamped."""
    return 100.0 - N(x, lo, hi)


def pct_str(p: float) -> str:
    try:
        return f"{float(p) * 100.0:.1f}%"
    except Exception:
        return "-"


def num_str(x: float, *, digits: int = 1) -> str:
    try:
        fmt = f"{{:.{int(digits)}f}}"
        return fmt.format(float(x))
    except Exception:
        return "-"


# -----------------------------------------------------------------------------
# Tier / confidence (user-facing)
# -----------------------------------------------------------------------------


TIER_ORDER = {
    "경고": 0,
    "우려": 1,
    "평균권": 2,
    "강점": 3,
    "특급": 4,
}


def tier_from_mu(mu: float) -> str:
    x = float(mu)
    if x >= 85:
        return "특급"
    if x >= 75:
        return "강점"
    if x >= 60:
        return "평균권"
    if x >= 45:
        return "우려"
    return "경고"


def tier_score(tier: str) -> int:
    return int(TIER_ORDER.get(str(tier), 0))


def confidence_from_sigma(sigma: float) -> str:
    s = float(sigma)
    if s <= 5.0:
        return "high"
    if s <= 9.0:
        return "medium"
    return "low"


def range_text_from_mu_sigma(mu: float, sigma: float) -> str:
    """Natural-language uncertainty band (no raw numbers)."""
    lo = tier_from_mu(float(mu) - 2.0 * float(sigma))
    hi = tier_from_mu(float(mu) + 2.0 * float(sigma))
    if lo == hi:
        return f"대략 {lo} 구간"
    # order in ascending tier
    if tier_score(lo) > tier_score(hi):
        lo, hi = hi, lo
    return f"대략 {lo}~{hi} 사이"


# -----------------------------------------------------------------------------
# College stats helpers
# -----------------------------------------------------------------------------


def compute_stat_weight(*, games: int, mpg: float) -> float:
    """0..1 reliability weight based on minutes played."""
    minutes = float(max(0.0, float(games) * float(mpg)))
    return clamp01((minutes - 180.0) / 720.0)


def pace_adjustment(pace: float) -> float:
    """Rough pace adjustment multiplier (70 possessions is 'neutral')."""
    p = float(pace) if pace is not None else 70.0
    p = float(max(60.0, min(80.0, p)))
    return 70.0 / p


def ts_load(ts_pct: float, usg: float) -> float:
    """Efficiency adjusted for usage burden (simple proxy)."""
    t = float(ts_pct)
    u = float(usg)
    out = t - 0.08 * (u - 0.20)
    return clamp01(out)


# -----------------------------------------------------------------------------
# True signal computation (0..100)
# -----------------------------------------------------------------------------


def _attr(attrs: Mapping[str, Any], key: str, default: float = 50.0) -> float:
    """Read raw attribute from SSOT attrs_json using project column names."""
    return _safe_float(attrs.get(key), default)


def compute_true_signals(
    *,
    ovr: int,
    attrs: Mapping[str, Any],
    derived: Mapping[str, float],
    college_stats: Mapping[str, Any],
    context: Mapping[str, Any],
) -> Tuple[Dict[str, float], Dict[str, float]]:
    """Compute true signal values.

    Returns:
      - true_signals: 0..100 values (higher is better)
      - prod_scores: production-only component (0..100) used for mismatch tags
    """

    # Stats (safe defaults)
    games = int(_safe_float(college_stats.get("games"), 0.0))
    mpg = float(_safe_float(college_stats.get("mpg"), 0.0))

    pts = float(_safe_float(college_stats.get("pts"), 0.0))
    reb = float(_safe_float(college_stats.get("reb"), 0.0))
    ast = float(_safe_float(college_stats.get("ast"), 0.0))
    stl = float(_safe_float(college_stats.get("stl"), 0.0))
    blk = float(_safe_float(college_stats.get("blk"), 0.0))
    tov = float(_safe_float(college_stats.get("tov"), 0.0))
    pf = float(_safe_float(college_stats.get("pf"), 0.0))

    fg_pct = float(_safe_float(college_stats.get("fg_pct"), 0.0))
    tp_pct = float(_safe_float(college_stats.get("tp_pct"), 0.0))
    ft_pct = float(_safe_float(college_stats.get("ft_pct"), 0.0))

    usg = float(_safe_float(college_stats.get("usg"), 0.0))
    ts_pct = float(_safe_float(college_stats.get("ts_pct"), 0.0))
    pace = float(_safe_float(college_stats.get("pace"), 70.0))

    # Derived helpers
    adj = pace_adjustment(pace)
    ptsA = pts * adj
    rebA = reb * adj
    astA = ast * adj

    atr = ast / max(tov, 0.5)
    stocks = stl + blk
    impact = ptsA + 0.7 * rebA + 0.9 * astA

    w_stats = compute_stat_weight(games=games, mpg=mpg)

    # Frequently used derived values (0..100)
    d = derived
    FIRST_STEP = _safe_float(d.get("FIRST_STEP"), 50.0)
    DRIVE_CREATE = _safe_float(d.get("DRIVE_CREATE"), 50.0)
    FIN_CONTACT = _safe_float(d.get("FIN_CONTACT"), 50.0)
    FIN_RIM = _safe_float(d.get("FIN_RIM"), 50.0)

    SHOT_3_CS = _safe_float(d.get("SHOT_3_CS"), 50.0)
    SHOT_3_OD = _safe_float(d.get("SHOT_3_OD"), 50.0)
    SHOT_MID_PU = _safe_float(d.get("SHOT_MID_PU"), 50.0)
    SHOT_FT = _safe_float(d.get("SHOT_FT"), 50.0)
    SHOT_TOUCH = _safe_float(d.get("SHOT_TOUCH"), 50.0)

    HANDLE_SAFE = _safe_float(d.get("HANDLE_SAFE"), 50.0)
    PASS_SAFE = _safe_float(d.get("PASS_SAFE"), 50.0)
    PASS_CREATE = _safe_float(d.get("PASS_CREATE"), 50.0)
    PNR_READ = _safe_float(d.get("PNR_READ"), 50.0)

    DEF_POA = _safe_float(d.get("DEF_POA"), 50.0)
    DEF_HELP = _safe_float(d.get("DEF_HELP"), 50.0)
    DEF_RIM = _safe_float(d.get("DEF_RIM"), 50.0)
    DEF_STEAL = _safe_float(d.get("DEF_STEAL"), 50.0)

    REB_OR = _safe_float(d.get("REB_OR"), 50.0)
    REB_DR = _safe_float(d.get("REB_DR"), 50.0)

    PHYSICAL = _safe_float(d.get("PHYSICAL"), 50.0)
    ENDURANCE = _safe_float(d.get("ENDURANCE"), 50.0)

    # Raw attrs used in some signals
    DrawFoul = _attr(attrs, "Draw Foul", 50.0)
    Speed = _attr(attrs, "Speed", 50.0)
    Agility = _attr(attrs, "Agility", 50.0)
    Strength = _attr(attrs, "Strength", 50.0)
    Vertical = _attr(attrs, "Vertical", 50.0)
    Stamina = _attr(attrs, "Stamina", 50.0)
    Hustle = _attr(attrs, "Hustle", 50.0)

    ShotIQ = _attr(attrs, "Shot IQ", 50.0)
    OffCons = _attr(attrs, "Offensive Consistency", 50.0)
    DefCons = _attr(attrs, "Defensive Consistency", 50.0)

    InteriorDef = _attr(attrs, "Interior Defense", 50.0)
    PassPerception = _attr(attrs, "Pass Perception", 50.0)

    WorkEthic = _attr(attrs, "M_WorkEthic", 60.0)
    Coachability = _attr(attrs, "M_Coachability", 60.0)
    Ambition = _attr(attrs, "M_Ambition", 60.0)
    Adaptability = _attr(attrs, "M_Adaptability", 60.0)

    height_in = float(_safe_float(context.get("height_in"), 78.0))
    weight_lb = float(_safe_float(context.get("weight_lb"), 210.0))
    age = float(_safe_float(context.get("age"), 19.0))
    class_year = float(_safe_float(context.get("class_year"), 1.0))

    # Burden adjusted TS
    tsL = ts_load(ts_pct, usg)

    true: Dict[str, float] = {}
    prod: Dict[str, float] = {}

    # 1) space_bending
    r = 0.45 * SHOT_3_CS + 0.15 * SHOT_3_OD + 0.20 * SHOT_FT + 0.20 * SHOT_TOUCH
    p = (
        0.45 * N(tp_pct, 0.27, 0.44)
        + 0.25 * N(ft_pct, 0.60, 0.88)
        + 0.20 * N(ts_pct, 0.47, 0.64)
        + 0.10 * N(usg, 0.10, 0.30)
    )
    prod["space_bending"] = clamp100(p)
    true["space_bending"] = clamp100((1.0 - w_stats) * r + w_stats * p)

    # 2) downhill_pressure
    r = 0.25 * FIRST_STEP + 0.25 * DRIVE_CREATE + 0.25 * FIN_CONTACT + 0.15 * FIN_RIM + 0.10 * DrawFoul
    p = (
        0.35 * N(ptsA, 6.0, 22.0)
        + 0.20 * N(usg, 0.12, 0.34)
        + 0.30 * N(ts_pct, 0.47, 0.63)
        + 0.15 * N(fg_pct, 0.40, 0.56)
    )
    prod["downhill_pressure"] = clamp100(p)
    true["downhill_pressure"] = clamp100((1.0 - w_stats) * r + w_stats * p)

    # 3) shotmaking_complexity
    r = 0.30 * SHOT_3_OD + 0.20 * SHOT_MID_PU + 0.20 * SHOT_TOUCH + 0.15 * HANDLE_SAFE + 0.15 * FIRST_STEP
    p = (
        0.30 * N(usg, 0.14, 0.34)
        + 0.35 * N(tsL, 0.46, 0.62)
        + 0.20 * N(ptsA, 6.0, 22.0)
        + 0.15 * INV(tov, 0.8, 3.8)
    )
    prod["shotmaking_complexity"] = clamp100(p)
    true["shotmaking_complexity"] = clamp100((1.0 - w_stats) * r + w_stats * p)

    # 4) playmaking_engine
    r = 0.35 * PASS_CREATE + 0.25 * PNR_READ + 0.20 * PASS_SAFE + 0.20 * HANDLE_SAFE
    p = (
        0.35 * N(astA, 1.5, 7.5)
        + 0.25 * N(atr, 0.8, 3.5)
        + 0.20 * INV(tov, 0.8, 3.8)
        + 0.20 * N(usg, 0.12, 0.34)
    )
    prod["playmaking_engine"] = clamp100(p)
    true["playmaking_engine"] = clamp100((1.0 - w_stats) * r + w_stats * p)

    # 5) processing_safety
    r = 0.25 * PASS_SAFE + 0.25 * HANDLE_SAFE + 0.20 * PNR_READ + 0.15 * ShotIQ + 0.15 * OffCons
    p = (
        0.45 * INV(tov, 0.8, 3.8)
        + 0.20 * INV(pf, 1.0, 3.6)
        + 0.20 * N(tsL, 0.46, 0.62)
        + 0.15 * N(mpg, 12.0, 34.0)
    )
    prod["processing_safety"] = clamp100(p)
    true["processing_safety"] = clamp100((1.0 - w_stats) * r + w_stats * p)

    # 6) perimeter_containment
    r = 0.50 * DEF_POA + 0.20 * Agility + 0.15 * Speed + 0.15 * DEF_STEAL
    p = 0.45 * N(stl, 0.4, 2.2) + 0.25 * INV(pf, 1.0, 3.6) + 0.30 * N(mpg, 12.0, 34.0)
    prod["perimeter_containment"] = clamp100(p)
    true["perimeter_containment"] = clamp100((1.0 - w_stats) * r + w_stats * p)

    # 7) team_defense_rim_support
    H = N(height_in, 70.0, 84.0)
    r = 0.40 * DEF_HELP + 0.25 * DEF_RIM + 0.15 * InteriorDef + 0.10 * H + 0.10 * Vertical
    p = 0.40 * N(blk, 0.2, 2.6) + 0.30 * N(rebA, 2.5, 12.0) + 0.30 * INV(pf, 1.0, 3.6)
    prod["team_defense_rim_support"] = clamp100(p)
    true["team_defense_rim_support"] = clamp100((1.0 - w_stats) * r + w_stats * p)

    # 8) defensive_playmaking
    r = 0.35 * DEF_STEAL + 0.20 * PassPerception + 0.15 * DEF_HELP + 0.15 * Hustle + 0.15 * Agility
    p = 0.65 * N(stocks, 0.6, 4.0) + 0.35 * INV(pf, 1.0, 3.6)
    prod["defensive_playmaking"] = clamp100(p)
    true["defensive_playmaking"] = clamp100((1.0 - w_stats) * r + w_stats * p)

    # 9) glass_physicality
    r = 0.30 * REB_DR + 0.20 * REB_OR + 0.20 * Strength + 0.15 * Hustle + 0.15 * PHYSICAL
    W = N(weight_lb, 170.0, 260.0)
    H = N(height_in, 70.0, 84.0)
    p = 0.60 * N(rebA, 2.5, 12.0) + 0.20 * W + 0.20 * H
    prod["glass_physicality"] = clamp100(p)
    true["glass_physicality"] = clamp100((1.0 - w_stats) * r + w_stats * p)

    # 10) motor_compete
    r = (
        0.22 * ENDURANCE
        + 0.12 * Hustle
        + 0.12 * Stamina
        + 0.12 * DefCons
        + 0.10 * OffCons
        + 0.17 * WorkEthic
        + 0.15 * Coachability
    )
    p = 0.45 * N(mpg, 12.0, 34.0) + 0.30 * N(rebA, 2.5, 12.0) + 0.25 * N(stocks, 0.6, 3.8)
    prod["motor_compete"] = clamp100(p)
    true["motor_compete"] = clamp100((1.0 - w_stats) * r + w_stats * p)

    # 11) runway
    pot_grade = attrs.get("Potential")
    pot = 100.0 * clamp01(float(potential_grade_to_scalar(pot_grade)))
    ageS = INV(age, 18.0, 23.0)
    habits = (WorkEthic + Ambition + Adaptability) / 3.0
    r = 0.60 * pot + 0.20 * ageS + 0.20 * habits

    classS = INV(class_year, 1.0, 4.0)
    p = 0.40 * N(impact, 10.0, 32.0) + 0.30 * classS + 0.30 * N(ts_pct, 0.47, 0.64)
    prod["runway"] = clamp100(p)
    true["runway"] = clamp100((1.0 - w_stats) * r + w_stats * p)

    # Defensive: ensure all signals exist
    for k in SIGNALS.keys():
        if k not in true:
            true[k] = 50.0
        if k not in prod:
            prod[k] = 50.0

    # NOTE: legacy 'ovr' isn't used directly; true signals are independent.
    _ = ovr

    return true, prod


# -----------------------------------------------------------------------------
# Evidence tags (rules)
# -----------------------------------------------------------------------------


CONF_RANK = {"low": 0, "medium": 1, "high": 2}


def _conf_gte(cur: str, required: str) -> bool:
    return int(CONF_RANK.get(str(cur), 0)) >= int(CONF_RANK.get(str(required), 0))


def _tag(
    *,
    axis: str,
    tag_id: str,
    tag: str,
    kind: str,
    priority: int,
    text: str,
) -> Dict[str, Any]:
    return {
        "axis": axis,
        "tag_id": tag_id,
        "tag": tag,
        "kind": kind,
        "priority": int(priority),
        "text": str(text),
    }


def _select_tags(cands: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Pick a small, high-signal subset of tags."""
    plus = sorted([t for t in cands if t.get("kind") == "PLUS"], key=lambda x: int(x.get("priority") or 0), reverse=True)
    minus = sorted([t for t in cands if t.get("kind") == "MINUS"], key=lambda x: int(x.get("priority") or 0), reverse=True)
    ques = sorted([t for t in cands if t.get("kind") == "QUESTION"], key=lambda x: int(x.get("priority") or 0), reverse=True)
    meta = sorted([t for t in cands if t.get("kind") == "META"], key=lambda x: int(x.get("priority") or 0), reverse=True)

    out: List[Dict[str, Any]] = []
    out.extend(meta[:2])
    out.extend(plus[:2])
    out.extend(minus[:1])
    out.extend(ques[:1])

    if not out:
        out.append(
            _tag(
                axis=str(cands[0].get("axis") if cands else ""),
                tag_id="DEFAULT",
                tag="관찰 필요",
                kind="META",
                priority=1,
                text="이번 달에는 확실한 신호가 아직 부족하다. 표본/역할이 늘어나는지 추가 관찰이 필요.",
            )
        )

    # Stable order: META -> PLUS -> MINUS -> QUESTION within same priority band
    kind_rank = {"META": 0, "PLUS": 1, "MINUS": 2, "QUESTION": 3}
    out.sort(key=lambda x: (kind_rank.get(str(x.get("kind")), 9), -int(x.get("priority") or 0)))
    return out


def build_evidence_tags(
    *,
    axis: str,
    estimate_mu: float,
    estimate_sigma: float,
    prod_score: float,
    derived: Mapping[str, float],
    attrs: Mapping[str, Any],
    stats: Mapping[str, Any],
    context: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    """Generate evidence tags for a signal axis.

    NOTE: This function is deterministic given its inputs.
    """

    axis_key = str(axis)

    # Common values
    tp_pct = float(_safe_float(stats.get("tp_pct"), 0.0))
    ft_pct = float(_safe_float(stats.get("ft_pct"), 0.0))
    ts_pct = float(_safe_float(stats.get("ts_pct"), 0.0))
    usg = float(_safe_float(stats.get("usg"), 0.0))

    pts = float(_safe_float(stats.get("pts"), 0.0))
    reb = float(_safe_float(stats.get("reb"), 0.0))
    ast = float(_safe_float(stats.get("ast"), 0.0))
    stl = float(_safe_float(stats.get("stl"), 0.0))
    blk = float(_safe_float(stats.get("blk"), 0.0))
    tov = float(_safe_float(stats.get("tov"), 0.0))
    pf = float(_safe_float(stats.get("pf"), 0.0))

    mpg = float(_safe_float(stats.get("mpg"), 0.0))
    games = int(_safe_float(stats.get("games"), 0.0))

    pos = str(context.get("pos") or "").upper()
    pos_bucket = str(context.get("pos_bucket") or "").upper()
    height_in = int(_safe_float(context.get("height_in"), 78.0))
    weight_lb = int(_safe_float(context.get("weight_lb"), 210.0))
    age = int(_safe_float(context.get("age"), 19.0))
    class_year = int(_safe_float(context.get("class_year"), 1.0))

    stocks = stl + blk

    # Derived / attrs
    SHOT_3_CS = _safe_float(derived.get("SHOT_3_CS"), 50.0)
    SHOT_3_OD = _safe_float(derived.get("SHOT_3_OD"), 50.0)
    SHOT_MID_PU = _safe_float(derived.get("SHOT_MID_PU"), 50.0)

    DRIVE_CREATE = _safe_float(derived.get("DRIVE_CREATE"), 50.0)
    FIN_RIM = _safe_float(derived.get("FIN_RIM"), 50.0)

    DEF_POA = _safe_float(derived.get("DEF_POA"), 50.0)
    DEF_HELP = _safe_float(derived.get("DEF_HELP"), 50.0)
    DEF_RIM = _safe_float(derived.get("DEF_RIM"), 50.0)
    DEF_STEAL = _safe_float(derived.get("DEF_STEAL"), 50.0)

    REB_OR = _safe_float(derived.get("REB_OR"), 50.0)
    REB_DR = _safe_float(derived.get("REB_DR"), 50.0)

    FIRST_STEP = _safe_float(derived.get("FIRST_STEP"), 50.0)

    Speed = _safe_float(attrs.get("Speed"), 50.0)
    Agility = _safe_float(attrs.get("Agility"), 50.0)
    Strength = _safe_float(attrs.get("Strength"), 50.0)

    Hustle = _safe_float(attrs.get("Hustle"), 50.0)
    WorkEthic = _safe_float(attrs.get("M_WorkEthic"), 60.0)
    Coachability = _safe_float(attrs.get("M_Coachability"), 60.0)
    Ambition = _safe_float(attrs.get("M_Ambition"), 60.0)

    confidence = confidence_from_sigma(estimate_sigma)

    tsL = ts_load(ts_pct, usg)
    ts_load_str = pct_str(tsL)

    # -----------------------------------------------------------------
    # Global overlays (scouting feel)
    # -----------------------------------------------------------------
    cands: List[Dict[str, Any]] = []

    # Confidence overlay
    if confidence == "low":
        cands.append(
            _tag(
                axis=axis_key,
                tag_id="LOW_CONF",
                tag="표본/관찰 부족",
                kind="META",
                priority=96,
                text="이번 달은 관찰/표본이 부족해 확신이 낮다. 다음 체크포인트에서 같은 신호가 반복되는지 확인 필요.",
            )
        )

    # Tool vs production mismatch overlay
    diff = float(estimate_mu) - float(prod_score)
    if diff >= 12.0 and confidence != "low":
        cands.append(
            _tag(
                axis=axis_key,
                tag_id="TOOLS_GT_PROD",
                tag="툴>생산",
                kind="META",
                priority=92,
                text="현장 평가는 좋은데 박스스코어 반영이 아직 늦다. 역할/환경이 맞으면 급상승 여지가 있다.",
            )
        )
    elif diff <= -12.0:
        cands.append(
            _tag(
                axis=axis_key,
                tag_id="PROD_GT_TOOLS",
                tag="생산>툴",
                kind="META",
                priority=92,
                text="생산은 찍히지만 툴 기반 확신은 아직 덜하다. 상위 레벨에서 같은 방식이 통하는지 재검증 필요.",
            )
        )

    # -----------------------------------------------------------------
    # Axis-specific rules
    # -----------------------------------------------------------------

    # --- space_bending -------------------------------------------------
    if axis_key == "space_bending":
        if SHOT_3_CS >= 72 and tp_pct >= 0.36:
            cands.append(
                _tag(
                    axis=axis_key,
                    tag_id="SB_CATCH_SHOOT_GRAVITY",
                    tag="캐치&슛 위협",
                    kind="PLUS",
                    priority=90,
                    text=f"캐치&슛 기반이 단단하다(3P {pct_str(tp_pct)}). 수비가 한 발만 늦어도 벌점을 줄 타입.",
                )
            )
        if ft_pct >= 0.80 and _safe_float(derived.get("SHOT_TOUCH"), 50.0) >= 70:
            cands.append(
                _tag(
                    axis=axis_key,
                    tag_id="SB_TOUCH_BACKBONE",
                    tag="터치 기반",
                    kind="PLUS",
                    priority=85,
                    text=f"FT {pct_str(ft_pct)} + 터치 신호가 안정적. 슛 감이 장기적으로 유지될 가능성이 높다.",
                )
            )
        if SHOT_3_OD >= 72 and tp_pct >= 0.33 and usg >= 0.22 and _conf_gte(confidence, "medium"):
            cands.append(
                _tag(
                    axis=axis_key,
                    tag_id="SB_OFFDRIBBLE_RANGE",
                    tag="오프드리블 레인지",
                    kind="PLUS",
                    priority=80,
                    text=f"오프드리블 슈팅까지 옵션으로 보인다(USG {usg:.2f}, 3P {pct_str(tp_pct)}). 클로즈아웃 대응이 가능.",
                )
            )

        if ft_pct < 0.70 or _safe_float(derived.get("SHOT_FT"), 50.0) <= 55:
            cands.append(
                _tag(
                    axis=axis_key,
                    tag_id="SB_FT_RED_FLAG",
                    tag="터치 의심",
                    kind="MINUS",
                    priority=85,
                    text=f"FT {pct_str(ft_pct)}가 낮아 터치 일관성에 물음표. 3점이 흔들릴 때 회복 장치가 약할 수 있다.",
                )
            )
        if usg <= 0.16 and tp_pct >= 0.36 and pts <= 10.0:
            cands.append(
                _tag(
                    axis=axis_key,
                    tag_id="SB_LOW_VOLUME_SHOOTER",
                    tag="볼륨 미확인",
                    kind="MINUS",
                    priority=70,
                    text=f"3점은 맞추지만 공격 비중이 낮다(USG {usg:.2f}, PTS {pts:.1f}). '볼륨'이 따라올지 확인 필요.",
                )
            )
        if SHOT_3_CS >= 70 and SHOT_3_OD <= 55:
            cands.append(
                _tag(
                    axis=axis_key,
                    tag_id="SB_STATIONARY_ONLY",
                    tag="정지형",
                    kind="MINUS",
                    priority=65,
                    text="정지 캐치&슛은 보이는데, 풀업/이동슈팅 확장은 아직 미지수.",
                )
            )
        if tp_pct >= 0.38 and ft_pct < 0.72:
            cands.append(
                _tag(
                    axis=axis_key,
                    tag_id="SB_TP_FT_GAP",
                    tag="3P-FT 괴리",
                    kind="QUESTION",
                    priority=60,
                    text=f"3점은 높지만 FT가 받쳐주지 않는다(3P {pct_str(tp_pct)}, FT {pct_str(ft_pct)}). 표본/메커니즘 추가 확인.",
                )
            )

    # --- downhill_pressure --------------------------------------------
    elif axis_key == "downhill_pressure":
        if FIRST_STEP >= 72 and _safe_float(derived.get("DRIVE_CREATE"), 50.0) >= 70:
            cands.append(
                _tag(
                    axis=axis_key,
                    tag_id="DP_FIRST_STEP_DOWNHILL",
                    tag="첫 스텝",
                    kind="PLUS",
                    priority=85,
                    text="첫 스텝이 빠르고 궤적이 낮아 수비를 한 번에 흔드는 장면이 잦다.",
                )
            )
        if _safe_float(derived.get("FIN_CONTACT"), 50.0) >= 70 and Strength >= 65:
            cands.append(
                _tag(
                    axis=axis_key,
                    tag_id="DP_CONTACT_FINISHER",
                    tag="컨택 피니시",
                    kind="PLUS",
                    priority=80,
                    text="컨택에서도 마무리가 남는다. 파울·보너스를 끌어낼 타입.",
                )
            )
        if pts >= 18.0 and usg >= 0.25:
            cands.append(
                _tag(
                    axis=axis_key,
                    tag_id="DP_USAGE_SCORING",
                    tag="고부담 득점",
                    kind="PLUS",
                    priority=75,
                    text=f"공격 비중이 크고도 득점 생산이 따라온다(PTS {pts:.1f}, USG {usg:.2f}).",
                )
            )

        if _safe_float(derived.get("DRIVE_CREATE"), 50.0) >= 70 and FIN_RIM <= 55:
            cands.append(
                _tag(
                    axis=axis_key,
                    tag_id="DP_FINISHING_BREAKS",
                    tag="피니시 흔들",
                    kind="MINUS",
                    priority=75,
                    text="제끼는 장면은 있는데, 림 근처 마무리가 흔들린다. 다음 단계는 피니시 정리.",
                )
            )
        if tov >= 3.0 and usg >= 0.25:
            cands.append(
                _tag(
                    axis=axis_key,
                    tag_id="DP_DRIVE_TOV_TAX",
                    tag="돌파 실수",
                    kind="MINUS",
                    priority=65,
                    text=f"돌파/창출은 하지만 실수도 늘어난다(TOV {tov:.1f}).",
                )
            )

        if _safe_float(derived.get("DRIVE_CREATE"), 50.0) >= 70 and SHOT_MID_PU <= 55 and SHOT_3_OD <= 55:
            cands.append(
                _tag(
                    axis=axis_key,
                    tag_id="DP_NO_PLAN_B",
                    tag="플랜B",
                    kind="QUESTION",
                    priority=55,
                    text="림이 막혔을 때 플랜B(풀업/킥아웃 타이밍)가 얼마나 준비되어 있나?",
                )
            )

    # --- shotmaking_complexity ----------------------------------------
    elif axis_key == "shotmaking_complexity":
        if SHOT_MID_PU >= 72 and usg >= 0.22:
            cands.append(
                _tag(
                    axis=axis_key,
                    tag_id="SM_PULLUP_MID",
                    tag="풀업 미드",
                    kind="PLUS",
                    priority=80,
                    text="풀업/원드리블 미드가 있다. 공격이 막힐 때도 득점 루트를 만든다.",
                )
            )
        if SHOT_3_OD >= 72 and tp_pct >= 0.33 and _conf_gte(confidence, "medium"):
            cands.append(
                _tag(
                    axis=axis_key,
                    tag_id="SM_OFFDRIBBLE_THREE",
                    tag="오프드리블 3",
                    kind="PLUS",
                    priority=85,
                    text="오프드리블 3까지 연결되는 레인지. 스크린이 얕아도 벌점.",
                )
            )
        if usg >= 0.26 and tsL >= 0.55:
            cands.append(
                _tag(
                    axis=axis_key,
                    tag_id="SM_LOAD_ADJUSTED_EFF",
                    tag="부담보정 효율",
                    kind="PLUS",
                    priority=75,
                    text=f"고사용률에서도 효율이 무너지지 않는다(USG {usg:.2f}, 부담보정 TS {ts_load_str}).",
                )
            )

        if (SHOT_MID_PU >= 70 or SHOT_3_OD >= 70) and ts_pct < 0.52:
            cands.append(
                _tag(
                    axis=axis_key,
                    tag_id="SM_SKILL_BUT_BAD_TASTE",
                    tag="셀렉션",
                    kind="MINUS",
                    priority=80,
                    text=f"무브는 있는데 효율이 낮다(TS {pct_str(ts_pct)}). 셀렉션/난이도 조절 필요.",
                )
            )
        if usg >= 0.25 and tov >= 3.2:
            cands.append(
                _tag(
                    axis=axis_key,
                    tag_id="SM_TOV_UNDER_LOAD",
                    tag="부담 실수",
                    kind="MINUS",
                    priority=70,
                    text=f"부담이 커질수록 실수가 늘어난다(TOV {tov:.1f}).",
                )
            )

        if usg >= 0.22 and (SHOT_3_OD >= 65 or SHOT_MID_PU >= 65) and height_in <= 73:
            cands.append(
                _tag(
                    axis=axis_key,
                    tag_id="SM_LENGTH_TEST",
                    tag="길이 테스트",
                    kind="QUESTION",
                    priority=50,
                    text="상대 길이가 좋아질 때도 같은 패턴이 통할지(릴리즈 공간 확보) 체크.",
                )
            )

    # --- playmaking_engine --------------------------------------------
    elif axis_key == "playmaking_engine":
        # Position aware assist thresholds
        assist_thr = 6.0 if pos_bucket == "G" else (4.0 if pos_bucket == "W" else 3.0)
        if ast >= assist_thr:
            cands.append(
                _tag(
                    axis=axis_key,
                    tag_id="PM_ASSIST_VOLUME",
                    tag="창출",
                    kind="PLUS",
                    priority=85,
                    text=f"볼을 만질 때 찬스가 생긴다(AST {ast:.1f}). 단순 연결이 아니라 '창출'로 이어짐.",
                )
            )
        atr = ast / max(tov, 0.5)
        if atr >= 2.2:
            cands.append(
                _tag(
                    axis=axis_key,
                    tag_id="PM_CLEAN_RATIO",
                    tag="깔끔한 결정",
                    kind="PLUS",
                    priority=80,
                    text=f"결정이 깔끔하다(AST/TOV {atr:.2f}).",
                )
            )
        if _safe_float(derived.get("PNR_READ"), 50.0) >= 72:
            cands.append(
                _tag(
                    axis=axis_key,
                    tag_id="PM_PNR_READER",
                    tag="PnR 리드",
                    kind="PLUS",
                    priority=75,
                    text="PnR에서 수비 반응을 읽는 능력이 보인다. 2차 창출로 확장 가능.",
                )
            )

        if tov >= 3.2 and atr <= 1.3:
            cands.append(
                _tag(
                    axis=axis_key,
                    tag_id="PM_TOV_RISK",
                    tag="턴오버 리스크",
                    kind="MINUS",
                    priority=85,
                    text=f"공을 오래 들면 사고가 난다(TOV {tov:.1f}). NBA 템포에서 더 벌어질 수 있음.",
                )
            )

        if ast >= 5.0 and usg < 0.20:
            cands.append(
                _tag(
                    axis=axis_key,
                    tag_id="PM_SYSTEM_DEPENDENT",
                    tag="시스템 의존",
                    kind="QUESTION",
                    priority=50,
                    text="현재 전술/역할 덕분인지, 상위 레벨에서도 같은 창출이 가능한지 크로스체크.",
                )
            )

    # --- processing_safety --------------------------------------------
    elif axis_key == "processing_safety":
        if tov <= 1.6:
            cands.append(
                _tag(
                    axis=axis_key,
                    tag_id="PS_LOW_TOV",
                    tag="사고 적음",
                    kind="PLUS",
                    priority=85,
                    text=f"사고가 적은 타입(TOV {tov:.1f}). 템포를 올려도 망가질 위험이 낮다.",
                )
            )
        if mpg >= 30.0:
            cands.append(
                _tag(
                    axis=axis_key,
                    tag_id="PS_COACH_TRUST_MINUTES",
                    tag="코치 신뢰",
                    kind="PLUS",
                    priority=60,
                    text=f"출전시간이 길다(MPG {mpg:.1f}). 코치가 '안심하고' 맡기는 타입일 가능성.",
                )
            )

        if tov >= 3.0:
            cands.append(
                _tag(
                    axis=axis_key,
                    tag_id="PS_TOV_HIGH",
                    tag="거친 처리",
                    kind="MINUS",
                    priority=85,
                    text=f"처리가 거칠다(TOV {tov:.1f}). 압박이 세면 리듬이 빨리 깨질 수 있음.",
                )
            )
        if pf >= 3.1:
            cands.append(
                _tag(
                    axis=axis_key,
                    tag_id="PS_FOUL_TROUBLE",
                    tag="파울 트러블",
                    kind="MINUS",
                    priority=80,
                    text=f"파울 트러블 위험(PF {pf:.1f}).",
                )
            )

        if games <= 10 or mpg <= 18.0:
            cands.append(
                _tag(
                    axis=axis_key,
                    tag_id="PS_SMALL_SAMPLE_ROLE",
                    tag="표본 부족",
                    kind="QUESTION",
                    priority=60,
                    text="표본/역할이 제한적. 더 큰 역할에서 안전성이 유지되는지 확인 필요.",
                )
            )

    # --- perimeter_containment ----------------------------------------
    elif axis_key == "perimeter_containment":
        if DEF_POA >= 72 and Agility >= 70:
            cands.append(
                _tag(
                    axis=axis_key,
                    tag_id="PC_POA_BASE",
                    tag="1선 버팀",
                    kind="PLUS",
                    priority=85,
                    text="앞을 지키는 기본이 있다. 1선에서 시간을 벌어줄 수 있는 타입.",
                )
            )
        if stl >= 1.5 and pf <= 2.6:
            cands.append(
                _tag(
                    axis=axis_key,
                    tag_id="PC_STEAL_WITH_DISCIPLINE",
                    tag="손질+규율",
                    kind="PLUS",
                    priority=65,
                    text=f"손질이 좋으면서도 무리한 파울은 적다(STL {stl:.1f}, PF {pf:.1f}).",
                )
            )

        if pf >= 3.2:
            cands.append(
                _tag(
                    axis=axis_key,
                    tag_id="PC_FOULY_SURVIVAL",
                    tag="파울로 생존",
                    kind="MINUS",
                    priority=85,
                    text=f"앞을 놓쳤을 때 파울로 끊는 경향(PF {pf:.1f}).",
                )
            )
        if Speed <= 55 or Agility <= 55:
            cands.append(
                _tag(
                    axis=axis_key,
                    tag_id="PC_SLOW_FEET",
                    tag="느린 발",
                    kind="MINUS",
                    priority=80,
                    text="발이 느려 1선에서 맞불이 어렵다.",
                )
            )

        if pos_bucket == "G" and Strength <= 55:
            cands.append(
                _tag(
                    axis=axis_key,
                    tag_id="PC_SCREEN_NAVIGATION_TEST",
                    tag="스크린 테스트",
                    kind="QUESTION",
                    priority=55,
                    text="스크린 네비게이션이 NBA에서도 통할지(힘에 밀리는지) 확인.",
                )
            )

    # --- team_defense_rim_support -------------------------------------
    elif axis_key == "team_defense_rim_support":
        if blk >= 1.4 and DEF_RIM >= 70:
            cands.append(
                _tag(
                    axis=axis_key,
                    tag_id="TR_RIM_PRESENCE",
                    tag="림 존재감",
                    kind="PLUS",
                    priority=85,
                    text=f"림 근처에서 존재감(BLK {blk:.1f}). 마지막에 한 번은 지워줄 타입.",
                )
            )
        if DEF_HELP >= 70:
            cands.append(
                _tag(
                    axis=axis_key,
                    tag_id="TR_ROTATION_IQ",
                    tag="로테 IQ",
                    kind="PLUS",
                    priority=75,
                    text="헬프/로테이션 위치 선정이 좋다. 팀수비를 망치지 않는 유형.",
                )
            )

        if blk <= 0.4 and DEF_RIM <= 55:
            cands.append(
                _tag(
                    axis=axis_key,
                    tag_id="TR_NO_RIM_IMPACT",
                    tag="림 임팩트 부족",
                    kind="MINUS",
                    priority=80,
                    text=f"림 근처에서 '지워주는' 장면이 적다(BLK {blk:.1f}).",
                )
            )
        if pos_bucket == "B" and reb <= 6.0:
            cands.append(
                _tag(
                    axis=axis_key,
                    tag_id="TR_BIG_WEAK_REBOUND",
                    tag="빅 리바 약",
                    kind="MINUS",
                    priority=70,
                    text=f"빅치고 리바운드가 약하다(REB {reb:.1f}).",
                )
            )

        if pos_bucket == "B" and DEF_RIM >= 65 and DEF_POA <= 55:
            cands.append(
                _tag(
                    axis=axis_key,
                    tag_id="TR_SCHEME_FIT",
                    tag="스킴 적합",
                    kind="QUESTION",
                    priority=45,
                    text="드랍/스위치 등 스킴에 따라 가치가 크게 달라질 타입.",
                )
            )

    # --- defensive_playmaking -----------------------------------------
    elif axis_key == "defensive_playmaking":
        if stocks >= 2.6:
            cands.append(
                _tag(
                    axis=axis_key,
                    tag_id="DP_STOCKS_VOLUME",
                    tag="공격권 전환",
                    kind="PLUS",
                    priority=85,
                    text=f"수비에서 공격권을 바꾸는 장면이 많다(STOCKS {stocks:.1f}).",
                )
            )
        if _safe_float(attrs.get("Pass Perception"), 50.0) >= 70 and DEF_STEAL >= 70:
            cands.append(
                _tag(
                    axis=axis_key,
                    tag_id="DP_PASSLANE_READER",
                    tag="패스레인",
                    kind="PLUS",
                    priority=75,
                    text="패스 라인을 읽고 치고 나오는 감각이 있다.",
                )
            )

        if stl >= 1.8 and pf >= 3.0:
            cands.append(
                _tag(
                    axis=axis_key,
                    tag_id="DP_GAMBLE_FOULY",
                    tag="도박성",
                    kind="MINUS",
                    priority=80,
                    text=f"스틸을 노리다 파울로 바뀌는 구간(PF {pf:.1f}). 도박성 조절 필요.",
                )
            )
        if stocks <= 1.0 and DEF_STEAL <= 55:
            cands.append(
                _tag(
                    axis=axis_key,
                    tag_id="DP_LOW_EVENTS",
                    tag="이벤트 적음",
                    kind="MINUS",
                    priority=70,
                    text="수비 이벤트가 적다. '바꾸는' 수비는 제한적일 수 있음.",
                )
            )

    # --- glass_physicality --------------------------------------------
    elif axis_key == "glass_physicality":
        reb_thr = 6.5 if pos_bucket == "G" else (7.5 if pos_bucket == "W" else 9.0)
        if reb >= reb_thr:
            cands.append(
                _tag(
                    axis=axis_key,
                    tag_id="GP_GLASS_IMPACT",
                    tag="리바 임팩트",
                    kind="PLUS",
                    priority=85,
                    text=f"리바운드로 포제션을 끝내거나 새로 만든다(REB {reb:.1f}).",
                )
            )
        if Strength >= 70 and _safe_float(derived.get("PHYSICAL"), 50.0) >= 70:
            cands.append(
                _tag(
                    axis=axis_key,
                    tag_id="GP_CONTACT_HOLD_GROUND",
                    tag="몸싸움",
                    kind="PLUS",
                    priority=65,
                    text="몸싸움에서 밀리지 않는다. NBA에서도 포지션을 빼앗기지 않을 확률이 높음.",
                )
            )

        if height_in >= 80 and weight_lb >= 220 and reb <= 6.0:
            cands.append(
                _tag(
                    axis=axis_key,
                    tag_id="GP_TOOLS_NOT_ON_BOX",
                    tag="사이즈 대비",
                    kind="MINUS",
                    priority=80,
                    text="사이즈 대비 리바 생산이 낮다. 박스아웃/타이밍 습관 확인 필요.",
                )
            )

        if REB_OR >= 70 and Speed <= 55:
            cands.append(
                _tag(
                    axis=axis_key,
                    tag_id="GP_TRANSITION_TRADEOFF",
                    tag="전환 트레이드오프",
                    kind="QUESTION",
                    priority=50,
                    text="공격 리바를 가는 대신 트랜지션 수비가 느려지는 구간이 있는지 확인.",
                )
            )

    # --- motor_compete -------------------------------------------------
    elif axis_key == "motor_compete":
        if mpg >= 32.0:
            cands.append(
                _tag(
                    axis=axis_key,
                    tag_id="MC_HEAVY_MINUTES_ENGINE",
                    tag="긴 출전 유지",
                    kind="PLUS",
                    priority=80,
                    text=f"출전시간이 길고 에너지 레벨이 유지된다(MPG {mpg:.1f}).",
                )
            )
        if WorkEthic >= 70 and Coachability >= 70:
            cands.append(
                _tag(
                    axis=axis_key,
                    tag_id="MC_GROWTH_HABITS",
                    tag="성장 습관",
                    kind="PLUS",
                    priority=60,
                    text="워크에틱/코치어빌리티가 좋다. 시스템에 들어가면 성장 속도가 빠를 타입.",
                )
            )

        # Mental risk: low habits or high ego
        ego = _safe_float(attrs.get("M_Ego"), 55.0)
        if WorkEthic <= 55 or Coachability <= 55 or ego >= 75:
            cands.append(
                _tag(
                    axis=axis_key,
                    tag_id="MC_MENTAL_RISK",
                    tag="습관 리스크",
                    kind="MINUS",
                    priority=80,
                    text="태도/습관 리스크 신호. 코칭을 받아들이는지 면밀한 확인 필요.",
                )
            )

    # --- runway --------------------------------------------------------
    elif axis_key == "runway":
        if age <= 19 or class_year == 1:
            cands.append(
                _tag(
                    axis=axis_key,
                    tag_id="RW_AGE_RUNWAY",
                    tag="어린 나이",
                    kind="PLUS",
                    priority=80,
                    text=f"나이가 어린 편이라 성장 곡선이 남아있다({age}세 / {class_year}학년).",
                )
            )
        if WorkEthic >= 70 and Ambition >= 70 and _safe_float(attrs.get("M_Adaptability"), 60.0) >= 70:
            cands.append(
                _tag(
                    axis=axis_key,
                    tag_id="RW_GROWTH_HABITS",
                    tag="성장 습관",
                    kind="PLUS",
                    priority=75,
                    text="성장 습관 신호가 좋다. '스킬을 쌓는' 타입일 가능성.",
                )
            )

        if age >= 22 or class_year >= 4:
            cands.append(
                _tag(
                    axis=axis_key,
                    tag_id="RW_OLDER_LIMITED_SCALE",
                    tag="성장 제한",
                    kind="MINUS",
                    priority=85,
                    text="나이/학년을 감안하면 성장 여지(스케일업)는 제한될 수 있다.",
                )
            )

        # Always add a dev-path question as a QUESTION tag (low priority)
        cands.append(
            _tag(
                axis=axis_key,
                tag_id="RW_DEV_PATH_DECISION",
                tag="개발 방향",
                kind="QUESTION",
                priority=50,
                text="성장 방향(3&D / 2차 핸들러 / 에너지 빅 등) 설계가 곧 롤을 결정할 타입.",
            )
        )

    # Fallback
    if not cands:
        cands.append(
            _tag(
                axis=axis_key,
                tag_id="DEFAULT",
                tag="관찰 필요",
                kind="META",
                priority=1,
                text="이번 달에는 확실한 신호가 아직 부족하다. 표본/역할이 늘어나는지 추가 관찰이 필요.",
            )
        )

    return _select_tags(cands)


# -----------------------------------------------------------------------------
# Higher-level helpers
# -----------------------------------------------------------------------------


def build_profile_tags(*, signal_summaries: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Small set of archetype-ish tags derived from signal tiers."""

    # Index by key
    idx: Dict[str, Mapping[str, Any]] = {str(s.get("key")): s for s in signal_summaries if isinstance(s, Mapping)}

    def _tier_at_least(k: str, min_tier: str) -> bool:
        t = str(idx.get(k, {}).get("tier") or "")
        return tier_score(t) >= tier_score(min_tier)

    def _conf_ok(k: str) -> bool:
        c = str(idx.get(k, {}).get("confidence") or "low")
        return c != "low"

    out: List[Dict[str, Any]] = []

    # Off-ball spacer
    if _tier_at_least("space_bending", "강점") and _conf_ok("space_bending"):
        out.append({"tag": "오프볼 스페이서", "confidence": "medium"})

    # 3&D wing-ish
    if _tier_at_least("space_bending", "평균권") and _tier_at_least("perimeter_containment", "강점") and _conf_ok("perimeter_containment"):
        out.append({"tag": "3&D 프로필", "confidence": "medium"})

    # Secondary creator
    if _tier_at_least("playmaking_engine", "강점") and _tier_at_least("processing_safety", "평균권"):
        out.append({"tag": "세컨더리 핸들러", "confidence": "medium"})

    # Rim-support big
    if _tier_at_least("team_defense_rim_support", "강점") and _tier_at_least("glass_physicality", "강점"):
        out.append({"tag": "림 서포트 빅", "confidence": "medium"})

    # Downhill slasher
    if _tier_at_least("downhill_pressure", "강점") and not _tier_at_least("space_bending", "평균권"):
        out.append({"tag": "드라이브 기반 득점", "confidence": "low" if not _conf_ok("downhill_pressure") else "medium"})

    # High ceiling
    if _tier_at_least("runway", "강점") and _conf_ok("runway"):
        out.append({"tag": "업사이드 프로필", "confidence": "medium"})

    # Deduplicate (stable)
    seen = set()
    uniq: List[Dict[str, Any]] = []
    for t in out:
        k = str(t.get("tag"))
        if not k or k in seen:
            continue
        seen.add(k)
        uniq.append(t)

    return uniq[:6]


def build_watchlist_questions(*, signals_payload: Sequence[Mapping[str, Any]], limit: int = 3) -> List[str]:
    """Pick top QUESTION tags across signals to form a watchlist."""
    cands: List[Tuple[int, str]] = []
    for sig in signals_payload:
        tags = sig.get("evidence_tags")
        if not isinstance(tags, list):
            continue
        for t in tags:
            if not isinstance(t, dict):
                continue
            if str(t.get("kind")) != "QUESTION":
                continue
            pr = int(t.get("priority") or 0)
            txt = str(t.get("text") or "").strip()
            if txt:
                cands.append((pr, txt))

    # Stable unique selection
    cands.sort(key=lambda x: x[0], reverse=True)
    seen = set()
    out: List[str] = []
    for _, txt in cands:
        if txt in seen:
            continue
        seen.add(txt)
        out.append(txt)
        if len(out) >= int(limit):
            break

    if not out:
        out = ["상위 레벨 수비 압박에서도 같은 신호가 유지되는지?", "역할이 커졌을 때도 결정/실수 관리가 되는지?", "강한 상대(상위 시드) 상대로도 생산이 유지되는지?"]
    return out[: int(limit)]


def build_delta_since_last(
    *,
    prev_signals: Optional[Sequence[Mapping[str, Any]]],
    curr_signals: Sequence[Mapping[str, Any]],
    limit: int = 3,
) -> Dict[str, Any]:
    """Generate small 'delta' bullets vs previous report (tier/confidence only)."""

    prev_idx: Dict[str, Mapping[str, Any]] = {}
    if prev_signals:
        for s in prev_signals:
            k = str(s.get("key") or "")
            if k:
                prev_idx[k] = s

    notes: List[str] = []

    for s in curr_signals:
        k = str(s.get("key") or "")
        if not k:
            continue
        prev = prev_idx.get(k)
        if not prev:
            continue
        t0 = str(prev.get("tier") or "")
        t1 = str(s.get("tier") or "")
        c0 = str(prev.get("confidence") or "")
        c1 = str(s.get("confidence") or "")

        if c0 and c1 and c0 != c1:
            # confidence improved?
            if _conf_gte(c1, c0):
                notes.append(f"{str(s.get('label') or k)} 신뢰도 상승({c0}→{c1})")
            else:
                notes.append(f"{str(s.get('label') or k)} 신뢰도 하락({c0}→{c1})")

        if t0 and t1 and t0 != t1:
            if tier_score(t1) > tier_score(t0):
                notes.append(f"{str(s.get('label') or k)} 평가 상향({t0}→{t1})")
            else:
                notes.append(f"{str(s.get('label') or k)} 평가 하향({t0}→{t1})")

    # Dedup + cap
    uniq: List[str] = []
    seen = set()
    for n in notes:
        if n in seen:
            continue
        seen.add(n)
        uniq.append(n)
        if len(uniq) >= int(limit):
            break

    return {"new_info": uniq}


def derive_college_notes(*, stats: Mapping[str, Any]) -> List[str]:
    """Small deterministic bullet notes from stat context (for LLM grounding)."""
    pts = float(_safe_float(stats.get("pts"), 0.0))
    usg = float(_safe_float(stats.get("usg"), 0.0))
    ts = float(_safe_float(stats.get("ts_pct"), 0.0))
    tov = float(_safe_float(stats.get("tov"), 0.0))
    pf = float(_safe_float(stats.get("pf"), 0.0))
    stl = float(_safe_float(stats.get("stl"), 0.0))
    blk = float(_safe_float(stats.get("blk"), 0.0))

    notes: List[str] = []

    if usg >= 0.26 and ts >= 0.58:
        notes.append("고사용률에서도 효율 유지")
    if usg >= 0.26 and ts < 0.52:
        notes.append("사용률 대비 효율 부담")
    if tov <= 1.5:
        notes.append("턴오버 관리 우수")
    if tov >= 3.2 and usg >= 0.24:
        notes.append("실수 부담(턴오버) 구간 존재")
    if pf >= 3.2:
        notes.append("파울 트러블 경향")
    if (stl + blk) >= 2.8:
        notes.append("수비 이벤트 생산")
    if pts >= 18.0 and usg < 0.20:
        notes.append("효율형 득점(낮은 부담 대비 생산)")

    return notes[:4]
