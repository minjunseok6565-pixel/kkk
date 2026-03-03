from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Set, Tuple

from ...errors import TradeError
from ...models import PlayerAsset, PickAsset, Asset, resolve_asset_receiver

from ..generation_tick import TradeGenerationTickContext
from ..asset_catalog import TradeAssetCatalog, BucketId

from ...rules.policies.salary_matching_policy import SalaryMatchingParams, check_salary_matching

from .types import DealGeneratorConfig, DealGeneratorBudget, DealGeneratorStats, DealCandidate, RuleFailureKind, parse_trade_error
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
    allow_locked_by_deal_id: Optional[str],
    budget: DealGeneratorBudget,
    banned_asset_keys: Set[str],
    banned_players: Set[str],
    stats: DealGeneratorStats,
    banned_receivers_by_player: Optional[Dict[str, Set[str]]] = None,
) -> Tuple[bool, Optional[DealCandidate], int]:
    """validate -> 실패 유형에 따라 최대 budget.max_repairs회 repair.

    Returns: (ok, candidate_or_none, validations_used)
    """

    validations_used = 0
    if banned_receivers_by_player is None:
        banned_receivers_by_player = {}

    if not _shape_ok(cand.deal, config=config, catalog=catalog):
        return False, None, validations_used

    for _ in range(int(budget.max_repairs) + 1):
        try:
            tick_ctx.validate_deal(cand.deal, allow_locked_by_deal_id=allow_locked_by_deal_id)
            validations_used += 1
            return True, cand, validations_used
        except TradeError as err:
            validations_used += 1
            failure = parse_trade_error(err)
            stats.bump_failure(str(failure.kind.value))

            if cand.repairs_used >= int(budget.max_repairs):
                _apply_prune_side_effects(failure, banned_asset_keys, banned_players, banned_receivers_by_player)
                return False, None, validations_used

            repaired = repair_once(
                cand,
                failure,
                tick_ctx=tick_ctx,
                catalog=catalog,
                config=config,
                banned_asset_keys=banned_asset_keys,
                banned_players=banned_players,
            )
            if not repaired:
                _apply_prune_side_effects(failure, banned_asset_keys, banned_players, banned_receivers_by_player)
                return False, None, validations_used

            cand.repairs_used += 1
            stats.repairs += 1

            # repair 후 shape check
            if not _shape_ok(cand.deal, config=config, catalog=catalog):
                return False, None, validations_used
        except Exception:
            # 상업용 루프: 예상 못한 예외로 tick이 죽지 않게 방어
            validations_used += 1
            stats.bump_failure("unexpected_exception_validate")
            return False, None, validations_used

    return False, None, validations_used


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
    if failure.kind in (RuleFailureKind.ASSET_LOCK, RuleFailureKind.OWNERSHIP, RuleFailureKind.DUPLICATE_ASSET):
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
        return _repair_salary_matching(cand, team_id, catalog, config, failure)

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
    if failure.kind in (RuleFailureKind.ASSET_LOCK, RuleFailureKind.OWNERSHIP, RuleFailureKind.DUPLICATE_ASSET):
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


def _repair_salary_matching(
    cand: DealCandidate,
    failing_team: str,
    catalog: TradeAssetCatalog,
    config: DealGeneratorConfig,
    failure: RuleFailure,
) -> bool:
    """SalaryMatchingRule 실패 수리.

    가장 안전한 수리:
    - failing_team outgoing에 filler 1명을 추가(FILLER_CHEAP -> EXPIRING -> FILLER_BAD_CONTRACT)

    단, SECOND_APRON 팀은 post-2024 CBA 기준으로 outgoing salary aggregation이 금지되므로
    (incoming이 단일 outgoing 계약으로 매칭 가능해야 함) 여기서는 보수적으로 제한된 수리만 시도한다.
    """

    status = str(failure.status or "")
    method = str(failure.method or "")
    if status == "SECOND_APRON":
        # SECOND_APRON salary matching: incoming must be matchable by a single outgoing salary (no aggregation).
        if method == "outgoing_second_apron":
            return _repair_second_apron_salary_mismatch(cand, failing_team, catalog, config, failure)
        return False

    out_catalog = catalog.outgoing_by_team.get(failing_team)
    if out_catalog is None:
        return False

    # max_players_per_side guard
    if _count_players(cand.deal, failing_team) >= int(config.max_players_per_side):
        return False

    # aggregation_solo_only가 이미 포함되면 추가 player를 붙이면 바로 다시 실패할 확률이 큼
    for a in cand.deal.legs.get(failing_team, []):
        if isinstance(a, PlayerAsset):
            c = out_catalog.players.get(a.player_id)
            if c is not None and bool(getattr(c, "aggregation_solo_only", False)):
                return False

    # receiver team(상대팀) 계산: return-ban 프리필터에 사용
    other = [t for t in cand.deal.teams if str(t).upper() != str(failing_team).upper()]
    receiver_team = str(other[0]).upper() if other else None

    # SalaryMatchingRule failure.details는 달러(float) 기반이므로, 달러 정수로 변환해 사용한다.
    # 이 숫자들은 SSOT(validate_deal)의 '현재 딜 상태' 기준이며, filler 추가 후 상태는 여기서 재시뮬레이션한다.
    payroll_before_d = _to_int_dollars(failure.details.get("payroll_before"))
    outgoing_salary_d0 = _to_int_dollars(failure.details.get("outgoing_salary"))
    incoming_salary_d0 = _to_int_dollars(failure.details.get("incoming_salary"))

    if incoming_salary_d0 <= 0:
        return False

    # max_players_per_side guard는 이미 위에서 통과했으므로, 여기서는 후보 스캔/선정만 한다.
    already = {a.player_id for a in cand.deal.legs.get(failing_team, []) if isinstance(a, PlayerAsset)}
    outgoing_players0 = len(already)
    # aggregation_solo_only는 "묶음(2+ outgoing) 금지"이므로,
    # 현재 outgoing이 0명(=단독 트레이드)인 경우에만 허용한다.
    allow_solo_only = (len(already) == 0)

    # 후보 filler를 버킷에서 전수 스캔하고, "salary matching을 실제로 통과시키는" 후보만 남긴다.
    buckets: Tuple[BucketId, ...] = ("FILLER_CHEAP", "EXPIRING", "FILLER_BAD_CONTRACT")
    seen: Set[str] = set()
    passing: List[Tuple[int, float, str]] = []  # (salary_d, market_total, player_id)

    trade_rules = catalog.trade_rules or {}

    params = SalaryMatchingParams.from_trade_rules(trade_rules)
    team_u = str(failing_team).upper()
    incoming_players0 = 0
    for sender_team, assets in (cand.deal.legs or {}).items():
        if str(sender_team).upper() == team_u:
            continue
        for a in assets or []:
            if not isinstance(a, PlayerAsset):
                continue
            try:
                recv = str(resolve_asset_receiver(cand.deal, sender_team, a)).upper()
            except Exception:
                continue
            if recv == team_u:
                incoming_players0 += 1

    for b in buckets:
        for pid in out_catalog.player_ids_by_bucket.get(b, tuple()):
            pid = str(pid)
            if pid in seen or pid in already:
                continue
            seen.add(pid)

            c = out_catalog.players.get(pid)
            if c is None:
                continue

            # return-ban / aggregation-solo-only 필터 (기존 _pick_bucket_player와 동일한 의도)
            if receiver_team and receiver_team in set(getattr(c, "return_ban_teams", None) or ()):
                continue
            # solo-only는 단독 outgoing이면 허용, 이미 outgoing이 있으면 추가 outgoing으로 붙이지 않음
            if bool(getattr(c, "aggregation_solo_only", False)) and not allow_solo_only:
                continue

            filler_salary_d = int(round(float(c.salary_m) * 1_000_000.0))
            if filler_salary_d <= 0:
                continue

            sim = check_salary_matching(
                payroll_before_d=payroll_before_d,
                outgoing_salary_d=outgoing_salary_d0 + filler_salary_d,
                incoming_salary_d=incoming_salary_d0,
                outgoing_players=outgoing_players0 + 1,
                incoming_players=incoming_players0,
                params=params,
            )
            if not sim.ok:
                continue

            mkt = float(getattr(c.market, "total", 0.0))
            passing.append((filler_salary_d, mkt, pid))

    if not passing:
        return False

    # "필요 샐러리를 충족하는 최소 salary" 우선, 그 안에서 market.total 최소
    passing.sort(key=lambda t: (t[0], t[1], t[2]))
    filler = passing[0][2]

    cand.deal.legs[failing_team].append(PlayerAsset(kind="player", player_id=filler))
    cand.tags.append("repair:add_filler_salary")
    return True


def _repair_second_apron_salary_mismatch(
    cand: DealCandidate,
    failing_team: str,
    catalog: TradeAssetCatalog,
    config: DealGeneratorConfig,
    failure: RuleFailure,
) -> bool:
    """SECOND_APRON + method=outgoing_second_apron salary mismatch 수리.

    2026+ (post-2024 CBA) 가정:
    - SECOND_APRON의 핵심 제약은 one-for-one이 아니라 *outgoing salary aggregation 금지*.
      즉, incoming_total이 '단일 outgoing 계약 1명'으로 매칭 가능해야 한다.

    SSOT 정렬:
    - failure.details["allowed_in"] 은 SalaryMatchingPolicy(SSOT)가 계산한 allowed_in(달러)이며,
      SECOND_APRON에서는 'max_single_outgoing_salary'와 동일해야 한다.
    - 따라서 수리 목표는: incoming_total_d <= allowed_in_d

    수리 전략(1-step, 다음 validate 루프에서 SSOT로 재검증)
    1) max_single_outgoing을 올린다(allowed_in ↑)
       - focal_player_id를 건드리지 않기 위해, 우선 failing_team outgoing 쪽을 swap/add로 보강한다.
    2) incoming_total을 내린다(incoming ↓)
       - 가능하면 focal이 아닌 incoming filler를 swap-down 또는 제거한다.

    NOTE:
    - player-specific aggregation_solo_only(별도 룰) 충돌을 줄이기 위해,
      solo-only 플레이어를 multi-player leg에 끼워 넣는 선택은 기본적으로 피한다.
    """

    team = str(failing_team).upper()
    others = [t for t in (cand.deal.teams or []) if str(t).upper() != team]
    if not others:
        return False
    other = str(others[0]).upper()

    # SSOT numbers (dollars)
    try:
        incoming_total_d = int(round(float(failure.details.get("incoming_salary") or 0.0)))
    except Exception:
        incoming_total_d = 0
    try:
        allowed_in_d = int(round(float(failure.details.get("allowed_in") or 0.0)))
    except Exception:
        allowed_in_d = 0

    if incoming_total_d <= 0 or allowed_in_d <= 0:
        return False
    if incoming_total_d <= allowed_in_d:
        return False

    # dollars 기반 비교: validate(SSOT)와 정렬해 float/rounding으로 인한 재실패를 줄인다.
    EPS_D = 1_000  # $1k
    required_out_d = incoming_total_d + EPS_D          # want a single outgoing >= this
    max_in_total_d = max(0, allowed_in_d - EPS_D)      # want incoming_total <= this

    focal_pid = str(cand.focal_player_id or "")
    all_pids: Set[str] = {
        str(a.player_id)
        for leg in (cand.deal.legs or {}).values()
        for a in (leg or [])
        if isinstance(a, PlayerAsset)
    }

    allow_solo_only = bool(getattr(config, "allow_solo_only_fillers", False))

    # Collect outgoing/incoming PlayerAssets (multi-player allowed)
    out_assets = list(cand.deal.legs.get(team, []) or [])
    out_players: List[PlayerAsset] = [a for a in out_assets if isinstance(a, PlayerAsset)]

    incoming_players: List[PlayerAsset] = []
    for a in cand.deal.legs.get(other, []) or []:
        if not isinstance(a, PlayerAsset):
            continue
        try:
            recv = str(resolve_asset_receiver(cand.deal, other, a)).upper()
        except Exception:
            recv = team  # 2-team deal fallback
        if recv == team:
            incoming_players.append(a)

    if not incoming_players:
        return False

    # =========================================================
    # Strategy 1) Raise max single outgoing (swap or add)
    # =========================================================
    out_cat = catalog.outgoing_by_team.get(team)
    if out_cat is not None:

        receiver_team = other
        # choose an existing outgoing to replace (prefer non-focal, lowest market)
        replace_pid: Optional[str] = None
        replace_key: Optional[Tuple[float, int]] = None  # (market, salary_d)
        for a in out_players:
            pid = str(a.player_id)
            if pid == focal_pid:
                continue
            c = out_cat.players.get(pid)
            if c is None:
                continue
            try:
                sal_d = int(round(float(c.salary_m) * 1_000_000.0))
            except Exception:
                sal_d = 0
            key = (float(getattr(getattr(c, "market", None), "total", 0.0) or 0.0), sal_d)
            if replace_key is None or key < replace_key:
                replace_key = key
                replace_pid = pid

        best_pid: Optional[str] = None
        best_key: Optional[Tuple[int, float, int]] = None  # (overshoot_d, market, salary_d)

        scan_buckets: Tuple[BucketId, ...] = (
            "FILLER_BAD_CONTRACT",
            "EXPIRING",
            "FILLER_CHEAP",
            "CONSOLIDATE",
            "SURPLUS_REDUNDANT",
            "SURPLUS_LOW_FIT",
            "VETERAN_SALE",
        )

        # Resulting outgoing player-count if we swap vs add (solo-only filter uses this)
        out_player_count_now = len(out_players)
        
        for b in scan_buckets:
            for pid in out_cat.player_ids_by_bucket.get(b, tuple()):
                pid_s = str(pid)
                if pid_s in all_pids:
                    continue
                c = out_cat.players.get(pid_s)
                if c is None:
                    continue
                if receiver_team in set(getattr(c, "return_ban_teams", None) or ()):
                    continue

                # Avoid selecting solo-only players into a multi-player leg (separate rule).
                if bool(getattr(c, "aggregation_solo_only", False)) and not allow_solo_only:
                    # swap keeps count, add increases count
                    resulting_count = out_player_count_now if replace_pid else (out_player_count_now + 1)
                    if resulting_count > 1:
                        continue

                sal_d = int(round(float(c.salary_m) * 1_000_000.0))
                if sal_d < required_out_d:
                    continue

                overshoot_d = sal_d - required_out_d
                mkt = float(getattr(getattr(c, "market", None), "total", 0.0) or 0.0)
                key = (overshoot_d, mkt, sal_d)
                if best_key is None or key < best_key:
                    best_key = key
                    best_pid = str(pid)

                    best_pid = pid_s

        if best_pid:
            if replace_pid:
                # Replace an existing outgoing PlayerAsset
                new_leg: List[Asset] = []
                for a in out_assets:
                    if isinstance(a, PlayerAsset) and str(a.player_id) == replace_pid:
                        new_leg.append(PlayerAsset(kind="player", player_id=best_pid))
                    else:
                        new_leg.append(a)
                cand.deal.legs[team] = new_leg
                cand.tags.append("repair:second_apron_raise_max_out_swap")
                return True

            # Otherwise, add a new outgoing PlayerAsset (to raise max_single_outgoing)
            cand.deal.legs[team] = list(out_assets) + [PlayerAsset(kind="player", player_id=best_pid)]
            cand.tags.append("repair:second_apron_raise_max_out_add")
            return True

    # =========================================================
    # Strategy 2) Reduce incoming total (swap-down or remove a non-focal incoming)
    # =========================================================
    other_cat = catalog.outgoing_by_team.get(other)
    if other_cat is None:
        return False
    if max_in_total_d <= 0:
        return False

    receiver_team = team
    # Build incoming meta (only incoming to failing_team)
    incoming_meta: List[Tuple[PlayerAsset, str, int, float]] = []
    for a in incoming_players:
        pid = str(a.player_id)
        c = other_cat.players.get(pid)
        if c is None:
            continue
        sal_d = int(round(float(c.salary_m) * 1_000_000.0))
        mkt = float(getattr(getattr(c, "market", None), "total", 0.0) or 0.0)
        incoming_meta.append((a, pid, sal_d, mkt))

    # Prefer modifying non-focal incoming (filler)
    non_focal = [x for x in incoming_meta if x[1] != focal_pid]
    if not non_focal:
        return False

    # pick the largest-salary non-focal incoming to maximize chance of fixing in 1 step
    non_focal.sort(key=lambda x: (-x[2], x[3]))
    target_asset, target_pid, target_sal_d, _target_mkt = non_focal[0]

    # If we keep other incoming unchanged, replacement must be <= limit
    limit_d = max_in_total_d - (incoming_total_d - target_sal_d)

    # Count player assets in other leg (for solo-only filter)
    other_leg_player_count = sum(
        1 for a in (cand.deal.legs.get(other, []) or []) if isinstance(a, PlayerAsset)
    )

    # -------------------------
    # 2A) swap-down (replace with cheaper incoming)
    # -------------------------
    if limit_d >= 0:
        best_pid2: Optional[str] = None
        best_key2: Optional[Tuple[int, float]] = None  # (slack_d, market)

        scan_buckets2: Tuple[BucketId, ...] = (
            "FILLER_CHEAP",
            "EXPIRING",
            "FILLER_BAD_CONTRACT",
            "SURPLUS_REDUNDANT",
            "SURPLUS_LOW_FIT",
            "CONSOLIDATE",
            "VETERAN_SALE",
        )

        for b in scan_buckets2:
            for pid in other_cat.player_ids_by_bucket.get(b, tuple()):
                pid_s = str(pid)
                if pid_s in all_pids:
                    continue
                c = other_cat.players.get(pid_s)
                if c is None:
                    continue
                if receiver_team in set(getattr(c, "return_ban_teams", None) or ()):
                    continue

                if bool(getattr(c, "aggregation_solo_only", False)) and not allow_solo_only:
                    # other leg already has multiple outgoing players -> solo-only would fail separate rule
                    if other_leg_player_count > 1:
                        continue

                sal_d = int(round(float(c.salary_m) * 1_000_000.0))
                if sal_d > limit_d:
                    continue

                slack_d = limit_d - sal_d  # 0에 가까울수록(= limit에 가까울수록) 좋음
                mkt = float(getattr(getattr(c, "market", None), "total", 0.0) or 0.0)
                key = (slack_d, mkt)
                if best_key2 is None or key < best_key2:
                    best_key2 = key
                    best_pid2 = pid_s

        if best_pid2:
            # Replace in other leg only for the asset that is incoming to failing_team
            new_leg2: List[Asset] = []
            for a in cand.deal.legs.get(other, []) or []:
                if isinstance(a, PlayerAsset) and str(a.player_id) == target_pid:
                    try:
                        recv = str(resolve_asset_receiver(cand.deal, other, a)).upper()
                    except Exception:
                        recv = team
                    if recv == team:
                        new_leg2.append(PlayerAsset(kind="player", player_id=best_pid2))
                    else:
                        new_leg2.append(a)
                else:
                    new_leg2.append(a)

            cand.deal.legs[other] = new_leg2
            cand.tags.append("repair:second_apron_reduce_in_swap")
            return True

    # -------------------------
    # 2B) remove a non-focal incoming (only if multiple incoming exist)
    # -------------------------
    if len(incoming_players) > 1 and (incoming_total_d - target_sal_d) <= max_in_total_d:
        removed = False
        new_leg3: List[Asset] = []
        for a in cand.deal.legs.get(other, []) or []:
            if (
                not removed
                and isinstance(a, PlayerAsset)
                and str(a.player_id) == target_pid
            ):
                try:
                    recv = str(resolve_asset_receiver(cand.deal, other, a)).upper()
                except Exception:
                    recv = team
                if recv == team:
                    removed = True
                    continue
            new_leg3.append(a)

        if removed:
            cand.deal.legs[other] = new_leg3
            cand.tags.append("repair:second_apron_reduce_in_remove")
            return True

    return False


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
    for b in ("FILLER_CHEAP", "EXPIRING", "FILLER_BAD_CONTRACT"):
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


