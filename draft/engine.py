from __future__ import annotations

"""Draft engine orchestration.

This module ties together:
  - finalize: compute plan, settle picks in DB, build DraftTurn list
  - pool/session: in-memory draft session
  - ai/apply: autopick + persist drafted rookies to DB

MVP focus:
  - minimal end-to-end functionality
  - deterministic, reproducible
  - explicit dict shapes for UI / API integration
"""

import datetime as _dt
import json
import game_time
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .finalize import (
    build_turns_from_plan,
    finalize_draft_year,
    has_order_plan,
    infer_db_path_from_state,
    infer_draft_year_from_state,
    load_order_plan,
    require_order_plan_settled,
)
from .types import DraftOrderPlan, DraftTurn, TeamId, norm_team_id
from .pool import DraftPool, load_pool_from_db
from .session import DraftSession, DraftPick
from .ai import DraftAIPolicy, DraftAIContext, DraftAISelection
from .ai_needs import NeedsPotentialGmPolicy

DEFAULT_AI_POLICY_KEY = "needs_potential_gm_v1"


def _fetch_applied_draft_results(*, db_path: str, draft_year: int) -> List[Dict[str, Any]]:
    """Load applied draft results (SSOT) for resume/idempotency.

    Returns rows ordered by overall_no. If draft_results doesn't exist yet, returns [].
    """
    # Local import to avoid heavier deps / potential cycles at module import time.
    from league_repo import LeagueRepo

    sql = """
    SELECT
      pick_id,
      overall_no,
      drafting_team,
      prospect_temp_id,
      player_id,
      contract_id
    FROM draft_results
    WHERE draft_year = ?
    ORDER BY overall_no ASC;
    """.strip()

    with LeagueRepo(str(db_path)) as repo:
        repo.init_db()
        try:
            rows = repo._conn.execute(sql, (int(draft_year),)).fetchall()
        except sqlite3.OperationalError:
            # Backward-compat: older DBs may not have draft_results yet.
            return []

    out: List[Dict[str, Any]] = []
    for r in rows:
        try:
            out.append(dict(r))
        except Exception:
            # Extremely defensive fallback
            d: Dict[str, Any] = {}
            try:
                for k in getattr(r, "keys", lambda: [])():
                    d[str(k)] = r[k]
            except Exception:
                d = {}
            out.append(d)
    return out



def _fetch_recorded_draft_selections(*, db_path: str, draft_year: int) -> List[Dict[str, Any]]:
    """Load recorded draft selections (pre-apply SSOT) for resume/interactive draft.

    Returns rows ordered by overall_no. If draft_selections doesn't exist yet, returns [].
    """
    # Local import to avoid heavier deps / potential cycles at module import time.
    from league_repo import LeagueRepo

    sql = """
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

    with LeagueRepo(str(db_path)) as repo:
        repo.init_db()
        try:
            rows = repo._conn.execute(sql, (int(draft_year),)).fetchall()
        except sqlite3.OperationalError:
            # Backward-compat: older DBs may not have draft_selections yet.
            return []

    out: List[Dict[str, Any]] = []
    for r in rows:
        try:
            out.append(dict(r))
        except Exception:
            # Extremely defensive fallback
            d: Dict[str, Any] = {}
            try:
                for k in getattr(r, "keys", lambda: [])():
                    d[str(k)] = r[k]
            except Exception:
                d = {}
            out.append(d)
    return out

def _infer_tx_date_from_state(state_snapshot: Mapping[str, Any]) -> str:
    league = state_snapshot.get("league", {}) if isinstance(state_snapshot, Mapping) else {}
    if isinstance(league, Mapping):
        cd = league.get("current_date")
        if cd:
            return str(cd)
    # Fail-loud: do not fall back to OS date; timeline must be in-game SSOT.
    raise ValueError("tx_date_iso is required (state_snapshot['league']['current_date'] missing; OS date fallback is disabled)")


@dataclass(slots=True)
class DraftEngineBundle:
    """A fully prepared draft bundle (plan + turns + session + pool)."""

    draft_year: int
    db_path: str
    plan: DraftOrderPlan
    turns: List[DraftTurn]
    settlement_events: List[Dict[str, Any]] = field(default_factory=list)

    pool: DraftPool = None  # type: ignore[assignment]
    session: DraftSession = None  # type: ignore[assignment]

    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "draft_year": int(self.draft_year),
            "db_path": str(self.db_path),
            "plan": self.plan.to_dict(),
            "turns": [t.to_dict() for t in self.turns],
            "settlement_events": list(self.settlement_events),
            "pool": None if self.pool is None else self.pool.to_dict(),
            "session": None if self.session is None else self.session.to_dict(),
            "meta": dict(self.meta) if isinstance(self.meta, dict) else {},
        }

    def to_public_dict(self, *, viewer_team_id: Optional[str] = None) -> Dict[str, Any]:
        """Public (user-facing) dict.

        Fog-of-war rule: do NOT expose db_path, and ensure pool/session are serialized
        through their public serializers (which redact sensitive keys like ovr/attrs/potential).
        """
        vt = viewer_team_id
        if vt is None:
            try:
                sm = self.session.meta if self.session is not None else None
                if isinstance(sm, dict):
                    vt = sm.get("viewer_team_id")  # may be None
            except Exception:
                vt = None

        pool_public = None if self.pool is None else self.pool.to_public_dict(viewer_team_id=vt)
        session_public = None if self.session is None else self.session.to_public_dict(viewer_team_id=vt)
        meta_out = dict(self.meta) if isinstance(self.meta, dict) else {}

        return {
            "draft_year": int(self.draft_year),
            "plan": self.plan.to_dict(),
            "turns": [t.to_dict() for t in self.turns],
            "settlement_events": list(self.settlement_events),
            "pool": pool_public,
            "session": session_public,
            "meta": meta_out,
        }


def prepare_bundle_from_state(
    state_snapshot: Mapping[str, Any],
    *,
    rng_seed: int,
    tie_break_seed: Optional[int] = None,
    use_lottery: bool = True,
    settle_db: bool = True,
    db_path: Optional[str] = None,
    draft_year: Optional[int] = None,
    pool_limit: Optional[int] = None,
    pool_season_year: Optional[int] = None,
    session_meta: Optional[Dict[str, Any]] = None,
) -> DraftEngineBundle:
    """Compute order, settle picks, build turns, and create a session with a pool.

    Returns a DraftEngineBundle that you can run interactively or auto-complete.
    """
    dbp = str(db_path) if db_path is not None else infer_db_path_from_state(state_snapshot)
    dy = int(draft_year) if draft_year is not None else infer_draft_year_from_state(state_snapshot)

    # SSOT: if a persisted order plan already exists for this draft year,
    # bundle/session must use that plan to stay consistent with run_lottery/run_settlement
    # and downstream apply_selections() validation.
    if has_order_plan(dbp, dy):
        plan = load_order_plan(dbp, dy)
        require_order_plan_settled(plan, where="prepare_bundle_from_state")
        turns = list(build_turns_from_plan(db_path=dbp, plan=plan, turn_attr_settled=True) or [])
        settlement_events: List[Dict[str, Any]] = []
    else:
        finalized = finalize_draft_year(
            state_snapshot,
            db_path=dbp,
            draft_year=dy,
            rng_seed=int(rng_seed),
            tie_break_seed=tie_break_seed,
            use_lottery=bool(use_lottery),
            settle_db=bool(settle_db),
        )

        plan = finalized["plan"]
        turns = list(finalized["turns"])
        settlement_events = list(finalized.get("settlement_events") or [])

    # Pool + session
    # NOTE: resume/idempotency is handled after pool/session creation by consulting
    # draft_results (applied SSOT).
    pool = load_pool_from_db(
        db_path=dbp,
        draft_year=int(plan.draft_year),
        season_year=pool_season_year,
        limit=pool_limit,
    )
    session = DraftSession(
        draft_year=int(plan.draft_year),
        turns=turns,
        pool=pool,
        cursor=0,
        picks_by_turn_index={},
        meta=dict(session_meta or {}),
    )

    # Resume safety: mark already-applied picks/prospects from SSOT and advance cursor.
    applied_rows = _fetch_applied_draft_results(db_path=dbp, draft_year=int(plan.draft_year))
    applied_pick_ids: List[str] = []
    if applied_rows:
        idx_by_overall = {int(t.overall_no): i for i, t in enumerate(turns)}
        applied_set = set()

        for row in applied_rows:
            try:
                overall_no = int(row.get("overall_no") or 0)
            except Exception:
                overall_no = 0
            idx = idx_by_overall.get(int(overall_no))
            if idx is None:
                raise RuntimeError(
                    f"draft_results has overall_no not present in turns: overall_no={overall_no} draft_year={plan.draft_year}"
                )
            turn = turns[int(idx)]

            pick_id_db = str(row.get("pick_id") or "")
            drafting_team_db = norm_team_id(row.get("drafting_team") or "")
            if pick_id_db and str(turn.pick_id) != pick_id_db:
                raise RuntimeError(
                    "draft resume mismatch (pick_id): "
                    f"overall_no={overall_no} db={pick_id_db!r} plan={turn.pick_id!r}"
                )
            if drafting_team_db and drafting_team_db != norm_team_id(turn.drafting_team):
                raise RuntimeError(
                    "draft resume mismatch (drafting_team): "
                    f"overall_no={overall_no} db={drafting_team_db!r} plan={turn.drafting_team!r}"
                )

            prospect_temp_id = str(row.get("prospect_temp_id") or "")
            player_id = str(row.get("player_id") or "")
            contract_id = str(row.get("contract_id") or "")

            # Ensure already-applied prospect cannot be selected again.
            if prospect_temp_id and session.pool.is_available(prospect_temp_id):
                session.pool.mark_picked(prospect_temp_id)

            # Populate pick history from DB (useful for debugging / stable session state).
            session.picks_by_turn_index[int(idx)] = DraftPick(
                overall_no=int(turn.overall_no),
                round=int(turn.round),
                slot=int(turn.slot),
                pick_id=str(turn.pick_id),
                drafting_team=turn.drafting_team,
                prospect_temp_id=prospect_temp_id,
                player_id=player_id or None,
                contract_id=contract_id or None,
                meta={"resumed_from_db": True},
            )

            applied_set.add(str(turn.pick_id))

        # Advance cursor across the already-applied prefix.
        while not session.is_complete() and str(session.current_turn().pick_id) in applied_set:
            session.cursor += 1

        applied_pick_ids = sorted(applied_set)

    remaining_turns = int(len(turns) - int(session.cursor))
    if int(len(pool.available_temp_ids)) < remaining_turns:
        raise RuntimeError(
            f"draft pool too small: available={len(pool.available_temp_ids)} remaining_turns={remaining_turns} draft_year={plan.draft_year}"
        )

    bundle = DraftEngineBundle(
        draft_year=int(plan.draft_year),
        db_path=dbp,
        plan=plan,
        turns=turns,
        settlement_events=settlement_events,
        pool=pool,
        session=session,
        meta={
            "rng_seed": int(rng_seed),
            "tie_break_seed": int(tie_break_seed) if tie_break_seed is not None else None,
            "ai_policy": DEFAULT_AI_POLICY_KEY,
            "pool_source": "college_db",
            "pool_limit": int(pool_limit) if pool_limit is not None else None,
            "pool_season_year": int(pool_season_year) if pool_season_year is not None else None,
            "use_lottery": bool(use_lottery),
            "settle_db": bool(settle_db),
            "applied_pick_ids": list(applied_pick_ids),
            "applied_picks_count": int(len(applied_pick_ids)),
            "resume_cursor": int(session.cursor),
        },
    )
    return bundle


def prepare_bundle_from_saved_plan(
    state_snapshot: Mapping[str, Any],
    *,
    db_path: Optional[str] = None,
    draft_year: Optional[int] = None,
    pool_limit: Optional[int] = None,
    pool_season_year: Optional[int] = None,
    session_meta: Optional[Dict[str, Any]] = None,
) -> DraftEngineBundle:
    """Prepare a draft bundle from a persisted order plan (draft_order_plans).

    Intended split flow:
      - lottery step persists an order plan (draft.finalize.store_order_plan)
      - settlement step updates draft_picks ownership (draft.finalize.settle_and_build_turns)
      - interactive draft records selections into draft_selections (this module)
      - apply step consumes draft_selections into draft_results (draft.pipeline.apply_selections)

    Resume safety:
      - Restores already-applied picks from draft_results (SSOT)
      - Restores recorded selections from draft_selections (pre-apply SSOT)
    """
    from .finalize import (
        infer_db_path_from_state,
        infer_draft_year_from_state,
        load_order_plan,
        build_turns_from_plan,
        require_order_plan_settled,
    )

    dbp = str(db_path) if db_path is not None else infer_db_path_from_state(state_snapshot)
    dy = int(draft_year) if draft_year is not None else infer_draft_year_from_state(state_snapshot)

    plan = load_order_plan(dbp, int(dy))
    # Fail-loud: draft must not proceed unless settlement was explicitly completed.
    require_order_plan_settled(plan, where="prepare_bundle_from_saved_plan")
    turns: List[DraftTurn] = list(build_turns_from_plan(db_path=dbp, plan=plan) or [])
    settlement_events: List[Dict[str, Any]] = []

    # Draft AI RNG seed (deterministic but non-zero).
    # Prefer the lottery seed if present; otherwise fall back to plan.meta or a draft-year-derived seed.
    ai_rng_seed: int
    try:
        ai_rng_seed = int(getattr(plan.lottery_result, "rng_seed", 0) or 0) if plan.lottery_result is not None else 0
    except Exception:
        ai_rng_seed = 0
    if ai_rng_seed == 0:
        try:
            ai_rng_seed = int((plan.meta or {}).get("rng_seed") or 0)
        except Exception:
            ai_rng_seed = 0
    if ai_rng_seed == 0:
        # Stable fallback: ensures softmax sampling is not trivially identical across all leagues.
        ai_rng_seed = int(plan.draft_year) * 1000003 + 1337

    # Pool + session
    pool = load_pool_from_db(
        db_path=dbp,
        draft_year=int(plan.draft_year),
        season_year=pool_season_year,
        limit=pool_limit,
    )
    session = DraftSession(
        draft_year=int(plan.draft_year),
        turns=turns,
        pool=pool,
        cursor=0,
        picks_by_turn_index={},
        meta=dict(session_meta or {}),
    )

    # (1) Applied SSOT: draft_results
    applied_rows = _fetch_applied_draft_results(db_path=dbp, draft_year=int(plan.draft_year))
    applied_pick_ids: List[str] = []
    if applied_rows:
        idx_by_overall = {int(t.overall_no): i for i, t in enumerate(turns)}
        applied_set = set()

        for row in applied_rows:
            try:
                overall_no = int(row.get("overall_no") or 0)
            except Exception:
                overall_no = 0
            idx = idx_by_overall.get(int(overall_no))
            if idx is None:
                raise RuntimeError(
                    f"draft_results has overall_no not present in turns: overall_no={overall_no} draft_year={plan.draft_year}"
                )
            turn = turns[int(idx)]

            pick_id_db = str(row.get("pick_id") or "")
            drafting_team_db = norm_team_id(row.get("drafting_team") or "")
            if pick_id_db and str(turn.pick_id) != pick_id_db:
                raise RuntimeError(
                    "draft resume mismatch (pick_id): "
                    f"overall_no={overall_no} db={pick_id_db!r} plan={turn.pick_id!r}"
                )
            if drafting_team_db and drafting_team_db != norm_team_id(turn.drafting_team):
                raise RuntimeError(
                    "draft resume mismatch (drafting_team): "
                    f"overall_no={overall_no} db={drafting_team_db!r} plan={turn.drafting_team!r}"
                )

            prospect_temp_id = str(row.get("prospect_temp_id") or "")
            player_id = str(row.get("player_id") or "")
            contract_id = str(row.get("contract_id") or "")

            if prospect_temp_id and session.pool.is_available(prospect_temp_id):
                session.pool.mark_picked(prospect_temp_id)

            session.picks_by_turn_index[int(idx)] = DraftPick(
                overall_no=int(turn.overall_no),
                round=int(turn.round),
                slot=int(turn.slot),
                pick_id=str(turn.pick_id),
                drafting_team=turn.drafting_team,
                prospect_temp_id=prospect_temp_id,
                player_id=player_id or None,
                contract_id=contract_id or None,
                meta={"resumed_from_db": True, "applied": True},
            )
            applied_set.add(str(turn.pick_id))

        while not session.is_complete() and str(session.current_turn().pick_id) in applied_set:
            session.cursor += 1

        applied_pick_ids = sorted(applied_set)

    # (2) Selection SSOT: draft_selections
    selection_rows = _fetch_recorded_draft_selections(db_path=dbp, draft_year=int(plan.draft_year))
    if selection_rows:
        idx_by_overall = {int(t.overall_no): i for i, t in enumerate(turns)}
        for row in selection_rows:
            try:
                overall_no = int(row.get("overall_no") or 0)
            except Exception:
                overall_no = 0
            if overall_no <= 0:
                continue
            idx = idx_by_overall.get(int(overall_no))
            if idx is None:
                raise RuntimeError(
                    f"draft_selections has overall_no not present in turns: overall_no={overall_no} draft_year={plan.draft_year}"
                )
            if int(idx) in session.picks_by_turn_index:
                # Applied SSOT takes precedence.
                continue

            turn = turns[int(idx)]
            pick_id_db = str(row.get("pick_id") or "")
            drafting_team_db = norm_team_id(row.get("drafting_team") or "")
            if pick_id_db and str(turn.pick_id) != pick_id_db:
                raise RuntimeError(
                    "draft selection resume mismatch (pick_id): "
                    f"overall_no={overall_no} db={pick_id_db!r} plan={turn.pick_id!r}"
                )
            if drafting_team_db and drafting_team_db != norm_team_id(turn.drafting_team):
                raise RuntimeError(
                    "draft selection resume mismatch (drafting_team): "
                    f"overall_no={overall_no} db={drafting_team_db!r} plan={turn.drafting_team!r}"
                )

            prospect_temp_id = str(row.get("prospect_temp_id") or "")
            if not prospect_temp_id:
                continue

            if session.pool.is_available(prospect_temp_id):
                session.pool.mark_picked(prospect_temp_id)

            meta_obj: Dict[str, Any] = {}
            meta_json = row.get("meta_json")
            if meta_json:
                try:
                    meta_obj = json.loads(str(meta_json))
                    if not isinstance(meta_obj, dict):
                        meta_obj = {}
                except Exception:
                    meta_obj = {}

            session.picks_by_turn_index[int(idx)] = DraftPick(
                overall_no=int(turn.overall_no),
                round=int(turn.round),
                slot=int(turn.slot),
                pick_id=str(turn.pick_id),
                drafting_team=turn.drafting_team,
                prospect_temp_id=prospect_temp_id,
                player_id=None,
                contract_id=None,
                meta={"resumed_from_db": True, "applied": False, "source": row.get("source") or "draft"} | meta_obj,
            )

        while not session.is_complete() and int(session.cursor) in session.picks_by_turn_index:
            session.cursor += 1

    remaining_turns = int(len(turns) - int(session.cursor))
    if int(len(pool.available_temp_ids)) < remaining_turns:
        raise RuntimeError(
            f"draft pool too small: available={len(pool.available_temp_ids)} remaining_turns={remaining_turns} draft_year={plan.draft_year}"
        )

    bundle = DraftEngineBundle(
        draft_year=int(plan.draft_year),
        db_path=dbp,
        plan=plan,
        turns=turns,
        settlement_events=settlement_events,
        pool=pool,
        session=session,
        meta={
            "pool_source": "college_db",
            "pool_limit": int(pool_limit) if pool_limit is not None else None,
            "pool_season_year": int(pool_season_year) if pool_season_year is not None else None,
            "use_saved_plan": True,
            "settle_db": False,
            "applied_pick_ids": list(applied_pick_ids),
            "applied_picks_count": int(len(applied_pick_ids)),
            "resume_cursor": int(session.cursor),
            "rng_seed": int(ai_rng_seed),
            "ai_policy": DEFAULT_AI_POLICY_KEY,
        },
    )
    return bundle


def _resolve_ai_policy(*, bundle: DraftEngineBundle, policy: Optional[DraftAIPolicy]) -> DraftAIPolicy:
    """Resolve which DraftAIPolicy to use.

    Project rule: the legacy OVR-only BPA policy is disabled. We never silently
    fall back to a different policy.
    """
    if policy is not None:
        # Hard guard: prevent accidental re-introduction of BPA-like policies.
        try:
            name = policy.__class__.__name__.lower()
        except Exception:
            name = ""
        if "bpa" in name and "ovr" in name:
            raise ValueError("OVR-only BPA policy is disabled. Use NeedsPotentialGmPolicy instead.")
        return policy

    try:
        ai_key = str((bundle.meta or {}).get("ai_policy") or "").strip()
    except Exception:
        ai_key = ""

    if not ai_key:
        ai_key = DEFAULT_AI_POLICY_KEY

    if ai_key in ("needs_potential_gm_v1", "needs", "team_needs"):
        return NeedsPotentialGmPolicy()

    # Fail-loud: do not silently fall back to an unintended policy.
    raise ValueError(f"Unknown ai_policy={ai_key!r}. Allowed: 'needs_potential_gm_v1'.")


def choose_ai_pick(
    *,
    policy: DraftAIPolicy,
    session: DraftSession,
    meta: Optional[Dict[str, Any]] = None,
) -> DraftAISelection:
    """Return AI selection (prospect_temp_id + optional meta) for current turn."""
    # NOTE: NeedsPotentialGmPolicy reads ctx.meta["db_path"] etc.
    # We allow callers to pass meta, and default to {} for older call sites.
    turn = session.current_turn()
    ctx = DraftAIContext(
        draft_year=session.draft_year,
        team_id=turn.drafting_team,
        turn=turn,
        meta=dict(meta or {}),
    )
    # Preferred API (new): choose(...) -> DraftAISelection
    if hasattr(policy, "choose"):
        sel = policy.choose(session.pool, ctx)  # type: ignore[attr-defined]
        if isinstance(sel, DraftAISelection):
            return sel
        if isinstance(sel, str):
            return DraftAISelection(prospect_temp_id=str(sel), meta={"policy": "legacy_str"})
    # Back-compat: choose_prospect_temp_id(...) -> str
    tid = str(policy.choose_prospect_temp_id(session.pool, ctx))  # type: ignore[attr-defined]
    return DraftAISelection(prospect_temp_id=tid, meta={"policy": "legacy"})



def record_pick_and_save_selection(
    *,
    bundle: DraftEngineBundle,
    prospect_temp_id: str,
    selected_at_iso: str,
    source: str = "draft",
    meta: Optional[Dict[str, Any]] = None,
) -> DraftPick:
    """Record a selection into draft_selections without applying it to NBA tables."""
    from league_repo import LeagueRepo

    session = bundle.session
    if session is None:
        raise RuntimeError("bundle.session is None")

    turn_index = int(session.cursor)
    turn = session.current_turn()
    tid = str(prospect_temp_id)

    if not session.pool.is_available(tid):
        raise ValueError(f"prospect not available: {tid}")

    # 1) reserve in session (marks pool + advances cursor)
    dp0 = session.record_pick(
        prospect_temp_id=tid,
        player_id=None,
        contract_id=None,
        meta={"selection_only": True, "reserved": True, "source": str(source)} | (dict(meta or {})),
    )

    meta_saved = dict(dp0.meta or {}) | {"reserved": False, "saved_to_db": True}
    # 2) persist selection to DB
    dbp = str(bundle.db_path)
    # Fail-loud: selection timestamps must follow in-game timeline (OS clock disabled).
    selected_at = game_time.require_date_iso(selected_at_iso, field="selected_at_iso")
    now = game_time.utc_like_from_date_iso(selected_at, field="selected_at_iso")

    sql_exists = "SELECT 1 FROM draft_selections WHERE pick_id=? LIMIT 1;"
    sql_ins = """
    INSERT INTO draft_selections(
      pick_id, draft_year, overall_no, round, slot,
      original_team, drafting_team,
      prospect_temp_id, selected_at, source, meta_json,
      created_at, updated_at
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
    """.strip()

    meta_json = json.dumps(meta_saved, ensure_ascii=False, separators=(",", ":"))

    try:
        with LeagueRepo(dbp) as repo:
            repo.init_db()
            with repo.transaction() as cur:
                try:
                    cur.execute("SELECT 1 FROM draft_selections LIMIT 1;")
                except sqlite3.OperationalError as e:
                    raise RuntimeError("draft_selections table missing. Apply db_schema/draft.py patch first.") from e

                row = cur.execute(sql_exists, (str(turn.pick_id),)).fetchone()
                if row is not None:
                    raise RuntimeError(f"selection already recorded for pick_id={turn.pick_id} draft_year={bundle.draft_year}")

                cur.execute(
                    sql_ins,
                    (
                        str(turn.pick_id),
                        int(bundle.draft_year),
                        int(turn.overall_no),
                        int(turn.round),
                        int(turn.slot),
                        str(turn.original_team),
                        str(turn.drafting_team),
                        str(tid),
                        str(selected_at),
                        str(source),
                        meta_json,
                        now,
                        now,
                    ),
                )
    except Exception:
        # rollback in-memory
        session.cursor = int(turn_index)
        session.picks_by_turn_index.pop(int(turn_index), None)
        session.pool.unmark_picked(tid)
        raise

    # 3) mark as saved
    dp = DraftPick(
        overall_no=dp0.overall_no,
        round=dp0.round,
        slot=dp0.slot,
        pick_id=dp0.pick_id,
        drafting_team=dp0.drafting_team,
        prospect_temp_id=dp0.prospect_temp_id,
        player_id=None,
        contract_id=None,
        meta=meta_saved,
    )
    session.picks_by_turn_index[int(turn_index)] = dp
    return dp


def auto_run_selections(
    *,
    bundle: DraftEngineBundle,
    selected_at_iso: str,
    policy: Optional[DraftAIPolicy] = None,
    max_picks: Optional[int] = None,
    stop_on_user_controlled_team_ids: Optional[Sequence[str]] = None,
    allow_autopick_user_team: bool = False,
    source: str = "draft_ai",
) -> List[DraftPick]:
    """Auto-advance the draft by recording selections only (no apply)."""
    sess = bundle.session
    if sess is None:
        raise RuntimeError("bundle.session is None")
    pol = _resolve_ai_policy(bundle=bundle, policy=policy)

    stop_set = {norm_team_id(t) for t in (stop_on_user_controlled_team_ids or []) if norm_team_id(t)}

    # Fail-closed: 유저팀 목록이 없으면 기본은 자동픽 금지.
    if (not allow_autopick_user_team) and (not stop_set):
        raise ValueError(
            "stop_on_user_controlled_team_ids is required unless allow_autopick_user_team=true"
        )
    picks: List[DraftPick] = []
    limit = int(max_picks) if max_picks is not None else None

    while not sess.is_complete():
        if limit is not None and len(picks) >= limit:
            break

        # Skip already-selected/applied turns (resume safety).
        while not sess.is_complete() and int(sess.cursor) in sess.picks_by_turn_index:
            sess.cursor += 1
        if sess.is_complete():
            break

        if stop_set and norm_team_id(sess.current_turn().drafting_team) in stop_set:
            break

        sel = choose_ai_pick(
            policy=pol,
            session=sess,
            meta={
                "db_path": str(bundle.db_path),
                "rng_seed": int((bundle.meta or {}).get("rng_seed") or 0),
                "total_picks": int(len(bundle.turns)),
                "debug": bool((bundle.meta or {}).get("ai_debug") or False),
            },
        )
        dp = record_pick_and_save_selection(
            bundle=bundle,
            prospect_temp_id=str(sel.prospect_temp_id),
            selected_at_iso=str(selected_at_iso),
            source=str(source),
            meta={
                "ai": dict(sel.meta or {}),
            },
        )
        picks.append(dp)

    return picks



# Convenience wrappers that operate on global state (optional).
def prepare_bundle_from_global_state(
    *,
    rng_seed: int,
    tie_break_seed: Optional[int] = None,
    use_lottery: bool = True,
    settle_db: bool = True,
    pool_limit: Optional[int] = None,
    pool_season_year: Optional[int] = None,
    session_meta: Optional[Dict[str, Any]] = None,
) -> DraftEngineBundle:
    import state  # local import to avoid cycles at module import time

    snap = state.export_full_state_snapshot()
    return prepare_bundle_from_state(
        snap,
        rng_seed=int(rng_seed),
        tie_break_seed=tie_break_seed,
        use_lottery=bool(use_lottery),
        settle_db=bool(settle_db),
        db_path=None,
        draft_year=None,
        pool_limit=pool_limit,
        pool_season_year=pool_season_year,
        session_meta=session_meta,
    )
