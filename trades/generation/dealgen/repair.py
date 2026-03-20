from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from itertools import combinations
from typing import Any, Deque, Dict, List, Mapping, Optional, Set, Tuple

from ...errors import TradeError
from ...models import Deal, PlayerAsset, PickAsset, Asset, resolve_asset_receiver

from ..generation_tick import TradeGenerationTickContext
from ..asset_catalog import TradeAssetCatalog

from ...rules.policies.salary_matching_policy import SalaryMatchingParams, check_salary_matching

from .types import (
    DealGeneratorConfig,
    DealGeneratorBudget,
    DealGeneratorStats,
    DealCandidate,
    RuleFailure,
    RuleFailureKind,
    parse_trade_error,
)
from .dedupe import dedupe_hash
from .utils import (
    _count_picks,
    _count_players,
    _current_pick_ids,
    _pick_best_pick_id,
    _shape_ok,
)
 

# =============================================================================
# Validate + Repair
# =============================================================================

def repair_until_valid(
    cand: DealCandidate,
    tick_ctx: TradeGenerationTickContext,
    catalog: TradeAssetCatalog,
    config: DealGeneratorConfig,
    *,
    budget: DealGeneratorBudget,
    banned_asset_keys: Set[str],
    banned_players: Set[str],
    stats: DealGeneratorStats,
    banned_receivers_by_player: Optional[Dict[str, Set[str]]] = None,
) -> Tuple[bool, Optional[DealCandidate], int]:
    """호환 래퍼: many API 결과에서 첫 후보만 반환."""
    valid, validations_used = repair_until_valid_many(
        cand,
        tick_ctx,
        catalog,
        config,
        budget=budget,
        banned_asset_keys=banned_asset_keys,
        banned_players=banned_players,
        stats=stats,
        banned_receivers_by_player=banned_receivers_by_player,
    )
    if not valid:
        return False, None, validations_used
    return True, valid[0], validations_used


def repair_until_valid_many(
    cand: DealCandidate,
    tick_ctx: TradeGenerationTickContext,
    catalog: TradeAssetCatalog,
    config: DealGeneratorConfig,
    *,
    budget: DealGeneratorBudget,
    banned_asset_keys: Set[str],
    banned_players: Set[str],
    stats: DealGeneratorStats,
    banned_receivers_by_player: Optional[Dict[str, Set[str]]] = None,
) -> Tuple[List[DealCandidate], int]:
    """복수 후보 validate/repair.

    md 설계 기준:
    - validate 실패 시 실패 유형별 수리 분기를 생성하여 큐에 적재
    - 중복 해시/분기 cap/예산 cap으로 폭발 방지
    - validate 통과 후보를 모두 반환
    """

    validations_used = 0
    if banned_receivers_by_player is None:
        banned_receivers_by_player = {}

    if not _shape_ok(cand.deal, config=config, catalog=catalog):
        return [], validations_used

    q: Deque[DealCandidate] = deque([cand])
    valid: List[DealCandidate] = []
    seen_hash: Set[str] = set()
    branch_cap = max(1, int(getattr(config, "salary_repair_branch_cap", 10) or 10))
    max_repaired_variants = max(1, int(getattr(config, "max_repaired_variants_per_base", 10) or 10))
    emitted_total = 0

    while q:
        if validations_used >= int(getattr(budget, "max_validations", 10**9)):
            break
        cur = q.popleft()
        if cur.repairs_used > int(budget.max_repairs):
            continue
        try:
            tick_ctx.validate_deal(cur.deal)
            validations_used += 1
            valid.append(cur)
            continue
        except TradeError as err:
            validations_used += 1
            failure = parse_trade_error(err)
            stats.bump_failure(str(failure.kind.value))

            if cur.repairs_used >= int(budget.max_repairs):
                _apply_prune_side_effects(failure, banned_asset_keys, banned_players, banned_receivers_by_player)
                continue

            variants = repair_once_many(
                cur,
                failure,
                tick_ctx=tick_ctx,
                catalog=catalog,
                config=config,
                banned_asset_keys=banned_asset_keys,
                banned_players=banned_players,
            )
            if not variants:
                _apply_prune_side_effects(failure, banned_asset_keys, banned_players, banned_receivers_by_player)
                continue

            emitted = 0
            for nxt in variants:
                if emitted >= branch_cap:
                    break
                if emitted_total >= max_repaired_variants:
                    break
                nxt.repairs_used = int(cur.repairs_used) + 1
                stats.repairs += 1
                if not _shape_ok(nxt.deal, config=config, catalog=catalog):
                    continue
                key = dedupe_hash(nxt.deal)
                if key in seen_hash:
                    continue
                seen_hash.add(key)
                q.append(nxt)
                emitted += 1
                emitted_total += 1
        except Exception:
            validations_used += 1
            stats.bump_failure("unexpected_exception_validate")
            continue

    return valid, validations_used


def repair_once_many(
    cand: DealCandidate,
    failure: RuleFailure,
    *,
    tick_ctx: TradeGenerationTickContext,
    catalog: TradeAssetCatalog,
    config: DealGeneratorConfig,
    banned_asset_keys: Set[str],
    banned_players: Set[str],
) -> List[DealCandidate]:
    """1-step repair 결과를 복수 후보로 반환."""
    if failure.kind == RuleFailureKind.SALARY_MATCHING:
        team_id = str(failure.team_id or "").upper()
        if not team_id:
            return []
        return _repair_salary_matching(cand, team_id, catalog, tick_ctx, config, failure)

    c2 = _clone_candidate(cand)
    ok = repair_once(
        c2,
        failure,
        tick_ctx=tick_ctx,
        catalog=catalog,
        config=config,
        banned_asset_keys=banned_asset_keys,
        banned_players=banned_players,
    )
    return [c2] if ok else []


def repair_once(
    cand: DealCandidate,
    failure: RuleFailure,
    *,
    tick_ctx: TradeGenerationTickContext,
    catalog: TradeAssetCatalog,
    config: DealGeneratorConfig,
    banned_asset_keys: Set[str],
    banned_players: Set[str],
) -> bool:
    """실패 유형에 따라 '최소 수정' 1회 적용.

    True면 cand.deal이 mutate되었음을 의미.
    False면 이 후보는 prune.
    """

    # 구조적으로 수리 의미가 거의 없는 유형
    if failure.kind in (RuleFailureKind.OWNERSHIP, RuleFailureKind.DUPLICATE_ASSET):
        return False

    if failure.kind == RuleFailureKind.PLAYER_ELIGIBILITY:
        reason = failure.reason or ""
        pid = failure.player_id
        if not pid:
            return False
        if reason == "recent_contract_signing":
            banned_players.add(pid)
            return False
        if reason == "aggregation_ban":
            # aggregation_ban: 해당 선수는 '트레이드 불가'가 아니라
            # '다른 선수와 묶어서(2+ outgoing) 보낼 수 없음'이므로,
            # 최소 수정은 pid를 유지하고 나머지 outgoing player를 제거하여 1-for-1로 만드는 것이다.
            team_id = str(failure.team_id or "").upper()
            if not team_id or team_id not in cand.deal.legs:
                return False
            assets = list(cand.deal.legs[team_id] or [])
            players = [a for a in assets if isinstance(a, PlayerAsset)]
            if len(players) <= 1:
                return False

            keep_player: Optional[PlayerAsset] = None
            for a in players:
                if a.player_id == pid:
                    keep_player = a
                    break
            if keep_player is None:
                # fallback: pid가 leg에 없으면 첫 번째 player만 남긴다.
                keep_player = players[0]

            non_players: List[Asset] = [a for a in assets if not isinstance(a, PlayerAsset)]
            cand.deal.legs[team_id] = [keep_player] + non_players
            cand.tags.append("repair:aggregation_keep_solo")
            return True
        return False

    if failure.kind == RuleFailureKind.RETURN_TO_TRADING_TEAM:
        return False

    if failure.kind == RuleFailureKind.ROSTER_LIMIT:
        team_id = str(failure.team_id or "").upper()
        if not team_id:
            return False
        return _repair_roster_limit(cand, team_id, catalog, config)

    if failure.kind == RuleFailureKind.SALARY_MATCHING:
        team_id = str(failure.team_id or "").upper()
        if not team_id:
            return False
        variants = _repair_salary_matching(cand, team_id, catalog, tick_ctx, config, failure)
        if not variants:
            return False
        best = variants[0]
        cand.deal = best.deal
        cand.tags.extend(best.tags)
        return True

    if failure.kind == RuleFailureKind.PICK_RULES:
        team_id = str(failure.team_id or "").upper()
        return _repair_pick_rules(cand, team_id, catalog, config, failure)

    return False


def _apply_prune_side_effects(
    failure: RuleFailure,
    banned_asset_keys: Set[str],
    banned_players: Set[str],
    banned_receivers_by_player: Optional[Dict[str, Set[str]]] = None,
) -> None:
    # (C) 같은 invalid를 반복 생성하지 않도록 금지 목록에 반영
    if failure.kind in (RuleFailureKind.OWNERSHIP, RuleFailureKind.DUPLICATE_ASSET):
        if failure.asset_key:
            banned_asset_keys.add(failure.asset_key)

    # ownership에서 플레이어 미소유는 플레이어 후보 자체를 금지하면 효과가 좋다.
    if failure.kind == RuleFailureKind.OWNERSHIP and failure.player_id:
        banned_players.add(failure.player_id)

    if failure.kind == RuleFailureKind.PLAYER_ELIGIBILITY and failure.player_id and failure.reason == "recent_contract_signing":
        banned_players.add(failure.player_id)

    # Return-to-trading-team: 특정 player가 특정 receiver로 못 가는 조합을 학습해서 재발 방지
    # (types.py가 아직 to_team을 보존하지 않는 상태에서도 안전하게 동작하도록 getattr 사용)
    if banned_receivers_by_player is not None and failure.kind == RuleFailureKind.RETURN_TO_TRADING_TEAM:
        pid = getattr(failure, "player_id", None)
        to_team = getattr(failure, "to_team", None)
        if pid and to_team:
            pid_s = str(pid)
            to_u = str(to_team).upper()
            banned_receivers_by_player.setdefault(pid_s, set()).add(to_u)


def _to_int_dollars(x: Any) -> int:
    """float/int/str 등을 달러 단위 정수로 안전하게 변환."""
    try:
        return int(round(float(x)))
    except Exception:
        return 0


@dataclass(frozen=True, slots=True)
class SalaryTeamSnapshot:
    team_id: str
    payroll_before_d: int
    outgoing_salary_d: int
    incoming_salary_d: int
    outgoing_players: int
    incoming_players: int
    max_single_outgoing_salary_d: int


@dataclass(frozen=True, slots=True)
class SalaryRepairContext:
    teams: Tuple[str, ...]
    trade_rules: Mapping[str, Any]
    by_team: Dict[str, SalaryTeamSnapshot]


@dataclass(frozen=True, slots=True)
class SalaryValidationOutcome:
    ok: bool
    by_team_ok: Dict[str, bool]
    by_team_slack_d: Dict[str, int]


@dataclass(frozen=True, slots=True)
class SalaryPackage:
    # (team_id, player_id) list
    additions: Tuple[Tuple[str, str], ...]
    k: int
    total_added_salary_d: int
    total_added_market: float


def _lookup_player_salary_d(catalog: TradeAssetCatalog, owner_team: str, player_id: str) -> int:
    out = catalog.outgoing_by_team.get(str(owner_team).upper())
    if out is None:
        return 0
    c = out.players.get(str(player_id))
    if c is None:
        return 0
    try:
        return int(round(float(c.salary_m) * 1_000_000.0))
    except Exception:
        return 0


def _snapshot_salary_for_team(
    deal: Deal,
    team_id: str,
    catalog: TradeAssetCatalog,
    tick_ctx: TradeGenerationTickContext,
) -> SalaryTeamSnapshot:
    tid = str(team_id).upper()

    payroll_before_d = 0
    rt = getattr(tick_ctx, "rule_tick_ctx", None)
    if rt is not None:
        try:
            rt.ensure_active_roster_index()
            payroll_before_d = _to_int_dollars(rt.team_payroll_before_map.get(tid, 0))
        except Exception:
            payroll_before_d = 0
    if payroll_before_d <= 0:
        ts = None
        try:
            ts = tick_ctx.get_team_situation(tid)
        except Exception:
            ts = None
        payroll_before_d = _to_int_dollars(getattr(getattr(ts, "constraints", None), "payroll", 0))

    outgoing_salary_d = 0
    outgoing_players = 0
    max_single_outgoing_salary_d = 0
    for a in deal.legs.get(tid, []) or []:
        if not isinstance(a, PlayerAsset):
            continue
        outgoing_players += 1
        sal_d = _lookup_player_salary_d(catalog, tid, a.player_id)
        outgoing_salary_d += sal_d
        if sal_d > max_single_outgoing_salary_d:
            max_single_outgoing_salary_d = sal_d

    incoming_salary_d = 0
    incoming_players = 0
    for from_team, assets in (deal.legs or {}).items():
        f_u = str(from_team).upper()
        if f_u == tid:
            continue
        for a in assets or []:
            if not isinstance(a, PlayerAsset):
                continue
            try:
                recv = str(resolve_asset_receiver(deal, from_team, a)).upper()
            except Exception:
                recv = tid
            if recv != tid:
                continue
            incoming_players += 1
            incoming_salary_d += _lookup_player_salary_d(catalog, f_u, a.player_id)

    return SalaryTeamSnapshot(
        team_id=tid,
        payroll_before_d=payroll_before_d,
        outgoing_salary_d=outgoing_salary_d,
        incoming_salary_d=incoming_salary_d,
        outgoing_players=outgoing_players,
        incoming_players=incoming_players,
        max_single_outgoing_salary_d=max_single_outgoing_salary_d,
    )


def _build_salary_context(
    cand: DealCandidate,
    catalog: TradeAssetCatalog,
    tick_ctx: TradeGenerationTickContext,
) -> SalaryRepairContext:
    teams: Tuple[str, ...] = tuple(str(t).upper() for t in (cand.deal.teams or ()))
    by_team: Dict[str, SalaryTeamSnapshot] = {}
    for tid in teams:
        by_team[tid] = _snapshot_salary_for_team(cand.deal, tid, catalog, tick_ctx)
    return SalaryRepairContext(
        teams=teams,
        trade_rules=dict(catalog.trade_rules or {}),
        by_team=by_team,
    )


def _validate_salary_for_all_teams(
    deal: Deal,
    ctx: SalaryRepairContext,
    catalog: TradeAssetCatalog,
    tick_ctx: TradeGenerationTickContext,
) -> SalaryValidationOutcome:
    params = SalaryMatchingParams.from_trade_rules(ctx.trade_rules)
    by_team_ok: Dict[str, bool] = {}
    by_team_slack_d: Dict[str, int] = {}
    all_ok = True
    for tid in ctx.teams:
        snap = _snapshot_salary_for_team(deal, tid, catalog, tick_ctx)
        sim = check_salary_matching(
            payroll_before_d=snap.payroll_before_d,
            outgoing_salary_d=snap.outgoing_salary_d,
            incoming_salary_d=snap.incoming_salary_d,
            outgoing_players=snap.outgoing_players,
            incoming_players=snap.incoming_players,
            max_single_outgoing_salary_d=snap.max_single_outgoing_salary_d,
            params=params,
        )
        by_team_ok[tid] = bool(sim.ok)
        by_team_slack_d[tid] = int(sim.allowed_in_d - snap.incoming_salary_d)
        if not sim.ok:
            all_ok = False
    return SalaryValidationOutcome(ok=all_ok, by_team_ok=by_team_ok, by_team_slack_d=by_team_slack_d)


def _clone_candidate(cand: DealCandidate) -> DealCandidate:
    legs_new: Dict[str, List[Asset]] = {
        str(t).upper(): list(assets or [])
        for t, assets in (cand.deal.legs or {}).items()
    }
    deal_new = Deal(
        teams=tuple(cand.deal.teams or ()),
        legs=legs_new,
        metadata=dict(cand.deal.metadata or {}),
    )
    return DealCandidate(
        deal=deal_new,
        focal_player_id=cand.focal_player_id,
        focal_rank=cand.focal_rank,
        skeleton_id=cand.skeleton_id,
        skeleton_domain=cand.skeleton_domain,
        target_tier=cand.target_tier,
        contract_tag=cand.contract_tag,
        compat_archetype=cand.compat_archetype,
        modifier_trace=list(cand.modifier_trace or []),
        tags=list(cand.tags or []),
        repairs_used=int(cand.repairs_used),
    )


def _iter_salary_add_candidates(
    cand: DealCandidate,
    team_id: str,
    catalog: TradeAssetCatalog,
    config: DealGeneratorConfig,
) -> List[str]:
    team_u = str(team_id).upper()
    out = catalog.outgoing_by_team.get(team_u)
    if out is None:
        return []

    leg = cand.deal.legs.get(team_u, []) or []
    already = {str(a.player_id) for a in leg if isinstance(a, PlayerAsset)}
    if len(already) >= int(config.max_players_per_side):
        return []

    other = [t for t in cand.deal.teams if str(t).upper() != team_u]
    receiver_team = str(other[0]).upper() if other else None

    # solo-only guard: existing solo-only outgoing이 이미 있으면 추가하지 않음.
    for pid0 in already:
        c0 = out.players.get(pid0)
        if c0 is not None and bool(getattr(c0, "aggregation_solo_only", False)):
            return []

    seen: Set[str] = set()
    picked: List[str] = []
    for b in ("FILLER_CHEAP", "FILLER_BAD_CONTRACT", "CONSOLIDATE"):
        for pid in out.player_ids_by_bucket.get(b, tuple()):
            pid_s = str(pid)
            if pid_s in seen or pid_s in already:
                continue
            seen.add(pid_s)
            c = out.players.get(pid_s)
            if c is None:
                continue
            if receiver_team and receiver_team in set(getattr(c, "return_ban_teams", None) or ()):
                continue
            if bool(getattr(c, "aggregation_solo_only", False)) and len(already) >= 1:
                continue
            picked.append(pid_s)
    return picked


def _candidate_player_meta(catalog: TradeAssetCatalog, team_id: str, pid: str) -> Tuple[int, float]:
    out = catalog.outgoing_by_team.get(str(team_id).upper())
    if out is None:
        return 0, 0.0
    c = out.players.get(str(pid))
    if c is None:
        return 0, 0.0
    sal_d = _to_int_dollars(float(getattr(c, "salary_m", 0.0) or 0.0) * 1_000_000.0)
    mkt = float(getattr(getattr(c, "market", None), "total", 0.0) or 0.0)
    return sal_d, mkt


def _build_salary_package(additions: List[Tuple[str, str]], catalog: TradeAssetCatalog) -> SalaryPackage:
    ordered = tuple(sorted((str(t).upper(), str(pid)) for t, pid in additions))
    total_sal = 0
    total_mkt = 0.0
    for tid, pid in ordered:
        sal_d, mkt = _candidate_player_meta(catalog, tid, pid)
        total_sal += sal_d
        total_mkt += mkt
    return SalaryPackage(
        additions=ordered,
        k=len(ordered),
        total_added_salary_d=total_sal,
        total_added_market=total_mkt,
    )


def _materialize_salary_package_candidate(
    cand: DealCandidate,
    pkg: SalaryPackage,
    config: DealGeneratorConfig,
) -> Optional[DealCandidate]:
    c2 = _clone_candidate(cand)
    # team별 인원 cap 사전 검증
    add_by_team: Dict[str, int] = {}
    for tid, _ in pkg.additions:
        add_by_team[tid] = add_by_team.get(tid, 0) + 1
    for tid, n_add in add_by_team.items():
        cur_players = _count_players(c2.deal, tid)
        if cur_players + n_add > int(config.max_players_per_side):
            return None
    for tid, pid in pkg.additions:
        c2.deal.legs.setdefault(tid, []).append(PlayerAsset(kind="player", player_id=pid))
    c2.tags.append(f"repair:salary_match_k{pkg.k}")
    return c2


def _search_min_k_salary_packages(
    cand: DealCandidate,
    ctx: SalaryRepairContext,
    catalog: TradeAssetCatalog,
    tick_ctx: TradeGenerationTickContext,
    config: DealGeneratorConfig,
    failing_team: str,
    *,
    max_k: int = 3,
    per_k_limit: int = 10,
) -> List[SalaryPackage]:
    teams = [str(t).upper() for t in (cand.deal.teams or ()) if str(t).upper() in ctx.by_team]
    if not teams:
        return []

    f_u = str(failing_team).upper()
    ordered_teams: List[str] = []
    if f_u in teams:
        ordered_teams.append(f_u)
    ordered_teams.extend([t for t in teams if t != f_u])

    pool_cap_per_team = max(1, int(getattr(config, "salary_repair_pool_cap_per_team", 20) or 20))
    combo_eval_cap = max(1, int(getattr(config, "salary_repair_combo_eval_cap", 1500) or 1500))
    pool_by_team: Dict[str, List[str]] = {}
    for tid in ordered_teams:
        raw = _iter_salary_add_candidates(cand, tid, catalog, config)
        # 조합 폭발 방지: 팀별 후보 풀 상한
        pool_by_team[tid] = list(raw[:pool_cap_per_team])
    if not any(pool_by_team.values()):
        return []

    eval_used = 0

    def _can_eval_more() -> bool:
        return eval_used < combo_eval_cap

    # k=1
    passing_k1: List[SalaryPackage] = []
    seen_k1: Set[Tuple[Tuple[str, str], ...]] = set()
    for tid in ordered_teams:
        for pid in pool_by_team.get(tid, []):
            pkg = _build_salary_package([(tid, pid)], catalog)
            if pkg.additions in seen_k1:
                continue
            seen_k1.add(pkg.additions)
            if not _can_eval_more():
                return passing_k1
            c2 = _materialize_salary_package_candidate(cand, pkg, config)
            if c2 is None:
                continue
            eval_used += 1
            out = _validate_salary_for_all_teams(c2.deal, ctx, catalog, tick_ctx)
            if not out.ok:
                continue
            passing_k1.append(pkg)
            if len(passing_k1) >= int(per_k_limit):
                return passing_k1
    if passing_k1:
        return passing_k1

    if int(max_k) < 2:
        return []

    # k=2
    passing_k2: List[SalaryPackage] = []
    seen_k2: Set[Tuple[Tuple[str, str], ...]] = set()

    # same-team pairs
    for tid in ordered_teams:
        pool = pool_by_team.get(tid, [])
        for p1, p2 in combinations(pool, 2):
            pkg = _build_salary_package([(tid, p1), (tid, p2)], catalog)
            if pkg.additions in seen_k2:
                continue
            seen_k2.add(pkg.additions)
            if not _can_eval_more():
                return passing_k2
            c2 = _materialize_salary_package_candidate(cand, pkg, config)
            if c2 is None:
                continue
            eval_used += 1
            out = _validate_salary_for_all_teams(c2.deal, ctx, catalog, tick_ctx)
            if not out.ok:
                continue
            passing_k2.append(pkg)
            if len(passing_k2) >= int(per_k_limit):
                return passing_k2

    # cross-team pairs
    for i in range(len(ordered_teams)):
        for j in range(i + 1, len(ordered_teams)):
            ti = ordered_teams[i]
            tj = ordered_teams[j]
            pi = pool_by_team.get(ti, [])
            pj = pool_by_team.get(tj, [])
            for a in pi:
                for b in pj:
                    pkg = _build_salary_package([(ti, a), (tj, b)], catalog)
                    if pkg.additions in seen_k2:
                        continue
                    seen_k2.add(pkg.additions)
                    if not _can_eval_more():
                        return passing_k2
                    c2 = _materialize_salary_package_candidate(cand, pkg, config)
                    if c2 is None:
                        continue
                    eval_used += 1
                    out = _validate_salary_for_all_teams(c2.deal, ctx, catalog, tick_ctx)
                    if not out.ok:
                        continue
                    passing_k2.append(pkg)
                    if len(passing_k2) >= int(per_k_limit):
                        return passing_k2
    if passing_k2:
        return passing_k2

    if int(max_k) < 3:
        return []

    # k=3
    passing_k3: List[SalaryPackage] = []
    seen_k3: Set[Tuple[Tuple[str, str], ...]] = set()

    # 3 same-team
    for tid in ordered_teams:
        pool = pool_by_team.get(tid, [])
        for p1, p2, p3 in combinations(pool, 3):
            pkg = _build_salary_package([(tid, p1), (tid, p2), (tid, p3)], catalog)
            if pkg.additions in seen_k3:
                continue
            seen_k3.add(pkg.additions)
            if not _can_eval_more():
                return passing_k3
            c2 = _materialize_salary_package_candidate(cand, pkg, config)
            if c2 is None:
                continue
            eval_used += 1
            out = _validate_salary_for_all_teams(c2.deal, ctx, catalog, tick_ctx)
            if not out.ok:
                continue
            passing_k3.append(pkg)
            if len(passing_k3) >= int(per_k_limit):
                return passing_k3

    # 2 + 1 split
    for i in range(len(ordered_teams)):
        for j in range(len(ordered_teams)):
            if i == j:
                continue
            ti = ordered_teams[i]
            tj = ordered_teams[j]
            pi = pool_by_team.get(ti, [])
            pj = pool_by_team.get(tj, [])
            for a, b in combinations(pi, 2):
                for c in pj:
                    pkg = _build_salary_package([(ti, a), (ti, b), (tj, c)], catalog)
                    if pkg.additions in seen_k3:
                        continue
                    seen_k3.add(pkg.additions)
                    if not _can_eval_more():
                        return passing_k3
                    c2 = _materialize_salary_package_candidate(cand, pkg, config)
                    if c2 is None:
                        continue
                    eval_used += 1
                    out = _validate_salary_for_all_teams(c2.deal, ctx, catalog, tick_ctx)
                    if not out.ok:
                        continue
                    passing_k3.append(pkg)
                    if len(passing_k3) >= int(per_k_limit):
                        return passing_k3

    # 1 + 1 + 1 split (3+ teams)
    if len(ordered_teams) >= 3:
        for i in range(len(ordered_teams)):
            for j in range(i + 1, len(ordered_teams)):
                for k in range(j + 1, len(ordered_teams)):
                    ti = ordered_teams[i]
                    tj = ordered_teams[j]
                    tk = ordered_teams[k]
                    pi = pool_by_team.get(ti, [])
                    pj = pool_by_team.get(tj, [])
                    pk = pool_by_team.get(tk, [])
                    for a in pi:
                        for b in pj:
                            for c in pk:
                                pkg = _build_salary_package([(ti, a), (tj, b), (tk, c)], catalog)
                                if pkg.additions in seen_k3:
                                    continue
                                seen_k3.add(pkg.additions)
                                if not _can_eval_more():
                                    return passing_k3
                                c2 = _materialize_salary_package_candidate(cand, pkg, config)
                                if c2 is None:
                                    continue
                                eval_used += 1
                                out = _validate_salary_for_all_teams(c2.deal, ctx, catalog, tick_ctx)
                                if not out.ok:
                                    continue
                                passing_k3.append(pkg)
                                if len(passing_k3) >= int(per_k_limit):
                                    return passing_k3

    return passing_k3


def _rank_salary_packages(packages: List[SalaryPackage]) -> List[SalaryPackage]:
    ranked = list(packages)
    ranked.sort(
        key=lambda p: (
            int(p.k),
            int(p.total_added_salary_d),
            float(p.total_added_market),
            tuple(p.additions),
        )
    )
    return ranked


def _materialize_salary_repaired_candidates(
    cand: DealCandidate,
    ranked_packages: List[SalaryPackage],
    config: DealGeneratorConfig,
    *,
    top_n: int,
) -> List[DealCandidate]:
    out: List[DealCandidate] = []
    for i, pkg in enumerate(ranked_packages[: max(0, int(top_n))], start=1):
        c2 = _materialize_salary_package_candidate(cand, pkg, config)
        if c2 is None:
            continue
        c2.tags.append(f"repair:salary_variant:{i}")
        out.append(c2)
    return out


def _repair_salary_matching(
    cand: DealCandidate,
    failing_team: str,
    catalog: TradeAssetCatalog,
    tick_ctx: TradeGenerationTickContext,
    config: DealGeneratorConfig,
    failure: RuleFailure,
) -> List[DealCandidate]:
    """SalaryMatchingRule 실패 수리 (3단계: 최소 인원 탐색 k=1/2/3 + 폭발 제어).

    - 레거시 SECOND_APRON 분기 로직 없이, 모든 팀에 대해 SSOT(check_salary_matching)로 검증한다.
    - 양 팀 후보 풀을 대상으로 k=1 -> k=2 -> k=3 순으로 탐색하며,
      k에서 해가 발견되면 즉시 종료(최소 인원 원칙)한다.
    """

    ctx = _build_salary_context(cand, catalog, tick_ctx)
    if not ctx.teams:
        return []

    per_k_limit = int(getattr(config, "salary_repair_per_k_limit", 10) or 10)
    top_n = int(getattr(config, "salary_repair_materialize_top_n", 10) or 10)
    max_k = min(3, int(getattr(config, "salary_repair_max_k", 3) or 3))

    packages = _search_min_k_salary_packages(
        cand,
        ctx,
        catalog,
        tick_ctx,
        config,
        failing_team,
        max_k=max_k,
        per_k_limit=per_k_limit,
    )
    if not packages:
        return []

    ranked = _rank_salary_packages(packages)
    materialized = _materialize_salary_repaired_candidates(cand, ranked, config, top_n=top_n)
    return materialized


def _repair_roster_limit(cand: DealCandidate, problem_team: str, catalog: TradeAssetCatalog, config: DealGeneratorConfig) -> bool:
    """ROSTER_LIMIT 수리."""

    other = [t for t in cand.deal.teams if str(t).upper() != problem_team]
    if not other:
        return False
    other_team = str(other[0]).upper()

    # 1) remove an incoming player to problem_team (player asset in other_team leg)
    other_assets = list(cand.deal.legs.get(other_team, []))
    player_ids = [a.player_id for a in other_assets if isinstance(a, PlayerAsset)]
    if len(player_ids) >= 2:
        other_out = catalog.outgoing_by_team.get(other_team)
        if other_out is not None:
            def market(pid: str) -> float:
                c = other_out.players.get(pid)
                return float(c.market.total) if c is not None else 0.0
            pid_remove = sorted(player_ids, key=market)[0]
        else:
            pid_remove = player_ids[-1]
        cand.deal.legs[other_team] = [a for a in other_assets if not (isinstance(a, PlayerAsset) and a.player_id == pid_remove)]
        cand.tags.append("repair:roster_remove_in")
        return True

    # 2) add outgoing from problem_team to reduce net incoming
    prob_out = catalog.outgoing_by_team.get(problem_team)
    if prob_out is None:
        return False

    if _count_players(cand.deal, problem_team) >= int(config.max_players_per_side):
        return False

    already = {a.player_id for a in cand.deal.legs.get(problem_team, []) if isinstance(a, PlayerAsset)}
    # aggregation_solo_only는 "묶음(2+ outgoing) 금지"이므로,
    # 현재 outgoing이 0명인 경우에만 solo-only를 허용한다.
    allow_solo_only = (len(already) == 0)

    # 기존 outgoing에 solo-only가 포함되어 있으면(=단독만 허용),
    # 여기서 outgoing을 추가하면 aggregation_ban으로 재실패할 가능성이 높으므로 수리하지 않는다.
    for pid0 in already:
        c0 = prob_out.players.get(pid0)
        if c0 is not None and bool(getattr(c0, "aggregation_solo_only", False)):
            return False

    receiver_team = other_team

    # 낮은 market을 우선으로 보내되, return-ban / solo-only 조건을 반영해서 후보를 고른다.
    best_pid: Optional[str] = None
    best_key: Optional[Tuple[float, float, str]] = None  # (market_total, salary_m, pid)
    for b in ("FILLER_CHEAP", "FILLER_BAD_CONTRACT"):
        for pid in prob_out.player_ids_by_bucket.get(b, tuple()):
            pid = str(pid)
            if pid in already:
                continue
            c = prob_out.players.get(pid)
            if c is None:
                continue
            if receiver_team and receiver_team in set(getattr(c, "return_ban_teams", None) or ()):
                continue
            if bool(getattr(c, "aggregation_solo_only", False)) and not allow_solo_only:
                continue
            mkt = float(getattr(getattr(c, "market", None), "total", 0.0) or 0.0)
            sal = float(getattr(c, "salary_m", 0.0) or 0.0)
            key = (mkt, sal, pid)
            if best_key is None or key < best_key:
                best_key = key
                best_pid = pid
    filler = best_pid
    if not filler:
        return False
    cand.deal.legs[problem_team].append(PlayerAsset(kind="player", player_id=filler))
    cand.tags.append("repair:roster_send_out")
    return True


def _repair_pick_rules(cand: DealCandidate, team_id: str, catalog: TradeAssetCatalog, config: DealGeneratorConfig, failure: RuleFailure) -> bool:
    """PickRulesRule 실패(stepien/pick_too_far 등) 수리."""

    if not team_id or team_id not in cand.deal.legs:
        return False

    reason = failure.reason or ""
    if reason == "pick_too_far" and failure.pick_id:
        pid = str(failure.pick_id)
        cand.deal.legs[team_id] = [a for a in cand.deal.legs[team_id] if not (isinstance(a, PickAsset) and a.pick_id == pid)]
        cand.tags.append("repair:pick_remove_far")
        return True

    out_cat = catalog.outgoing_by_team.get(team_id)
    if out_cat is None:
        return False

    picks_out = [a for a in cand.deal.legs[team_id] if isinstance(a, PickAsset)]
    if not picks_out:
        return False

    sensitive_set = set(out_cat.pick_ids_by_bucket.get("FIRST_SENSITIVE", tuple()))
    safe_set = set(out_cat.pick_ids_by_bucket.get("FIRST_SAFE", tuple()))

    pid_remove: Optional[str] = None
    for a in picks_out:
        if a.pick_id in sensitive_set:
            pid_remove = a.pick_id
            break
    if pid_remove is None:
        for a in picks_out:
            if a.pick_id in safe_set:
                pid_remove = a.pick_id
                break
    if pid_remove is None:
        pid_remove = picks_out[-1].pick_id

    cand.deal.legs[team_id] = [a for a in cand.deal.legs[team_id] if not (isinstance(a, PickAsset) and a.pick_id == pid_remove)]
    cand.tags.append("repair:stepien_remove_pick")

    # optional replacement: first -> second
    if pid_remove in safe_set or pid_remove in sensitive_set:
        if _count_picks(cand.deal, team_id) >= int(config.max_picks_per_side):
            return True
        replacement = _pick_best_pick_id(out_cat, bucket="SECOND", excluded=_current_pick_ids(cand.deal, team_id))
        if replacement:
            cand.deal.legs[team_id].append(out_cat.picks[replacement].as_asset())
            cand.tags.append("repair:stepien_replace_second")

    return True
