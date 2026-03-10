from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from cap_model import CapModel

from .contract_texture import ContractTexture

_ALLOWED_POSTURES = frozenset({"AGGRESSIVE_BUY", "SOFT_BUY", "STAND_PAT", "SOFT_SELL", "SELL"})

_POSTURE_WEIGHT = {
    "AGGRESSIVE_BUY": 0.60,
    "SOFT_BUY": 0.80,
    "STAND_PAT": 1.00,
    "SOFT_SELL": 1.20,
    "SELL": 1.40,
}


@dataclass(frozen=True, slots=True)
class CapLedgerView:
    current_operating_room: float
    future_locked_money: float
    optionality_profile: float
    liquidity_profile: float
    apron_pressure: float
    extension_pressure: float | None
    posture_adjusted_flex: float
    source_coverage: Mapping[str, bool]
    ledger_notes: tuple[str, ...]


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def _sf(x: object, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return float(default)


def _normalize_posture(posture: str) -> tuple[str, bool]:
    p = str(posture or "").strip().upper()
    if p in _ALLOWED_POSTURES:
        return (p, True)
    return ("STAND_PAT", False)


def _aggregate_texture_metrics(textures: Sequence[ContractTexture]) -> tuple[float, float, float, float, float, list[str]]:
    notes: list[str] = []
    if not textures:
        notes.append("MISSING_INPUT_CONTRACT_TEXTURES")
        return (0.0, 0.0, 0.0, 0.0, 0.0, notes)

    future_locked = sum(_sf(t.guaranteed_commitment, 0.0) for t in textures)
    avg_optionality = sum(_sf(t.reversibility, 0.0) for t in textures) / max(len(textures), 1)
    avg_liquidity = sum(_sf(t.matching_utility, 0.0) for t in textures) / max(len(textures), 1)
    avg_trigger = sum(_sf(t.trigger_risk, 0.0) for t in textures) / max(len(textures), 1)
    avg_toxic = sum(_sf(t.toxic_risk, 0.0) for t in textures) / max(len(textures), 1)

    return (
        float(future_locked),
        _clamp01(avg_optionality),
        _clamp01(avg_liquidity),
        _clamp01(avg_trigger),
        _clamp01(avg_toxic),
        notes,
    )


def build_cap_ledgers(
    team_contract_textures: Mapping[str, Sequence[ContractTexture]],
    *,
    posture_by_team: Mapping[str, str],
    current_season_year: int,
    cap_model: CapModel,
    team_payroll_map: Mapping[str, float],
) -> dict[str, CapLedgerView]:
    out: dict[str, CapLedgerView] = {}

    cap_nums = cap_model.numbers_for_season(int(current_season_year))
    salary_cap = float(cap_nums.salary_cap)
    first_apron = float(cap_nums.first_apron)

    team_ids = set(str(k) for k in team_contract_textures.keys()) | set(str(k) for k in team_payroll_map.keys())

    for team_id in sorted(team_ids):
        notes: list[str] = []
        coverage = {
            "contract_textures": True,
            "posture": True,
            "cap_model": True,
            "team_payroll": True,
            "extension_pressure": True,
        }

        posture_raw = posture_by_team.get(team_id, "STAND_PAT")
        posture, posture_ok = _normalize_posture(str(posture_raw))
        if not posture_ok:
            coverage["posture"] = False
            notes.append(f"MISSING_INPUT_POSTURE:{team_id}")

        payroll = team_payroll_map.get(team_id)
        if payroll is None:
            coverage["team_payroll"] = False
            notes.append(f"MISSING_INPUT_TEAM_PAYROLL:{team_id}")
            payroll_value = 0.0
        else:
            payroll_value = _sf(payroll, 0.0)

        textures = list(team_contract_textures.get(team_id, ()))
        (
            future_locked,
            optionality,
            liquidity,
            avg_trigger,
            avg_toxic,
            agg_notes,
        ) = _aggregate_texture_metrics(textures)
        if agg_notes:
            coverage["contract_textures"] = False
            notes.extend(agg_notes)

        current_operating_room = float(salary_cap - payroll_value)
        apron_pressure = _clamp01((payroll_value - first_apron) / max(salary_cap, 1.0)) if payroll_value > first_apron else 0.0

        extension_pressure: float | None
        if textures:
            extension_pressure = _clamp01(avg_trigger * (1.0 - optionality))
        else:
            extension_pressure = None
            coverage["extension_pressure"] = False
            notes.append(f"MISSING_INPUT_EXTENSION_PRESSURE:{team_id}")

        raw_flex = (
            0.45 * _clamp01(current_operating_room / max(salary_cap, 1.0))
            + 0.25 * optionality
            + 0.20 * liquidity
            - 0.20 * apron_pressure
            - 0.20 * avg_toxic
            - 0.10 * (extension_pressure if extension_pressure is not None else 0.0)
        )
        posture_adjusted_flex = float(raw_flex * _POSTURE_WEIGHT.get(posture, 1.0))

        out[team_id] = CapLedgerView(
            current_operating_room=float(current_operating_room),
            future_locked_money=float(future_locked),
            optionality_profile=float(optionality),
            liquidity_profile=float(liquidity),
            apron_pressure=float(apron_pressure),
            extension_pressure=(None if extension_pressure is None else float(extension_pressure)),
            posture_adjusted_flex=float(posture_adjusted_flex),
            source_coverage=coverage,
            ledger_notes=tuple(notes),
        )

    return out


def score_cap_flex_delta(
    before: CapLedgerView,
    after: CapLedgerView,
    *,
    posture: str,
) -> float:
    posture_norm, _ = _normalize_posture(posture)
    weight = _POSTURE_WEIGHT.get(posture_norm, 1.0)

    delta_flex = _sf(after.posture_adjusted_flex) - _sf(before.posture_adjusted_flex)
    delta_room = (_sf(after.current_operating_room) - _sf(before.current_operating_room)) / 10_000_000.0
    delta_apron = _sf(before.apron_pressure) - _sf(after.apron_pressure)

    # posture는 점수 민감도만 조절(절대 규정 판정 아님)
    return float((0.70 * delta_flex + 0.20 * delta_room + 0.10 * delta_apron) * weight)
