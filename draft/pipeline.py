from __future__ import annotations

"""draft.pipeline

Stepwise offseason orchestration helpers for the draft system.

This module exists to support a split flow (UI-visible steps) such as:
  1) Lottery (top-4) -> persist order plan
  2) Settlement (protections/swaps) -> seed + settle draft_picks, build turns
  3) Draft session (selections recorded elsewhere)
  4) Apply selections -> create NBA players/roster/contracts, write draft_results

Notes:
- This module does NOT implement the interactive draft session itself.
  It assumes pre-apply choices are stored in DB table `draft_selections`.
- `draft_results` remains the SSOT for applied picks (idempotent / resumable).
"""

import datetime as _dt
import game_time
import sqlite3
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .types import DraftOrderPlan, DraftTurn, norm_team_id


# Keep seeds consistent with the previous monolithic start_new_season flow.
_RNG_SEED_OFFSET = 100_003
_TIE_BREAK_SEED_OFFSET = 200_017


def _infer_prev_season_year(draft_year: int) -> int:
    return int(draft_year) - 1


def _infer_rng_seed(prev_year: int) -> int:
    # Matches state.start_new_season(...) usage in this codebase.
    return int(prev_year) + _RNG_SEED_OFFSET


def _infer_tie_break_seed(prev_year: int) -> int:
    # Matches state.start_new_season(...) usage in this codebase.
    return int(prev_year) + _TIE_BREAK_SEED_OFFSET


def run_lottery(state_snapshot: Mapping[str, Any], db_path: str, draft_year: int) -> DraftOrderPlan:
    """Compute (or load) the DraftOrderPlan for the given draft_year.

    Idempotency:
    - If `draft_order_plans` already has a plan for draft_year, returns it as-is.
    - Otherwise computes a plan from state standings, runs lottery, and persists it.

    Args:
        state_snapshot: full exported state snapshot (must include master_schedule finals).
        db_path: league sqlite path (used to persist the plan).
        draft_year: target draft year.

    Returns:
        DraftOrderPlan (includes lottery_result when use_lottery=True).
    """
    from .finalize import compute_plan_from_state, has_order_plan, load_order_plan, store_order_plan

    dy = int(draft_year)
    dbp = str(db_path)

    if has_order_plan(dbp, dy):
        return load_order_plan(dbp, dy)

    prev_year = _infer_prev_season_year(dy)
    rng_seed = _infer_rng_seed(prev_year)
    tie_break_seed = _infer_tie_break_seed(prev_year)

    plan = compute_plan_from_state(
        state_snapshot,
        draft_year=dy,
        rng_seed=int(rng_seed),
        tie_break_seed=int(tie_break_seed),
        use_lottery=True,
    )
    store_order_plan(dbp, dy, plan)
    return plan


def run_settlement(db_path: str, draft_year: int) -> Tuple[List[Dict[str, Any]], List[DraftTurn]]:
    """Settle picks (protections/swaps) and build the 60 DraftTurn list.

    Requires:
    - A persisted DraftOrderPlan for draft_year (created by run_lottery).

    Returns:
        (settlement_events, turns)
    """
    from .finalize import load_order_plan, settle_and_build_turns, store_order_plan, mark_order_plan_settled

    dy = int(draft_year)
    dbp = str(db_path)

    plan = load_order_plan(dbp, dy)
    out = settle_and_build_turns(db_path=dbp, plan=plan, settle_db=True)
    settlement_events = list(out.get("settlement_events") or [])
    turns = list(out.get("turns") or [])

    # Persist a strict "settled" marker onto the stored order plan (fail-loud gate).
    settled_at_utc = game_time.now_utc_like_iso()
    plan2 = mark_order_plan_settled(plan, settled=True, settled_at_utc=settled_at_utc)
    store_order_plan(dbp, dy, plan2)

    return settlement_events, turns


def apply_selections(db_path: str, draft_year: int, tx_date_iso: Optional[str] = None, *, cap_model: Optional["CapModel"] = None) -> int:
    """Apply pre-recorded selections (draft_selections) into the NBA DB.

    This performs the *apply* side effects:
      - players/roster/contracts/transactions_log
      - draft_results upsert (SSOT)
      - college cleanup for drafted players (handled inside apply_pick_to_db)

    Preconditions (enforced):
      - A persisted DraftOrderPlan must exist (run_lottery).
      - Settlement must have been executed already (run_settlement), and the stored plan must be marked settled.
      - draft_selections must contain a selection for every *pending* overall slot
        (i.e., each pick not yet present in draft_results for this draft_year).
      
    Args:
        tx_date_iso: ISO date string to stamp into transactions log (must be the in-game date).

    Returns:
        applied_count: number of picks newly applied (already-applied are not counted).
    """
    from league_repo import LeagueRepo
    from .finalize import load_order_plan, build_turns_from_plan, require_order_plan_settled
    from .pool import load_pool_from_db
    from .apply import apply_pick_to_db

    dy = int(draft_year)
    dbp = str(db_path)

    # Ensure plan exists and settlement has been explicitly completed (fail-loud).
    plan = load_order_plan(dbp, dy)
    require_order_plan_settled(plan, where="apply_selections")
    turns: List[DraftTurn] = list(build_turns_from_plan(db_path=dbp, plan=plan, turn_attr_settled=True) or [])
    if not turns:
        raise RuntimeError(f"No draft turns produced for draft_year={dy}")

    # Build lookup keyed by pick_id (stable identity across selections/results).
    turn_by_pick_id: Dict[str, DraftTurn] = {str(t.pick_id): t for t in turns}
    turn_pick_ids = set(turn_by_pick_id.keys())

    # Load applied results SSOT first. This enables resume after partial apply
    # without requiring college prospect rows (may have been deleted already).
    res_sql = """
    SELECT
      pick_id,
      overall_no,
      "round",
      slot,
      original_team,
      drafting_team,
      prospect_temp_id,
      player_id,
      contract_id,
      applied_at,
      source,
      meta_json
    FROM draft_results
    WHERE draft_year = ?;
    """.strip()

    applied_by_pick_id: Dict[str, Dict[str, Any]] = {}
    with LeagueRepo(dbp) as repo:
        repo.init_db()
        try:
            rows = repo._conn.execute(res_sql, (dy,)).fetchall()
        except sqlite3.OperationalError:
            rows = []

        for r in rows:
            try:
                d = dict(r)
            except Exception:
                d = {}
                try:
                    for k in getattr(r, "keys", lambda: [])():
                        d[str(k)] = r[k]
                except Exception:
                    d = {}

            pid = str(d.get("pick_id") or "")
            if not pid:
                continue
            if pid in applied_by_pick_id:
                raise RuntimeError(f"Duplicate draft_results row for pick_id={pid!r} draft_year={dy}")
            applied_by_pick_id[pid] = d

    applied_pick_ids = set(applied_by_pick_id.keys())

    # Safety: applied results must correspond to the current computed turns for this draft_year.
    stray = [pid for pid in applied_pick_ids if pid not in turn_pick_ids]
    if stray:
        preview = ", ".join(repr(s) for s in stray[:5])
        raise RuntimeError(
            "draft_results contains pick_id(s) not present in current turns: "
            f"{preview} (count={len(stray)}) draft_year={dy}"
        )

    # If everything is already applied, validate basic alignment and exit idempotently.
    if applied_pick_ids and len(applied_pick_ids) == len(turns):
        for pid, row in applied_by_pick_id.items():
            t = turn_by_pick_id.get(pid)
            if t is None:
                continue
            try:
                if int(row.get("overall_no") or 0) != int(t.overall_no):
                    raise RuntimeError(
                        "draft_results overall_no mismatch vs turns: "
                        f"pick_id={pid!r} res={row.get('overall_no')!r} turn={t.overall_no!r} draft_year={dy}"
                    )
            except Exception as e:
                raise RuntimeError(
                    f"draft_results overall_no unreadable for pick_id={pid!r} draft_year={dy}"
                ) from e

            res_team = norm_team_id(row.get("drafting_team") or "")
            if res_team and res_team != norm_team_id(t.drafting_team):
                raise RuntimeError(
                    "draft_results drafting_team mismatch vs turns: "
                    f"pick_id={pid!r} res={res_team!r} turn={t.drafting_team!r} draft_year={dy}"
                )
        return 0

    # Load selections SSOT (pre-apply).
    sel_sql = """
    SELECT
      pick_id,
      overall_no,
      drafting_team,
      prospect_temp_id,
      source,
      meta_json
    FROM draft_selections
    WHERE draft_year = ?
    ORDER BY overall_no ASC;
    """.strip()

    selections: List[Dict[str, Any]] = []
    with LeagueRepo(dbp) as repo:
        repo.init_db()
        try:
            rows = repo._conn.execute(sel_sql, (dy,)).fetchall()
        except sqlite3.OperationalError as e:
            raise RuntimeError("draft_selections table missing or unreadable") from e

        for r in rows:
            try:
                selections.append(dict(r))
            except Exception:
                d: Dict[str, Any] = {}
                try:
                    for k in getattr(r, "keys", lambda: [])():
                        d[str(k)] = r[k]
                except Exception:
                    d = {}
                selections.append(d)

    sel_by_overall: Dict[int, Dict[str, Any]] = {}
    for s in selections:
        ov = int(s.get("overall_no") or 0)
        if ov <= 0:
            continue
        if ov in sel_by_overall:
            raise RuntimeError(f"Duplicate draft selection for overall_no={ov} draft_year={dy}")
        sel_by_overall[ov] = s

    # Only enforce presence for pending picks (resume-safe).
    pending_turns: List[DraftTurn] = [t for t in turns if str(t.pick_id) not in applied_pick_ids]
    missing_pending = [int(t.overall_no) for t in pending_turns if int(t.overall_no) not in sel_by_overall]
    if missing_pending:
        missing_pending.sort()
        preview = ", ".join(str(x) for x in missing_pending[:10])
        raise RuntimeError(
            "Missing selection(s) for pending pick(s): "
            f"overall_no={preview} (count={len(missing_pending)}) draft_year={dy}"
        )

    # Load the full prospect pool *before* applying any picks, because apply_pick_to_db
    # may remove college rows for drafted prospects.
    prev_year = _infer_prev_season_year(dy)
    pool = load_pool_from_db(db_path=dbp, draft_year=dy, season_year=int(prev_year), limit=None)
    prospect_by_id = dict(pool.prospects_by_temp_id)

    # Apply in overall order.
    applied_count = 0
    # Use the in-game date provided by the caller.
    # Fail-loud: do NOT fall back to OS date (timeline immersion).
    _tx = tx_date_iso
    if isinstance(_tx, _dt.date):
        _tx = _tx.isoformat()
    if not _tx:
        raise ValueError("tx_date_iso is required (pass in-game date ISO; OS date fallback is disabled)")
    tx_date_iso = str(_tx)

    for turn in turns:
        pid = str(turn.pick_id)
        ov = int(turn.overall_no)

        applied_row = applied_by_pick_id.get(pid)
        sel = sel_by_overall.get(ov)

        if applied_row is not None:
            # Resume-safe: already applied picks do not require prospect lookup.
            try:
                if int(applied_row.get("overall_no") or 0) != ov:
                    raise RuntimeError(
                        "draft_results overall_no mismatch vs turns: "
                        f"pick_id={pid!r} res={applied_row.get('overall_no')!r} turn={ov!r} draft_year={dy}"
                    )
            except Exception as e:
                raise RuntimeError(
                    f"draft_results overall_no unreadable for pick_id={pid!r} draft_year={dy}"
                ) from e

            res_team = norm_team_id(applied_row.get("drafting_team") or "")
            if res_team and res_team != norm_team_id(turn.drafting_team):
                raise RuntimeError(
                    "draft_results drafting_team mismatch vs turns: "
                    f"pick_id={pid!r} res={res_team!r} turn={turn.drafting_team!r} draft_year={dy}"
                )

            # If a selection exists, ensure it matches the applied result.
            if sel is not None:
                pick_id_sel = str(sel.get("pick_id") or "")
                if pick_id_sel and pid != pick_id_sel:
                    raise RuntimeError(
                        "Selection pick_id mismatch vs turns/results: "
                        f"overall_no={ov} sel={pick_id_sel!r} turn={pid!r} draft_year={dy}"
                    )

                drafting_team_sel = norm_team_id(sel.get("drafting_team") or "")
                if drafting_team_sel and drafting_team_sel != norm_team_id(turn.drafting_team):
                    raise RuntimeError(
                        "Selection drafting_team mismatch: "
                        f"overall_no={ov} sel={drafting_team_sel!r} turn={turn.drafting_team!r} draft_year={dy}"
                    )

                temp_id_sel = str(sel.get("prospect_temp_id") or "")
                temp_id_res = str(applied_row.get("prospect_temp_id") or "")
                if temp_id_sel and temp_id_res and temp_id_sel != temp_id_res:
                    raise RuntimeError(
                        "Selection prospect_temp_id mismatch vs draft_results: "
                        f"overall_no={ov} sel={temp_id_sel!r} res={temp_id_res!r} draft_year={dy}"
                    )

            continue

        # Pending pick: selection must exist.
        if sel is None:
            raise RuntimeError(f"Missing selection for overall_no={ov} draft_year={dy}")

        pick_id_sel = str(sel.get("pick_id") or "")
        if pick_id_sel and pid != pick_id_sel:
            raise RuntimeError(
                "Selection pick_id mismatch: "
                f"overall_no={ov} sel={pick_id_sel!r} turn={pid!r} draft_year={dy}"
            )

        drafting_team_sel = norm_team_id(sel.get("drafting_team") or "")
        if drafting_team_sel and drafting_team_sel != norm_team_id(turn.drafting_team):
            raise RuntimeError(
                "Selection drafting_team mismatch: "
                f"overall_no={ov} sel={drafting_team_sel!r} turn={turn.drafting_team!r} draft_year={dy}"
            )

        temp_id = str(sel.get("prospect_temp_id") or "")
        if not temp_id:
            raise RuntimeError(f"Selection missing prospect_temp_id for overall_no={ov} draft_year={dy}")

        prospect = prospect_by_id.get(temp_id)
        if prospect is None:
            # Defensive: pool.get raises KeyError with a good message.
            prospect = pool.get(temp_id)

        source = str(sel.get("source") or "draft")

        # Contract terms are determined inside apply_pick_to_db (round-aware):
        # - 1R: rookie scale w/ 3rd/4th year TEAM options
        # - 2R: SRPE templates (2+1 / 3+1) [when enabled in draft.apply]
        res = apply_pick_to_db(
            db_path=dbp,
            turn=turn,
            prospect=prospect,
            draft_year=dy,
            cap_model=cap_model,
            tx_date_iso=tx_date_iso,
            source=source,
        )

        already = False
        try:
            already = bool((res.tx_entry or {}).get("already_applied") or False)
        except Exception:
            already = False
        if not already:
            applied_count += 1

    return int(applied_count)
