from __future__ import annotations

import math
import random
from collections import defaultdict
from typing import Any, Dict, List, Mapping, MutableMapping, Optional, Tuple

from ratings_2k import compute_ovr_proxy
from ratings_2k import _apply_body_caps as _apply_body_caps  # noqa: PLC2701
from ratings_2k import _apply_relationship_constraints as _apply_relationship_constraints  # noqa: PLC2701

from .mapping import ALL_CATEGORIES, CATEGORY_KEYS
from .types import intensity_multiplier, stable_seed
from . import config as cfg


def _clamp_int(x: float, lo: int = cfg.MIN_ATTR, hi: int = cfg.MAX_ATTR) -> int:
    try:
        v = int(round(float(x)))
    except Exception:
        v = int(lo)
    return int(max(lo, min(hi, v)))


def _norm_mental(v: Any) -> float:
    """Normalize mental attribute 25..99 -> 0..1."""
    try:
        x = float(v)
    except Exception:
        x = 60.0
    x = max(float(cfg.MIN_ATTR), min(float(cfg.MAX_ATTR), x))
    return (x - float(cfg.MIN_ATTR)) / (float(cfg.MAX_ATTR) - float(cfg.MIN_ATTR))


def _sigmoid(x: float) -> float:
    # Numerically stable enough for small magnitudes.
    return 1.0 / (1.0 + math.exp(-x))


def _age_growth_factor(age: int, peak_age: float) -> float:
    """0..1-ish factor: young -> high, after peak -> low."""
    a = float(age)
    # At age == peak_age -> 0.5
    raw = 1.0 / (1.0 + math.exp((a - float(peak_age)) / float(cfg.AGE_GROWTH_K)))
    # Keep some residual growth even after peak.
    return float(cfg.AGE_GROWTH_FLOOR) + (1.0 - float(cfg.AGE_GROWTH_FLOOR)) * raw


def _age_decline_factor(age: int, decline_start: float, late_decline: float) -> float:
    a = float(age)
    start = _sigmoid((a - float(decline_start)) / float(cfg.AGE_DECLINE_START_K))  # 0..1
    late = _sigmoid((a - float(late_decline)) / float(cfg.AGE_DECLINE_LATE_K))  # 0..1
    return start * (1.0 + float(cfg.AGE_DECLINE_LATE_MULT) * late)


def _cap_factor(cur_proxy: float, ceiling_proxy: float) -> float:
    room = max(0.0, float(ceiling_proxy) - float(cur_proxy))
    # Diminishing returns near the ceiling.
    return room / (room + float(cfg.CAP_ROOM_DAMP))


def _minutes_factor(minutes: float, *, ref: float) -> float:
    m = max(0.0, float(minutes))
    # 0 min -> ~MINUTES_FLOOR, heavy minutes -> ~MINUTES_FLOOR+MINUTES_SCALE
    return float(cfg.MINUTES_FLOOR) + float(cfg.MINUTES_SCALE) * math.sqrt(m / (m + float(ref)))


def _effective_category_weights(
    *,
    team_plan: Mapping[str, Any],
    player_plan: Mapping[str, Any],
) -> Dict[str, float]:
    # Start with baseline (balanced).
    w: Dict[str, float] = {c: 1.0 for c in ALL_CATEGORIES}

    focus = str(team_plan.get("focus") or "BALANCED").upper()
    if focus and focus != "BALANCED" and focus in w:
        w[focus] = float(cfg.TEAM_FOCUS_MULTIPLIER)
        for c in w:
            if c != focus:
                w[c] *= float(cfg.TEAM_NONFOCUS_MULTIPLIER)

    # Explicit team weights override the focus shaping.
    weights_raw = team_plan.get("weights")
    if isinstance(weights_raw, Mapping) and weights_raw:
        for k, v in weights_raw.items():
            kk = str(k).upper()
            if kk not in w:
                continue
            try:
                w[kk] = max(0.0, float(v))
            except Exception:
                continue

    # Player plan adds bias.
    primary = str(player_plan.get("primary") or "BALANCED").upper()
    secondary = str(player_plan.get("secondary") or "").upper()
    if primary and primary != "BALANCED" and primary in w:
        w[primary] += float(cfg.PLAYER_PRIMARY_BONUS)
    if secondary and secondary != "BALANCED" and secondary in w:
        w[secondary] += float(cfg.PLAYER_SECONDARY_BONUS)

    # Normalize.
    s = sum(max(0.0, x) for x in w.values())
    if s <= 0:
        return {c: 1.0 / len(ALL_CATEGORIES) for c in ALL_CATEGORIES}
    return {c: max(0.0, x) / s for c, x in w.items()}


DECLINE_WEIGHTS: Dict[str, float] = dict(cfg.DECLINE_WEIGHTS)


def _weighted_choice(rng: random.Random, items: List[Tuple[str, float]]) -> str:
    total = sum(max(0.0, w) for _k, w in items)
    if total <= 0:
        return items[0][0]
    r = rng.random() * total
    acc = 0.0
    for k, w in items:
        ww = max(0.0, float(w))
        acc += ww
        if r <= acc:
            return k
    return items[-1][0]


def _choose_key_for_positive(rng: random.Random, attrs: Mapping[str, Any], keys: List[str]) -> Optional[str]:
    scored = []
    for k in keys:
        try:
            v = float(attrs.get(k, 50.0))
        except Exception:
            v = 50.0
        headroom = max(0.0, float(cfg.MAX_ATTR) - v)
        if headroom <= 0.0:
            continue
        scored.append((k, max(0.25, headroom)))
    if not scored:
        return None
    return _weighted_choice(rng, scored)


def _choose_key_for_negative(rng: random.Random, attrs: Mapping[str, Any], keys: List[str]) -> Optional[str]:
    scored = []
    for k in keys:
        try:
            v = float(attrs.get(k, 50.0))
        except Exception:
            v = 50.0
        room = max(0.0, v - float(cfg.MIN_ATTR))
        if room <= 0.0:
            continue
        scored.append((k, max(0.25, room)))
    if not scored:
        return None
    return _weighted_choice(rng, scored)


def _apply_point_alloc(
    rng: random.Random,
    *,
    attrs: MutableMapping[str, Any],
    n_points: int,
    category_weights: Mapping[str, float],
    sign: int,
) -> Dict[str, int]:
    deltas: Dict[str, int] = defaultdict(int)
    cats = list(category_weights.items())
    if not cats:
        return {}
    for _ in range(int(max(0, n_points))):
        cat = _weighted_choice(rng, cats)
        keys = CATEGORY_KEYS.get(cat) or []
        if not keys:
            continue
        if sign > 0:
            k = _choose_key_for_positive(rng, attrs, keys)
        else:
            k = _choose_key_for_negative(rng, attrs, keys)
        if not k:
            continue
        deltas[k] += int(sign)
        # apply immediately so subsequent choices see updated headroom
        try:
            attrs[k] = _clamp_int(float(attrs.get(k, 50.0)) + float(sign))
        except Exception:
            attrs[k] = _clamp_int(50.0 + float(sign))
    return dict(deltas)


def apply_growth_tick(
    *,
    player_id: str,
    attrs: MutableMapping[str, Any],
    pos: str,
    age: int,
    height_in: int,
    minutes: float,
    growth_mult: float = 1.0,
    profile: Mapping[str, Any],
    team_plan: Mapping[str, Any],
    player_plan: Mapping[str, Any],
    tick_id: str,
    tick_kind: str,
) -> Dict[str, Any]:
    """Apply a single growth tick to attrs (in-place).

    Parameters
    ----------
    tick_kind:
        "offseason" or "monthly" (controls magnitude).

    growth_mult:
        Optional multiplier (0..1) applied to positive growth only.
        Used by the injury system to suppress growth while a player is OUT.

    Returns
    -------
    dict with summary + deltas.
    """
    pid = str(player_id)
    rng = random.Random(stable_seed("growth_tick", tick_kind, str(tick_id), pid))

    # Snapshot proxy before.
    try:
        cur_proxy = float(compute_ovr_proxy(attrs, pos=str(pos)))
    except Exception:
        cur_proxy = 60.0

    ceiling_proxy = float(profile.get("ceiling_proxy") or (cur_proxy + 8.0))
    peak_age = float(profile.get("peak_age") or 27.0)
    decline_start = float(profile.get("decline_start_age") or 31.0)
    late_decline = float(profile.get("late_decline_age") or 35.0)

    # Mentals.
    work = _norm_mental(attrs.get("M_WorkEthic"))
    coach = _norm_mental(attrs.get("M_Coachability"))
    amb = _norm_mental(attrs.get("M_Ambition"))
    loyal = _norm_mental(attrs.get("M_Loyalty"))
    ego = _norm_mental(attrs.get("M_Ego"))
    adapt = _norm_mental(attrs.get("M_Adaptability"))

    drive = float(cfg.DRIVE_BASE) + float(cfg.DRIVE_SCALE) * (
        float(cfg.DRIVE_W_WORK) * work
        + float(cfg.DRIVE_W_COACH) * coach
        + float(cfg.DRIVE_W_AMB) * amb
        + float(cfg.DRIVE_W_ADAPT) * adapt
    )
    drive = max(float(cfg.DRIVE_MIN), min(float(cfg.DRIVE_MAX), drive))

    stability = float(cfg.STABILITY_BASE) + float(cfg.STABILITY_SCALE) * (
        float(cfg.STABILITY_W_WORK) * work
        + float(cfg.STABILITY_W_COACH) * coach
        + float(cfg.STABILITY_W_LOYAL) * loyal
        + float(cfg.STABILITY_W_ADAPT) * adapt
    ) - float(cfg.STABILITY_EGO_PENALTY) * ego
    stability = max(float(cfg.STABILITY_MIN), min(float(cfg.STABILITY_MAX), stability))

    # Training multipliers.
    t_int = intensity_multiplier(team_plan.get("intensity"))
    p_int = intensity_multiplier(player_plan.get("intensity"))
    intensity_mult = float(cfg.TEAM_INTENSITY_SHARE) * t_int + float(cfg.PLAYER_INTENSITY_SHARE) * p_int

    # Age curve.
    g_age = _age_growth_factor(int(age), float(peak_age))
    d_age = _age_decline_factor(int(age), float(decline_start), float(late_decline))

    # Minutes curve.
    if str(tick_kind).lower() == "offseason":
        m_mult = _minutes_factor(minutes, ref=float(cfg.OFFSEASON_MINUTES_REF))
        base_pos = float(cfg.OFFSEASON_BASE_POS)
        base_neg = float(cfg.OFFSEASON_BASE_NEG)
    else:
        m_mult = _minutes_factor(minutes, ref=float(cfg.MONTHLY_MINUTES_REF))
        base_pos = float(cfg.MONTHLY_BASE_POS)
        base_neg = float(cfg.MONTHLY_BASE_NEG)

    # Cap.
    cap_mult = _cap_factor(cur_proxy, ceiling_proxy)

    # Stochasticity: low stability -> more variance.
    sigma = float(cfg.NOISE_SIGMA_BASE) + float(cfg.NOISE_SIGMA_SCALE) * (1.0 - stability)
    noise = max(float(cfg.NOISE_MULT_MIN), min(float(cfg.NOISE_MULT_MAX), rng.gauss(1.0, sigma)))

    # Positive & negative point budgets.
    try:
        gm = float(growth_mult)
    except Exception:
        gm = 1.0
    # Injury-driven growth suppression is expected to be in 0..1.
    # Clamp to avoid accidental amplification.
    gm = max(0.0, min(1.0, gm))
    pos_points_f = base_pos * g_age * drive * intensity_mult * m_mult * cap_mult * noise * gm

    # Work ethic reduces decline (maintenance).
    maintenance = float(cfg.MAINTENANCE_BASE) + float(cfg.MAINTENANCE_W_WORK) * work + float(cfg.MAINTENANCE_W_COACH) * coach
    neg_points_f = (
        base_neg
        * d_age
        * (float(cfg.DECLINE_MULT_BASE) - float(cfg.DECLINE_MULT_MAINTENANCE_SCALE) * maintenance)
        * (float(cfg.DECLINE_NOISE_BASE) + float(cfg.DECLINE_NOISE_SCALE) * rng.random())
    )

    # Convert to discrete "rating points".
    def _to_int_points(x: float) -> int:
        if x <= 0.0:
            return 0
        n = int(math.floor(x))
        frac = x - float(n)
        if rng.random() < frac:
            n += 1
        return int(max(0, n))

    pos_n = _to_int_points(pos_points_f)
    neg_n = _to_int_points(neg_points_f)

    # Apply allocations.
    category_weights = _effective_category_weights(team_plan=team_plan, player_plan=player_plan)

    # Work on a copy for point allocation decisions.
    tmp_attrs: Dict[str, Any] = dict(attrs)
    pos_deltas = _apply_point_alloc(rng, attrs=tmp_attrs, n_points=pos_n, category_weights=category_weights, sign=+1)
    neg_deltas = _apply_point_alloc(rng, attrs=tmp_attrs, n_points=neg_n, category_weights=DECLINE_WEIGHTS, sign=-1)

    # Merge deltas and apply to real attrs.
    merged: Dict[str, int] = defaultdict(int)
    for k, v in pos_deltas.items():
        merged[k] += int(v)
    for k, v in neg_deltas.items():
        merged[k] += int(v)

    changed_keys: List[str] = []
    for k, dv in merged.items():
        try:
            before = float(attrs.get(k, 50.0))
        except Exception:
            before = 50.0
        after = _clamp_int(before + float(dv))
        if after != int(round(before)):
            attrs[k] = int(after)
            changed_keys.append(k)

    # Apply realism constraints.
    try:
        _apply_body_caps(attrs, height_in=int(height_in))
    except Exception:
        pass
    try:
        _apply_relationship_constraints(attrs, pos=str(pos), height_in=int(height_in))
    except Exception:
        pass

    # Enforce ceiling in terms of OVR proxy by shaving off positive changes if needed.
    # (Only if we actually improved.)
    positive_keys = [k for k, dv in merged.items() if dv > 0]
    if positive_keys:
        try:
            new_proxy = float(compute_ovr_proxy(attrs, pos=str(pos)))
        except Exception:
            new_proxy = cur_proxy

        guard = 0
        while new_proxy > ceiling_proxy and guard < int(cfg.CEILING_SHAVE_GUARD) and positive_keys:
            guard += 1
            k = rng.choice(positive_keys)
            try:
                cur_v = int(attrs.get(k, 50) or 50)
            except Exception:
                cur_v = 50
            if cur_v <= int(cfg.MIN_ATTR):
                positive_keys = [x for x in positive_keys if x != k]
                continue
            attrs[k] = int(max(int(cfg.MIN_ATTR), cur_v - 1))
            try:
                new_proxy = float(compute_ovr_proxy(attrs, pos=str(pos)))
            except Exception:
                break

    # Final proxy after.
    try:
        final_proxy = float(compute_ovr_proxy(attrs, pos=str(pos)))
    except Exception:
        final_proxy = cur_proxy

    return {
        "player_id": pid,
        "tick_kind": str(tick_kind),
        "tick_id": str(tick_id),
        "age": int(age),
        "minutes": float(minutes),
        "growth_mult": float(gm),
        "proxy_before": float(cur_proxy),
        "proxy_after": float(final_proxy),
        "delta_proxy": float(final_proxy - cur_proxy),
        "pos_points": int(pos_n),
        "neg_points": int(neg_n),
        "deltas": dict(merged),
        "changed_keys": changed_keys,
    }
