from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from contracts.terms import remaining_salary_total, remaining_years, salary_for_season, salary_schedule

from .types import ContractOptionSnapshot, ContractSnapshot


@dataclass(frozen=True, slots=True)
class ContractTexture:
    guaranteed_commitment: float
    control_direction: float
    reversibility: float
    trigger_risk: float
    matching_utility: float
    toxic_risk: float
    notes: tuple[str, ...]
    source_coverage: Mapping[str, bool]


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, float(v)))


def _option_direction(option_type: str) -> float:
    t = str(option_type or "").strip().upper()
    # 설계 요구: ContractSnapshot.options의 TO/PO/ETO 매핑 사용
    if t in {"TEAM", "TO"}:
        return 1.0
    if t in {"PLAYER", "PO", "ETO"}:
        return -1.0
    return 0.0


def _pending_options(options: Sequence[ContractOptionSnapshot]) -> list[ContractOptionSnapshot]:
    out: list[ContractOptionSnapshot] = []
    for o in options:
        status = str(o.status or "").upper()
        if status in {"", "PENDING"}:
            out.append(o)
    return out


def build_contract_textures(
    contracts: Sequence[ContractSnapshot],
    *,
    current_season_year: int,
    salary_cap: float | None,
) -> dict[str, ContractTexture]:
    out: dict[str, ContractTexture] = {}

    has_cap = salary_cap is not None and float(salary_cap) > 0.0
    cap_value = float(salary_cap or 0.0)

    for c in contracts:
        notes: list[str] = []
        coverage = {
            "contract_terms": True,
            "options": True,
            "salary_cap": has_cap,
        }

        rem_years = int(remaining_years(c, current_season_year=int(current_season_year)))
        rem_total = float(remaining_salary_total(c, current_season_year=int(current_season_year)))
        sched = salary_schedule(c, from_year=int(current_season_year), positive_only=True)

        if rem_years <= 0:
            coverage["contract_terms"] = False
            notes.append(f"MISSING_INPUT_CONTRACT_TERMS:{c.contract_id}")

        pending = _pending_options(c.options)
        if c.options and not pending:
            notes.append(f"OPTION_ALREADY_RESOLVED:{c.contract_id}")

        direction_vals = [_option_direction(o.type) for o in pending]
        control_direction = 0.0
        if direction_vals:
            control_direction = float(sum(direction_vals) / len(direction_vals))

        if any(abs(v) < 1e-12 for v in direction_vals):
            notes.append(f"MISSING_INPUT_OPTION_TYPE:{c.contract_id}")

        trigger_risk = _clamp01(len(pending) / max(rem_years, 1))

        base_reversibility = 1.0 / (1.0 + max(rem_years, 0))
        opt_reversibility = 0.0
        if direction_vals:
            # 팀 옵션 많을수록 되돌리기 쉬움(+), 플레이어 옵션은 어려움(-)
            opt_reversibility = 0.15 * (sum(direction_vals) / len(direction_vals))
        reversibility = _clamp01(base_reversibility + opt_reversibility)

        salary_now = float(salary_for_season(c, int(current_season_year)))

        if has_cap:
            commit_ratio = rem_total / cap_value
            current_ratio = salary_now / cap_value
            # 너무 큰 만기부담은 매칭 유틸 저하, 현재연봉이 중간대면 거래 매칭 유틸 ↑
            matching_utility = _clamp01((1.0 - _clamp01(commit_ratio / 2.5)) * (1.0 - abs(current_ratio - 0.18)))
            toxic_risk = _clamp01(_clamp01(commit_ratio / 2.0) + 0.25 * max(0.0, -control_direction) + 0.15 * trigger_risk)
        else:
            matching_utility = 0.0
            toxic_risk = _clamp01(0.25 * max(0.0, -control_direction) + 0.15 * trigger_risk)
            notes.append(f"MISSING_INPUT_SALARY_CAP:{c.contract_id}")

        if not sched:
            notes.append(f"MISSING_INPUT_SALARY_BY_YEAR:{c.contract_id}")

        out[c.contract_id] = ContractTexture(
            guaranteed_commitment=rem_total,
            control_direction=float(control_direction),
            reversibility=float(reversibility),
            trigger_risk=float(trigger_risk),
            matching_utility=float(matching_utility),
            toxic_risk=float(toxic_risk),
            notes=tuple(notes),
            source_coverage=coverage,
        )

    return out
