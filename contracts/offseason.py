"""Offseason contract handling."""

from __future__ import annotations

import logging
from typing import Any, Dict, Mapping, Optional

logger = logging.getLogger(__name__)
_WARN_COUNTS: Dict[str, int] = {}


def _warn_limited(code: str, msg: str, *, limit: int = 5) -> None:
    n = _WARN_COUNTS.get(code, 0)
    if n < limit:
        logger.warning("%s %s", code, msg, exc_info=True)
    _WARN_COUNTS[code] = n + 1


from league_service import LeagueService
from contracts.policy.bird_rights_policy import (
    BIRD_NONE,
    cap_hold_multiplier,
    classify_bird_type,
)

def _get_db_path(game_state: dict) -> str:
    # Fail-fast: safest behavior is to never silently write to a default DB.
    league_state = game_state.get("league")
    if not isinstance(league_state, dict):
        raise ValueError("game_state['league'] must be a dict and contain 'db_path'")
    db_path = league_state.get("db_path")
    if not db_path:
        raise ValueError("game_state['league']['db_path'] is required")
    return str(db_path)


def process_offseason(
    game_state: dict,
    from_season_year: int,
    to_season_year: int,
    *,
    decision_date_iso: str,
    decision_policy=None,
) -> dict:

    from contracts.options_policy import default_option_decision_policy

    # Validate years early (avoid partial writes with invalid inputs).
    fy = int(from_season_year)
    ty = int(to_season_year)
    if fy <= 0 or ty <= 0:
        raise ValueError("from_season_year and to_season_year must be positive ints")
 
    # Fail-fast DB path acquisition (no env/default fallback).
    db_path = _get_db_path(game_state)

    # ---------------------------------------------------------------------
    # Idempotency guard: prevent double-processing the same offseason window.
    # (Dev/UI button can be clicked multiple times; double-running is harmful.)
    # ---------------------------------------------------------------------
    from league_repo import LeagueRepo

    meta_key = f"contracts_offseason_done_{ty}"
    with LeagueRepo(db_path) as _repo:
        _repo.init_db()
        row = _repo._conn.execute("SELECT value FROM meta WHERE key=?;", (meta_key,)).fetchone()
        _already_done = bool(row is not None and str(row["value"]) == "1")

    if _already_done:
        settlement_result: Dict[str, Any] = {
            "draft_year": int(fy + 1),
            "ok": False,
            "skipped": True,
            "reason": "handled_by_draft_engine",
        }
        return {
            "expired": 0,
            "released": 0,
            "contracts_transition": {
                "ok": False,
                "skipped": True,
                "reason": "already_done",
                "from_season_year": int(fy),
                "to_season_year": int(ty),
            },
            "trade_settlement": settlement_result,
        }

    # Wrap policy so it can see the real game_state even if LeagueService passes a stub.
    if decision_policy is None:
        decision_policy = default_option_decision_policy
    if callable(decision_policy):
        user_policy = decision_policy

        def _wrapped_policy(option: dict, player_id: str, contract: dict, _stub_state: dict):
            return user_policy(option, player_id, contract, game_state)

        decision_policy_to_pass = _wrapped_policy
    else:
        decision_policy_to_pass = None

    # Run DB writes in a single service context.
    with LeagueService.open(db_path) as svc:

        # 1) 계약 만료/옵션 처리 (SSOT)
        expire_result = svc.expire_contracts_for_season_transition(
            fy,
            ty,
            decision_date_iso=str(decision_date_iso),
            decision_policy=decision_policy_to_pass,
        )

        expired = int(expire_result.get("expired") or 0)
        released = int(expire_result.get("released") or 0)
        released_ids = [str(x) for x in (expire_result.get("released_player_ids") or [])]
        released_set = set(released_ids)

        # 1-b) Bird rights + cap holds for own-team expiring FAs.
        expired_contract_ids = [str(x) for x in (expire_result.get("expired_contract_ids") or [])]
        contracts_map = svc.repo.get_contracts_map(active_only=False)
        rights_rows: list[dict[str, Any]] = []
        hold_rows: list[dict[str, Any]] = []

        for cid in expired_contract_ids:
            c = contracts_map.get(str(cid))
            if not isinstance(c, Mapping):
                continue
            pid = str(c.get("player_id") or "")
            if not pid or (released_set and pid not in released_set):
                continue
            prev_team = str(c.get("team_id") or "").upper()
            if not prev_team or prev_team == "FA":
                continue

            contract_years = max(0, _safe_int(c.get("years"), 0))
            tenure_years = _same_team_tenure_years(
                contracts_map=contracts_map,
                player_id=pid,
                team_id=prev_team,
                as_of_year=int(fy),
                fallback_years=int(contract_years),
            )
            bird_type = classify_bird_type(tenure_years)
            if str(bird_type).upper() == BIRD_NONE:
                continue

            prev_salary = _salary_for_prev_season(c, from_season_year=int(fy))
            hold_mult = float(cap_hold_multiplier(bird_type))
            hold_amount = max(0, int(round(float(prev_salary) * float(hold_mult))))

            rights_rows.append(
                {
                    "season_year": int(ty),
                    "team_id": str(prev_team),
                    "player_id": str(pid),
                    "bird_type": str(bird_type).upper(),
                    "tenure_years_same_team": int(tenure_years),
                    "is_renounced": 0,
                }
            )
            hold_rows.append(
                {
                    "season_year": int(ty),
                    "team_id": str(prev_team),
                    "player_id": str(pid),
                    "source_type": "BIRD",
                    "bird_type": str(bird_type).upper(),
                    "hold_amount": int(hold_amount),
                    "is_released": 0,
                    "released_reason": None,
                }
            )

        if rights_rows:
            svc.repo.upsert_team_bird_rights(rights_rows)
        if hold_rows:
            svc.repo.upsert_team_cap_holds(hold_rows)

        expire_result["bird_rights_created"] = int(len(rights_rows))
        expire_result["cap_holds_created"] = int(len(hold_rows))

        # NOTE: UI cache updates are handled outside via state.start_new_season() post-mutation
        # (ui_cache_rebuild_all). Do not mutate legacy game_state["players"] here.

        # 2) 드래프트 정산(보호/스왑)은 draft 엔진(draft.finalize / draft.engine)에서 수행한다.
        # contracts.offseason은 계약/옵션 처리만 담당한다.
        settlement_result: Dict[str, Any] = {
            "draft_year": int(fy + 1),
            "ok": False,
            "skipped": True,
            "reason": "handled_by_draft_engine",
        }

        svc.repo.validate_integrity()

        # Apply offseason EXP progression once per offseason window.
        # Policy: increment only ACTIVE roster players.
        with svc.repo.transaction() as cur:
            cur.execute(
                """
                UPDATE players
                   SET exp = COALESCE(exp, 0) + 1,
                       updated_at = ?
                 WHERE player_id IN (
                    SELECT r.player_id
                      FROM roster r
                     WHERE LOWER(COALESCE(r.status, 'active')) = 'active'
                 );
                """,
                (decision_date_iso,),
            )

        # Mark idempotency after successful processing.
        with svc.repo.transaction() as cur:
            cur.execute(
                "INSERT INTO meta(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value;",
                (meta_key, "1"),
            )

    return {
        "expired": expired,
        "released": released,
        "contracts_transition": expire_result,
        "trade_settlement": settlement_result,
    }


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return int(default)
        return int(value)
    except Exception:
        return int(default)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return float(default)
        return float(value)
    except Exception:
        return float(default)


def _same_team_tenure_years(
    *,
    contracts_map: Mapping[str, Mapping[str, Any]],
    player_id: str,
    team_id: str,
    as_of_year: int,
    fallback_years: int = 0,
) -> int:
    years_covered: set[int] = set()
    pid = str(player_id)
    tid = str(team_id).upper()
    for _cid, c in (contracts_map or {}).items():
        if not isinstance(c, Mapping):
            continue
        if str(c.get("player_id") or "") != pid:
            continue
        if str(c.get("team_id") or "").upper() != tid:
            continue
        start = _safe_int(c.get("start_season_year"), 0)
        years = _safe_int(c.get("years"), 0)
        if start <= 0 or years <= 0:
            continue
        end = start + years - 1
        for y in range(start, end + 1):
            years_covered.add(int(y))

    tenure = 0
    y = int(as_of_year)
    while y in years_covered:
        tenure += 1
        y -= 1

    if tenure <= 0:
        tenure = max(0, int(fallback_years))
    return int(tenure)


def _salary_for_prev_season(contract: Mapping[str, Any], *, from_season_year: int) -> int:
    if not isinstance(contract, Mapping):
        return 0
    salary_by_year = contract.get("salary_by_year")
    if isinstance(salary_by_year, Mapping):
        key = str(int(from_season_year))
        if key in salary_by_year:
            return max(0, int(round(_safe_float(salary_by_year.get(key), 0.0))))
    return 0
