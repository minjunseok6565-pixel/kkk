from __future__ import annotations

"""MLE domain policy helpers (pure + cursor-based utilities).

Scope:
- season/channel first-year limits
- team eligibility by payroll bands + room flag
- offer validation (first-year cap, years cap, raise curve)
- seasonal first-year budget accounting helpers

Notes:
- This module intentionally does not mutate global state.
- DB writes are limited to the explicit consume_* helper using the provided cursor.
"""

from dataclasses import dataclass, field
from typing import Any, Mapping

from contracts.negotiation.utils import safe_float, safe_int
from contracts.policy.raise_limits import (
    max_raise_pct_for_contract_channel,
    validate_salary_curve_with_anchor,
)
from config import (
    MLE_ANNUAL_GROWTH_RATE,
    MLE_BASE_NT,
    MLE_BASE_ROOM,
    MLE_BASE_SEASON_YEAR,
    MLE_BASE_TP,
)

_MLE_CHANNELS = ("NT_MLE", "TP_MLE", "ROOM_MLE")
_DEFAULT_MAX_YEARS: dict[str, int] = {
    "NT_MLE": 4,
    "TP_MLE": 2,
    "ROOM_MLE": 3,
}


@dataclass(frozen=True, slots=True)
class MleOfferValidation:
    ok: bool
    channel: str
    first_year_salary: int
    first_year_limit: int
    years: int
    max_years: int
    max_raise_pct: float
    reasons: list[dict[str, Any]] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        return {
            "ok": bool(self.ok),
            "channel": str(self.channel),
            "first_year_salary": int(self.first_year_salary),
            "first_year_limit": int(self.first_year_limit),
            "years": int(self.years),
            "max_years": int(self.max_years),
            "max_raise_pct": float(self.max_raise_pct),
            "reasons": [dict(x) for x in (self.reasons or [])],
        }


def _normalize_channel(channel: Any) -> str:
    ch = str(channel or "").strip().upper()
    if ch not in _MLE_CHANNELS:
        raise ValueError(f"Unsupported MLE channel: {channel!r}")
    return ch


def _trade_rules_dict(trade_rules: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict(trade_rules) if isinstance(trade_rules, Mapping) else {}


def _mle_channels_cfg(trade_rules: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    tr = _trade_rules_dict(trade_rules)
    raw = tr.get("mle_channels")
    out: dict[str, dict[str, Any]] = {
        "NT_MLE": {"first_year_base": int(MLE_BASE_NT), "max_years": 4},
        "TP_MLE": {"first_year_base": int(MLE_BASE_TP), "max_years": 2},
        "ROOM_MLE": {"first_year_base": int(MLE_BASE_ROOM), "max_years": 3},
    }
    if not isinstance(raw, Mapping):
        return out

    for ch in _MLE_CHANNELS:
        row = raw.get(ch)
        if not isinstance(row, Mapping):
            continue
        base = int(safe_int(row.get("first_year_base"), out[ch]["first_year_base"]))
        max_years = int(safe_int(row.get("max_years"), out[ch]["max_years"]))
        if base > 0:
            out[ch]["first_year_base"] = int(base)
        if max_years > 0:
            out[ch]["max_years"] = int(max_years)
    return out


def _mle_base_season_year(trade_rules: Mapping[str, Any] | None) -> int:
    tr = _trade_rules_dict(trade_rules)
    return int(safe_int(tr.get("mle_base_season_year"), int(MLE_BASE_SEASON_YEAR)))


def _mle_growth_rate(trade_rules: Mapping[str, Any] | None) -> float:
    tr = _trade_rules_dict(trade_rules)
    growth = float(safe_float(tr.get("mle_annual_growth_rate"), float(MLE_ANNUAL_GROWTH_RATE)))
    return 0.0 if growth < 0.0 else float(growth)


def _team_payroll_for_season(cur, team_id: str, season_year: int) -> int:
    """Best-effort team payroll by summing roster salary_amount (current-season oriented)."""
    try:
        row = cur.execute(
            """
            SELECT SUM(COALESCE(salary_amount, 0)) AS payroll
            FROM roster
            WHERE UPPER(team_id)=UPPER(?) AND status='active';
            """,
            (str(team_id),),
        ).fetchone()
    except Exception:
        return 0

    if row is None:
        return 0
    try:
        return int(safe_int(row["payroll"], 0))  # sqlite.Row
    except Exception:
        try:
            return int(safe_int(row[0], 0))
        except Exception:
            return 0


def first_year_limit_for_channel(
    channel: Any,
    season_year: Any,
    trade_rules: Mapping[str, Any] | None,
) -> int:
    ch = _normalize_channel(channel)
    season = int(safe_int(season_year, 0))
    if season <= 0:
        return 0

    cfg = _mle_channels_cfg(trade_rules)
    base = int(safe_int(cfg.get(ch, {}).get("first_year_base"), 0))
    if base <= 0:
        return 0

    years_passed = int(season - _mle_base_season_year(trade_rules))
    multiplier = (1.0 + float(_mle_growth_rate(trade_rules))) ** int(years_passed)
    out = int(round(float(base) * float(multiplier)))
    return 0 if out < 0 else int(out)


def max_years_for_channel(channel: Any, trade_rules: Mapping[str, Any] | None) -> int:
    ch = _normalize_channel(channel)
    cfg = _mle_channels_cfg(trade_rules)
    mx = int(safe_int(cfg.get(ch, {}).get("max_years"), _DEFAULT_MAX_YEARS[ch]))
    return 1 if mx < 1 else int(mx)


def _room_flag_for_team(cur, team_id: str, season_year: int) -> bool:
    try:
        row = cur.execute(
            """
            SELECT became_below_cap_once
            FROM team_room_mle_flags
            WHERE season_year=? AND UPPER(team_id)=UPPER(?)
            LIMIT 1;
            """,
            (int(season_year), str(team_id)),
        ).fetchone()
    except Exception:
        return False

    if row is None:
        return False
    try:
        v = row["became_below_cap_once"]
    except Exception:
        try:
            v = row[0]
        except Exception:
            v = 0
    return bool(int(safe_int(v, 0)) == 1)


def eligible_channels_for_team(
    team_id: Any,
    season_year: Any,
    cur,
    trade_rules: Mapping[str, Any] | None,
) -> list[str]:
    """Return currently eligible MLE channels for a team.

    Eligibility policy:
    - NT_MLE: payroll > salary_cap and payroll <= first_apron
    - TP_MLE: payroll > first_apron and payroll <= second_apron
    - ROOM_MLE: team_room_mle_flags.became_below_cap_once == 1
    """
    tid = str(team_id or "").strip().upper()
    sy = int(safe_int(season_year, 0))
    if not tid or sy <= 0:
        return []

    tr = _trade_rules_dict(trade_rules)
    cap = int(safe_int(tr.get("salary_cap"), 0))
    first = int(safe_int(tr.get("first_apron"), 0))
    second = int(safe_int(tr.get("second_apron"), 0))

    payroll = _team_payroll_for_season(cur, tid, sy)

    out: list[str] = []
    if cap > 0 and first > 0 and payroll > cap and payroll <= first:
        out.append("NT_MLE")
    if first > 0 and second > 0 and payroll > first and payroll <= second:
        out.append("TP_MLE")
    if _room_flag_for_team(cur, tid, sy):
        out.append("ROOM_MLE")

    # keep deterministic ordering
    return [ch for ch in _MLE_CHANNELS if ch in out]


def _normalize_salary_by_year_map(value: Any) -> dict[int, float]:
    if not isinstance(value, Mapping):
        return {}
    out: dict[int, float] = {}
    for k, v in value.items():
        try:
            y = int(k)
        except Exception:
            continue
        salary = float(safe_float(v, 0.0))
        if salary <= 0.0:
            continue
        out[int(y)] = float(salary)
    return out


def _extract_offer_years_and_curve(offer: Mapping[str, Any]) -> tuple[int, dict[int, float]]:
    years = int(safe_int(offer.get("years"), 0))
    curve = _normalize_salary_by_year_map(offer.get("salary_by_year"))
    if years <= 0 and curve:
        years = int(len(curve))
    return years, curve


def validate_mle_offer(
    channel: Any,
    offer: Mapping[str, Any],
    season_year: Any,
    trade_rules: Mapping[str, Any] | None,
) -> MleOfferValidation:
    ch = _normalize_channel(channel)
    sy = int(safe_int(season_year, 0))
    years, curve = _extract_offer_years_and_curve(offer if isinstance(offer, Mapping) else {})

    reasons: list[dict[str, Any]] = []

    limit_first = int(first_year_limit_for_channel(ch, sy, trade_rules))
    max_years = int(max_years_for_channel(ch, trade_rules))

    first_salary = 0
    if curve:
        y0 = min(curve.keys())
        first_salary = int(round(float(curve[y0])))
    if years <= 0:
        reasons.append({"code": "MLE_INVALID_YEARS", "message": "offer.years must be >= 1"})
    if years > max_years:
        reasons.append(
            {
                "code": "MLE_YEARS_EXCEED_MAX",
                "message": f"{ch} max years exceeded",
                "details": {"years": int(years), "max_years": int(max_years)},
            }
        )
    if first_salary <= 0:
        reasons.append({"code": "MLE_INVALID_FIRST_YEAR_SALARY", "message": "first-year salary must be > 0"})
    elif limit_first > 0 and first_salary > limit_first:
        reasons.append(
            {
                "code": "MLE_FIRST_YEAR_LIMIT_EXCEEDED",
                "message": f"{ch} first-year limit exceeded",
                "details": {"first_year_salary": int(first_salary), "limit": int(limit_first)},
            }
        )

    max_raise_pct = float(max_raise_pct_for_contract_channel(ch, trade_rules=trade_rules, season_year=sy))
    anchor_salary = float(curve[min(curve.keys())]) if curve else 0.0
    raise_chk = validate_salary_curve_with_anchor(
        curve,
        anchor_salary=anchor_salary,
        max_delta_pct=max_raise_pct,
        allow_descend=True,
    )
    if not raise_chk.ok:
        reasons.append(
            {
                "code": "MLE_RAISE_LIMIT_EXCEEDED",
                "message": "salary raise curve exceeds max_raise_pct",
                "details": raise_chk.to_payload(),
            }
        )

    return MleOfferValidation(
        ok=(len(reasons) == 0),
        channel=str(ch),
        first_year_salary=int(first_salary),
        first_year_limit=int(limit_first),
        years=int(years),
        max_years=int(max_years),
        max_raise_pct=float(max_raise_pct),
        reasons=reasons,
    )


def _budget_spent_first_year_total(
    team_id: Any,
    channel: Any,
    season_year: Any,
    cur,
) -> int:
    tid = str(team_id or "").strip().upper()
    ch = _normalize_channel(channel)
    sy = int(safe_int(season_year, 0))
    if not tid or sy <= 0:
        return 0

    try:
        row = cur.execute(
            """
            SELECT first_year_spent_total
            FROM team_contract_exception_budget_usage
            WHERE season_year=? AND UPPER(team_id)=UPPER(?) AND channel=?
            LIMIT 1;
            """,
            (int(sy), str(tid), str(ch)),
        ).fetchone()
    except Exception:
        return 0

    if row is None:
        return 0
    try:
        return int(safe_int(row["first_year_spent_total"], 0))
    except Exception:
        try:
            return int(safe_int(row[0], 0))
        except Exception:
            return 0


def get_remaining_first_year_budget(
    team_id: Any,
    channel: Any,
    season_year: Any,
    cur,
    trade_rules: Mapping[str, Any] | None,
) -> int:
    limit_first = int(first_year_limit_for_channel(channel, season_year, trade_rules))
    spent = int(_budget_spent_first_year_total(team_id, channel, season_year, cur))
    remain = int(limit_first) - int(spent)
    return 0 if remain < 0 else int(remain)


def consume_first_year_budget(
    team_id: Any,
    channel: Any,
    season_year: Any,
    first_year_salary: Any,
    cur,
    trade_rules: Mapping[str, Any] | None,
) -> dict[str, int | str | bool]:
    """Consume first-year budget for team/channel/season via UPSERT.

    Returns a small payload with before/after remaining budget.
    """
    tid = str(team_id or "").strip().upper()
    ch = _normalize_channel(channel)
    sy = int(safe_int(season_year, 0))
    amount = int(safe_int(first_year_salary, 0))
    if not tid:
        raise ValueError("team_id is required")
    if sy <= 0:
        raise ValueError("season_year must be > 0")
    if amount <= 0:
        raise ValueError("first_year_salary must be > 0")

    remaining_before = int(get_remaining_first_year_budget(tid, ch, sy, cur, trade_rules))
    if amount > remaining_before:
        raise ValueError(
            f"Insufficient MLE first-year budget: channel={ch} remaining={remaining_before} requested={amount}"
        )

    try:
        cur.execute(
            """
            INSERT INTO team_contract_exception_budget_usage(
                season_year, team_id, channel, first_year_spent_total
            ) VALUES (?, UPPER(?), ?, ?)
            ON CONFLICT(season_year, team_id, channel)
            DO UPDATE SET first_year_spent_total = first_year_spent_total + excluded.first_year_spent_total;
            """,
            (int(sy), str(tid), str(ch), int(amount)),
        )
    except Exception as exc:
        raise RuntimeError("Failed to consume first-year budget (table/migration required)") from exc

    remaining_after = int(get_remaining_first_year_budget(tid, ch, sy, cur, trade_rules))
    return {
        "ok": True,
        "team_id": str(tid),
        "channel": str(ch),
        "season_year": int(sy),
        "consumed": int(amount),
        "remaining_before": int(remaining_before),
        "remaining_after": int(remaining_after),
    }
