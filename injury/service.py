from __future__ import annotations

"""Injury subsystem business logic.

This module defines three public entrypoints:
- prepare_game_injuries: between-game processing + pre-game availability/debuffs
- make_in_game_injury_hook: returns a callable used by match engine during simulation
- finalize_game_injuries: persist game injuries after the match ends

Commercial safety
-----------------
This system is designed to *never* hard-crash the sim. Callers are expected to
wrap calls with try/except (as done for fatigue).

Determinism
-----------
The match engine RNG stream should remain stable even when injury logic is added.
Therefore, in-game injury rolls use a deterministic hash-based RNG seeded from
(game_id, possession, clock, pid, etc.) rather than consuming the engine RNG.
"""

import datetime as _dt
import hashlib
import json
import logging
import math
import random
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

import game_time
import schema
from derived_formulas import compute_derived
from league_repo import LeagueRepo
from matchengine_v3.models import GameState, Player, TeamState
from ratings_2k import compute_ovr_proxy

from sim.roster_adapter import resolve_effective_schemes

from practice.service import resolve_practice_session
from practice import types as p_types

import fatigue.config as fat_cfg
import fatigue.repo as fat_repo

from training import config as training_config
from training import repo as training_repo
from training.types import intensity_multiplier

from . import catalog
from . import config
from . import repo as inj_repo
from .status import STATUS_HEALTHY, STATUS_OUT, STATUS_RETURNING, status_for_date
from .types import InjuryEvent, PreparedGameInjuries


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Small math helpers
# ---------------------------------------------------------------------------


def _clamp(x: float, lo: float, hi: float) -> float:
    try:
        v = float(x)
    except Exception:
        v = float(lo)
    if v < lo:
        return float(lo)
    if v > hi:
        return float(hi)
    return float(v)


def _clamp01(x: float) -> float:
    return _clamp(x, 0.0, 1.0)


def _lerp(a: float, b: float, t: float) -> float:
    tt = _clamp01(t)
    return float(a) + (float(b) - float(a)) * tt


def _sigmoid(z: float) -> float:
    # Numerically stable logistic-ish
    try:
        v = float(z)
    except Exception:
        v = 0.0
    if v >= 0:
        ez = math.exp(-v)
        return 1.0 / (1.0 + ez)
    ez = math.exp(v)
    return ez / (1.0 + ez)


def stable_seed(*parts: str) -> int:
    """Deterministic seed from string parts (stable across runs)."""
    h = hashlib.sha256("|".join([str(p) for p in parts]).encode("utf-8")).hexdigest()
    return int(h[:16], 16)


def _date_from_iso(date_iso: str) -> _dt.date:
    return _dt.date.fromisoformat(str(date_iso)[:10])


def _date_iso(d: _dt.date) -> str:
    return d.isoformat()


def _add_days(date_iso: str, days: int) -> str:
    d = _date_from_iso(date_iso)
    return _date_iso(d + _dt.timedelta(days=int(days)))


def _days_between(a_iso: str, b_iso: str) -> int:
    """Return (b - a) in days."""
    return (_date_from_iso(b_iso) - _date_from_iso(a_iso)).days


# Training intensity helpers
# - Player plan intensity still exists (growth/training system).
# - Team *practice* intensity is sourced from practice sessions.
# ---------------------------------------------------------------------------


def _plan_intensity_mult(plan: Optional[Mapping[str, Any]]) -> float:
    if not isinstance(plan, Mapping):
        return 1.0
    try:
        return float(intensity_multiplier(plan.get("intensity")))
    except Exception:
        return 1.0


def _blended_intensity_mult(*, team_mult: float, player_mult: float) -> float:
    """Blend team vs player training intensity.

    Uses training.config TEAM/PLAYER_INTENSITY_SHARE and normalizes if needed.
    """
    try:
        ts = float(training_config.TEAM_INTENSITY_SHARE)
    except Exception:
        ts = 0.6
    try:
        ps = float(training_config.PLAYER_INTENSITY_SHARE)
    except Exception:
        ps = 0.4
    total = ts + ps
    if total <= 0:
        return 1.0
    return float((ts * float(team_mult) + ps * float(player_mult)) / total)


# ---------------------------------------------------------------------------
# Fatigue -> energy estimator (used for training injuries + wear modifier)
# ---------------------------------------------------------------------------


def _rest_units(*, last_date: Optional[_dt.date], target_date: _dt.date) -> float:
    if last_date is None:
        return 0.0
    dd = (target_date - last_date).days
    if dd <= 0:
        return 0.0
    if dd == 1:
        return float(fat_cfg.OVERNIGHT_REST_UNITS)
    return float(fat_cfg.OVERNIGHT_REST_UNITS) + float(dd - 1)


def _combined_recovery_mult(*, endurance: float, age: int, intensity_mult: float) -> float:
    # EnduranceFactor = lerp(min,max, ENDURANCE/100)
    end_fac = _lerp(float(fat_cfg.ENDURANCE_FACTOR_MIN), float(fat_cfg.ENDURANCE_FACTOR_MAX), float(endurance) / 100.0)

    # AgeRecoveryFactor = 1 - AGE_REC_DROP_MAX * sigmoid((age-AGE_REC_START)/AGE_REC_K)
    age_fac = 1.0 - float(fat_cfg.AGE_REC_DROP_MAX) * _sigmoid((float(age) - float(fat_cfg.AGE_REC_START)) / float(fat_cfg.AGE_REC_K))

    # TrainingRecoveryFactor = intensity_mult ** (-TRAIN_REC_POW)
    try:
        train_fac = float(intensity_mult) ** (-float(fat_cfg.TRAIN_REC_POW))
    except Exception:
        train_fac = 1.0

    r = float(end_fac) * float(age_fac) * float(train_fac)
    return _clamp(r, float(fat_cfg.RECOVERY_MULT_MIN), float(fat_cfg.RECOVERY_MULT_MAX))


def _apply_rest_recovery(*, st: float, lt: float, rest_units: float, recovery_mult: float) -> Tuple[float, float]:
    try:
        st0 = max(0.0, float(st))
    except Exception:
        st0 = 0.0
    try:
        lt0 = max(0.0, float(lt))
    except Exception:
        lt0 = 0.0

    ru = max(0.0, float(rest_units))
    r = max(0.0, float(recovery_mult))

    st_rest = st0 * math.exp(-float(fat_cfg.ST_REC_RATE) * ru * r)
    lt_rest = lt0 * math.exp(-float(fat_cfg.LT_REC_RATE) * ru * r)
    return (_clamp01(st_rest), _clamp01(lt_rest))


def _energy_from_st_lt(*, st: float, lt: float) -> float:
    cond_raw = 1.0 - float(st) - float(fat_cfg.LT_WEIGHT) * float(lt)
    return _clamp(cond_raw, float(fat_cfg.START_ENERGY_MIN), 1.0)


def estimate_energy_and_wear_for_date(
    *,
    fatigue_state: Mapping[str, Any],
    derived: Mapping[str, float],
    age: int,
    intensity_mult: float,
    target_date: _dt.date,
) -> Tuple[float, float]:
    """Estimate (energy, lt_wear) on a given date based on fatigue SSOT state.

    This mirrors the between-game recovery logic (fatigue.prepare_game_fatigue).
    """
    try:
        st0 = float(fatigue_state.get("st", 0.0) or 0.0)
    except Exception:
        st0 = 0.0
    try:
        lt0 = float(fatigue_state.get("lt", 0.0) or 0.0)
    except Exception:
        lt0 = 0.0

    last_date_iso = fatigue_state.get("last_date")
    last_date: Optional[_dt.date] = None
    if last_date_iso:
        try:
            last_date = _dt.date.fromisoformat(str(last_date_iso)[:10])
        except Exception:
            last_date = None

    ru = _rest_units(last_date=last_date, target_date=target_date)
    endurance = float(derived.get("ENDURANCE", 50.0) or 50.0)
    rmult = _combined_recovery_mult(endurance=endurance, age=int(age), intensity_mult=float(intensity_mult))
    st_rest, lt_rest = _apply_rest_recovery(st=st0, lt=lt0, rest_units=ru, recovery_mult=rmult)
    energy = _energy_from_st_lt(st=st_rest, lt=lt_rest)
    return (float(energy), float(lt_rest))


# ---------------------------------------------------------------------------
# Hazard model
# ---------------------------------------------------------------------------


def _freq_mult(injury_freq: float) -> float:
    f = _clamp(float(injury_freq), 1.0, 10.0)
    return float(math.exp(float(config.FREQ_EXP_K) * (f - 5.0)))


def _fatigue_mult(*, energy: float, durability: float) -> float:
    e = _clamp(float(energy), 0.0, 1.0)
    dur = _clamp(float(durability), 1.0, 100.0)
    dur01 = dur / 100.0
    sens = _lerp(float(config.DUR_FATIGUE_MULT_MAX), float(config.DUR_FATIGUE_MULT_MIN), dur01)
    mult = 1.0 + float(config.FATIGUE_K) * float(sens) * ((1.0 - e) ** float(config.FATIGUE_POW))
    return float(min(float(config.FATIGUE_MULT_CAP), mult))


def _age_mult(age: int) -> float:
    a = int(age or 0)
    if a <= 0:
        return 1.0
    return 1.0 + float(config.AGE_MAX_MULT) * _sigmoid((float(a) - float(config.AGE_INFLECT)) / float(config.AGE_SCALE))


def _wear_mult(lt_wear: float) -> float:
    lt = _clamp01(float(lt_wear))
    mult = 1.0 + float(config.WEAR_LT_K) * lt
    return float(min(float(config.WEAR_MULT_CAP), mult))


def _training_mult(intensity_mult: float) -> float:
    try:
        return float(float(intensity_mult) ** float(config.TRAINING_INTENSITY_EXP))
    except Exception:
        return 1.0


def _reinjury_bonus_total(recent_counts: Mapping[str, int]) -> float:
    total = 0
    for v in (recent_counts or {}).values():
        try:
            total += int(v or 0)
        except Exception:
            continue
    bonus = float(config.REINJURY_BONUS_PER_PRIOR) * float(max(0, total))
    return float(min(float(config.REINJURY_BONUS_CAP), bonus))


def hazard_probability(
    *,
    base_lambda: float,
    dt: float,
    injury_freq: float,
    energy: float,
    durability: float,
    age: int,
    lt_wear: float,
    intensity_mult: float = 1.0,
    reinjury_recent_counts: Optional[Mapping[str, int]] = None,
    include_training_mult: bool = False,
) -> float:
    """Compute p = 1-exp(-lambda*dt) with modifiers."""
    lam = float(base_lambda)
    lam *= _freq_mult(injury_freq)
    lam *= _fatigue_mult(energy=energy, durability=durability)
    lam *= _age_mult(int(age))
    lam *= _wear_mult(float(lt_wear))

    if reinjury_recent_counts:
        lam *= (1.0 + _reinjury_bonus_total(reinjury_recent_counts))

    if include_training_mult:
        lam *= _training_mult(float(intensity_mult))

    lam *= float(config.GLOBAL_INJURY_MULT)

    # Safety: if dt is tiny or lambda is tiny, p ~ lambda*dt.
    dtv = max(0.0, float(dt))
    if lam <= 0.0 or dtv <= 0.0:
        return 0.0

    try:
        return float(1.0 - math.exp(-lam * dtv))
    except OverflowError:
        return 1.0


# ---------------------------------------------------------------------------
# Injury rolling (type/severity/effects)
# ---------------------------------------------------------------------------


def _weighted_choice(rng: random.Random, weights: Mapping[str, float]) -> str:
    items = [(str(k), float(v)) for k, v in (weights or {}).items() if float(v) > 0.0]
    if not items:
        return ""
    total = sum(w for _, w in items)
    if total <= 0:
        return items[0][0]
    r = rng.random() * total
    acc = 0.0
    for k, w in items:
        acc += w
        if r <= acc:
            return k
    return items[-1][0]


def _choose_body_part(
    *,
    rng: random.Random,
    pos: str,
    recent_counts: Mapping[str, int],
) -> str:
    base = catalog.body_part_weights_for_pos(pos)

    # Apply recurrence bias per body part.
    out: Dict[str, float] = {}
    for bp, w in base.items():
        c = 0
        try:
            c = int((recent_counts or {}).get(bp, 0) or 0)
        except Exception:
            c = 0
        bias = min(float(config.REINJURY_PART_BIAS_CAP), float(config.REINJURY_PART_BIAS_PER_COUNT) * float(max(0, c)))
        out[bp] = float(w) * (1.0 + bias)

    picked = _weighted_choice(rng, out)
    return catalog.normalize_body_part(picked)


def _choose_template(
    *,
    rng: random.Random,
    body_part: str,
    context: str,
) -> catalog.InjuryTemplate:
    temps = catalog.templates_for_body_part(body_part, context=context)
    if not temps:
        # Fallback: pick any template.
        temps = list(catalog.all_templates())
    weights = {t.injury_type: float(t.weight) for t in temps}
    choice = _weighted_choice(rng, weights)
    for t in temps:
        if t.injury_type == choice:
            return t
    return temps[0]


def _choose_severity_base(rng: random.Random, probs: Mapping[int, float]) -> int:
    items = [(int(k), float(v)) for k, v in (probs or {}).items() if float(v) > 0.0]
    if not items:
        return 2
    total = sum(v for _, v in items)
    if total <= 0:
        return int(items[0][0])
    r = rng.random() * total
    acc = 0.0
    for k, v in sorted(items, key=lambda x: x[0]):
        acc += v
        if r <= acc:
            return int(k)
    return int(items[-1][0])


def _severity_bump_prob(*, energy: float, age: int, lt_wear: float, context: str) -> float:
    e = _clamp(float(energy), 0.0, 1.0)
    lt = _clamp01(float(lt_wear))
    a = int(age or 0)

    fat_term = float(config.SEVERITY_BUMP_FAT_W) * ((1.0 - e) ** float(config.SEVERITY_BUMP_FAT_POW))
    age_term = 0.0
    if a > 0:
        age_term = float(config.SEVERITY_BUMP_AGE_W) * _sigmoid((float(a) - float(config.SEV_AGE_INFLECT)) / float(config.SEV_AGE_SCALE))
    lt_term = float(config.SEVERITY_BUMP_LT_W) * lt

    p = fat_term + age_term + lt_term
    if catalog.normalize_context(context) == catalog.CONTEXT_TRAINING:
        p *= float(config.TRAINING_SEVERITY_BUMP_MULT)
    return _clamp01(p)


def _maybe_bump_severity(
    *,
    rng: random.Random,
    severity: int,
    energy: float,
    age: int,
    lt_wear: float,
    context: str,
) -> int:
    sev = int(severity)
    if sev < 1:
        sev = 1
    if sev > 5:
        sev = 5

    if not bool(config.ENABLE_SEVERE_INJURIES) and sev >= int(config.SEVERE_THRESHOLD):
        # If severe injuries disabled, cap at threshold-1.
        return min(sev, int(config.SEVERE_THRESHOLD) - 1)

    p = _severity_bump_prob(energy=energy, age=age, lt_wear=lt_wear, context=context)

    # One-step bump.
    if rng.random() < p:
        sev = min(5, sev + 1)

    # Rare extra bump for truly catastrophic cases.
    if rng.random() < (p * 0.25):
        sev = min(5, sev + 1)

    if not bool(config.ENABLE_SEVERE_INJURIES) and sev >= int(config.SEVERE_THRESHOLD):
        sev = min(sev, int(config.SEVERE_THRESHOLD) - 1)

    # Multi-year injuries are optional.
    if not bool(config.ENABLE_MULTI_YEAR) and sev >= 5:
        # Keep tier 5 but ensure durations don't explode in service.
        return 5

    return sev


def _roll_days(rng: random.Random, lo_hi: Tuple[int, int]) -> int:
    lo, hi = int(lo_hi[0]), int(lo_hi[1])
    if hi < lo:
        lo, hi = hi, lo
    if hi <= lo:
        return max(0, lo)
    return int(rng.randint(max(0, lo), max(0, hi)))


def _age_temp_mult(age: int) -> float:
    a = int(age or 0)
    if a <= 0:
        return 1.0
    return 1.0 + float(config.TEMP_AGE_MAX_MULT) * _sigmoid((float(a) - float(config.TEMP_AGE_INFLECT)) / float(config.TEMP_AGE_SCALE))


def _perm_age_mult(age: int) -> float:
    a = int(age or 0)
    if a <= 0:
        return 1.0
    return 1.0 + float(config.PERM_AGE_MAX_MULT) * _sigmoid((float(a) - float(config.PERM_AGE_INFLECT)) / float(config.PERM_AGE_SCALE))


def _make_temp_debuff(
    *,
    rng: random.Random,
    template: catalog.InjuryTemplate,
    severity: int,
    age: int,
) -> Dict[str, int]:
    base = -int(config.TEMP_DROP_PER_SEV) * int(severity)
    age_mult = _age_temp_mult(age)

    out: Dict[str, int] = {}
    for k, w in (template.temp_attr_weights or {}).items():
        try:
            ww = float(w)
        except Exception:
            continue
        if ww <= 0:
            continue
        # Small randomness for variety (does not affect determinism if rng is deterministic).
        jitter = _lerp(0.85, 1.15, rng.random())
        drop = int(round(float(base) * ww * age_mult * jitter))
        # Keep debuffs meaningful but not ridiculous.
        drop = int(max(drop, -25))
        if drop != 0:
            out[str(k)] = int(drop)
    return out


def _perm_chance(severity: int, age: int) -> float:
    base = float(config.PERM_BASE_CHANCE_BY_SEV.get(int(severity), 0.0))
    p = base * _perm_age_mult(age)
    return _clamp01(p)


def _make_perm_drop(
    *,
    rng: random.Random,
    template: catalog.InjuryTemplate,
    severity: int,
    age: int,
) -> Dict[str, int]:
    sev = int(severity)
    if sev < 3:
        return {}

    if rng.random() >= _perm_chance(sev, age):
        return {}

    out: Dict[str, int] = {}
    for k, w in (template.perm_attr_weights or {}).items():
        try:
            ww = float(w)
        except Exception:
            continue
        if ww <= 0:
            continue
        mag = rng.randint(int(config.PERM_DROP_MIN), int(config.PERM_DROP_MAX))
        # Scale magnitude by severity and weight.
        scaled = int(round(float(mag) * (0.75 + 0.18 * float(sev)) * ww))
        scaled = int(_clamp(scaled, 1, 12))
        out[str(k)] = int(scaled)
    return out


def roll_injury_event(
    *,
    rng: random.Random,
    game_or_day_id: str,
    player_id: str,
    team_id: str,
    season_year: int,
    date_iso: str,
    context: str,
    pos: str,
    injury_freq: float,
    durability: float,
    energy: float,
    age: int,
    lt_wear: float,
    recent_counts_by_part: Mapping[str, int],
    max_severity: Optional[int] = None,
    quarter: Optional[int] = None,
    clock_sec: Optional[int] = None,
    game_id: Optional[str] = None,
) -> InjuryEvent:
    """Roll a concrete InjuryEvent.

    The caller is responsible for deciding that an injury occurs (probability test).
    """
    ctx = catalog.normalize_context(context)

    # Body part selection includes recurrence bias.
    body_part = _choose_body_part(rng=rng, pos=pos, recent_counts=recent_counts_by_part)

    # Select template within body part.
    template = _choose_template(rng=rng, body_part=body_part, context=ctx)

    # Base severity from template distribution, then bump by fatigue/age/wear.
    sev0 = _choose_severity_base(rng, template.severity_probs)
    sev = _maybe_bump_severity(rng=rng, severity=sev0, energy=energy, age=age, lt_wear=lt_wear, context=ctx)

    if max_severity is not None:
        try:
            sev = min(int(sev), int(max_severity))
        except Exception:
            pass

    # Duration + returning.
    dur_range = template.duration_days.get(sev) or catalog.DEFAULT_DURATION_DAYS_BY_SEV.get(sev) or (7, 21)
    ret_range = template.returning_days.get(sev) or catalog.DEFAULT_RETURNING_DAYS_BY_SEV.get(sev) or (7, 21)

    duration_days = _roll_days(rng, dur_range)
    returning_days = _roll_days(rng, ret_range)

    out_until = _add_days(date_iso, duration_days)
    returning_until = _add_days(out_until, returning_days)

    temp_debuff = _make_temp_debuff(rng=rng, template=template, severity=sev, age=age)
    perm_drop = _make_perm_drop(rng=rng, template=template, severity=sev, age=age)

    # Stable injury_id: ensures idempotency if a sim is re-run.
    iid = f"{game_or_day_id}:{player_id}:{template.injury_type}:{sev}:{duration_days}:{returning_days}"

    return InjuryEvent(
        injury_id=str(iid),
        player_id=str(player_id),
        team_id=str(team_id).upper(),
        season_year=int(season_year),
        date=str(date_iso)[:10],
        context=ctx,
        game_id=str(game_id) if game_id is not None else None,
        quarter=int(quarter) if quarter is not None else None,
        clock_sec=int(clock_sec) if clock_sec is not None else None,
        body_part=str(template.body_part),
        injury_type=str(template.injury_type),
        severity=int(sev),
        duration_days=int(duration_days),
        out_until_date=str(out_until),
        returning_days=int(returning_days),
        returning_until_date=str(returning_until),
        temp_debuff=temp_debuff,
        perm_drop=perm_drop,
    )


# ---------------------------------------------------------------------------
# Player attribute update (permanent drops)
# ---------------------------------------------------------------------------


def _json_loads(value: Any) -> Dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    try:
        return json.loads(value)
    except Exception:
        return {}


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str)


def _apply_perm_drop_to_player_row(
    cur,
    *,
    player_id: str,
    perm_drop: Mapping[str, int],
    now_iso: str,
) -> None:
    """Apply permanent attribute drops to players.attrs_json and update ovr."""
    if not perm_drop:
        return

    row = cur.execute(
        "SELECT attrs_json, pos, age, height_in, weight_lb, ovr FROM players WHERE player_id=?;",
        (str(player_id),),
    ).fetchone()
    if not row:
        return

    attrs = _json_loads(row[0])
    if not isinstance(attrs, dict):
        attrs = {}

    # Apply drops.
    for k, v in perm_drop.items():
        key = str(k)
        try:
            drop = int(v)
        except Exception:
            continue
        if drop <= 0:
            continue
        try:
            old = int(round(float(attrs.get(key, 50))))
        except Exception:
            old = 50
        new = max(int(training_config.MIN_ATTR), old - int(drop))
        attrs[key] = int(new)

    # Recompute OVR proxy.
    try:
        pos = str(row[1] or "G")
    except Exception:
        pos = "G"

    try:
        new_ovr = int(round(float(compute_ovr_proxy(attrs, pos=pos))))
    except Exception:
        # Fallback to existing.
        try:
            new_ovr = int(row[5] or 0)
        except Exception:
            new_ovr = 0

    new_ovr = int(max(0, min(99, new_ovr)))

    cur.execute(
        "UPDATE players SET attrs_json=?, ovr=?, updated_at=? WHERE player_id=?;",
        (_json_dumps(attrs), int(new_ovr), str(now_iso), str(player_id)),
    )


# ---------------------------------------------------------------------------
# Between-game processing: prepare
# ---------------------------------------------------------------------------


def _load_recent_counts_by_part(
    cur,
    *,
    player_ids: Sequence[str],
    since_date_iso: str,
) -> Dict[str, Dict[str, int]]:
    """Load recent injury counts per body part for players since a cutoff date."""
    ids = [str(pid) for pid in player_ids if str(pid)]
    if not ids:
        return {}
    placeholders = ",".join(["?"] * len(ids))
    rows = cur.execute(
        f"""
        SELECT player_id, body_part, COUNT(*)
        FROM injury_events
        WHERE player_id IN ({placeholders})
          AND date >= ?
        GROUP BY player_id, body_part;
        """,
        ids + [str(since_date_iso)[:10]],
    ).fetchall()

    out: Dict[str, Dict[str, int]] = {}
    for r in rows:
        pid = str(r[0])
        bp = catalog.normalize_body_part(str(r[1]))
        try:
            c = int(r[2] or 0)
        except Exception:
            c = 0
        out.setdefault(pid, {})[bp] = int(c)
    return out


def _scaled_returning_debuff(state: Mapping[str, Any], *, on_date_iso: str) -> Dict[str, float]:
    """Return a scaled temporary debuff dict for returning players."""
    temp = state.get("temp_debuff") or {}
    if not isinstance(temp, Mapping) or not temp:
        return {}

    out_until = state.get("out_until_date")
    returning_until = state.get("returning_until_date")
    if not out_until or not returning_until:
        return {}

    try:
        out_until_iso = str(out_until)[:10]
        returning_until_iso = str(returning_until)[:10]
        on_iso = str(on_date_iso)[:10]
    except Exception:
        return {}

    total = max(1, _days_between(out_until_iso, returning_until_iso))
    remaining = max(0, _days_between(on_iso, returning_until_iso))

    # Full debuff on first day back (on_date==out_until). Fade to 0 at returning_until.
    scale = float(remaining) / float(total)
    scale = _clamp01(scale)

    out: Dict[str, float] = {}
    for k, v in temp.items():
        try:
            base = float(v)
        except Exception:
            continue
        if base == 0:
            continue
        out[str(k)] = float(base) * scale
    return out


def _default_injury_state(*, player_id: str, team_id: str, game_date_iso: str) -> Dict[str, Any]:
    return {
        "player_id": str(player_id),
        "team_id": str(team_id).upper(),
        "status": "HEALTHY",
        "injury_id": None,
        "start_date": None,
        "out_until_date": None,
        "returning_until_date": None,
        "body_part": None,
        "injury_type": None,
        "severity": 0,
        "temp_debuff": {},
        "perm_drop": {},
        "reinjury_count": {},
        "last_processed_date": str(game_date_iso)[:10],
    }


def prepare_game_injuries(
    repo: LeagueRepo,
    *,
    game_id: str,
    game_date_iso: str,
    season_year: int,
    home_team_id: str,
    away_team_id: str,
    home_tactics: Optional[Mapping[str, Any]] = None,
    away_tactics: Optional[Mapping[str, Any]] = None,
) -> PreparedGameInjuries:
    """Prepare injury availability + returning debuffs for a game.

    Side effects:
    - Processes training-day injuries between a player's last_processed_date and game_date.
    - Persists updated player_injury_state and appends injury_events for training injuries.

    Returns:
        PreparedGameInjuries used for roster building and in-game injury hook.
    """
    gdi = game_time.require_date_iso(game_date_iso, field="game_date_iso")
    gdate = _date_from_iso(gdi)

    gid = str(game_id).strip()
    if not gid:
        raise ValueError("prepare_game_injuries: game_id is empty")

    hid = str(schema.normalize_team_id(home_team_id)).upper()
    aid = str(schema.normalize_team_id(away_team_id)).upper()

    # Resolve effective schemes for practice-session defaults.
    # This mirrors readiness.service so CPU coach presets are respected.
    try:
        home_off_scheme, home_def_scheme = resolve_effective_schemes(hid, home_tactics)
    except Exception:
        home_off_scheme, home_def_scheme = ("", "")
    try:
        away_off_scheme, away_def_scheme = resolve_effective_schemes(aid, away_tactics)
    except Exception:
        away_off_scheme, away_def_scheme = ("", "")

    # Collect roster rows for both teams (active only).
    home_roster = repo.get_team_roster(hid)
    away_roster = repo.get_team_roster(aid)

    # Roster PID lists (stable order) for practice-session participant autofill.
    home_roster_pids: List[str] = [
        str(schema.normalize_player_id(r.get("player_id"), strict=False))
        for r in (home_roster or [])
        if r.get("player_id")
    ]
    away_roster_pids: List[str] = [
        str(schema.normalize_player_id(r.get("player_id"), strict=False))
        for r in (away_roster or [])
        if r.get("player_id")
    ]

    def _meta_from_rows(team_id: str, rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            pid = str(schema.normalize_player_id(r.get("player_id")))
            attrs = r.get("attrs") or {}
            try:
                derived = compute_derived(attrs)
            except Exception:
                derived = {}
            try:
                age = int(r.get("age") or attrs.get("Age") or 0)
            except Exception:
                age = 0
            pos = str(r.get("pos") or attrs.get("POS") or attrs.get("Position") or "G")
            out[pid] = {
                "team_id": str(team_id).upper(),
                "player_id": pid,
                "pos": pos,
                "age": int(age),
                "attrs": dict(attrs) if isinstance(attrs, dict) else {},
                "derived": dict(derived) if isinstance(derived, dict) else {},
            }
        return out

    meta_by_pid: Dict[str, Dict[str, Any]] = {}
    meta_by_pid.update(_meta_from_rows(hid, home_roster))
    meta_by_pid.update(_meta_from_rows(aid, away_roster))

    all_pids: List[str] = list(meta_by_pid.keys())

    # Prepare return payload structures.
    unavailable_by_team: Dict[str, Set[str]] = {hid: set(), aid: set()}
    attrs_mods_by_pid: Dict[str, Dict[str, float]] = {}
    lt_wear_by_pid: Dict[str, float] = {}
    reinjury_counts_by_pid: Dict[str, Dict[str, int]] = {}
    training_events: List[InjuryEvent] = []

    # Determine recency cutoff.
    since = gdate - _dt.timedelta(days=int(config.REINJURY_RECENCY_DAYS))
    since_iso = _date_iso(since)

    now_iso = game_time.utc_like_from_date_iso(gdi, field="game_date_iso")

    with repo.transaction() as cur:
        # Load injury SSOT state.
        states = inj_repo.get_player_injury_states(cur, all_pids)

        # Load fatigue SSOT states (best-effort; injury system can run without fatigue).
        try:
            fat_states = fat_repo.get_player_fatigue_states(cur, all_pids)
        except Exception:
            fat_states = {}

        # Player training plans (individual intensity is still part of the growth system).
        player_int_cache: Dict[str, float] = {}
        for pid in all_pids:
            plan, _is_user = training_repo.get_player_training_plan(cur, player_id=pid, season_year=int(season_year))
            player_int_cache[pid] = _plan_intensity_mult(plan)

        # Practice session cache for this preparation call.
        # Keyed by (team_id, date_iso). Prevents repeated DB reads/writes when
        # looping players across the same day.
        practice_session_cache: Dict[Tuple[str, str], Dict[str, Any]] = {}

        def _get_practice_session(team_id: str, *, date_iso: str) -> Dict[str, Any]:
            day_iso = str(date_iso)[:10]
            key = (str(team_id).upper(), day_iso)
            cached = practice_session_cache.get(key)
            if cached is not None:
                return cached

            tid = str(team_id).upper()
            if tid == hid:
                roster_raw = home_roster_pids
                fb_off, fb_def = home_off_scheme, home_def_scheme
            else:
                roster_raw = away_roster_pids
                fb_off, fb_def = away_off_scheme, away_def_scheme

            # Exclude players who are already OUT on this day from scrimmage participant autofill.
            # SSOT: OUT status is derived from injury.state via status_for_date.
            roster_pids: List[str] = []
            for pid in roster_raw:
                st = states.get(str(pid))
                if isinstance(st, dict):
                    try:
                        if status_for_date(st, on_date_iso=day_iso) == STATUS_OUT:
                            continue
                    except Exception:
                        pass
                roster_pids.append(str(pid))

            try:
                d2g = int((gdate - _date_from_iso(day_iso)).days)
                if d2g < 0:
                    d2g = 0
            except Exception:
                d2g = None

            sess = resolve_practice_session(
                cur,
                team_id=tid,
                season_year=int(season_year),
                date_iso=day_iso,
                fallback_off_scheme=fb_off or None,
                fallback_def_scheme=fb_def or None,
                roster_pids=roster_pids,
                days_to_next_game=d2g,
                now_iso=now_iso,
            )
            practice_session_cache[key] = sess
            return sess

        def _effective_team_practice_mult(
            *,
            team_id: str,
            player_id: str,
            fatigue_last_date: Optional[_dt.date],
            target_date: _dt.date,
        ) -> float:
            """Geometric-mean of per-day practice intensity between fatigue_last_date and target_date.

            This is used as a stable approximation for recovery and wear estimation.
            """
            if fatigue_last_date is None:
                return 1.0
            dd = (target_date - fatigue_last_date).days
            if dd <= 1:
                return 1.0

            # Safety/perf: cap the lookback window so long gaps don't create
            # huge volumes of auto practice rows.
            max_off_days = 21
            if dd > (max_off_days + 1):
                fatigue_last_date = target_date - _dt.timedelta(days=int(max_off_days + 1))
                dd = int(max_off_days + 1)

            logs: List[float] = []
            for step in range(1, dd):
                day = fatigue_last_date + _dt.timedelta(days=int(step))
                if day >= target_date:
                    break
                day_iso = _date_iso(day)
                sess = _get_practice_session(str(team_id).upper(), date_iso=day_iso)
                mult = float(p_types.intensity_for_pid(sess, str(player_id)))
                logs.append(math.log(max(1e-6, mult)))

            if not logs:
                return 1.0
            return float(math.exp(sum(logs) / float(len(logs))))

        # Recent injury counts per body part for recurrence bias.
        reinjury_counts_by_pid = _load_recent_counts_by_part(cur, player_ids=all_pids, since_date_iso=since_iso)

        # --- Normalize + ensure defaults for all players involved ---
        for pid, meta in meta_by_pid.items():
            tid = str(meta.get("team_id") or "").upper()
            st = states.get(pid)
            if not isinstance(st, dict):
                st = _default_injury_state(player_id=pid, team_id=tid, game_date_iso=gdi)
                states[pid] = st

            # Always keep team_id current (trades).
            st["team_id"] = tid

            # If last_processed_date is missing, set to current game date (do not backfill).
            if not st.get("last_processed_date"):
                st["last_processed_date"] = gdi

        # --- Process training injuries on off-days between last_processed_date and game_date ---
        # We process per-player because last_processed_date is per player.
        # This keeps behavior stable even if a player was traded or missed previous prep calls.
        for pid, meta in meta_by_pid.items():
            st = states.get(pid) or {}
            if not isinstance(st, dict):
                continue

            last_proc = st.get("last_processed_date")
            if not last_proc:
                st["last_processed_date"] = gdi
                continue

            try:
                last_d = _date_from_iso(str(last_proc))
            except Exception:
                st["last_processed_date"] = gdi
                continue

            # We process days (last_d+1) .. (gdate-1).
            delta_days = (gdate - last_d).days
            if delta_days <= 1:
                # back-to-back or same-day; nothing to process
                st["last_processed_date"] = gdi
                continue

            tid = str(meta.get("team_id") or "").upper()
            player_mult = float(player_int_cache.get(pid, 1.0))

            attrs = meta.get("attrs") or {}
            derived = meta.get("derived") or {}
            try:
                age_i = int(meta.get("age") or 0)
            except Exception:
                age_i = 0

            # InjuryFreq and Durability inputs.
            try:
                injury_freq = float(attrs.get("I_InjuryFreq", 5.0) or 5.0)
            except Exception:
                injury_freq = 5.0
            try:
                durability = float(attrs.get("Overall Durability", attrs.get("Durability", 70.0)) or 70.0)
            except Exception:
                durability = 70.0

            # Recent counts for risk bonus + body part bias.
            recent_counts = reinjury_counts_by_pid.get(pid, {})

            # Fatigue state for energy estimation.
            fstate = fat_states.get(pid) or {}

            # Iterate training days.
            for step in range(1, delta_days):
                day = last_d + _dt.timedelta(days=int(step))
                day_iso = _date_iso(day)

                # Skip the game day itself (exclusive end).
                if day >= gdate:
                    break

                # If player is already OUT on this day, skip.
                status_on_day = status_for_date(st, on_date_iso=day_iso)
                if status_on_day == STATUS_OUT:
                    continue

                # Team soft-safety: if available players would fall too low, suppress training injuries.
                team_players = [p for p, m in meta_by_pid.items() if str(m.get("team_id") or "").upper() == tid]
                available = 0
                for tpid in team_players:
                    tst = states.get(tpid) or {}
                    if not isinstance(tst, dict):
                        available += 1
                        continue
                    if status_for_date(tst, on_date_iso=day_iso) != STATUS_OUT:
                        available += 1
                if available <= int(config.MIN_AVAILABLE_PLAYERS_SOFT):
                    continue

                # Practice session intensity for this day.
                # NOTE: Scrimmage can mark *non-participants* as REST.
                sess_raw = _get_practice_session(tid, date_iso=day_iso)
                sess = p_types.normalize_session(sess_raw)
                eff_type = str(sess.get("type") or "").upper()
                if eff_type == "SCRIMMAGE":
                    pset = set(sess.get("participant_pids") or [])
                    if str(pid) not in pset:
                        eff_type = str(sess.get("non_participant_type") or "RECOVERY").upper()
                if eff_type == "REST":
                    continue

                team_mult_day = float(p_types.intensity_for_pid(sess, pid))
                intensity_mult_day = _blended_intensity_mult(team_mult=team_mult_day, player_mult=float(player_mult))

                # Estimate energy and wear for the day (recovery since last fatigue last_date).
                try:
                    energy_day, lt_wear_day = estimate_energy_and_wear_for_date(
                        fatigue_state=fstate,
                        derived=derived,
                        age=age_i,
                        intensity_mult=float(intensity_mult_day),
                        target_date=day,
                    )
                except Exception:
                    energy_day, lt_wear_day = (1.0, 0.0)

                p_day = hazard_probability(
                    base_lambda=float(config.BASE_TRAINING_HAZARD_PER_DAY),
                    dt=1.0,
                    injury_freq=injury_freq,
                    energy=energy_day,
                    durability=durability,
                    age=age_i,
                    lt_wear=lt_wear_day,
                    intensity_mult=float(intensity_mult_day),
                    reinjury_recent_counts=recent_counts,
                    include_training_mult=True,
                )

                if p_day <= 0.0:
                    continue

                # Deterministic RNG for this player/day.
                rr = random.Random(stable_seed("TRAIN_INJ", gid, day_iso, tid, pid))
                if rr.random() >= p_day:
                    continue

                # Roll the injury itself.
                ev = roll_injury_event(
                    rng=rr,
                    game_or_day_id=f"TR:{gid}:{day_iso}",
                    player_id=pid,
                    team_id=tid,
                    season_year=int(season_year),
                    date_iso=day_iso,
                    context=catalog.CONTEXT_TRAINING,
                    pos=str(meta.get("pos") or "G"),
                    injury_freq=injury_freq,
                    durability=durability,
                    energy=energy_day,
                    age=age_i,
                    lt_wear=lt_wear_day,
                    recent_counts_by_part=recent_counts,
                )

                training_events.append(ev)

                # Apply permanent drop immediately for training injuries (no finalize step).
                if ev.perm_drop:
                    try:
                        _apply_perm_drop_to_player_row(cur, player_id=pid, perm_drop=ev.perm_drop, now_iso=now_iso)
                    except Exception:
                        logger.warning("INJURY_PERM_DROP_APPLY_FAILED pid=%s", pid, exc_info=True)

                # Update injury state to OUT.
                st.update(
                    {
                        "status": "OUT",
                        "injury_id": ev.injury_id,
                        "start_date": ev.date,
                        "out_until_date": ev.out_until_date,
                        "returning_until_date": ev.returning_until_date,
                        "body_part": ev.body_part,
                        "injury_type": ev.injury_type,
                        "severity": int(ev.severity),
                        "temp_debuff": dict(ev.temp_debuff),
                        "perm_drop": dict(ev.perm_drop),
                    }
                )

                # Update reinjury counts (state-level cumulative counts).
                rcount = st.get("reinjury_count")
                if not isinstance(rcount, dict):
                    rcount = {}
                bp = catalog.normalize_body_part(ev.body_part)
                rcount[bp] = int(rcount.get(bp, 0) or 0) + 1
                st["reinjury_count"] = rcount

                # Also update recent_counts in-memory for subsequent rolls.
                recent_counts = dict(recent_counts or {})
                recent_counts[bp] = int(recent_counts.get(bp, 0) or 0) + 1
                reinjury_counts_by_pid[pid] = recent_counts

                # After a training injury occurs, stop processing further days for this player.
                break

            # Mark processing complete up to game date.
            st["last_processed_date"] = gdi

        # --- Normalize state for game date and build output maps ---
        for pid, meta in meta_by_pid.items():
            tid = str(meta.get("team_id") or "").upper()
            st = states.get(pid) or {}
            if not isinstance(st, dict):
                continue

            status = status_for_date(st, on_date_iso=gdi)
            st["status"] = status

            if status == STATUS_HEALTHY:
                # Clear current injury payload; history remains in injury_events.
                st.update(
                    {
                        "injury_id": None,
                        "start_date": None,
                        "out_until_date": None,
                        "returning_until_date": None,
                        "body_part": None,
                        "injury_type": None,
                        "severity": 0,
                        "temp_debuff": {},
                        "perm_drop": {},
                    }
                )
            elif status == STATUS_OUT:
                unavailable_by_team.setdefault(tid, set()).add(pid)
            elif status == STATUS_RETURNING:
                mods = _scaled_returning_debuff(st, on_date_iso=gdi)
                if mods:
                    attrs_mods_by_pid[pid] = mods

            # Estimate wear at game date (for in-game hazard multiplier).
            fstate = fat_states.get(pid) or {}
            derived = meta.get("derived") or {}
            try:
                age_i = int(meta.get("age") or 0)
            except Exception:
                age_i = 0
            player_mult = float(player_int_cache.get(pid, 1.0))

            fatigue_last_date = None
            try:
                ld = fstate.get("last_date")
                if ld:
                    fatigue_last_date = _date_from_iso(str(ld))
            except Exception:
                fatigue_last_date = None

            team_mult = _effective_team_practice_mult(
                team_id=tid,
                player_id=pid,
                fatigue_last_date=fatigue_last_date,
                target_date=gdate,
            )
            intensity_mult = _blended_intensity_mult(team_mult=float(team_mult), player_mult=float(player_mult))
            try:
                _energy, lt_wear = estimate_energy_and_wear_for_date(
                    fatigue_state=fstate,
                    derived=derived,
                    age=age_i,
                    intensity_mult=float(intensity_mult),
                    target_date=gdate,
                )
            except Exception:
                lt_wear = 0.0
            lt_wear_by_pid[pid] = float(lt_wear)

        # Persist training events.
        try:
            inj_repo.insert_injury_events(cur, [e.to_row() for e in training_events], now=now_iso)
        except Exception:
            logger.warning("INJURY_TRAINING_EVENTS_INSERT_FAILED", exc_info=True)

        # Persist updated states.
        try:
            inj_repo.upsert_player_injury_states(cur, states, now=now_iso)
        except Exception:
            logger.warning("INJURY_STATE_UPSERT_FAILED", exc_info=True)

    return PreparedGameInjuries(
        game_id=gid,
        game_date_iso=gdi,
        season_year=int(season_year),
        home_team_id=hid,
        away_team_id=aid,
        unavailable_pids_by_team=unavailable_by_team,
        attrs_mods_by_pid=attrs_mods_by_pid,
        reinjury_counts_by_pid=reinjury_counts_by_pid,
        lt_wear_by_pid=lt_wear_by_pid,
        training_new_events=training_events,
    )


# ---------------------------------------------------------------------------
# In-game hook
# ---------------------------------------------------------------------------


def make_in_game_injury_hook(
    prepared: PreparedGameInjuries,
    *,
    context: Mapping[str, Any],
    home: TeamState,
    away: TeamState,
) -> Callable[[float, GameState, TeamState, TeamState], List[InjuryEvent]]:
    """Create an in-game injury hook.

    The returned callable is intended to be invoked by the match engine once per
    simulation segment, e.g. after fatigue/clock updates.

    Signature:
        hook(seg_elapsed_sec, game_state, home, away) -> list[InjuryEvent]

    Side effects:
        - Adds/updates game_state.injured_out and game_state.injury_events
        - Marks injured players as "out" for the rest of the game

    Determinism:
        - Does NOT consume the engine RNG stream.
    """

    game_id = str(getattr(context, "game_id", None) or getattr(context, "get", lambda k, d=None: d)("game_id", None) or prepared.game_id)
    season_year = int(prepared.season_year)
    game_date_iso = str(prepared.game_date_iso)[:10]

    # Build pid->Player maps for attributes.
    home_tid = str(getattr(home, "team_id", "") or prepared.home_team_id).upper()
    away_tid = str(getattr(away, "team_id", "") or prepared.away_team_id).upper()

    pid_to_player: Dict[str, Player] = {}
    for p in (getattr(home, "lineup", []) or []):
        pid_to_player[str(getattr(p, "pid", ""))] = p
    for p in (getattr(away, "lineup", []) or []):
        pid_to_player[str(getattr(p, "pid", ""))] = p

    # Track per-game caps.
    injuries_total = 0
    injuries_by_team: Dict[str, int] = {home_tid: 0, away_tid: 0}
    severe_used = 0

    def hook(seg_elapsed_sec: float, game_state: GameState, home_ts: TeamState, away_ts: TeamState) -> List[InjuryEvent]:
        nonlocal injuries_total, injuries_by_team, severe_used

        dt_sec = max(0.0, float(seg_elapsed_sec))
        if dt_sec <= 0.0:
            return []

        if injuries_total >= int(config.MAX_INJURIES_PER_GAME_TOTAL):
            return []

        # Ensure game_state containers exist (forward-compatible with future dataclass fields).
        if not hasattr(game_state, "injured_out"):
            setattr(game_state, "injured_out", {home_tid: set(), away_tid: set()})
        if not hasattr(game_state, "injury_events"):
            setattr(game_state, "injury_events", [])

        injured_out = getattr(game_state, "injured_out")
        if not isinstance(injured_out, dict):
            injured_out = {home_tid: set(), away_tid: set()}
            setattr(game_state, "injured_out", injured_out)

        events_list = getattr(game_state, "injury_events")
        if not isinstance(events_list, list):
            events_list = []
            setattr(game_state, "injury_events", events_list)

        # Collect active players on court.
        home_on = list(getattr(home_ts, "on_court_pids", []) or [])
        away_on = list(getattr(away_ts, "on_court_pids", []) or [])

        new_events: List[InjuryEvent] = []

        # Helper to process one team.
        def _process_team(team_id: str, on_pids: Sequence[str]) -> None:
            nonlocal injuries_total, severe_used

            if injuries_total >= int(config.MAX_INJURIES_PER_GAME_TOTAL):
                return
            if injuries_by_team.get(team_id, 0) >= int(config.MAX_INJURIES_PER_TEAM_PER_GAME):
                return

            # If already too many severe injuries in this game, suppress additional severe outcomes
            # by capping severity bump later.

            for pid in on_pids:
                if injuries_total >= int(config.MAX_INJURIES_PER_GAME_TOTAL):
                    return
                if injuries_by_team.get(team_id, 0) >= int(config.MAX_INJURIES_PER_TEAM_PER_GAME):
                    return

                pid_s = str(pid)
                if not pid_s:
                    continue

                # Already injured in this game?
                if pid_s in (injured_out.get(team_id) or set()):
                    continue

                p_obj = pid_to_player.get(pid_s)
                if p_obj is None:
                    continue

                # Gather inputs.
                # I_InjuryFreq and Overall Durability are expected to be wired into Player in roster_adapter patch.
                injury_freq = float(getattr(p_obj, "injury_freq", 5.0) or 5.0)
                durability = float(getattr(p_obj, "durability", 70.0) or 70.0)
                age = int(getattr(p_obj, "age", 0) or 0)

                # Current energy from game_state.fatigue.
                try:
                    energy = float((getattr(game_state, "fatigue", {}) or {}).get(team_id, {}).get(pid_s, 1.0))
                except Exception:
                    energy = 1.0

                lt_wear = float(prepared.lt_wear_by_pid.get(pid_s, 0.0) or 0.0)
                recent_counts = prepared.reinjury_counts_by_pid.get(pid_s, {})

                p = hazard_probability(
                    base_lambda=float(config.BASE_GAME_HAZARD_PER_SEC),
                    dt=dt_sec,
                    injury_freq=injury_freq,
                    energy=energy,
                    durability=durability,
                    age=age,
                    lt_wear=lt_wear,
                    intensity_mult=1.0,
                    reinjury_recent_counts=recent_counts,
                    include_training_mult=False,
                )

                if p <= 0.0:
                    continue

                # Deterministic RNG for this moment/player.
                q = int(getattr(game_state, "quarter", 0) or 0)
                clock = int(getattr(game_state, "clock_sec", 0) or 0)
                poss = int(getattr(game_state, "possession", 0) or 0)

                rr = random.Random(stable_seed("GAME_INJ", game_id, str(poss), str(q), str(clock), team_id, pid_s))
                if rr.random() >= p:
                    continue

                # Roll injury details.
                ev = roll_injury_event(
                    rng=rr,
                    game_or_day_id=f"G:{game_id}:{poss}:{q}:{clock}",
                    player_id=pid_s,
                    team_id=team_id,
                    season_year=season_year,
                    date_iso=game_date_iso,
                    context=catalog.CONTEXT_GAME,
                    pos=str(getattr(p_obj, "pos", "G") or "G"),
                    injury_freq=injury_freq,
                    durability=durability,
                    energy=energy,
                    age=age,
                    lt_wear=lt_wear,
                    recent_counts_by_part=recent_counts,
                    max_severity=(int(config.SEVERE_THRESHOLD) - 1) if severe_used >= int(config.MAX_SEVERE_INJURY_PER_GAME) else None,
                    quarter=q,
                    clock_sec=clock,
                    game_id=game_id,
                )

                # Apply event.
                new_events.append(ev)
                events_list.append(ev.to_row())
                injured_out.setdefault(team_id, set()).add(pid_s)

                injuries_total += 1
                injuries_by_team[team_id] = injuries_by_team.get(team_id, 0) + 1
                if int(ev.severity) >= int(config.SEVERE_THRESHOLD):
                    severe_used += 1

                # Stop after one injury for this team this segment (keeps PBP sane).
                return

        _process_team(home_tid, home_on)
        _process_team(away_tid, away_on)

        return new_events

    return hook


# ---------------------------------------------------------------------------
# Post-game finalize
# ---------------------------------------------------------------------------


def _extract_injury_events_from_raw_result(raw_result: Mapping[str, Any]) -> List[Dict[str, Any]]:
    if not isinstance(raw_result, Mapping):
        return []

    # Primary location: raw_result["game_state"]["injury_events"]
    gs = raw_result.get("game_state")
    if isinstance(gs, Mapping):
        ie = gs.get("injury_events")
        if isinstance(ie, list):
            return [x for x in ie if isinstance(x, Mapping)]

    # Fallback: raw_result["injury_events"]
    ie2 = raw_result.get("injury_events")
    if isinstance(ie2, list):
        return [x for x in ie2 if isinstance(x, Mapping)]

    return []


def finalize_game_injuries(
    repo: LeagueRepo,
    *,
    prepared: PreparedGameInjuries,
    home: TeamState,
    away: TeamState,
    raw_result: Mapping[str, Any],
) -> None:
    """Finalize and persist injuries after a game.

    Expects the match engine to include injury event rows in raw_result.

    Side effects:
    - Appends new events into injury_events
    - Updates player_injury_state for injured players
    - Applies permanent drops to players table for new events

    Commercial safety:
    - If payload is missing, logs warning and no-ops.
    """
    events = _extract_injury_events_from_raw_result(raw_result)
    if not events:
        return

    # Normalize date.
    gdi = game_time.require_date_iso(prepared.game_date_iso, field="game_date_iso")
    now_iso = game_time.utc_like_from_date_iso(gdi, field="game_date_iso")

    # Determine which injuries are new (idempotency).
    injury_ids = [str(e.get("injury_id") or "") for e in events if str(e.get("injury_id") or "")]
    if not injury_ids:
        return

    with repo.transaction() as cur:
        # Find existing ids to avoid double-applying permanent drops.
        placeholders = ",".join(["?"] * len(injury_ids))
        existing_rows = cur.execute(
            f"SELECT injury_id FROM injury_events WHERE injury_id IN ({placeholders});",
            injury_ids,
        ).fetchall()
        existing = {str(r[0]) for r in existing_rows}

        new_events = [e for e in events if str(e.get("injury_id") or "") not in existing]
        if not new_events:
            return

        # Insert events (append-only log).
        try:
            inj_repo.insert_injury_events(cur, new_events, now=now_iso)
        except Exception:
            logger.warning("INJURY_EVENTS_INSERT_FAILED", exc_info=True)

        # Update injury states for affected players.
        pids = list({str(e.get("player_id") or "") for e in new_events if str(e.get("player_id") or "")})
        states = inj_repo.get_player_injury_states(cur, pids)

        for e in new_events:
            pid = str(e.get("player_id") or "")
            tid = str(e.get("team_id") or "").upper()
            if not pid or not tid:
                continue

            st = states.get(pid)
            if not isinstance(st, dict):
                st = _default_injury_state(player_id=pid, team_id=tid, game_date_iso=gdi)
                states[pid] = st

            st["team_id"] = tid
            st["status"] = "OUT"
            st["injury_id"] = str(e.get("injury_id") or "")
            st["start_date"] = str(e.get("date") or gdi)[:10]
            st["out_until_date"] = str(e.get("out_until_date") or "")[:10]
            st["returning_until_date"] = str(e.get("returning_until_date") or "")[:10]
            st["body_part"] = str(e.get("body_part") or "").upper() or None
            st["injury_type"] = str(e.get("injury_type") or "").upper() or None
            try:
                st["severity"] = int(e.get("severity") or 1)
            except Exception:
                st["severity"] = 1

            # Temp/permanent payloads.
            temp = e.get("temp_debuff")
            perm = e.get("perm_drop")
            if isinstance(temp, str):
                try:
                    temp = json.loads(temp)
                except Exception:
                    temp = {}
            if isinstance(perm, str):
                try:
                    perm = json.loads(perm)
                except Exception:
                    perm = {}

            st["temp_debuff"] = dict(temp) if isinstance(temp, Mapping) else {}
            st["perm_drop"] = dict(perm) if isinstance(perm, Mapping) else {}

            # Update cumulative reinjury counts.
            rcount = st.get("reinjury_count")
            if not isinstance(rcount, dict):
                rcount = {}
            bp = catalog.normalize_body_part(str(st.get("body_part") or ""))
            rcount[bp] = int(rcount.get(bp, 0) or 0) + 1
            st["reinjury_count"] = rcount

            # Ensure last_processed_date at least game date.
            st["last_processed_date"] = gdi

            # Apply permanent drop for new game injuries.
            if isinstance(perm, Mapping) and perm:
                try:
                    _apply_perm_drop_to_player_row(cur, player_id=pid, perm_drop=perm, now_iso=now_iso)
                except Exception:
                    logger.warning("INJURY_PERM_DROP_APPLY_FAILED pid=%s", pid, exc_info=True)

        try:
            inj_repo.upsert_player_injury_states(cur, states, now=now_iso)
        except Exception:
            logger.warning("INJURY_STATE_UPSERT_FAILED", exc_info=True)
