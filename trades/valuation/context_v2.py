from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from contracts.terms import remaining_years, salary_for_season

from cap_model import CapModel
from decision_context import DecisionContext

from .cap_ledger import CapLedgerView, build_cap_ledgers
from .contract_texture import ContractTexture, build_contract_textures
from .decision_policy import DecisionPolicyConfig
from .draft_lottery_rules import get_draft_lottery_rules
from .fit_engine import FitEngine
from .market_pricing import MarketPricingConfig
from .package_effects import PackageEffectsConfig
from .pick_distribution import PickDistributionBundle, build_pick_distributions_from_standings
from .role_texture import RoleTexture, build_role_textures
from .team_utility import TeamUtilityConfig
from .types import (
    ContractSnapshot,
    PickSnapshot,
    PlayerSnapshot,
    SwapSnapshot,
    ValuationDataProvider,
)


@dataclass(frozen=True, slots=True)
class ContextDiffReport:
    """Dual-read(v1/v2) 최소 diff telemetry 리포트."""

    pick_ev_delta: float
    contract_burden_delta: float
    cap_flex_delta: float
    missing_metrics: tuple[str, ...] = tuple()


@dataclass(frozen=True, slots=True)
class ContextDiagnostics:
    """v2 context build 중 coverage/reason 상태를 기록한다."""

    source_coverage: Mapping[str, bool]
    reason_flags: tuple[str, ...] = tuple()
    diff_report: ContextDiffReport | None = None


@dataclass(frozen=True, slots=True)
class ValuationContextV2:
    players: Mapping[str, PlayerSnapshot]
    picks: Mapping[str, PickSnapshot]
    swaps: Mapping[str, SwapSnapshot]
    contracts: Mapping[str, ContractSnapshot]
    pick_distributions: Mapping[str, PickDistributionBundle]
    role_textures: Mapping[str, RoleTexture]
    contract_textures: Mapping[str, ContractTexture]
    cap_ledgers: Mapping[str, CapLedgerView]
    diagnostics: ContextDiagnostics


def _get_ids(asset_ids_by_kind: Mapping[str, Sequence[str]], *keys: str) -> tuple[str, ...]:
    out: list[str] = []
    for k in keys:
        for v in asset_ids_by_kind.get(k, ()):
            sv = str(v)
            if sv and sv not in out:
                out.append(sv)
    return tuple(out)


def _safe_delta(v1_metrics: Mapping[str, float], v2_metrics: Mapping[str, float], key: str) -> float:
    try:
        return float(v2_metrics.get(key, 0.0)) - float(v1_metrics.get(key, 0.0))
    except Exception:
        return 0.0


def _extract_standings_order(asset_ids_by_kind: Mapping[str, Sequence[str]]) -> tuple[str, ...]:
    return _get_ids(
        asset_ids_by_kind,
        "standings_order_worst_to_best",
        "standings",
    )


def _build_team_contract_textures(
    contracts: Mapping[str, ContractSnapshot],
    contract_textures: Mapping[str, ContractTexture],
) -> dict[str, list[ContractTexture]]:
    by_team: dict[str, list[ContractTexture]] = {}
    for contract_id, snap in contracts.items():
        tid = str(snap.team_id or "").upper().strip()
        tex = contract_textures.get(contract_id)
        if not tid or tex is None:
            continue
        by_team.setdefault(tid, []).append(tex)
    return by_team


def _build_team_payroll_map(players: Mapping[str, PlayerSnapshot]) -> dict[str, float]:
    out: dict[str, float] = {}
    for p in players.values():
        tid = str(p.team_id or "").upper().strip()
        if not tid:
            continue
        try:
            sal = float(p.salary_amount or 0.0)
        except Exception:
            sal = 0.0
        out[tid] = out.get(tid, 0.0) + max(0.0, sal)
    return out




def _safe_float(x: object, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return float(default)


def _legacy_contract_commitment(contract: ContractSnapshot, *, current_season_year: int) -> float:
    sal_now = max(0.0, _safe_float(salary_for_season(contract, int(current_season_year)), 0.0))
    years = max(0, int(remaining_years(contract, current_season_year=int(current_season_year))))
    return float(sal_now * years)


def _build_v1_metrics(
    *,
    provider: ValuationDataProvider,
    picks: Mapping[str, PickSnapshot],
    contracts: Mapping[str, ContractSnapshot],
    team_payroll_map: Mapping[str, float],
    salary_cap: float,
    current_season_year: int,
    reason_flags: list[str],
) -> dict[str, float]:
    v1_pick_ev = 0.0
    for pick_id in picks.keys():
        exp = provider.get_pick_expectation(pick_id)
        if exp is None or exp.expected_pick_number is None:
            reason_flags.append(f"MISSING_INPUT_V1_PICK_EXPECTATION:{pick_id}")
            v1_pick_ev += 16.0
            continue
        v1_pick_ev += _safe_float(exp.expected_pick_number, 16.0)

    team_commitment: dict[str, float] = {}
    total_commitment = 0.0
    for contract in contracts.values():
        commitment = _legacy_contract_commitment(contract, current_season_year=int(current_season_year))
        total_commitment += commitment
        tid = str(contract.team_id or "").upper().strip()
        if tid:
            team_commitment[tid] = team_commitment.get(tid, 0.0) + commitment

    cap_base = max(float(salary_cap), 1.0)
    cap_flex = 0.0
    for team_id, payroll in team_payroll_map.items():
        room_ratio = (cap_base - _safe_float(payroll, 0.0)) / cap_base
        burden_ratio = team_commitment.get(str(team_id).upper(), 0.0) / cap_base
        cap_flex += room_ratio - burden_ratio

    return {
        "pick_ev_delta": float(v1_pick_ev),
        "contract_burden_delta": float(total_commitment),
        "cap_flex_delta": float(cap_flex),
    }


def _build_v2_metrics(
    *,
    pick_distributions: Mapping[str, PickDistributionBundle],
    contract_textures: Mapping[str, ContractTexture],
    cap_ledgers: Mapping[str, CapLedgerView],
) -> dict[str, float]:
    return {
        "pick_ev_delta": float(sum(b.ev_pick for b in pick_distributions.values())),
        "contract_burden_delta": float(sum(t.guaranteed_commitment for t in contract_textures.values())),
        "cap_flex_delta": float(sum(v.posture_adjusted_flex for v in cap_ledgers.values())),
    }
def collect_v1_v2_diff(
    v1_metrics: Mapping[str, float],
    v2_metrics: Mapping[str, float],
) -> ContextDiffReport:
    """전역 설계 고정 3지표만 계산한다."""

    required = ("pick_ev_delta", "contract_burden_delta", "cap_flex_delta")
    missing: list[str] = []
    for key in required:
        if key not in v1_metrics or key not in v2_metrics:
            missing.append(f"MISSING_INPUT_{key.upper()}")

    return ContextDiffReport(
        pick_ev_delta=_safe_delta(v1_metrics, v2_metrics, "pick_ev_delta"),
        contract_burden_delta=_safe_delta(v1_metrics, v2_metrics, "contract_burden_delta"),
        cap_flex_delta=_safe_delta(v1_metrics, v2_metrics, "cap_flex_delta"),
        missing_metrics=tuple(missing),
    )


def build_valuation_context_v2(
    provider: ValuationDataProvider,
    decision_context_by_team: Mapping[str, DecisionContext],
    *,
    current_season_year: int,
    current_date_iso: str,
    market_pricing_config: MarketPricingConfig,
    team_utility_config: TeamUtilityConfig,
    package_effects_config: PackageEffectsConfig,
    decision_policy_config: DecisionPolicyConfig,
    asset_ids_by_kind: Mapping[str, Sequence[str]],
    dual_read: bool = False,
) -> ValuationContextV2:
    """ValuationDataProvider 기반 v2 오케스트레이터."""

    _ = (current_date_iso, team_utility_config, package_effects_config, decision_policy_config)

    players: dict[str, PlayerSnapshot] = {}
    picks: dict[str, PickSnapshot] = {}
    swaps: dict[str, SwapSnapshot] = {}
    contracts: dict[str, ContractSnapshot] = {}

    reason_flags: list[str] = []
    coverage = {
        "players": True,
        "picks": True,
        "swaps": True,
        "contracts": True,
        "pick_distributions": True,
        "role_textures": True,
        "contract_textures": True,
        "cap_ledgers": True,
    }

    for player_id in _get_ids(asset_ids_by_kind, "player", "players"):
        try:
            snap = provider.get_player_snapshot(player_id)
            players[player_id] = snap
            if snap.contract is not None and snap.contract.contract_id:
                contracts[snap.contract.contract_id] = snap.contract
        except Exception:
            coverage["players"] = False
            reason_flags.append(f"MISSING_INPUT_PLAYER_SNAPSHOT:{player_id}")

    for pick_id in _get_ids(asset_ids_by_kind, "pick", "picks"):
        try:
            picks[pick_id] = provider.get_pick_snapshot(pick_id)
        except Exception:
            coverage["picks"] = False
            reason_flags.append(f"MISSING_INPUT_PICK_SNAPSHOT:{pick_id}")

    for swap_id in _get_ids(asset_ids_by_kind, "swap", "swaps"):
        try:
            swaps[swap_id] = provider.get_swap_snapshot(swap_id)
        except Exception:
            coverage["swaps"] = False
            reason_flags.append(f"MISSING_INPUT_SWAP_SNAPSHOT:{swap_id}")

    if not contracts:
        coverage["contracts"] = False
        reason_flags.append("MISSING_INPUT_CONTRACT_SNAPSHOT")

    pick_distributions: dict[str, PickDistributionBundle] = {}
    role_textures: dict[str, RoleTexture] = {}
    contract_textures: dict[str, ContractTexture] = {}
    cap_ledgers: dict[str, CapLedgerView] = {}

    standings_order = _extract_standings_order(asset_ids_by_kind)
    season_rules = get_draft_lottery_rules(int(current_season_year))
    if not standings_order:
        coverage["pick_distributions"] = False
        reason_flags.append("MISSING_INPUT_STANDINGS_ORDER")
    if season_rules is None:
        coverage["pick_distributions"] = False
        reason_flags.append("MISSING_INPUT_DRAFT_LOTTERY_RULES")
    if standings_order and season_rules is not None:
        pick_distributions = build_pick_distributions_from_standings(
            picks=tuple(picks.values()),
            swaps=tuple(swaps.values()),
            standings_order_worst_to_best=standings_order,
            season_rules=season_rules,
        )

    role_textures = build_role_textures(
        tuple(players.values()),
        fit_engine=FitEngine(),
    )
    if len(role_textures) != len(players):
        coverage["role_textures"] = False
        reason_flags.append("MISSING_INPUT_ROLE_TEXTURE_OUTPUT")

    contract_textures = build_contract_textures(
        tuple(contracts.values()),
        current_season_year=int(current_season_year),
        salary_cap=(float(market_pricing_config.salary_cap) if market_pricing_config.salary_cap is not None else None),
    )
    if len(contract_textures) != len(contracts):
        coverage["contract_textures"] = False
        reason_flags.append("MISSING_INPUT_CONTRACT_TEXTURE_OUTPUT")

    team_contract_textures = _build_team_contract_textures(contracts, contract_textures)
    team_payroll_map = _build_team_payroll_map(players)
    posture_by_team: dict[str, str] = {}
    for team_id, ctx in decision_context_by_team.items():
        posture_by_team[str(team_id).upper()] = str(getattr(ctx, "posture", "STAND_PAT"))

    if not team_payroll_map:
        coverage["cap_ledgers"] = False
        reason_flags.append("MISSING_INPUT_TEAM_PAYROLL_MAP")

    cap_ledgers = build_cap_ledgers(
        team_contract_textures=team_contract_textures,
        posture_by_team=posture_by_team,
        current_season_year=int(current_season_year),
        cap_model=CapModel.defaults(),
        team_payroll_map=team_payroll_map,
    )

    diff_report = None
    if dual_read:
        season_cap = float(market_pricing_config.salary_cap or 0.0)
        if season_cap <= 0.0:
            season_cap = float(CapModel.defaults().numbers_for_season(int(current_season_year)).salary_cap)

        v1_metrics = _build_v1_metrics(
            provider=provider,
            picks=picks,
            contracts=contracts,
            team_payroll_map=team_payroll_map,
            salary_cap=season_cap,
            current_season_year=int(current_season_year),
            reason_flags=reason_flags,
        )
        v2_metrics = _build_v2_metrics(
            pick_distributions=pick_distributions,
            contract_textures=contract_textures,
            cap_ledgers=cap_ledgers,
        )
        diff_report = collect_v1_v2_diff(v1_metrics=v1_metrics, v2_metrics=v2_metrics)

    if not decision_context_by_team:
        reason_flags.append("MISSING_INPUT_DECISION_CONTEXT")

    diagnostics = ContextDiagnostics(
        source_coverage=coverage,
        reason_flags=tuple(reason_flags),
        diff_report=diff_report,
    )

    return ValuationContextV2(
        players=players,
        picks=picks,
        swaps=swaps,
        contracts=contracts,
        pick_distributions=pick_distributions,
        role_textures=role_textures,
        contract_textures=contract_textures,
        cap_ledgers=cap_ledgers,
        diagnostics=diagnostics,
    )
