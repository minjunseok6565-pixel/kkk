from __future__ import annotations

"""
trades/valuation/service.py

IO / orchestration layer that wires the *pure* valuation engine into the project.

Ownership boundaries (enforced by design)
-----------------------------------------
This module MAY:
- call validate_deal(...) to ensure legality/feasibility (salary matching, Stepien, apron rules, locks, etc.)
- build TeamSituation and DecisionContext (team_situation.py + decision_context.py)
- build ValuationDataProvider backed by LeagueRepo snapshots (data_context.py)
- call valuation engine: deal_evaluator + decision_policy

This module MUST NOT:
- re-implement rule validation or re-check hard constraints (that's validator + trades/rules/*)
- re-interpret team status or re-generate needs (that's team_situation.py)
- create new "knobs" or override DecisionContext outputs (that's decision_context.py)
"""

from dataclasses import replace
from datetime import date
from typing import Any, Dict, Optional, Sequence, Tuple
import os

import random

import game_time

# --- Project-level trade types / errors ---
from ..models import Deal, PickAsset, PlayerAsset, SwapAsset
from ..errors import TradeError
from ..validator import validate_deal

# --- Valuation engine (pure) ---
from .deal_evaluator import evaluate_deal_for_team as _evaluate_deal_for_team
from .env import ValuationEnv
from .market_pricing import MarketPricingConfig
from .team_utility import TeamUtilityConfig
from .package_effects import PackageEffectsConfig
from .decision_policy import decide_deal as _decide_deal
from .decision_policy import DecisionPolicyConfig
from .types import DealDecision, TeamDealEvaluation, TeamSideValuation
from .context_v2 import build_valuation_context_v2
from .types import PickExpectation

# --- Valuation data provider (Repo IO layer) ---
from .data_context import (
    RepoValuationDataContext,
    PickExpectationMap,
    build_repo_valuation_data_context,
)

# --- team_situation / decision_context live outside trades/ in the current project layout.
# Keep imports flexible so the same file survives refactors.
try:
    from team_situation import build_team_situation_context, TeamSituationEvaluator  # type: ignore
except Exception:  # pragma: no cover
    from data.team_situation import build_team_situation_context, TeamSituationEvaluator  # type: ignore

try:
    from decision_context import (  # type: ignore
        DecisionContext,
        GMTradeTraits,
        build_decision_context,
        gm_traits_from_profile_json,
    )
except Exception:  # pragma: no cover
    from data.decision_context import (  # type: ignore
        DecisionContext,
        GMTradeTraits,
        build_decision_context,
        gm_traits_from_profile_json,
    )

try:
    import state  # type: ignore
except Exception as exc:  # pragma: no cover
    state = None  # type: ignore

try:
    from league_repo import LeagueRepo  # type: ignore
except Exception as exc:  # pragma: no cover
    LeagueRepo = None  # type: ignore

try:
    from schema import normalize_team_id  # type: ignore
except Exception:  # pragma: no cover
    def normalize_team_id(x: str, strict: bool = False) -> str:  # type: ignore
        return str(x or "").upper()


# -----------------------------------------------------------------------------
# Small helpers (defensive; service layer should not crash the server on minor gaps)
# -----------------------------------------------------------------------------
def _safe_date(d: Any) -> date:
    if isinstance(d, date):
        return d
    # accept ISO string
    if isinstance(d, str):
        try:
            return date.fromisoformat(d[:10])
        except Exception:
            pass
    # Never fall back to OS date.
    if state is not None:
        try:
            return game_time.game_date()
        except Exception as exc:
            raise TradeError(
                code="MISSING_GAME_DATE",
                message="In-game current_date is required (OS clock disabled).",
                details={"exc_type": type(exc).__name__},
            )
    raise TradeError(code="MISSING_GAME_DATE", message="In-game current_date is required (OS clock disabled).")


def _safe_db_path(db_path: Optional[str]) -> str:
    if db_path:
        return str(db_path)
    if state is not None:
        try:
            return str(state.get_db_path())
        except Exception:
            pass
    raise TradeError(code="MISSING_DB_PATH", message="db_path is required for valuation service")


def _resolve_current_season_year(current_season_year: Optional[int], *, current_date: date) -> int:
    if current_season_year is not None:
        try:
            return int(current_season_year)
        except Exception:
            pass
    # state.league.season_year is the SSOT in this project
    if state is not None:
        try:
            snap = state.snapshot_state()
            league = snap.get("league") if isinstance(snap, dict) else None
            sy = (league or {}).get("season_year") if isinstance(league, dict) else None
            if sy:
                return int(sy)
        except Exception:
            pass
    y = int(getattr(current_date, "year", 0) or 0)
    if y <= 0:
        raise TradeError(code="MISSING_GAME_DATE", message="Invalid current_date; OS clock disabled.")
    return y


def _build_standings_order_worst_to_best(team_situation_ctx: Any) -> Optional[Sequence[str]]:
    """
    Build a league-wide standings order worst->best for pick expectation heuristics.
    Uses only already-snapshotted data (no new DB/state reads).
    """
    rec_index = getattr(team_situation_ctx, "records_index", None)
    if not isinstance(rec_index, dict) or not rec_index:
        return None

    rows = []
    for tid, rec in rec_index.items():
        if not isinstance(rec, dict):
            continue
        wins = int(rec.get("wins", 0) or 0)
        losses = int(rec.get("losses", 0) or 0)
        gp = wins + losses
        win_pct = (wins / gp) if gp > 0 else 0.0
        pf = float(rec.get("pf", 0) or 0.0)
        pa = float(rec.get("pa", 0) or 0.0)
        point_diff_pg = ((pf - pa) / gp) if gp > 0 else 0.0
        rows.append((str(tid).upper(), float(win_pct), float(point_diff_pg)))

    # worst -> best: lowest win_pct, then lowest point_diff_pg
    rows_sorted = sorted(rows, key=lambda x: (x[1], x[2], x[0]))
    return [r[0] for r in rows_sorted]

def _extract_salary_cap_from_league_ctx(team_situation_ctx: Any) -> Optional[float]:
    """Best-effort extraction of current league salary cap from TeamSituationContext.

    SSOT path in this project:
      team_situation_ctx.league_ctx.trade_rules.salary_cap

    Returns None when unavailable.
    """
    try:
        league_ctx = getattr(team_situation_ctx, "league_ctx", None)
        if not isinstance(league_ctx, dict):
            return None
        trade_rules = league_ctx.get("trade_rules", {}) or {}
        if not isinstance(trade_rules, dict):
            return None
        cap = trade_rules.get("salary_cap")
        cap_f = float(cap)
        return cap_f if cap_f > 0 else None
    except Exception:
        return None


def _extract_trade_rules_from_league_ctx(team_situation_ctx: Any) -> Optional[Dict[str, Any]]:
    """Best-effort extraction of league.trade_rules from TeamSituationContext.

    SSOT path in this project:
      team_situation_ctx.league_ctx.trade_rules

    Returns:
        dict when available; None otherwise.
    """
    try:
        league_ctx = getattr(team_situation_ctx, "league_ctx", None)
        if not isinstance(league_ctx, dict):
            return None
        trade_rules = league_ctx.get("trade_rules", None)
        return dict(trade_rules) if isinstance(trade_rules, dict) else None
    except Exception:
        return None


def _strip_breakdown(side: TeamSideValuation, evaluation: TeamDealEvaluation) -> Tuple[TeamSideValuation, TeamDealEvaluation]:
    """
    Remove step-by-step breakdown tuples to reduce payload size when requested.
    (Keeps numeric totals and high-level fit flags.)
    """
    def _strip_tv(tv):
        return replace(tv, market_steps=tuple(), team_steps=tuple())

    incoming = tuple(_strip_tv(tv) for tv in side.incoming)
    outgoing = tuple(_strip_tv(tv) for tv in side.outgoing)
    side2 = replace(side, incoming=incoming, outgoing=outgoing, package_steps=tuple())
    eval2 = replace(evaluation, side=side2)
    return side2, eval2


def _resolve_bool_flag(explicit: Optional[bool], *, env_name: str, default: bool) -> bool:
    if explicit is not None:
        return bool(explicit)
    raw = os.getenv(env_name, "")
    if not raw:
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "t", "yes", "y", "on"}


def _build_context_v2_asset_ids(
    deal: Deal,
    *,
    standings_order_worst_to_best: Optional[Sequence[str]],
) -> Dict[str, Sequence[str]]:
    player_ids: list[str] = []
    pick_ids: list[str] = []
    swap_ids: list[str] = []

    for assets in (deal.legs or {}).values():
        if not isinstance(assets, list):
            continue
        for a in assets:
            if isinstance(a, PlayerAsset):
                pid = str(a.player_id)
                if pid and pid not in player_ids:
                    player_ids.append(pid)
            elif isinstance(a, PickAsset):
                pkid = str(a.pick_id)
                if pkid and pkid not in pick_ids:
                    pick_ids.append(pkid)
            elif isinstance(a, SwapAsset):
                sid = str(a.swap_id)
                if sid and sid not in swap_ids:
                    swap_ids.append(sid)

    out: Dict[str, Sequence[str]] = {
        "players": tuple(player_ids),
        "picks": tuple(pick_ids),
        "swaps": tuple(swap_ids),
    }
    if standings_order_worst_to_best:
        out["standings_order_worst_to_best"] = tuple(str(t) for t in standings_order_worst_to_best)
    return out


class _ContextV2ProviderAdapter:
    """v2 context를 SSOT로 사용하는 provider adapter.

    - snapshots(player/pick/swap/fixed)은 base provider를 위임 사용
    - pick expectation은 context_v2.pick_distributions를 기준으로 synthesize
    - market_pricing v2 입력을 위해 get_pick_distribution()를 제공
    """

    def __init__(self, *, base_provider: RepoValuationDataContext, v2_ctx: Any):
        self._base = base_provider
        self._v2 = v2_ctx

    def get_player_snapshot(self, player_id):
        return self._base.get_player_snapshot(player_id)

    def get_pick_snapshot(self, pick_id):
        return self._base.get_pick_snapshot(pick_id)

    def get_swap_snapshot(self, swap_id):
        return self._base.get_swap_snapshot(swap_id)

    def get_fixed_asset_snapshot(self, asset_id):
        return self._base.get_fixed_asset_snapshot(asset_id)

    def get_pick_distribution(self, pick_id):
        return self._v2.pick_distributions.get(str(pick_id))

    def get_pick_expectation(self, pick_id):
        bundle = self.get_pick_distribution(pick_id)
        if bundle is None:
            return self._base.get_pick_expectation(pick_id)
        return PickExpectation(
            pick_id=str(pick_id),
            expected_pick_number=(float(bundle.compat_expected_pick_number) if bundle.compat_expected_pick_number is not None else None),
            confidence=0.65,
            meta={
                "source": "context_v2.pick_distributions",
                "ev_pick": float(bundle.ev_pick),
                "variance": float(bundle.variance),
            },
        )

    @property
    def current_season_year(self):
        return self._base.current_season_year

    @property
    def current_date_iso(self):
        return self._base.current_date_iso


# -----------------------------------------------------------------------------
# Public API (service entrypoint)
# -----------------------------------------------------------------------------
def evaluate_deal_for_team(
    deal: Deal,
    team_id: str,
    *,
    tick_ctx: Optional[Any] = None,
    current_date: Optional[date] = None,
    db_path: Optional[str] = None,
    current_season_year: Optional[int] = None,
    standings_order_worst_to_best: Optional[Sequence[str]] = None,
    pick_expectations: Optional[PickExpectationMap] = None,
    include_breakdown: bool = True,
    include_package_effects: bool = True,
    allow_counter: bool = True,
    rng: Optional[random.Random] = None,
    rng_seed: Optional[int] = None,
    allow_locked_by_deal_id: Optional[str] = None,
    validate: bool = True,
    use_valuation_context_v2: Optional[bool] = None,
    valuation_context_v2_stage: str = "full",
    valuation_context_v2_dual_read: bool = True,
) -> Tuple[DealDecision, TeamDealEvaluation]:
    """
    Evaluate a deal from `team_id`'s perspective and return (decision, evaluation).

    This is the canonical orchestration function that the debug API should call.

    Parameters
    ----------
    deal:
        A canonical Deal object (see trades.models).
    team_id:
        Evaluating team id.
    current_date:
        In-game date. Defaults to state.get_current_date_as_date() when available.
    db_path:
        SQLite DB path. Defaults to state.get_db_path() when available.
    current_season_year:
        Season year for contract/pick calculations. Defaults to state.league.season_year when available.
    standings_order_worst_to_best:
        Optional league-wide order used for pick expectation heuristic.
        If not provided, we attempt to derive from team_situation snapshot records_index.
    pick_expectations:
        If provided, overrides standings-based expectation builder.
    include_breakdown:
        If False, strips verbose step logs from the returned evaluation (lighter payload).
    include_package_effects:
        Apply package effects (diminishing returns / roster saturation, etc.)
    allow_counter:
        Whether the decision policy is allowed to return COUNTER.
    rng / rng_seed:
        Control the (optional) stochastic edge behavior for counter decisions.
    allow_locked_by_deal_id:
        Pass-through to validate_deal for committed-deal lock exceptions.
    validate:
        If True, runs validate_deal first. (Recommended for server usage.)
    use_valuation_context_v2:
        Feature flag. If False(off), 기존 경로와 완전히 동일하게 동작.
        If True(on), context_v2를 빌드하고 stage에 따라 점진 반영.
    valuation_context_v2_stage:
        - "shadow": debug 전용(진단 중심, 보수 설정)
        - "gradual": package_effects texture weight를 보수적으로 적용
        - "full": package_effects texture weight 기본값으로 적용
    valuation_context_v2_dual_read:
        context_v2 dual-read diff telemetry 수집 여부.
    """
    tid = normalize_team_id(team_id, strict=False)

    # ------------------------------------------------------------------
    # Tick context fast-path (generation/orchestration can inject caches)
    # ------------------------------------------------------------------
    if tick_ctx is not None:
        # Normalize / validate invariants vs explicit args (fail-fast on mismatch)
        t_dbp = getattr(tick_ctx, "db_path", None)
        t_cd = getattr(tick_ctx, "current_date", None)
        t_sy = getattr(tick_ctx, "season_year", None)

        if db_path is not None and t_dbp is not None and str(db_path) != str(t_dbp):
            raise TradeError(code="TICK_CTX_MISMATCH", message=f"db_path mismatch (arg={db_path!r}, tick_ctx={t_dbp!r})")
        if current_date is not None and t_cd is not None and _safe_date(current_date) != _safe_date(t_cd):
            raise TradeError(code="TICK_CTX_MISMATCH", message=f"current_date mismatch (arg={current_date!r}, tick_ctx={t_cd!r})")
        if current_season_year is not None and t_sy is not None:
            try:
                if int(current_season_year) != int(t_sy):
                    raise TradeError(
                        code="TICK_CTX_MISMATCH",
                        message=f"current_season_year mismatch (arg={current_season_year!r}, tick_ctx={t_sy!r})",
                    )
            except Exception:
                pass

        # Use tick_ctx invariants as SSOT for this call
        cd = _safe_date(t_cd)
        dbp = _safe_db_path(str(t_dbp))
        try:
            season_year = int(t_sy)
        except Exception:
            season_year = _resolve_current_season_year(current_season_year, current_date=cd)
    else:
        cd = _safe_date(current_date)
        dbp = _safe_db_path(db_path)
        season_year = _resolve_current_season_year(current_season_year, current_date=cd)

    # RNG setup: default deterministic if no rng supplied.
    if rng is None:
        rng = random.Random(rng_seed) if rng_seed is not None else random.Random(0)

    # 1) Hard rule validation (salary matching, Stepien, apron, locks, etc.)
    if validate:
        if tick_ctx is not None:
            rule_tick_ctx = getattr(tick_ctx, "rule_tick_ctx", None)
            validate_deal(
                deal,
                current_date=cd,
                allow_locked_by_deal_id=allow_locked_by_deal_id,
                db_path=dbp,
                tick_ctx=rule_tick_ctx,
            )
        else:
            validate_deal(
                deal,
                current_date=cd,
                allow_locked_by_deal_id=allow_locked_by_deal_id,
                db_path=dbp,
            )

    # 2) TeamSituation snapshot + per-team evaluation
    if tick_ctx is not None:
        ts_ctx = getattr(tick_ctx, "team_situation_ctx", None)
        team_situations = getattr(tick_ctx, "team_situations", None)
        if ts_ctx is None:
            # Fallback: behave like legacy path (shouldn't happen when tick_ctx is well-formed)
            ts_ctx = build_team_situation_context(db_path=dbp, current_date=cd)
        if isinstance(team_situations, dict) and tid in team_situations:
            ts_eval = team_situations[tid]
        else:
            # Fallback: evaluate only this team (still uses tick snapshots)
            repo_obj = getattr(tick_ctx, "repo", None)
            ts_eval = TeamSituationEvaluator(ctx=ts_ctx, db_path=dbp, repo=repo_obj).evaluate_team(tid)
    else:
        ts_ctx = build_team_situation_context(db_path=dbp, current_date=cd)
        ts_eval = TeamSituationEvaluator(ctx=ts_ctx, db_path=dbp).evaluate_team(tid)

    # 3) GM profile -> GMTradeTraits -> DecisionContext
    if tick_ctx is not None:
        decision_contexts = getattr(tick_ctx, "decision_contexts", None)
        if isinstance(decision_contexts, dict) and tid in decision_contexts:
            ctx = decision_contexts[tid]
        else:
            # Fallback: derive traits/profile if not cached
            gm_profile: Dict[str, Any] = {}
            if LeagueRepo is None:  # pragma: no cover
                raise TradeError(code="LEAGUE_REPO_IMPORT_FAILED", message="LeagueRepo import failed; cannot read gm profile")

            repo_obj = getattr(tick_ctx, "repo", None)
            try:
                if repo_obj is not None:
                    gp = repo_obj.get_gm_profile(tid) or {}
                    gm_profile = dict(gp) if isinstance(gp, dict) else {"value": gp}
                else:
                    with LeagueRepo(dbp) as repo2:
                        gp = repo2.get_gm_profile(tid) or {}
                        gm_profile = dict(gp) if isinstance(gp, dict) else {"value": gp}
            except Exception:
                gm_profile = {}
            gm_traits: GMTradeTraits = gm_traits_from_profile_json(gm_profile, default=GMTradeTraits())
            ctx = build_decision_context(team_situation=ts_eval, gm_traits=gm_traits, team_id=tid)
    else:
        gm_profile: Dict[str, Any] = {}
        if LeagueRepo is None:  # pragma: no cover
            raise TradeError(code="LEAGUE_REPO_IMPORT_FAILED", message="LeagueRepo import failed; cannot read gm profile")

        try:
            with LeagueRepo(dbp) as repo:
                gp = repo.get_gm_profile(tid) or {}
                gm_profile = dict(gp) if isinstance(gp, dict) else {"value": gp}
        except Exception:
            # Fallback to default mid traits when profile missing or read fails.
            gm_profile = {}

        gm_traits = gm_traits_from_profile_json(gm_profile, default=GMTradeTraits())
        ctx = build_decision_context(team_situation=ts_eval, gm_traits=gm_traits, team_id=tid)

    # 4) Build valuation provider (trade assets + contract ledger + pick expectations)
    if tick_ctx is not None:
        provider = getattr(tick_ctx, "provider", None)
        # Optional override path (debug/experiments): if caller provides expectations/order explicitly,
        # build a one-off provider using tick snapshots (still avoids new DB reads).
        if provider is None or pick_expectations is not None or standings_order_worst_to_best is not None:
            order = standings_order_worst_to_best or getattr(tick_ctx, "standings_order_worst_to_best", None) or _build_standings_order_worst_to_best(ts_ctx)
            repo_obj = getattr(tick_ctx, "repo", None)
            assets_snap = getattr(ts_ctx, "assets_snapshot", None)
            ledger_snap = getattr(ts_ctx, "contract_ledger", None)
            provider = build_repo_valuation_data_context(
                db_path=dbp,
                current_season_year=season_year,
                current_date_iso=cd.isoformat(),
                standings_order_worst_to_best=order,
                pick_expectations=pick_expectations,
                repo=repo_obj,
                assets_snapshot=(assets_snap if isinstance(assets_snap, dict) else None),
                contract_ledger=(ledger_snap if isinstance(ledger_snap, dict) else None),
            )
    else:
        order = standings_order_worst_to_best or _build_standings_order_worst_to_best(ts_ctx)
        provider = build_repo_valuation_data_context(
            db_path=dbp,
            current_season_year=season_year,
            current_date_iso=cd.isoformat(),
            standings_order_worst_to_best=order,
            pick_expectations=pick_expectations,
        )

    # 5) Pure valuation (market -> team utility -> package effects)
    #
    # SSOT: build valuation runtime env once and pass it down the pure pipeline.
    trade_rules = _extract_trade_rules_from_league_ctx(ts_ctx) or {}
    env = ValuationEnv.from_trade_rules(trade_rules, current_season_year=int(season_year))

    # Cap-normalized valuation: keep config.salary_cap populated for legacy
    # code paths that still read it (team_utility finance thresholds, etc.).
    # IMPORTANT: the canonical source is now `env.cap_model`.
    salary_cap = _extract_salary_cap_from_league_ctx(ts_ctx)
    if salary_cap is None:
        try:
            cap_now = float(env.salary_cap())
            salary_cap = cap_now if cap_now > 0 else None
        except Exception:
            salary_cap = None
            
    market_cfg = MarketPricingConfig(salary_cap=salary_cap) if salary_cap is not None else MarketPricingConfig()
    team_cfg = TeamUtilityConfig(salary_cap=salary_cap) if salary_cap is not None else TeamUtilityConfig()
    use_v2 = _resolve_bool_flag(
        use_valuation_context_v2,
        env_name="TRADE_VALUATION_CONTEXT_V2",
        default=True,
    )
    stage = str(valuation_context_v2_stage or "full").strip().lower()
    if stage not in {"shadow", "gradual", "full"}:
        stage = "full"
    package_cfg: Optional[PackageEffectsConfig] = None
    if use_v2 and stage in {"shadow", "gradual", "full"}:
        base = PackageEffectsConfig()
        if stage == "shadow":
            package_cfg = replace(
                base,
                dual_read_v2_components=True,
                texture_overlap_weight=0.0,
                contract_texture_control_weight=0.0,
                contract_texture_trigger_weight=0.0,
                contract_texture_toxic_weight=0.0,
            )
        elif stage == "gradual":
            package_cfg = replace(
                base,
                dual_read_v2_components=bool(valuation_context_v2_dual_read),
                texture_overlap_weight=0.55,
                contract_texture_control_weight=0.08,
                contract_texture_trigger_weight=0.05,
                contract_texture_toxic_weight=0.04,
            )
        else:
            package_cfg = replace(
                base,
                dual_read_v2_components=bool(valuation_context_v2_dual_read),
            )

    active_provider = provider
    v2_ctx = None
    if use_v2:
        try:
            decision_context_by_team: Dict[str, DecisionContext] = {tid: ctx}
            if tick_ctx is not None:
                dc_all = getattr(tick_ctx, "decision_contexts", None)
                if isinstance(dc_all, dict):
                    for team_key, team_ctx in dc_all.items():
                        if isinstance(team_ctx, DecisionContext):
                            decision_context_by_team[str(team_key)] = team_ctx

            asset_ids_by_kind = _build_context_v2_asset_ids(
                deal,
                standings_order_worst_to_best=(order if isinstance(order, Sequence) else None),
            )
            v2_ctx = build_valuation_context_v2(
                provider=provider,
                decision_context_by_team=decision_context_by_team,
                current_season_year=int(season_year),
                current_date_iso=cd.isoformat(),
                market_pricing_config=market_cfg,
                team_utility_config=team_cfg,
                package_effects_config=(package_cfg or PackageEffectsConfig()),
                decision_policy_config=DecisionPolicyConfig(),
                asset_ids_by_kind=asset_ids_by_kind,
                dual_read=bool(valuation_context_v2_dual_read),
            )
            active_provider = _ContextV2ProviderAdapter(base_provider=provider, v2_ctx=v2_ctx)
        except Exception:
            v2_ctx = None

    side, evaluation = _evaluate_deal_for_team(
        deal=deal,
        team_id=tid,
        ctx=ctx,
        provider=active_provider,
        env=env,
        include_package_effects=include_package_effects,
        attach_leg_metadata=True,
        market_config=market_cfg,
        team_config=team_cfg,
        package_config=package_cfg,
    )

    if use_v2:
        v2_diag_meta: Dict[str, Any] = {
            "enabled": True,
            "stage": stage,
            "dual_read": bool(valuation_context_v2_dual_read),
        }
        try:
            if v2_ctx is None:
                raise RuntimeError("context_v2_build_failed")
            v2_diag_meta["diagnostics"] = {
                "source_coverage": dict(v2_ctx.diagnostics.source_coverage),
                "reason_flags": list(v2_ctx.diagnostics.reason_flags),
                "diff_report": (
                    {
                        "pick_ev_delta": float(v2_ctx.diagnostics.diff_report.pick_ev_delta),
                        "contract_burden_delta": float(v2_ctx.diagnostics.diff_report.contract_burden_delta),
                        "cap_flex_delta": float(v2_ctx.diagnostics.diff_report.cap_flex_delta),
                        "missing_metrics": list(v2_ctx.diagnostics.diff_report.missing_metrics),
                    }
                    if v2_ctx.diagnostics.diff_report is not None
                    else None
                ),
            }
        except Exception as exc:
            v2_diag_meta["error"] = {
                "type": type(exc).__name__,
                "message": str(exc),
            }

        new_meta = dict(evaluation.meta or {})
        new_meta["context_v2"] = v2_diag_meta
        evaluation = replace(evaluation, meta=new_meta)

    # 6) Decision
    decision = _decide_deal(
        evaluation=evaluation,
        ctx=ctx,
        rng=rng,
        allow_counter=allow_counter,
    )

    # 7) Optional payload slimming
    if not include_breakdown:
        side2, evaluation2 = _strip_breakdown(side, evaluation)
        # Keep decision as-is (it is already small).
        return decision, evaluation2

    return decision, evaluation
