from __future__ import annotations

"""Shared raise-limit SSOT and validators for contract salary curves.

This module is intentionally pure:
- no DB access
- no state import
- deterministic calculations only

Design:
- one SSOT map for contract channel/type -> max raise pct
- optional per-league override via trade_rules mapping
- reusable validator for salary_by_year curves
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping

from contracts.negotiation.utils import safe_float


DEFAULT_MAX_RAISE_PCT_BY_CHANNEL: dict[str, float] = {
    # Main FA route (non-MLE)
    "STANDARD_FA": 0.08,
    # MLE channels
    "NT_MLE": 0.05,
    "TP_MLE": 0.05,
    "ROOM_MLE": 0.05,
    # Other negotiation types (defaults; tunable)
    "RE_SIGN": 0.08,
    "EXTEND": 0.08,
}


@dataclass(frozen=True, slots=True)
class RaiseCurveValidation:
    ok: bool
    max_raise_pct: float
    checked_years: list[int] = field(default_factory=list)
    violations: list[dict[str, Any]] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        return {
            "ok": bool(self.ok),
            "max_raise_pct": float(self.max_raise_pct),
            "checked_years": [int(y) for y in (self.checked_years or [])],
            "violations": [dict(v) for v in (self.violations or [])],
        }


def _normalize_channel(channel: Any) -> str:
    return str(channel or "STANDARD_FA").strip().upper() or "STANDARD_FA"


def _coerce_raise_overrides(raw: Any) -> dict[str, float]:
    """Extract optional override map from trade_rules-like mapping.

    Expected shape:
      trade_rules["contract_raise_max_pct_by_channel"] = {
          "STANDARD_FA": 0.08,
          "NT_MLE": 0.05,
          ...
      }
    """
    if not isinstance(raw, Mapping):
        return {}
    out: dict[str, float] = {}
    for k, v in raw.items():
        key = _normalize_channel(k)
        pct = float(safe_float(v, -1.0))
        if pct < 0.0:
            continue
        out[key] = float(pct)
    return out


def max_raise_pct_for_contract_channel(
    channel: Any,
    trade_rules: Mapping[str, Any] | None = None,
    season_year: Any | None = None,
) -> float:
    """Return max raise pct for a given contract channel/type.

    `season_year` is accepted for forward compatibility (future seasonal overrides)
    and currently not used for branching.
    """
    _ = season_year  # forward-compat placeholder
    ch = _normalize_channel(channel)

    base = float(DEFAULT_MAX_RAISE_PCT_BY_CHANNEL.get(ch, DEFAULT_MAX_RAISE_PCT_BY_CHANNEL["STANDARD_FA"]))

    tr = trade_rules if isinstance(trade_rules, Mapping) else {}
    overrides = _coerce_raise_overrides(tr.get("contract_raise_max_pct_by_channel"))
    if ch in overrides:
        return float(overrides[ch])
    return float(base)


def _normalize_salary_by_year(salary_by_year: Mapping[Any, Any] | None) -> dict[int, float]:
    if not isinstance(salary_by_year, Mapping):
        return {}
    out: dict[int, float] = {}
    for k, v in salary_by_year.items():
        try:
            y = int(k)
        except Exception:
            continue
        val = float(safe_float(v, 0.0))
        if val <= 0.0:
            continue
        out[int(y)] = float(val)
    return out


def validate_salary_raise_curve(
    salary_by_year: Mapping[Any, Any] | None,
    max_raise_pct: Any,
) -> RaiseCurveValidation:
    """Validate that each next-year salary <= previous_year * (1 + max_raise_pct)."""
    curve = _normalize_salary_by_year(salary_by_year)
    pct = float(safe_float(max_raise_pct, 0.0))
    if pct < 0.0:
        pct = 0.0

    years = sorted(curve.keys())
    if len(years) <= 1:
        return RaiseCurveValidation(ok=True, max_raise_pct=float(pct), checked_years=[int(y) for y in years], violations=[])

    violations: list[dict[str, Any]] = []
    eps = 1e-6
    for i in range(1, len(years)):
        prev_y = int(years[i - 1])
        y = int(years[i])
        prev_sal = float(curve[prev_y])
        sal = float(curve[y])
        max_allowed = float(prev_sal) * (1.0 + float(pct))
        if sal > (max_allowed + eps):
            violations.append(
                {
                    "prev_year": int(prev_y),
                    "year": int(y),
                    "prev_salary": float(prev_sal),
                    "salary": float(sal),
                    "max_allowed": float(max_allowed),
                    "raise_pct": (float(sal) / float(prev_sal) - 1.0) if prev_sal > 0.0 else None,
                }
            )

    return RaiseCurveValidation(
        ok=(len(violations) == 0),
        max_raise_pct=float(pct),
        checked_years=[int(y) for y in years],
        violations=violations,
    )
