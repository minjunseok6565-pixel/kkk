from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

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
class ContextDiagnostics:
    """v2 context build 중 coverage/reason 상태를 기록한다."""

    source_coverage: Mapping[str, bool]
    reason_flags: tuple[str, ...] = tuple()


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

    if not decision_context_by_team:
        reason_flags.append("MISSING_INPUT_DECISION_CONTEXT")

    diagnostics = ContextDiagnostics(
        source_coverage=coverage,
        reason_flags=tuple(reason_flags),
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
