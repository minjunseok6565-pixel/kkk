from __future__ import annotations

from collections import defaultdict

import json
import logging
from datetime import date
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple

from league_repo import LeagueRepo
from ratings_2k import compute_ovr_proxy

from .defaults import default_player_plan, default_team_plan
from .growth_engine import apply_growth_tick
from .growth_profile import ensure_profile
from .repo import (
    get_growth_profile,
    get_player_training_plan,
    get_team_training_plan,
    list_growth_profiles,
    list_player_training_plans_for_season,
    list_team_training_plans_for_season,
    upsert_growth_profile,
    upsert_player_training_plan,
    upsert_team_training_plan,
)
from .types import normalize_player_plan, normalize_team_plan


logger = logging.getLogger(__name__)


def _json_loads(value: Any) -> Dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    try:
        return json.loads(value)
    except Exception:
        return {}


def _json_dumps(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str)


def get_or_default_team_plan(
    *,
    repo: LeagueRepo,
    team_id: str,
    season_year: int,
) -> Tuple[Dict[str, Any], bool]:
    """Return (plan, is_default)."""
    with repo.transaction() as cur:
        raw = get_team_training_plan(cur, team_id=team_id, season_year=int(season_year))
    if raw is None:
        return (default_team_plan(team_id=team_id, season_year=int(season_year)), True)
    return (normalize_team_plan(raw), False)


def get_or_default_player_plan(
    *,
    repo: LeagueRepo,
    player_id: str,
    season_year: int,
    attrs: Mapping[str, Any],
) -> Tuple[Dict[str, Any], bool]:
    """Return (plan, is_default).

    If a row exists but is_user_set==0, we still treat it as default.
    """
    with repo.transaction() as cur:
        raw, is_user_set = get_player_training_plan(cur, player_id=str(player_id), season_year=int(season_year))
    if raw is None:
        return (default_player_plan(player_id=str(player_id), season_year=int(season_year), attrs=attrs), True)
    plan = normalize_player_plan(raw)
    return (plan, not bool(is_user_set))


def set_team_plan(
    *,
    db_path: str,
    team_id: str,
    season_year: int,
    plan: Mapping[str, Any],
    now_iso: str,
) -> Dict[str, Any]:
    with LeagueRepo(db_path) as repo:
        repo.init_db()
        with repo.transaction() as cur:
            upsert_team_training_plan(
                cur,
                team_id=str(team_id).upper(),
                season_year=int(season_year),
                plan=normalize_team_plan(plan),
                now=str(now_iso),
            )
    return {"ok": True, "team_id": str(team_id).upper(), "season_year": int(season_year)}


def set_player_plan(
    *,
    db_path: str,
    player_id: str,
    season_year: int,
    plan: Mapping[str, Any],
    now_iso: str,
    is_user_set: bool = True,
) -> Dict[str, Any]:
    with LeagueRepo(db_path) as repo:
        repo.init_db()
        with repo.transaction() as cur:
            upsert_player_training_plan(
                cur,
                player_id=str(player_id),
                season_year=int(season_year),
                plan=normalize_player_plan(plan),
                now=str(now_iso),
                is_user_set=bool(is_user_set),
            )
    return {"ok": True, "player_id": str(player_id), "season_year": int(season_year)}


def _load_minutes_from_workflow_state(workflow_state: Mapping[str, Any]) -> Dict[str, float]:
    """Extract season-total minutes from state.player_stats."""
    out: Dict[str, float] = {}
    ps = workflow_state.get("player_stats") or {}
    if not isinstance(ps, Mapping):
        return out
    for pid, entry in ps.items():
        if not isinstance(entry, Mapping):
            continue
        totals = entry.get("totals") or {}
        if not isinstance(totals, Mapping):
            continue
        try:
            mins = float(totals.get("MIN") or 0.0)
        except Exception:
            mins = 0.0
        out[str(pid)] = float(max(0.0, mins))
    return out


# ---------------------------------------------------------------------------
# Injury-aware growth suppression
# ---------------------------------------------------------------------------

def _month_period_bounds(month_key: str) -> Tuple[date, date]:
    """Return (start_inclusive, end_exclusive) for a YYYY-MM month key."""
    mk = str(month_key)
    # Normalize: accept 'YYYY-MM' only (safe default to Jan if malformed).
    try:
        y = int(mk.split("-")[0])
        m = int(mk.split("-")[1])
        if m < 1 or m > 12:
            raise ValueError("bad month")
    except Exception:
        y, m = 2000, 1
    start = date(y, m, 1)
    if m == 12:
        end = date(y + 1, 1, 1)
    else:
        end = date(y, m + 1, 1)
    return (start, end)


def _merge_and_count_days(intervals: list[Tuple[date, date]]) -> int:
    """Merge [start,end) intervals and return union length in days."""
    if not intervals:
        return 0
    ivs = sorted(intervals, key=lambda x: (x[0], x[1]))
    total = 0
    cur_s, cur_e = ivs[0]
    for s, e in ivs[1:]:
        if s <= cur_e:
            if e > cur_e:
                cur_e = e
            continue
        total += max(0, (cur_e - cur_s).days)
        cur_s, cur_e = s, e
    total += max(0, (cur_e - cur_s).days)
    return int(total)


def _compute_growth_mult_by_pid(
    *,
    repo: LeagueRepo,
    tick_kind: str,
    tick_id: str,
    player_ids: list[str],
    now_iso: str,
) -> Dict[str, float]:
    """Compute growth_mult (0..1) per player based on injury tables.

    Rules (v1):
      - monthly: growth is suppressed proportionally to OUT days overlapping the month.
      - offseason: if a player is currently OUT on now_iso, suppress growth (mult=0).
    """
    kind = str(tick_kind).lower().strip()
    if not player_ids:
        return {}

    # Default to full growth.
    out: Dict[str, float] = {str(pid): 1.0 for pid in player_ids}

    try:
        if kind == "monthly":
            start, end = _month_period_bounds(str(tick_id))
            days_total = max(1, (end - start).days)

            # Bulk query overlapping injury events.
            placeholders = ",".join(["?"] * len(player_ids))
            rows = repo._conn.execute(
                f"""
                SELECT player_id, date, out_until_date
                FROM injury_events
                WHERE date < ? AND out_until_date > ?
                  AND player_id IN ({placeholders})
                """,
                [end.isoformat(), start.isoformat(), *player_ids],
            ).fetchall()

            intervals_by_pid: Dict[str, list[Tuple[date, date]]] = defaultdict(list)
            for pid, s_iso, e_iso in rows:
                pid_s = str(pid)
                try:
                    s = date.fromisoformat(str(s_iso)[:10])
                    e = date.fromisoformat(str(e_iso)[:10])
                except Exception:
                    continue
                # Clamp to period bounds.
                if s < start:
                    s = start
                if e > end:
                    e = end
                if e <= s:
                    continue
                intervals_by_pid[pid_s].append((s, e))

            for pid_s, intervals in intervals_by_pid.items():
                miss_days = _merge_and_count_days(intervals)
                miss_days = max(0, min(days_total, miss_days))
                out[pid_s] = max(0.0, 1.0 - (float(miss_days) / float(days_total)))

            return out

        if kind == "offseason":
            today_iso = str(now_iso)[:10]
            placeholders = ",".join(["?"] * len(player_ids))
            rows = repo._conn.execute(
                f"""
                SELECT player_id, out_until_date
                FROM player_injury_state
                WHERE player_id IN ({placeholders})
                """,
                [*player_ids],
            ).fetchall()

            for pid, out_until in rows:
                pid_s = str(pid)
                if not out_until:
                    continue
                try:
                    out_until_iso = str(out_until)[:10]
                except Exception:
                    continue
                # out_until_date is treated as exclusive (first playable date).
                if out_until_iso > today_iso:
                    out[pid_s] = 0.0
            return out

    except Exception:
        # Commercial safety: do not break growth if injury tables are missing or malformed.
        logger.debug("injury growth_mult computation failed; defaulting to 1.0", exc_info=True)
        return out

    return out


def _apply_leaguewide_growth_tick(
    *,
    repo: LeagueRepo,
    tick_kind: str,
    tick_id: str,
    training_season_year: int,
    minutes_by_player: Mapping[str, float],
    now_iso: str,
    bump_age: bool = False,
    only_player_ids: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """Core implementation: apply one growth tick to many players."""
    repo.init_db()

    # Bulk-load plan caches for the season to avoid per-player queries.
    with repo.transaction() as cur:
        team_plan_cache = list_team_training_plans_for_season(cur, season_year=int(training_season_year))
        player_plan_cache = list_player_training_plans_for_season(cur, season_year=int(training_season_year))
        profile_cache = list_growth_profiles(cur)

    # Players + roster lookup.
    pid_filter_sql = ""
    pid_filter_args: list[Any] = []
    if only_player_ids:
        ids = [str(x) for x in only_player_ids if str(x)]
        if ids:
            placeholders = ",".join(["?"] * len(ids))
            pid_filter_sql = f" AND p.player_id IN ({placeholders})"
            pid_filter_args = ids

    rows = repo._conn.execute(
        """
        SELECT p.player_id, p.pos, p.age, p.height_in, p.weight_lb, p.ovr, p.attrs_json, r.team_id
        FROM players p
        JOIN roster r ON r.player_id = p.player_id
        WHERE r.status='active'
        """ + pid_filter_sql + "\n        ORDER BY p.player_id ASC;",
        pid_filter_args,
    ).fetchall()

    # Injury-aware growth suppression (0..1). Defaults to 1.0 if injury tables are absent.
    player_ids_all = [str(x[0]) for x in rows]
    growth_mult_by_pid = _compute_growth_mult_by_pid(
        repo=repo,
        tick_kind=str(tick_kind),
        tick_id=str(tick_id),
        player_ids=player_ids_all,
        now_iso=str(now_iso),
    )

    updated_player_ids: list[str] = []
    tick_results: list[dict] = []
    created_profiles: int = 0

    with repo.transaction() as cur:
        for r in rows:
            pid = str(r[0])
            pos = str(r[1] or "SF")
            age = int(r[2] or 0)
            height_in = int(r[3] or 78)
            # weight_lb = int(r[4] or 220)  # reserved for later
            team_id = str(r[7] or "FA").upper()
            attrs = _json_loads(r[6])

            # Age bump (offseason).
            if bump_age:
                age = int(age) + 1

            # Plans (default if missing).
            team_plan_raw = team_plan_cache.get(team_id)
            if team_plan_raw is None:
                team_plan = default_team_plan(team_id=team_id, season_year=int(training_season_year))
            else:
                team_plan = normalize_team_plan(team_plan_raw)

            player_plan_raw = player_plan_cache.get(pid)
            if player_plan_raw is None:
                player_plan = default_player_plan(player_id=pid, season_year=int(training_season_year), attrs=attrs)
                is_user_set = False
            else:
                player_plan = normalize_player_plan(player_plan_raw)
                # If a plan exists in cache, we consider it user-set unless explicitly stored as default.
                # (We don't have is_user_set in the bulk cache here; treat presence as user-set.)
                is_user_set = True

            # Growth profile.
            prof_existing = profile_cache.get(pid)
            prof = ensure_profile(existing=prof_existing, player_id=pid, attrs=attrs, pos=pos, age=age)
            if prof_existing is None:
                created_profiles += 1
            # Persist profile (upsert is cheap).
            upsert_growth_profile(cur, profile=prof, now=str(now_iso))

            minutes = float(minutes_by_player.get(pid, 0.0) or 0.0)

            # Apply tick.
            out = apply_growth_tick(
                player_id=pid,
                attrs=attrs,
                pos=pos,
                age=int(age),
                height_in=int(height_in),
                minutes=float(minutes),
                growth_mult=float(growth_mult_by_pid.get(pid, 1.0) or 1.0),
                profile=prof,
                team_plan=team_plan,
                player_plan=player_plan,
                tick_id=str(tick_id),
                tick_kind=str(tick_kind),
            )
            tick_results.append(out)

            # Compute OVR from proxy (simple).
            try:
                new_ovr = int(round(float(compute_ovr_proxy(attrs, pos=pos))))
            except Exception:
                new_ovr = int(r[5] or 0)
            new_ovr = int(max(0, min(99, new_ovr)))

            # Write player row.
            cur.execute(
                """
                UPDATE players
                SET age=?, ovr=?, attrs_json=?, updated_at=?
                WHERE player_id=?;
                """,
                (int(age), int(new_ovr), _json_dumps(attrs), str(now_iso), pid),
            )
            updated_player_ids.append(pid)

            # Optionally persist auto-default player plan for transparency/debug.
            # We do NOT write default plans by default (keeps DB clean). If you want
            # UI to show defaults explicitly, you can flip this later.
            _ = is_user_set

    # Summaries.
    delta_list = [float(x.get("delta_proxy") or 0.0) for x in tick_results]
    avg_delta = sum(delta_list) / len(delta_list) if delta_list else 0.0
    max_gain = max(delta_list) if delta_list else 0.0
    max_drop = min(delta_list) if delta_list else 0.0

    # Keep response payload small: return only a preview of biggest movers.
    def _sort_key(x: dict) -> float:
        try:
            return float(x.get("delta_proxy") or 0.0)
        except Exception:
            return 0.0

    top_gainers = sorted(tick_results, key=_sort_key, reverse=True)[:12]
    top_decliners = sorted(tick_results, key=_sort_key)[:12]

    return {
        "ok": True,
        "tick_kind": str(tick_kind),
        "tick_id": str(tick_id),
        "training_season_year": int(training_season_year),
        "players_updated": len(updated_player_ids),
        "created_profiles": int(created_profiles),
        "avg_delta_proxy": float(avg_delta),
        "max_gain_proxy": float(max_gain),
        "max_drop_proxy": float(max_drop),
        "updated_player_ids": updated_player_ids,
        "top_gainers": top_gainers,
        "top_decliners": top_decliners,
    }


def apply_offseason_growth(
    *,
    db_path: str,
    from_season_year: int,
    to_season_year: int,
    in_game_date_iso: str,
    workflow_state: Mapping[str, Any],
) -> Dict[str, Any]:
    """Apply offseason growth once per season transition.

    - Ages existing NBA players (+1)
    - Applies a larger "offseason" growth tick using training plans for `to_season_year`
    - Uses last season total minutes from `workflow_state.player_stats`
    - Idempotent via meta key `nba_offseason_growth_done_{to_season_year}`
    """
    fy = int(from_season_year)
    ty = int(to_season_year)
    if fy <= 0 or ty <= 0 or ty != fy + 1:
        raise ValueError("invalid season year transition for offseason growth")

    now_iso = str(in_game_date_iso)

    minutes_by_player = _load_minutes_from_workflow_state(workflow_state)

    with LeagueRepo(db_path) as repo:
        repo.init_db()

        meta_key = f"nba_offseason_growth_done_{ty}"
        row = repo._conn.execute("SELECT value FROM meta WHERE key=?;", (meta_key,)).fetchone()
        if row and str(row[0]) == "1":
            return {
                "ok": True,
                "skipped": True,
                "reason": "already_done",
                "meta_key": meta_key,
                "from_season_year": fy,
                "to_season_year": ty,
            }

        result = _apply_leaguewide_growth_tick(
            repo=repo,
            tick_kind="offseason",
            tick_id=f"offseason_{ty}",
            training_season_year=ty,
            minutes_by_player=minutes_by_player,
            now_iso=now_iso,
            bump_age=True,
        )

        # Mark idempotency.
        with repo.transaction() as cur:
            cur.execute(
                "INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value;",
                (meta_key, "1"),
            )

        result.update({"from_season_year": fy, "to_season_year": ty, "meta_key": meta_key})
        return result


def apply_monthly_growth(
    *,
    db_path: str,
    season_year: int,
    month_key: str,
    minutes_by_player: Mapping[str, float],
    now_iso: str,
) -> Dict[str, Any]:
    """Apply an in-season monthly growth tick for a given month_key (YYYY-MM).

    Idempotent via meta key `nba_growth_tick_done_{month_key}`.
    """
    sy = int(season_year)
    mk = str(month_key)
    meta_key = f"nba_growth_tick_done_{mk}"

    with LeagueRepo(db_path) as repo:
        repo.init_db()

        row = repo._conn.execute("SELECT value FROM meta WHERE key=?;", (meta_key,)).fetchone()
        if row and str(row[0]) == "1":
            return {"ok": True, "skipped": True, "reason": "already_done", "month": mk, "meta_key": meta_key}

        result = _apply_leaguewide_growth_tick(
            repo=repo,
            tick_kind="monthly",
            tick_id=mk,
            training_season_year=sy,
            minutes_by_player=minutes_by_player,
            now_iso=str(now_iso),
            bump_age=False,
        )

        with repo.transaction() as cur:
            cur.execute(
                "INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value;",
                (meta_key, "1"),
            )

        result.update({"month": mk, "meta_key": meta_key})
        return result
