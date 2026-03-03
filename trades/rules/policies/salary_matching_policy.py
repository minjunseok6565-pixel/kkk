from __future__ import annotations

"""Salary matching policy.

This module centralizes salary matching logic so that validation rules and
generation/AI systems can share identical behavior.

Policy mirrors the behavior currently implemented in
`trades/rules/builtin/salary_matching_rule.py`:

- apron status is based on *payroll_after* (after applying the trade)
- cap-room exception is evaluated before any matching requirement
- SECOND_APRON enforces the post-2024 CBA aggregation ban (incoming must be matchable by a single outgoing salary)
- allowed incoming salary is computed per the same thresholds/multipliers

The policy operates primarily on *dollar integer* inputs ("*_d" suffix) to
avoid float rounding drift, but preserves the SSOT ordering and method strings
used by SalaryMatchingRule so that callers can round-trip into TradeError.details.
"""

from dataclasses import dataclass
import math
from typing import Any, Dict, Mapping, Optional, Tuple

from salary_matching_brackets import derive_salary_matching_brackets


def _to_int_dollars(x: Any) -> int:
    """Convert float/int/str into a dollar integer defensively."""

    try:
        return int(round(float(x)))
    except Exception:
        return 0


@dataclass(frozen=True, slots=True)
class SalaryMatchingParams:
    """Season/cap model parameters for salary matching."""

    salary_cap_d: int
    first_apron_d: int
    second_apron_d: int

    match_small_out_max_d: int
    match_mid_out_max_d: int
    match_mid_add_d: int
    match_buffer_d: int

    first_apron_mult: float
    second_apron_mult: float

    @classmethod
    def from_trade_rules(cls, trade_rules: Mapping[str, Any]) -> "SalaryMatchingParams":
        """Build params from league.trade_rules.

        Notes:
            - `match_mid_add` is season-scaled in state_modules/state_cap.py when
              match_auto_update is enabled.
            - Bracket thresholds are derived from (mid_add, buffer) to guarantee
              continuity at the boundaries. This prevents non-monotonic allowed
              incoming amounts caused by inconsistent constants.
        """

        tr = trade_rules or {}

        # Core matching knobs.
        match_mid_add_d = _to_int_dollars(
            tr.get("match_mid_add")
            or tr.get("match_base_mid_add")
            or 8_527_000
        )
        match_buffer_d = _to_int_dollars(tr.get("match_buffer") or 250_000)

        # Derive thresholds for continuity (SSOT: salary_matching_brackets.py).
        match_small_out_max_d, match_mid_out_max_d = derive_salary_matching_brackets(
            match_mid_add_d=int(match_mid_add_d),
            match_buffer_d=int(match_buffer_d),
        )
        
        return cls(
            salary_cap_d=_to_int_dollars(tr.get("salary_cap") or 0.0),
            first_apron_d=_to_int_dollars(tr.get("first_apron") or 0.0),
            second_apron_d=_to_int_dollars(tr.get("second_apron") or 0.0),
            match_small_out_max_d=match_small_out_max_d,
            match_mid_out_max_d=match_mid_out_max_d,
            match_mid_add_d=match_mid_add_d,
            match_buffer_d=match_buffer_d,
            first_apron_mult=float(tr.get("first_apron_mult") or 1.00),
            second_apron_mult=float(tr.get("second_apron_mult") or 1.00),
        )


@dataclass(frozen=True, slots=True)
class SalaryMatchingResult:
    """Result of salary matching check (dollar integer based)."""

    ok: bool
    status: str
    method: str
    allowed_in_d: int
    payroll_after_d: int
    max_incoming_cap_room_d: Optional[int] = None
    max_single_outgoing_salary_d: Optional[int] = None
    reason: Optional[str] = None


def resolve_apron_status(payroll_after_d: int, params: SalaryMatchingParams) -> str:
    if payroll_after_d >= params.second_apron_d:
        return "SECOND_APRON"
    if payroll_after_d >= params.first_apron_d:
        return "FIRST_APRON"
    return "BELOW_FIRST_APRON"


def check_salary_matching(
    *,
    payroll_before_d: int,
    outgoing_salary_d: int,
    incoming_salary_d: int,
    outgoing_players: int,
    incoming_players: int,
    max_single_outgoing_salary_d: Optional[int] = None,
    params: SalaryMatchingParams,
    eps: float = 1e-6,
) -> SalaryMatchingResult:
    """Check salary matching for a single team.

    Args:
        payroll_before_d: Team payroll before trade (dollars).
        outgoing_salary_d: Outgoing salary in the trade (dollars).
        incoming_salary_d: Incoming salary in the trade (dollars).
        outgoing_players: Outgoing player count.
        incoming_players: Incoming player count.
        max_single_outgoing_salary_d: Largest single outgoing player salary (dollars).
            Required for SECOND_APRON (post-2024 aggregation ban); callers should compute this
            from the outgoing player list rather than relying on outgoing_salary_d (which is a sum).
        params: SalaryMatchingParams.
        eps: Small epsilon added before floor() to counteract float drift.

    Returns:
        SalaryMatchingResult(ok, status, method, allowed_in_d, payroll_after_d, ...)

    Notes:
        - Mirrors SalaryMatchingRule.validate ordering.
        - method strings are aligned with SSOT (SalaryMatchingRule / repair).
    """

    # SalaryMatchingRule: teams with no incoming salary simply pass.
    if incoming_salary_d <= 0:
        return SalaryMatchingResult(
            ok=True,
            status="",
            method="no_incoming",
            allowed_in_d=0,
            payroll_after_d=int(payroll_before_d - outgoing_salary_d + incoming_salary_d),
            reason="no_incoming",
        )

    payroll_after_d = int(payroll_before_d - outgoing_salary_d + incoming_salary_d)
    status = resolve_apron_status(payroll_after_d, params)

    # cap-room exception (SSOT와 동일한 위치/순서)
    if payroll_before_d < params.salary_cap_d:
        cap_room_d = params.salary_cap_d - payroll_before_d
        max_incoming_d = cap_room_d + outgoing_salary_d
        if incoming_salary_d <= max_incoming_d:
            return SalaryMatchingResult(
                ok=True,
                status=status,
                method="cap_room",
                allowed_in_d=max_incoming_d,
                payroll_after_d=payroll_after_d,
                max_incoming_cap_room_d=max_incoming_d,
                reason="cap_room_ok",
            )

    if outgoing_salary_d <= 0:
        return SalaryMatchingResult(
            ok=False,
            status=status,
            method="outgoing_required",
            allowed_in_d=0,
            payroll_after_d=payroll_after_d,
            reason="outgoing_required",
        )

    # SECOND_APRON (post-2024 CBA): no outgoing salary aggregation for matching.
    #
    # Practical rule:
    #   - A 2nd-apron team may not use multiple outgoing salaries to determine its allowed incoming.
    #   - Therefore, allowed incoming is based on the *single largest* outgoing player salary.
    #
    # Callers must pass `max_single_outgoing_salary_d` computed from the outgoing player list.
    if status == "SECOND_APRON":
        max_single_d = int(max_single_outgoing_salary_d or 0)

        # Backwards-compatible fallback: if not supplied, approximate only when the deal has
        # exactly one outgoing player; otherwise fail closed (allowed_in=0).
        if max_single_d <= 0 and int(outgoing_players) == 1:
            max_single_d = int(outgoing_salary_d)

        # Defensive: never exceed total outgoing salary (max_single <= sum by definition).
        if max_single_d > int(outgoing_salary_d):
            max_single_d = int(outgoing_salary_d)

        allowed_in_d = int(math.floor(max_single_d * params.second_apron_mult + eps))
        method = "outgoing_second_apron"

    elif status == "FIRST_APRON":
        allowed_in_d = int(math.floor(outgoing_salary_d * params.first_apron_mult + eps))
        method = "outgoing_first_apron"

    else:
        if outgoing_salary_d <= params.match_small_out_max_d:
            allowed_in_d = int(2 * outgoing_salary_d + params.match_buffer_d)
        elif outgoing_salary_d <= params.match_mid_out_max_d:
            allowed_in_d = int(outgoing_salary_d + params.match_mid_add_d)
        else:
            # SSOT uses floor(outgoing * 1.25) + buffer; 1.25 == 5/4 so integer math is exact.
            allowed_in_d = int((outgoing_salary_d * 5) // 4 + params.match_buffer_d)
        method = "outgoing_below_first_apron"

    if incoming_salary_d > allowed_in_d:
        return SalaryMatchingResult(
            ok=False,
            status=status,
            method=method,
            allowed_in_d=allowed_in_d,
            payroll_after_d=payroll_after_d,
            reason="incoming_gt_allowed_in",
        )

    return SalaryMatchingResult(
        ok=True,
        status=status,
        method=method,
        allowed_in_d=allowed_in_d,
        payroll_after_d=payroll_after_d,
        reason="ok",
    )


def check_salary_matching_with_evidence(
    *,
    payroll_before_d: int,
    outgoing_salary_d: int,
    incoming_salary_d: int,
    outgoing_players: int,
    incoming_players: int,
    max_single_outgoing_salary_d: Optional[int] = None,
    trade_rules: Mapping[str, Any],
    eps: float = 1e-6,
) -> Tuple[SalaryMatchingResult, Dict[str, Any]]:
    """Convenience wrapper that also returns an evidence payload."""

    params = SalaryMatchingParams.from_trade_rules(trade_rules)
    result = check_salary_matching(
        payroll_before_d=payroll_before_d,
        outgoing_salary_d=outgoing_salary_d,
        incoming_salary_d=incoming_salary_d,
        outgoing_players=outgoing_players,
        incoming_players=incoming_players,
        max_single_outgoing_salary_d=max_single_outgoing_salary_d,
        params=params,
        eps=eps,
    )

    evidence: Dict[str, Any] = {
        "payroll_before_d": payroll_before_d,
        "outgoing_salary_d": outgoing_salary_d,
        "incoming_salary_d": incoming_salary_d,
        "outgoing_players": outgoing_players,
        "incoming_players": incoming_players,
        "max_single_outgoing_salary_d": int(max_single_outgoing_salary_d or 0),
        "payroll_after_d": result.payroll_after_d,
        "status": result.status,
        "method": result.method,
        "allowed_in_d": result.allowed_in_d,
        "ok": result.ok,
        "reason": result.reason,
        "params": {
            "salary_cap_d": params.salary_cap_d,
            "first_apron_d": params.first_apron_d,
            "second_apron_d": params.second_apron_d,
            "match_small_out_max_d": params.match_small_out_max_d,
            "match_mid_out_max_d": params.match_mid_out_max_d,
            "match_mid_add_d": params.match_mid_add_d,
            "match_buffer_d": params.match_buffer_d,
            "first_apron_mult": params.first_apron_mult,
            "second_apron_mult": params.second_apron_mult,
        },
    }
    return result, evidence


def build_trade_error_details(
    *,
    rule_id: str,
    team_id: str,
    payroll_before_d: int,
    outgoing_salary_d: int,
    incoming_salary_d: int,
    result: SalaryMatchingResult,
) -> Dict[str, Any]:
    """Build a TradeError.details payload compatible with SalaryMatchingRule.

    Callers (rules/generator) can use this to keep a stable details schema.
    Values are emitted as float to match existing TradeError payloads.
    """

    return {
        "rule": str(rule_id),
        "team_id": str(team_id),
        "status": result.status,
        "payroll_before": float(payroll_before_d),
        "payroll_after": float(result.payroll_after_d),
        "outgoing_salary": float(outgoing_salary_d),
        "incoming_salary": float(incoming_salary_d),
        "allowed_in": float(result.allowed_in_d),
        "method": str(result.method),
    }
