from __future__ import annotations

"""Scouting service (DB-backed).

This module implements:
  - per-team scouting staff seeding (6~7 scouts per team)
  - monthly (end-of-month) scouting checkpoints

Key gameplay rules (as requested):
  - Scouting is 100% user-driven.
    If there are no ACTIVE scouting_assignments, this module must be a no-op.
  - Reports are written at *month end*.
  - If a scout was assigned within 14 days of the month end, they do NOT write
    a report for that month (insufficient info).
  - Scouting info improves over time via Bayesian/Kalman-style updates
    (uncertainty shrinks) but *bias remains*.
  - Scouts have specialties; axes improve at different speeds.
"""

import datetime as _dt
import json
import logging
import math
import os
import random
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import game_time
from league_repo import LeagueRepo
from scouting.signals_v2 import (
    SIGNALS,
    SignalDef,
    build_delta_since_last,
    build_evidence_tags,
    build_profile_tags,
    build_watchlist_questions,
    confidence_from_sigma,
    compute_stat_weight,
    compute_true_signals,
    derive_college_notes,
    num_str,
    pct_str,
    range_text_from_mu_sigma,
    tier_from_mu,
)

from scouting.report_ai import ScoutingReportWriter


logger = logging.getLogger(__name__)

try:
    # Used to compute gameplay-relevant skill axes from attrs_json.
    from derived_formulas import compute_derived
except Exception:  # pragma: no cover
    compute_derived = None  # type: ignore[assignment]


# -----------------------------------------------------------------------------
# JSON helpers (keep identical style with the rest of the project)
# -----------------------------------------------------------------------------

def _json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str)


def _json_loads(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    s = str(value)
    if not s:
        return default
    try:
        return json.loads(s)
    except Exception:
        return default


# -----------------------------------------------------------------------------
# Deterministic RNG seeding
# -----------------------------------------------------------------------------

def _stable_seed(*parts: object) -> int:
    """Stable seed across runs, independent of Python's hash randomization."""
    s = "|".join(str(p) for p in parts)
    h = 2166136261
    for ch in s.encode("utf-8"):
        h ^= ch
        h = (h * 16777619) & 0xFFFFFFFF
    return int(h)


def _pos_bucket(pos: Any) -> str:
    """Coarse positional bucket: G/W/B.

    - G: guards
    - W: wings/forwards
    - B: bigs
    """
    p = str(pos or "").strip().upper()
    if p in ("PG", "SG", "G"):
        return "G"
    if p in ("SF", "PF", "F", "W"):
        return "W"
    return "B"


def _rule_when_matches(when: Any, ctx: Mapping[str, Any]) -> bool:
    if not isinstance(when, dict):
        return False

    pos = str(ctx.get("pos") or "").strip().upper()
    pos_bucket = str(ctx.get("pos_bucket") or "").strip().upper()

    # List membership filters
    if "pos_in" in when:
        arr = when.get("pos_in")
        if isinstance(arr, (list, tuple, set)):
            allowed = {str(x).strip().upper() for x in arr}
            if pos not in allowed:
                return False
    if "pos_bucket" in when:
        arr = when.get("pos_bucket")
        if isinstance(arr, (list, tuple, set)):
            allowed = {str(x).strip().upper() for x in arr}
            if pos_bucket not in allowed:
                return False

    # Numeric ranges
    def _num(key: str, default: Optional[float] = None) -> Optional[float]:
        v = ctx.get(key)
        try:
            if v is None:
                return default
            return float(v)
        except Exception:
            return default

    def _w_num(key: str) -> Optional[float]:
        v = when.get(key)
        try:
            if v is None:
                return None
            return float(v)
        except Exception:
            return None

    height = _num("height_in")
    weight = _num("weight_lb")
    age = _num("age")
    cy = _num("class_year")

    hmin = _w_num("height_in_min")
    hmax = _w_num("height_in_max")
    if height is not None:
        if hmin is not None and height < hmin:
            return False
        if hmax is not None and height > hmax:
            return False

    wmin = _w_num("weight_lb_min")
    wmax = _w_num("weight_lb_max")
    if weight is not None:
        if wmin is not None and weight < wmin:
            return False
        if wmax is not None and weight > wmax:
            return False

    amin = _w_num("age_min")
    amax = _w_num("age_max")
    if age is not None:
        if amin is not None and age < amin:
            return False
        if amax is not None and age > amax:
            return False

    cmin = _w_num("class_year_min")
    cmax = _w_num("class_year_max")
    if cy is not None:
        if cmin is not None and cy < cmin:
            return False
        if cmax is not None and cy > cmax:
            return False

    return True


def _bias_delta_from_rules(
    *,
    bias_rules: Any,
    ctx: Mapping[str, Any],
    axis_key: str,
    scout_id: str,
    player_id: str,
    period_key: str,
) -> float:
    """Compute additional bias delta from conditional bias rules."""
    if not isinstance(bias_rules, list) or not bias_rules:
        return 0.0

    total = 0.0
    for rule in bias_rules:
        if not isinstance(rule, dict):
            continue
        when = rule.get("when")
        if not _rule_when_matches(when, ctx):
            continue

        # Optional probability (deterministic per scout+player+month+rule)
        prob = rule.get("prob")
        try:
            p = float(prob) if prob is not None else 1.0
        except Exception:
            p = 1.0
        p = max(0.0, min(1.0, p))
        if p < 1.0:
            rule_name = str(rule.get("name") or "rule")
            rng = random.Random(_stable_seed("bias_rule", scout_id, player_id, period_key, rule_name))
            if rng.random() > p:
                continue

        axis_delta = rule.get("axis_delta")
        if not isinstance(axis_delta, dict):
            continue
        if axis_key not in axis_delta:
            continue
        try:
            total += float(axis_delta.get(axis_key) or 0.0)
        except Exception:
            continue

    return float(total)


# -----------------------------------------------------------------------------
# Date helpers
# -----------------------------------------------------------------------------

def _parse_date_iso(value: Any, *, field: str) -> _dt.date:
    s = str(value)[:10]
    try:
        return _dt.date.fromisoformat(s)
    except Exception as e:
        raise ValueError(f"{field} must be ISO YYYY-MM-DD: got {value!r}") from e


def _month_floor(d: _dt.date) -> _dt.date:
    return _dt.date(int(d.year), int(d.month), 1)


def _add_one_month(d: _dt.date) -> _dt.date:
    y = int(d.year)
    m = int(d.month) + 1
    if m == 13:
        return _dt.date(y + 1, 1, 1)
    return _dt.date(y, m, 1)


def _month_end(month_start: _dt.date) -> _dt.date:
    nxt = _add_one_month(month_start)
    return nxt - _dt.timedelta(days=1)


def _infer_college_season_year_from_date(d: _dt.date) -> int:
    # Match college/service.py assumption: season starts in October.
    return int(d.year) if int(d.month) >= 10 else int(d.year - 1)


# -----------------------------------------------------------------------------
# Scouting signals (v2)
# -----------------------------------------------------------------------------

# NOTE: Canonical signals are defined in scouting.signals_v2.SIGNALS.
# This service module keeps only the Bayesian/Kalman update state (mu/sigma per signal).
#
# Legacy "overall/athleticism/shooting" axes have been fully removed.


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return float(default)
        return float(v)
    except Exception:
        return float(default)


def _clamp100(x: float) -> float:
    return float(max(0.0, min(100.0, x)))


SIGNAL_KEYS = set(SIGNALS.keys())


# -----------------------------------------------------------------------------
# Scout staff profiles (seeded, per team)
# -----------------------------------------------------------------------------


def _default_scout_templates(*, scouts_per_team: int) -> List[Dict[str, Any]]:
    """Return scout templates used by ensure_scouts_seeded().

    These templates define:
      - which *signals* each scout focuses on (focus_axes)
      - how quickly they learn each signal (learn_rate_by_axis)
      - how noisy their observations are (acc_mult_by_axis)
      - systematic bias (bias_offset_by_axis + conditional bias_rules)

    IMPORTANT: We intentionally keep scout assignment mechanics unchanged.
               Only the measurement axes/signals are replaced in v2.
    """
    n = int(scouts_per_team)
    if n <= 0:
        return []

    # 7-role default staff. If n < 7 we truncate; if n > 7 we append generalists.
    base: List[Dict[str, Any]] = [
        {
            "specialty_key": "ATHLETICS",
            "display_name": "Tools Scout",
            "focus_axes": ["downhill_pressure", "glass_physicality", "motor_compete"],
            "style_tags": ["practical", "tools-focused", "contact"],
            "acc": {
                "downhill_pressure": 0.75,
                "glass_physicality": 0.85,
                "motor_compete": 0.70,
            },
            "learn": {
                "downhill_pressure": 1.25,
                "glass_physicality": 1.05,
                "motor_compete": 0.90,
            },
            "bias": {
                "space_bending": -1.0,
            },
            "bias_rules": [
                {
                    "name": "small_guard_contact_skeptic",
                    "when": {"pos_bucket": ["G"], "height_in_max": 73},
                    "axis_delta": {"glass_physicality": -1.0, "downhill_pressure": -0.5},
                },
                {
                    "name": "big_wing_tools_hype",
                    "when": {"pos_bucket": ["W"], "height_in_min": 79},
                    "axis_delta": {"glass_physicality": 1.0, "downhill_pressure": 0.5, "runway": 0.5},
                },
                {
                    "name": "older_runway_discount",
                    "when": {"age_min": 22},
                    "axis_delta": {"runway": -2.0},
                },
            ],
        },
        {
            "specialty_key": "SHOOTING",
            "display_name": "Shooting Specialist",
            "focus_axes": ["space_bending", "shotmaking_complexity"],
            "style_tags": ["mechanics", "detail", "range"],
            "acc": {"space_bending": 0.75, "shotmaking_complexity": 0.65},
            "learn": {"space_bending": 1.40, "shotmaking_complexity": 1.20},
            "bias": {"team_defense_rim_support": -1.0},
            "bias_rules": [
                {
                    "name": "big_shooter_skeptic",
                    "when": {"pos_bucket": ["B"]},
                    "axis_delta": {"space_bending": -1.5},
                },
                {
                    "name": "guard_range_credit",
                    "when": {"pos_bucket": ["G"]},
                    "axis_delta": {"shotmaking_complexity": 0.6},
                },
            ],
        },
        {
            "specialty_key": "DEFENSE",
            "display_name": "Defense Scout",
            "focus_axes": ["perimeter_containment", "team_defense_rim_support", "defensive_playmaking"],
            "style_tags": ["defense-first", "scheme", "matchups"],
            "acc": {
                "perimeter_containment": 0.75,
                "team_defense_rim_support": 0.65,
                "defensive_playmaking": 0.75,
            },
            "learn": {
                "perimeter_containment": 1.25,
                "team_defense_rim_support": 1.15,
                "defensive_playmaking": 1.05,
            },
            "bias": {"shotmaking_complexity": -0.8},
            "bias_rules": [
                {
                    "name": "small_guard_defense_skeptic",
                    "when": {"pos_bucket": ["G"], "height_in_max": 73},
                    "axis_delta": {"perimeter_containment": -1.5},
                },
                {
                    "name": "true_rim_size_hype",
                    "when": {"pos_bucket": ["B"], "height_in_min": 82},
                    "axis_delta": {"team_defense_rim_support": 1.2, "glass_physicality": 0.6},
                },
            ],
        },
        {
            "specialty_key": "PLAYMAKING",
            "display_name": "Playmaking Scout",
            "focus_axes": ["playmaking_engine", "processing_safety"],
            "style_tags": ["processing", "reads", "decision-making"],
            "acc": {"playmaking_engine": 0.75, "processing_safety": 0.70},
            "learn": {"playmaking_engine": 1.35, "processing_safety": 1.15},
            "bias": {"downhill_pressure": -0.5},
            "bias_rules": [
                {
                    "name": "big_playmaking_skeptic",
                    "when": {"pos_bucket": ["B"]},
                    "axis_delta": {"playmaking_engine": -1.0},
                },
                {
                    "name": "pg_processing_credit",
                    "when": {"pos_in": ["PG"]},
                    "axis_delta": {"playmaking_engine": 1.0, "processing_safety": 0.8},
                },
            ],
        },
        {
            "specialty_key": "MEDICAL",
            "display_name": "Risk Scout",
            "focus_axes": ["motor_compete"],
            "style_tags": ["risk", "availability", "conservative"],
            "acc": {"motor_compete": 0.90},
            "learn": {"motor_compete": 0.70},
            "bias": {"motor_compete": -1.5},
            "bias_rules": [
                {
                    "name": "heavy_body_mileage_skeptic",
                    "when": {"weight_lb_min": 255},
                    "axis_delta": {"motor_compete": -1.0, "glass_physicality": -0.5},
                },
                {
                    "name": "older_mileage_skeptic",
                    "when": {"age_min": 22},
                    "axis_delta": {"motor_compete": -0.8},
                },
            ],
        },
        {
            "specialty_key": "CHARACTER",
            "display_name": "Intangibles Scout",
            "focus_axes": ["runway", "motor_compete"],
            "style_tags": ["intangibles", "coachability", "context"],
            "acc": {"runway": 0.85, "motor_compete": 0.80},
            "learn": {"runway": 0.65, "motor_compete": 0.80},
            "bias": {},
            "bias_rules": [
                {
                    "name": "upperclass_maturity_bump",
                    "when": {"class_year_min": 3},
                    "axis_delta": {"processing_safety": 0.6, "motor_compete": 0.5, "runway": -0.5},
                },
                {
                    "name": "freshman_unknown_penalty",
                    "when": {"class_year_max": 1},
                    "axis_delta": {"motor_compete": -0.5},
                },
            ],
        },
        {
            "specialty_key": "ANALYTICS",
            "display_name": "Analytics Scout",
            "focus_axes": ["space_bending", "processing_safety", "runway"],
            "style_tags": ["projection", "context", "probabilistic"],
            "acc": {"space_bending": 0.95, "processing_safety": 0.90, "runway": 0.90},
            "learn": {"space_bending": 1.00, "processing_safety": 0.95, "runway": 0.85},
            "bias": {"shotmaking_complexity": -0.8},
            "bias_rules": [
                {
                    "name": "young_upside_credit",
                    "when": {"age_max": 20},
                    "axis_delta": {"runway": 1.5},
                },
                {
                    "name": "older_runway_discount",
                    "when": {"age_min": 22},
                    "axis_delta": {"runway": -2.0},
                },
            ],
        },
    ]

    out: List[Dict[str, Any]] = []
    for i, t in enumerate(base):
        if i >= n:
            break
        out.append(t)

    # If n > 7, add generalists.
    extra = n - len(out)
    for j in range(extra):
        out.append(
            {
                "specialty_key": f"GENERAL_{j+1}",
                "display_name": "Regional Scout",
                "focus_axes": ["space_bending", "perimeter_containment", "runway"],
                "style_tags": ["generalist"],
                "acc": {"space_bending": 1.00, "perimeter_containment": 1.00, "runway": 1.00},
                "learn": {"space_bending": 0.90, "perimeter_containment": 0.90, "runway": 0.90},
                "bias": {},
                "bias_rules": [],
            }
        )

    return out


def ensure_scouts_seeded(
    *,
    db_path: str,
    team_ids: Sequence[str],
    scouts_per_team: int = 7,
) -> Dict[str, Any]:
    """Ensure each team has a seeded scout staff (idempotent).

    This creates rows in scouting_scouts only.
    It does NOT create assignments; scouting remains fully user-driven.
    """
    if not db_path:
        raise ValueError("db_path is required")
    teams = [str(t).strip().upper() for t in (team_ids or []) if str(t).strip()]
    if not teams:
        return {"ok": True, "created": 0, "existing": 0, "teams": 0}

    templates = _default_scout_templates(scouts_per_team=int(scouts_per_team))
    if not templates:
        return {"ok": True, "created": 0, "existing": 0, "teams": len(teams)}

    now = game_time.now_utc_like_iso()

    created = 0
    existing = 0
    updated = 0

    with LeagueRepo(db_path) as repo:
        repo.init_db()

        with repo.transaction() as cur:
            for team_id in teams:
                for t in templates:
                    specialty_key = str(t.get("specialty_key") or "GENERAL").strip().upper()
                    display_name = str(t.get("display_name") or "Scout").strip()

                    scout_id = f"SCT_{team_id}_{specialty_key}"
                    row = cur.execute(
                        "SELECT profile_json FROM scouting_scouts WHERE scout_id=? LIMIT 1;",
                        (scout_id,),
                    ).fetchone()
                    if row:
                        existing += 1

                        # Profile upgrade policy:
                        # - If the existing profile is from v1/v0 (schema_version < 3),
                        #   we hard-upgrade the tuning fields to the v2 signal system.
                        # - If schema_version >= 3, we keep a non-destructive policy
                        #   (only fill missing keys), so manual edits remain intact.
                        cur_profile = _json_loads(row[0], default={})
                        if not isinstance(cur_profile, dict):
                            cur_profile = {}

                        # Desired defaults (only applied when the key is missing)
                        desired = {
                            "schema_version": 3,
                            "specialty_key": specialty_key,
                            "focus_axes": list(t.get("focus_axes") or []),
                            "acc_mult_by_axis": dict(t.get("acc") or {}),
                            "learn_rate_by_axis": dict(t.get("learn") or {}),
                            "bias_offset_by_axis": dict(t.get("bias") or {}),
                            "bias_rules": list(t.get("bias_rules") or []),
                            "style_tags": list(t.get("style_tags") or []),
                            "rng_seed": _stable_seed("scout", team_id, specialty_key),
                        }

                        patched = False
                        try:
                            cur_sv = int(cur_profile.get("schema_version") or 0)
                        except Exception:
                            cur_sv = 0

                        if cur_sv < 3:
                            # Hard upgrade: replace tuning fields to match new signals.
                            for k, dv in desired.items():
                                cur_profile[k] = dv
                            patched = True
                        else:
                            # Non-destructive: only fill missing keys.
                            for k, dv in desired.items():
                                if k not in cur_profile:
                                    cur_profile[k] = dv
                                    patched = True

                        if patched:
                            cur.execute(
                                "UPDATE scouting_scouts SET profile_json=?, updated_at=? WHERE scout_id=?;",
                                (_json_dumps(cur_profile), now, scout_id),
                            )
                            updated += 1
                        continue

                    profile = {
                        "schema_version": 3,
                        "specialty_key": specialty_key,
                        "focus_axes": list(t.get("focus_axes") or []),
                        "acc_mult_by_axis": dict(t.get("acc") or {}),
                        "learn_rate_by_axis": dict(t.get("learn") or {}),
                        "bias_offset_by_axis": dict(t.get("bias") or {}),
                        "bias_rules": list(t.get("bias_rules") or []),
                        "style_tags": list(t.get("style_tags") or []),
                        "rng_seed": _stable_seed("scout", team_id, specialty_key),
                    }

                    traits = {
                        "experience_years": 0,
                        "reputation": "avg",
                    }

                    cur.execute(
                        """
                        INSERT INTO scouting_scouts(
                            scout_id, team_id, display_name, specialty_key,
                            profile_json, traits_json, is_active,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                        """,
                        (
                            scout_id,
                            team_id,
                            display_name,
                            specialty_key,
                            _json_dumps(profile),
                            _json_dumps(traits),
                            1,
                            now,
                            now,
                        ),
                    )
                    created += 1

    return {
        "ok": True,
        "teams": len(teams),
        "scouts_per_team": int(scouts_per_team),
        "created": int(created),
        "existing": int(existing),
        "updated": int(updated),
    }


# -----------------------------------------------------------------------------
# Kalman update core
# -----------------------------------------------------------------------------


def _kalman_update(*, mu: float, sigma: float, z: float, meas_sigma: float, sigma_floor: float) -> Tuple[float, float]:
    """One-step scalar Kalman update.

    - mu/sigma: prior mean/std
    - z: measurement
    - meas_sigma: measurement std
    - sigma_floor: lower bound for posterior sigma
    """
    mu0 = float(mu)
    s0 = float(max(1e-6, sigma))
    z0 = float(z)
    r0 = float(max(1e-6, meas_sigma))

    P = s0 * s0
    R = r0 * r0
    denom = P + R
    if denom <= 1e-12:
        return mu0, float(max(sigma_floor, s0))
    K = P / denom
    mu1 = mu0 + K * (z0 - mu0)
    P1 = (1.0 - K) * P
    s1 = math.sqrt(float(max(1e-9, P1)))
    s1 = float(max(float(sigma_floor), s1))
    return float(mu1), float(s1)


def _effective_meas_sigma(
    *,
    axis: SignalDef,
    base_days: int,
    acc_mult: float,
    learn_rate: float,
) -> float:
    """Compute measurement noise from observation window length + scout profile."""
    days = float(max(1, int(base_days)))
    lr = float(max(0.05, float(learn_rate)))
    eff = days * lr
    std = float(axis.base_meas_std) * float(max(0.1, float(acc_mult))) / math.sqrt(float(max(1.0, eff)))
    std = float(max(float(axis.meas_floor), std))
    return std


# -----------------------------------------------------------------------------
# Monthly checkpoint
# -----------------------------------------------------------------------------


def run_monthly_scouting_checkpoints(
    db_path: str,
    *,
    from_date: str,
    to_date: str,
    api_key: Optional[str] = None,
    min_days_assigned_for_report: int = 14,
) -> Dict[str, Any]:
    """Run end-of-month scouting report checkpoints between two dates.

    Only months whose month_end <= to_date are processed.

    Idempotent:
      - scouting_reports has UNIQUE(assignment_id, period_key)
      - assignment progress is only updated when a report row is created
    """
    d0 = _parse_date_iso(from_date, field="from_date")
    d1 = _parse_date_iso(to_date, field="to_date")
    if d1 < d0:
        d0, d1 = d1, d0

    min_days = int(min_days_assigned_for_report)
    if min_days < 0:
        min_days = 0

    # Pre-compute month periods to process.
    periods: List[Tuple[str, _dt.date, _dt.date]] = []  # (period_key, month_start, month_end)
    cur = _month_floor(d0)
    end_floor = _month_floor(d1)
    while cur <= end_floor:
        pk = f"{cur.year:04d}-{cur.month:02d}"
        mend = _month_end(cur)
        # Only process if month end is within [d0..d1].
        if mend < d0:
            cur = _add_one_month(cur)
            continue
        if mend > d1:
            # This month hasn't ended yet (relative to to_date). Stop.
            break
        periods.append((pk, cur, mend))
        cur = _add_one_month(cur)

    # Fast no-op: if there are no periods, nothing to do.
    if not periods:
        return {
            "ok": True,
            "from_date": str(from_date),
            "to_date": str(to_date),
            "handled": [],
            "generated_reports": 0,
            "skipped": {"no_periods": True},
        }

    now = game_time.now_utc_like_iso()
    handled: List[Dict[str, Any]] = []
    total_created = 0
    pending_text: List[Dict[str, Any]] = []  # created reports needing LLM text
    text_generated = 0
    text_failed = 0
    text_skipped_missing_api_key = 0

    def _resolve_llm_api_key(v: Optional[str]) -> Optional[str]:
        if v and str(v).strip():
            return str(v).strip()
        for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GENAI_API_KEY"):
            vv = os.getenv(k)
            if vv and str(vv).strip():
                return str(vv).strip()
        return None

    with LeagueRepo(db_path) as repo:
        repo.init_db()

        # Global no-op if there are no ACTIVE assignments at all.
        any_active = repo._conn.execute(
            "SELECT 1 FROM scouting_assignments WHERE status='ACTIVE' LIMIT 1;"
        ).fetchone()
        if not any_active:
            return {
                "ok": True,
                "from_date": str(from_date),
                "to_date": str(to_date),
                "handled": [],
                "generated_reports": 0,
                "skipped": {"no_active_assignments": True},
            }

        for pk, mstart, mend in periods:
            as_of = mend.isoformat()
            season_year = int(_infer_college_season_year_from_date(mend))

            created = 0
            skipped_recent = 0
            skipped_existing = 0
            skipped_missing_player = 0
            skipped_not_assigned_yet = 0
            skipped_no_days = 0

            with repo.transaction() as cur:
                # Load all ACTIVE assignments (user-driven) that could apply to this month end.
                # We filter by assigned_date <= as_of and (ended_date is null or ended_date >= as_of).
                rows = cur.execute(
                    """
                    SELECT assignment_id, team_id, scout_id, target_player_id, target_kind,
                           assigned_date, ended_date, progress_json
                    FROM scouting_assignments
                    WHERE status='ACTIVE';
                    """
                ).fetchall()

                for r in rows:
                    assignment_id = str(r[0])
                    team_id = str(r[1])
                    scout_id = str(r[2])
                    player_id = str(r[3])
                    target_kind = str(r[4] or "COLLEGE")
                    assigned_date_s = str(r[5] or "")[:10]
                    ended_date_s = str(r[6] or "")[:10] if r[6] else ""
                    progress = _json_loads(r[7], default={})
                    if not isinstance(progress, dict):
                        progress = {}

                    # Progress schema upgrade:
                    #   - v1 stored per-axis state under "axes" (legacy).
                    #   - v2 stores per-signal state under "signals".
                    # We intentionally do NOT attempt to translate legacy axes into new signals.
                    # Instead we reset the signal state when upgrading.
                    try:
                        psv = int(progress.get("schema_version") or 0)
                    except Exception:
                        psv = 0
                    if psv < 2:
                        progress = {
                            "schema_version": 2,
                            "signals": {},
                            "last_obs_date": progress.get("last_obs_date"),
                            "total_obs_days": progress.get("total_obs_days") or 0,
                        }
                    if not isinstance(progress.get("signals"), dict):
                        progress["signals"] = {}

                    # Date gating
                    try:
                        assigned_d = _dt.date.fromisoformat(assigned_date_s)
                    except Exception:
                        # Malformed assignment row; skip safely.
                        skipped_not_assigned_yet += 1
                        continue

                    if assigned_d > mend:
                        skipped_not_assigned_yet += 1
                        continue
                    if ended_date_s:
                        try:
                            ended_d = _dt.date.fromisoformat(ended_date_s)
                        except Exception:
                            ended_d = None
                        if ended_d is not None and ended_d < mend:
                            # Not active at this month end.
                            continue

                    days_since_assigned = int((mend - assigned_d).days)
                    if days_since_assigned <= min_days:
                        skipped_recent += 1
                        continue

                    # Idempotency: if report already exists for this assignment+month, skip.
                    exists = cur.execute(
                        """
                        SELECT 1 FROM scouting_reports
                        WHERE assignment_id=? AND period_key=?
                        LIMIT 1;
                        """,
                        (assignment_id, pk),
                    ).fetchone()
                    if exists:
                        skipped_existing += 1
                        continue

                    # Load scout profile
                    srow = cur.execute(
                        """
                        SELECT display_name, specialty_key, profile_json
                        FROM scouting_scouts
                        WHERE scout_id=?
                        LIMIT 1;
                        """,
                        (scout_id,),
                    ).fetchone()
                    if not srow:
                        # Staff row missing (should not happen if seeded)
                        continue
                    scout_name = str(srow[0] or "Scout")
                    specialty_key = str(srow[1] or "GENERAL")
                    scout_profile = _json_loads(srow[2], default={})
                    if not isinstance(scout_profile, dict):
                        scout_profile = {}

                    focus_axes = scout_profile.get("focus_axes")
                    if not isinstance(focus_axes, list) or not focus_axes:
                        focus_axes = []

                    acc_mult_by_axis = scout_profile.get("acc_mult_by_axis")
                    if not isinstance(acc_mult_by_axis, dict):
                        acc_mult_by_axis = {}
                    learn_rate_by_axis = scout_profile.get("learn_rate_by_axis")
                    if not isinstance(learn_rate_by_axis, dict):
                        learn_rate_by_axis = {}
                    bias_offset_by_axis = scout_profile.get("bias_offset_by_axis")
                    if not isinstance(bias_offset_by_axis, dict):
                        bias_offset_by_axis = {}

                    bias_rules = scout_profile.get("bias_rules")
                    if not isinstance(bias_rules, list):
                        bias_rules = []
                    style_tags = scout_profile.get("style_tags")
                    if not isinstance(style_tags, list):
                        style_tags = []

                    # Load target player (college only for now)
                    prow = None
                    if str(target_kind).upper() == "COLLEGE":
                        prow = cur.execute(
                            """
                            SELECT player_id, name, pos, age, height_in, weight_lb,
                                   college_team_id, class_year, entry_season_year, status,
                                   ovr, attrs_json
                            FROM college_players
                            WHERE player_id=?
                            LIMIT 1;
                            """,
                            (player_id,),
                        ).fetchone()

                    if not prow:
                        skipped_missing_player += 1
                        # Auto-end assignment if the target player is missing
                        # (e.g., drafted and removed from college_players).
                        if str(target_kind).upper() == "COLLEGE":
                            try:
                                progress["ended_reason"] = "missing_player"
                                progress["ended_at"] = as_of
                                cur.execute(
                                    """
                                    UPDATE scouting_assignments
                                    SET status='ENDED', ended_date=?, progress_json=?, updated_at=?
                                    WHERE assignment_id=? AND status='ACTIVE';
                                    """,
                                    (as_of, _json_dumps(progress), now, assignment_id),
                                )
                            except Exception as e:
                                logger.warning("auto_end_assignment_failed: %s", e)
                        continue

                    # Unpack player
                    p_name = str(prow[1] or "Unknown")
                    p_pos = str(prow[2] or "G")
                    p_age = int(prow[3] or 19)
                    p_h = int(prow[4] or 78)
                    p_w = int(prow[5] or 210)
                    p_team = str(prow[6] or "")
                    p_class = int(prow[7] or 1)
                    p_entry_sy = int(prow[8] or season_year)
                    p_status = str(prow[9] or "ACTIVE")
                    p_ovr = int(prow[10] or 60)
                    p_attrs = _json_loads(prow[11], default={})
                    if not isinstance(p_attrs, dict):
                        p_attrs = {}

                    player_snapshot = {
                        "player_id": str(player_id),
                        "name": p_name,
                        "pos": p_pos,
                        "age": int(p_age),
                        "height_in": int(p_h),
                        "weight_lb": int(p_w),
                        "college_team_id": p_team,
                        "class_year": int(p_class),
                        "entry_season_year": int(p_entry_sy),
                        "status": p_status,
                    }

                    # Determine observation window length since last report (or assignment).
                    last_obs_s = None
                    if isinstance(progress.get("last_obs_date"), str):
                        last_obs_s = str(progress.get("last_obs_date"))[:10]
                    last_obs_d: Optional[_dt.date] = None
                    if last_obs_s:
                        try:
                            last_obs_d = _dt.date.fromisoformat(last_obs_s)
                        except Exception:
                            last_obs_d = None

                    # Window start: max(assigned_date, last_obs_date + 1)
                    if last_obs_d is not None:
                        window_start = max(assigned_d, last_obs_d + _dt.timedelta(days=1))
                    else:
                        window_start = assigned_d
                    window_end = mend
                    if window_start > window_end:
                        skipped_no_days += 1
                        continue
                    days_covered = int((window_end - window_start).days) + 1
                    if days_covered <= 0:
                        skipped_no_days += 1
                        continue

                    # Compute true signals (attrs + college production)
                    # NOTE: derived metrics are gameplay-oriented; college stats add context and reliability.
                    derived: Dict[str, float] = {}
                    if compute_derived is not None:
                        try:
                            derived = compute_derived(dict(p_attrs)) or {}
                        except Exception:
                            derived = {}

                    # Load current season college stats (if available)
                    stats = {}
                    try:
                        srow2 = cur.execute(
                            """
                            SELECT stats_json
                            FROM college_player_season_stats
                            WHERE season_year=? AND player_id=?
                            LIMIT 1;
                            """,
                            (int(season_year), str(player_id)),
                        ).fetchone()
                        if srow2:
                            stats = _json_loads(srow2[0], default={}) or {}
                    except Exception:
                        stats = {}
                    if not isinstance(stats, dict):
                        stats = {}

                    ctx = {
                        "pos": p_pos,
                        "pos_bucket": _pos_bucket(p_pos),
                        "height_in": int(p_h),
                        "weight_lb": int(p_w),
                        "age": int(p_age),
                        "class_year": int(p_class),
                    }

                    true_signals, prod_scores = compute_true_signals(
                        ovr=int(p_ovr),
                        attrs=p_attrs,
                        derived=derived,
                        college_stats=stats,
                        context=ctx,
                    )

                    # Prepare/update progress signal state
                    signals_state = progress.get("signals")
                    if not isinstance(signals_state, dict):
                        signals_state = {}

                    updated_signals: Dict[str, Any] = {}
                    signals_payload: List[Dict[str, Any]] = []

                    # Which signals this scout reports on (focus only).
                    signals_to_update: List[str] = []
                    for ax in focus_axes:
                        k = str(ax)
                        if k in SIGNAL_KEYS and k not in signals_to_update:
                            signals_to_update.append(k)

                    # Analytics scouts add a small baseline set (scouting translation signals).
                    if str(specialty_key).upper() == "ANALYTICS":
                        for k in ("space_bending", "processing_safety", "runway"):
                            if k in SIGNAL_KEYS and k not in signals_to_update:
                                signals_to_update.append(k)

                    # If still empty (e.g., legacy scout profiles), default to runway so the report isn't blank.
                    if not signals_to_update:
                        signals_to_update = ["runway"]

                    for signal_key in signals_to_update:
                        sig_def = SIGNALS.get(signal_key)
                        if not sig_def:
                            continue

                        st = signals_state.get(signal_key)
                        if not isinstance(st, dict):
                            st = {}
                        mu0 = _safe_float(st.get("mu"), 50.0)
                        sigma0 = _safe_float(st.get("sigma"), sig_def.init_sigma)

                        true_v = _safe_float(true_signals.get(signal_key), 50.0)

                        base_bias = _safe_float(bias_offset_by_axis.get(signal_key), 0.0)
                        bias = base_bias + _bias_delta_from_rules(
                            bias_rules=bias_rules,
                            ctx=ctx,
                            axis_key=signal_key,
                            scout_id=scout_id,
                            player_id=player_id,
                            period_key=pk,
                        )

                        acc_mult = _safe_float(acc_mult_by_axis.get(signal_key), 1.0)
                        learn = _safe_float(learn_rate_by_axis.get(signal_key), 1.0)

                        meas_sigma = _effective_meas_sigma(
                            axis=sig_def,
                            base_days=days_covered,
                            acc_mult=acc_mult,
                            learn_rate=learn,
                        )

                        # Deterministic observation noise (seeded per signal / month / scout / player)
                        rng = random.Random(_stable_seed("scout_obs", scout_id, player_id, pk, signal_key))
                        z = true_v + bias + rng.gauss(0.0, meas_sigma)
                        z = _clamp100(z)

                        mu1, sigma1 = _kalman_update(
                            mu=mu0,
                            sigma=sigma0,
                            z=z,
                            meas_sigma=meas_sigma,
                            sigma_floor=sig_def.sigma_floor,
                        )

                        updated_signals[signal_key] = {
                            "mu": float(round(mu1, 2)),
                            "sigma": float(round(sigma1, 2)),
                            "last_meas_sigma": float(round(meas_sigma, 2)),
                        }

                        tier = tier_from_mu(mu1)
                        conf = confidence_from_sigma(sigma1)
                        range_text = range_text_from_mu_sigma(mu1, sigma1)

                        evidence_tags = build_evidence_tags(
                            axis=signal_key,
                            estimate_mu=mu1,
                            estimate_sigma=sigma1,
                            prod_score=float(_safe_float(prod_scores.get(signal_key), 50.0)),
                            derived=derived,
                            attrs=p_attrs,
                            stats=stats,
                            context=ctx,
                        )

                        signals_payload.append(
                            {
                                "key": signal_key,
                                "label": sig_def.label,
                                "group": sig_def.group,
                                "tier": tier,
                                "confidence": conf,
                                "range_text": range_text,
                                "evidence_tags": evidence_tags,
                            }
                        )

                    # Update progress JSON
                    signals_state.update(updated_signals)
                    progress["signals"] = signals_state
                    progress["schema_version"] = 2
                    progress["last_obs_date"] = as_of
                    progress["total_obs_days"] = int(_safe_float(progress.get("total_obs_days"), 0.0) + days_covered)
                    progress["updated_at"] = now

                    # College context summary (for grounded LLM writing)
                    stat_weight = compute_stat_weight(
                        games=int(_safe_float(stats.get("games"), 0.0)),
                        mpg=float(_safe_float(stats.get("mpg"), 0.0)),
                    )
                    college_context = {
                        "season_year": int(season_year),
                        "college_team_id": str(p_team),
                        "games": int(_safe_float(stats.get("games"), 0.0)),
                        "mpg": float(_safe_float(stats.get("mpg"), 0.0)),
                        "pts": float(_safe_float(stats.get("pts"), 0.0)),
                        "reb": float(_safe_float(stats.get("reb"), 0.0)),
                        "ast": float(_safe_float(stats.get("ast"), 0.0)),
                        "stl": float(_safe_float(stats.get("stl"), 0.0)),
                        "blk": float(_safe_float(stats.get("blk"), 0.0)),
                        "tov": float(_safe_float(stats.get("tov"), 0.0)),
                        "pf": float(_safe_float(stats.get("pf"), 0.0)),
                        "tp_pct": float(_safe_float(stats.get("tp_pct"), 0.0)),
                        "ft_pct": float(_safe_float(stats.get("ft_pct"), 0.0)),
                        "ts_pct": float(_safe_float(stats.get("ts_pct"), 0.0)),
                        "usg": float(_safe_float(stats.get("usg"), 0.0)),
                        "pace": float(_safe_float(stats.get("pace"), 70.0)),
                        "stat_weight": float(round(stat_weight, 3)),
                        "notes": derive_college_notes(stats=stats),
                    }

                    college_context["stat_line"] = (
                        f"{num_str(college_context['pts'])}P / {num_str(college_context['reb'])}R / {num_str(college_context['ast'])}A, "
                        f"3P {pct_str(college_context['tp_pct'])}, FT {pct_str(college_context['ft_pct'])}, "
                        f"TS {pct_str(college_context['ts_pct'])}, USG {num_str(college_context['usg']*100.0, digits=1)}%"
                    )

                    # Delta vs previous report (tier/confidence only)
                    prev_signals = None
                    try:
                        prev_row = cur.execute(
                            """
                            SELECT payload_json
                            FROM scouting_reports
                            WHERE assignment_id=? AND as_of_date < ?
                            ORDER BY as_of_date DESC
                            LIMIT 1;
                            """,
                            (assignment_id, as_of),
                        ).fetchone()
                        if prev_row:
                            prev_payload = _json_loads(prev_row[0], default={}) or {}
                            if isinstance(prev_payload, dict):
                                ps = prev_payload.get("signals")
                                if isinstance(ps, list):
                                    prev_signals = ps
                    except Exception:
                        prev_signals = None

                    delta_since_last = build_delta_since_last(prev_signals=prev_signals, curr_signals=signals_payload)
                    profile_tags = build_profile_tags(signal_summaries=signals_payload)
                    watchlist_questions = build_watchlist_questions(signals_payload=signals_payload, limit=3)

                    # Build structured payload for LLM (text generation happens at month-end checkpoint)
                    payload = {
                        "schema_version": 2,
                        "method": "kalman_signals_v2",
                        "period_key": pk,
                        "as_of_date": as_of,
                        "days_covered": int(days_covered),
                        "scout": {
                            "scout_id": scout_id,
                            "display_name": scout_name,
                            "specialty_key": specialty_key,
                            "style_tags": style_tags,
                            "focus_signals": list(signals_to_update),
                        },
                        "player": {
                            "player_id": str(player_id),
                            "name": p_name,
                            "pos": p_pos,
                            "age": int(p_age),
                            "height_in": int(p_h),
                            "weight_lb": int(p_w),
                            "college_team_id": p_team,
                            "class_year": int(p_class),
                            "entry_season_year": int(p_entry_sy),
                            "status": p_status,
                        },
                        "college_context": college_context,
                        "signals": signals_payload,
                        "profile_tags": profile_tags,
                        "watchlist_questions": watchlist_questions,
                        "delta_since_last": delta_since_last,
                        "meta": {
                            "note": "Scouting signals include systematic biases; confidence reflects uncertainty, not correctness.",
                        },
                    }
                    report_id = f"SREP_{assignment_id}_{pk}"

                    cur.execute(
                        """
                        INSERT INTO scouting_reports(
                            report_id, assignment_id, team_id, scout_id,
                            target_player_id, target_kind,
                            season_year, period_key, as_of_date,
                            days_covered, player_snapshot_json,
                            payload_json, report_text, status, llm_meta_json,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                        """,
                        (
                            report_id,
                            assignment_id,
                            team_id,
                            scout_id,
                            player_id,
                            target_kind,
                            int(season_year),
                            pk,
                            as_of,
                            int(days_covered),
                            _json_dumps(player_snapshot),
                            _json_dumps(payload),
                            None,  # report_text (LLM-generated below)
                            "READY_STRUCT",
                            _json_dumps({"status": "PENDING"}),
                            now,
                            now,
                        ),
                    )

                    pending_text.append(
                        {
                            "report_id": report_id,
                            "team_id": team_id,
                            "scout_id": scout_id,
                            "player_id": player_id,
                            "period_key": pk,
                            "payload": payload,
                        }
                    )

                    # Persist assignment progress
                    cur.execute(
                        """
                        UPDATE scouting_assignments
                        SET progress_json=?, updated_at=?
                        WHERE assignment_id=?;
                        """,
                        (_json_dumps(progress), now, assignment_id),
                    )

                    created += 1

            handled.append(
                {
                    "period_key": pk,
                    "as_of_date": as_of,
                    "season_year": int(season_year),
                    "created": int(created),
                    "skipped": {
                        "recent_assignment": int(skipped_recent),
                        "existing": int(skipped_existing),
                        "missing_player": int(skipped_missing_player),
                        "not_assigned_yet": int(skipped_not_assigned_yet),
                        "no_days": int(skipped_no_days),
                    },
                }
            )
            total_created += int(created)

        # -----------------------------------------------------------------
        # Option A: generate LLM report_text at month-end checkpoint
        # -----------------------------------------------------------------
        if pending_text:
            eff_key = _resolve_llm_api_key(api_key)
            if not eff_key:
                text_skipped_missing_api_key = len(pending_text)
                logger.warning(
                    "scouting_reports_text_generation_skipped: missing_api_key (pending=%s)",
                    len(pending_text),
                )
                with repo.transaction() as cur:
                    for item in pending_text:
                        rid = str(item.get("report_id") or "")
                        if not rid:
                            continue
                        cur.execute(
                            """
                            UPDATE scouting_reports
                            SET status='FAILED_TEXT', llm_meta_json=?, updated_at=?
                            WHERE report_id=?;
                            """,
                            (
                                _json_dumps(
                                    {
                                        "status": "FAILED",
                                        "error": "missing_api_key",
                                        "hint": "Provide api_key in /api/advance-league or set GEMINI_API_KEY env var.",
                                    }
                                ),
                                now,
                                rid,
                            ),
                        )
            else:
                try:
                    writer = ScoutingReportWriter(api_key=eff_key)
                except Exception as e:
                    text_failed = len(pending_text)
                    logger.exception("failed_to_initialize_scouting_report_writer: %s", e)
                    with repo.transaction() as cur:
                        for item in pending_text:
                            rid = str(item.get("report_id") or "")
                            if not rid:
                                continue
                            cur.execute(
                                """
                                UPDATE scouting_reports
                                SET status='FAILED_TEXT', llm_meta_json=?, updated_at=?
                                WHERE report_id=?;
                                """,
                                (
                                    _json_dumps(
                                        {
                                            "status": "FAILED",
                                            "error": f"writer_init_failed: {e}",
                                        }
                                    ),
                                    now,
                                    rid,
                                ),
                            )
                else:
                    for item in pending_text:
                        rid = str(item.get("report_id") or "")
                        payload = item.get("payload")
                        if not rid or not isinstance(payload, dict):
                            continue
                        try:
                            text, meta = writer.write(payload)
                            text = str(text or "").strip()
                            if not text:
                                raise ValueError("LLM returned empty report_text")

                            with repo.transaction() as cur:
                                cur.execute(
                                    """
                                    UPDATE scouting_reports
                                    SET report_text=?, status='READY_TEXT', llm_meta_json=?, updated_at=?
                                    WHERE report_id=?;
                                    """,
                                    (text, _json_dumps(meta), now, rid),
                                )
                            text_generated += 1
                        except Exception as e:
                            text_failed += 1
                            logger.warning("scouting_report_text_generation_failed: %s", e)
                            with repo.transaction() as cur:
                                cur.execute(
                                    """
                                    UPDATE scouting_reports
                                    SET status='FAILED_TEXT', llm_meta_json=?, updated_at=?
                                    WHERE report_id=?;
                                    """,
                                    (
                                        _json_dumps(
                                            {
                                                "status": "FAILED",
                                                "error": str(e),
                                            }
                                        ),
                                        now,
                                        rid,
                                    ),
                                )

    return {
        "ok": True,
        "from_date": str(from_date),
        "to_date": str(to_date),
        "handled": handled,
        "generated_reports": int(total_created),
        "llm": {
            "text_generated": int(text_generated),
            "text_failed": int(text_failed),
            "text_skipped_missing_api_key": int(text_skipped_missing_api_key),
        },
    }
