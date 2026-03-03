from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...errors import DEAL_INVALIDATED, TradeError
from ..base import TradeContext, build_team_trade_totals, build_team_payrolls
from ..policies.salary_matching_policy import (
    SalaryMatchingParams,
    build_trade_error_details,
    check_salary_matching,
)


def _to_int_dollars(x: Any) -> int:
    """float/int/str 등을 달러 단위 정수로 안전하게 변환.

    SalaryMatchingPolicy가 달러 정수 기반이므로 룰에서도 동일 스케일로 입력을 정규화한다.
    """
    try:
        return int(round(float(x)))
    except Exception:
        return 0


@dataclass
class SalaryMatchingRule:
    rule_id: str = "salary_matching"
    priority: int = 90
    enabled: bool = True

    def validate(self, deal, ctx: TradeContext) -> None:
        trade_rules = ctx.game_state.get("league", {}).get("trade_rules", {})
        params = SalaryMatchingParams.from_trade_rules(trade_rules)

        trade_totals = build_team_trade_totals(deal, ctx)
        payrolls = build_team_payrolls(deal, ctx, trade_totals=trade_totals)

        for team_id in deal.teams:
            totals = trade_totals[team_id]
            outgoing_salary = float(totals.get("outgoing_salary") or 0.0)
            incoming_salary = float(totals.get("incoming_salary") or 0.0)
            max_outgoing_salary = float(totals.get("max_outgoing_salary") or 0.0)
            outgoing_players = int(totals.get("outgoing_players_count") or 0)
            incoming_players = int(totals.get("incoming_players_count") or 0)

            if incoming_salary == 0:
                continue

            payroll_before = float(payrolls[team_id].get("payroll_before") or 0.0)
            payroll_after = float(payrolls[team_id].get("payroll_after") or 0.0)
            
            payroll_before_d = _to_int_dollars(payroll_before)
            outgoing_salary_d = _to_int_dollars(outgoing_salary)
            incoming_salary_d = _to_int_dollars(incoming_salary)
            max_outgoing_salary_d = _to_int_dollars(max_outgoing_salary)

            result = check_salary_matching(
                payroll_before_d=payroll_before_d,
                outgoing_salary_d=outgoing_salary_d,
                incoming_salary_d=incoming_salary_d,
                outgoing_players=outgoing_players,
                incoming_players=incoming_players,
                max_single_outgoing_salary_d=max_outgoing_salary_d,
                params=params,
            )

            if result.ok:
                continue

            # TradeError.details 포맷은 기존과 동일하게 유지한다(생성기/repair 파이프라인 호환성).
            details = build_trade_error_details(
                rule_id=self.rule_id,
                team_id=team_id,
                payroll_before_d=payroll_before_d,
                outgoing_salary_d=outgoing_salary_d,
                incoming_salary_d=incoming_salary_d,
                result=result,
            )

            # legacy payload fidelity: 기존 SSOT 계산 값( float )을 그대로 유지
            details["payroll_before"] = payroll_before
            details["payroll_after"] = payroll_after
            details["outgoing_salary"] = outgoing_salary
            details["incoming_salary"] = incoming_salary

            # allowed_in 타입도 기존과 최대한 일치시킨다.
            if result.method in ("outgoing_second_apron", "outgoing_first_apron"):
                details["allowed_in"] = int(result.allowed_in_d)
            elif result.method in ("outgoing_required",):
                details["allowed_in"] = 0.0
            else:
                details["allowed_in"] = float(result.allowed_in_d)

            raise TradeError(DEAL_INVALIDATED, "Salary matching failed", details)
