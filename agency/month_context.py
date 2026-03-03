from __future__ import annotations

"""Month attribution helpers for the player agency system.

Why this exists
---------------
A player can be traded mid-month. If we attribute the entire month's minutes to
the *current* roster team, player dissatisfaction becomes implausible:
- The player complains about Team B even though the minutes were earned on Team A.
- Or the opposite: Team A gets blamed even after a late-month role change on Team B.

This module computes, from the in-memory state snapshot, a per-player split of:
- minutes, games played, games present (incl. DNP) by team
- first/last appearance dates by team
- a robust "primary team" for that month (policy-driven)

Design principles
-----------------
- Pure functions: no DB I/O, no imports from global `state`.
- Deterministic: tie-breaking is stable (team_id / player_id).
- Defensive: missing or partial snapshots degrade gracefully.
- Explainable: outputs include explicit reason codes for primary team selection.

Important notes
---------------
- We count "games_present" when a player row exists in the boxscore for a team.
  If your match engine does not include DNP players in boxscores, games_present
  will equal games_played and the logic still works.
- We count "games_played" when MIN > 0.
  Agency's existing tick currently uses games_played as the denominator for MPG.
  The service layer can choose which to use (played vs present) based on design.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple

from .utils import clamp01, norm_date_iso, norm_month_key, safe_float, safe_int


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MonthContextConfig:
    """Policy parameters for month attribution.

    min_games_last_team
        If the player appears with their *last* team in at least this many games
        in the month, treat that last team as the primary team.

        This prevents over-weighting a 1-game cameo after a late-month trade.
        But if the player really spent meaningful time with the new team, we
        want dissatisfaction to reflect the new context.

    full_weight_games
        Games count at which sample_weight reaches 1.0. Below this, the service
        layer should scale event severity / trigger probability for small samples.

    min_games_for_primary
        Minimum total appearances required to choose any primary team.
        If total appearances are below this, primary_team may be None.
    """

    min_games_last_team: int = 3
    full_weight_games: int = 10
    min_games_for_primary: int = 1


DEFAULT_MONTH_CONTEXT_CONFIG = MonthContextConfig()


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class TeamSlice:
    """Per-team slice of a player's participation in a month.

    In addition to minutes/games, we store simple role evidence:
    - games_started: appeared in Q1 PERIOD_START on-court five
    - games_closed: appeared in last PERIOD_END on-court five
    - usage_est: FGA + 0.44*FTA + TOV (approx; derived from boxscore row)
    """

    team_id: str

    minutes: float = 0.0
    games_present: int = 0  # row exists in boxscore (may include MIN=0)
    games_played: int = 0  # MIN > 0

    games_started: int = 0
    games_closed: int = 0
    usage_est: float = 0.0

    first_date: Optional[str] = None  # YYYY-MM-DD
    last_date: Optional[str] = None  # YYYY-MM-DD

    def add_game(
        self,
        *,
        game_date_iso: str,
        minutes: float,
        started: bool = False,
        closed: bool = False,
        usage_est: float = 0.0,
    ) -> None:
        d = norm_date_iso(game_date_iso)
        if not d:
            return
        m = max(0.0, float(minutes))

        self.games_present = int(self.games_present + 1)
        if m > 0.0:
            self.games_played = int(self.games_played + 1)
            self.minutes = float(self.minutes + m)

        if started:
            self.games_started = int(self.games_started + 1)
        if closed:
            self.games_closed = int(self.games_closed + 1)

        # usage_est is derived; safe to accumulate even if m==0.
        self.usage_est = float(self.usage_est + max(0.0, float(usage_est)))

        if self.first_date is None or str(d) < str(self.first_date):
            self.first_date = d
        if self.last_date is None or str(d) > str(self.last_date):
            self.last_date = d

    def to_summary(self) -> Dict[str, Any]:
        return {
            "minutes": float(self.minutes),
            "games_present": int(self.games_present),
            "games_played": int(self.games_played),
            "games_started": int(self.games_started),
            "games_closed": int(self.games_closed),
            "usage_est": float(self.usage_est),
            "first_date": self.first_date,
            "last_date": self.last_date,
        }


@dataclass(frozen=True, slots=True)
class PlayerMonthSplit:
    """Per-player, per-month team split."""

    player_id: str
    month_key: str

    teams: Dict[str, TeamSlice] = field(default_factory=dict)

    team_last: Optional[str] = None
    team_dominant: Optional[str] = None

    primary_team: Optional[str] = None
    primary_reason: Optional[str] = None

    # Sample size on primary team (useful for scaling effects)
    sample_games_present: int = 0
    sample_games_played: int = 0
    sample_minutes: float = 0.0
    sample_weight: float = 0.0

    # Totals across all teams in the month
    total_games_present: int = 0
    total_games_played: int = 0
    total_minutes: float = 0.0

    def multi_team(self) -> bool:
        return len(self.teams) >= 2

    def to_summary(self, *, max_teams: int = 4) -> Dict[str, Any]:
        teams_items = sorted(self.teams.items(), key=lambda kv: str(kv[0]))
        if max_teams > 0:
            teams_items = teams_items[: int(max_teams)]

        return {
            "player_id": self.player_id,
            "month_key": self.month_key,
            "team_last": self.team_last,
            "team_dominant": self.team_dominant,
            "primary_team": self.primary_team,
            "primary_reason": self.primary_reason,
            "sample_games_present": int(self.sample_games_present),
            "sample_games_played": int(self.sample_games_played),
            "sample_minutes": float(self.sample_minutes),
            "sample_weight": float(self.sample_weight),
            "total_games_present": int(self.total_games_present),
            "total_games_played": int(self.total_games_played),
            "total_minutes": float(self.total_minutes),
            "teams": {tid: sl.to_summary() for tid, sl in teams_items},
        }


# ---------------------------------------------------------------------------
# Core collection
# ---------------------------------------------------------------------------


def _iter_month_final_games(
    state_snapshot: Mapping[str, Any],
    *,
    month_key: str,
    phase: str = "regular",
) -> Iterable[Tuple[str, str]]:
    """Yield (game_id, date_iso) for final games in the specified month/phase."""
    games = state_snapshot.get("games") or []
    if not isinstance(games, list):
        return []

    mk = norm_month_key(month_key)
    if not mk:
        return []

    out: list[Tuple[str, str]] = []
    phase_norm = str(phase or "regular")
    for g in games:
        if not isinstance(g, Mapping):
            continue
        if str(g.get("phase") or "regular") != phase_norm:
            continue
        if str(g.get("status") or "") != "final":
            continue
        d = str(g.get("date") or "")
        if not d.startswith(mk):
            continue
        gid = g.get("game_id")
        if not gid:
            continue
        date_iso = str(d)[:10]
        if not norm_date_iso(date_iso):
            continue
        out.append((str(gid), date_iso))

    # Stable: sort by date then game_id
    out.sort(key=lambda x: (x[1], x[0]))
    return out


def _extract_starters_closers_from_replay(
    game_result: Mapping[str, Any],
) -> Tuple[Dict[str, set[str]], Dict[str, set[str]]]:
    """Return (starters_by_team_id, closers_by_team_id) for one game_result.

    Source of truth: matchengine_v3 replay_events, PERIOD_START/PERIOD_END with include_lineups=True.

    - Starters: Q1 PERIOD_START on_court_by_team_id
    - Closers: last PERIOD_END on_court_by_team_id (regulation or OT)

    Best-effort: if replay_events are missing or don't include lineup snapshots,
    returns empty dicts and the rest of month attribution still works.
    """
    starters: Dict[str, set[str]] = {}
    closers: Dict[str, set[str]] = {}

    re = game_result.get("replay_events") if isinstance(game_result, Mapping) else None
    if not isinstance(re, list) or not re:
        return starters, closers

    # Starters: first PERIOD_START in Q1
    for evt in re:
        if not isinstance(evt, Mapping):
            continue
        if str(evt.get("event_type") or "") != "PERIOD_START":
            continue
        q = safe_int(evt.get("quarter"), 0)
        if q != 1:
            continue
        oc = evt.get("on_court_by_team_id")
        if isinstance(oc, Mapping):
            for tid, pids in oc.items():
                tid_u = str(tid or "").upper()
                if not tid_u or not isinstance(pids, list):
                    continue
                starters[tid_u] = {str(pid) for pid in pids if str(pid)}
        break

    # Closers: last PERIOD_END (includes OT)
    for evt in reversed(re):
        if not isinstance(evt, Mapping):
            continue
        if str(evt.get("event_type") or "") != "PERIOD_END":
            continue
        oc = evt.get("on_court_by_team_id")
        if isinstance(oc, Mapping):
            for tid, pids in oc.items():
                tid_u = str(tid or "").upper()
                if not tid_u or not isinstance(pids, list):
                    continue
                closers[tid_u] = {str(pid) for pid in pids if str(pid)}
        break

    return starters, closers


def collect_month_splits(
    state_snapshot: Mapping[str, Any],
    *,
    month_key: str,
    cfg: MonthContextConfig = DEFAULT_MONTH_CONTEXT_CONFIG,
    phase: str = "regular",
) -> Dict[str, PlayerMonthSplit]:
    """Collect per-player team splits for a finished month.

    Args:
        state_snapshot: output of state.export_full_state_snapshot()
        month_key: YYYY-MM
        cfg: policy parameters
        phase: game phase to consider (default: regular)

    Returns:
        dict[player_id] -> PlayerMonthSplit
    """
    mk = norm_month_key(month_key)
    if not mk:
        return {}

    game_results = state_snapshot.get("game_results") or {}
    if not isinstance(game_results, Mapping):
        return {}

    # Build in-progress mutable structure: pid -> tid -> TeamSlice
    tmp: Dict[str, Dict[str, TeamSlice]] = {}

    for gid, date_iso in _iter_month_final_games(state_snapshot, month_key=mk, phase=phase):
        gr = game_results.get(gid)
        if not isinstance(gr, Mapping):
            continue
        teams = gr.get("teams") or {}
        if not isinstance(teams, Mapping):
            continue

        starters_by_team, closers_by_team = _extract_starters_closers_from_replay(gr)

        for tid_raw, team_obj in teams.items():
            if not isinstance(team_obj, Mapping):
                continue
            tid = str(tid_raw or "").upper()
            if not tid:
                # Attempt fallback from team_obj
                tid = str(team_obj.get("TeamID") or "").upper()
            if not tid:
                continue

            players = team_obj.get("players") or []
            if not isinstance(players, list):
                continue

            for row in players:
                if not isinstance(row, Mapping):
                    continue
                pid_raw = row.get("PlayerID")
                if not pid_raw:
                    continue
                pid = str(pid_raw)
                if not pid:
                    continue

                mins = safe_float(row.get("MIN"), 0.0)

                by_team = tmp.setdefault(pid, {})
                sl = by_team.get(tid)
                if sl is None:
                    sl = TeamSlice(team_id=tid)
                    by_team[tid] = sl
                # Approx usage: FGA + 0.44*FTA + TOV (derived from boxscore row)
                usage_est = (
                    safe_float(row.get("FGA"), 0.0)
                    + 0.44 * safe_float(row.get("FTA"), 0.0)
                    + safe_float(row.get("TOV"), 0.0)
                )

                started = bool(pid in (starters_by_team.get(tid) or set()))
                closed = bool(pid in (closers_by_team.get(tid) or set()))

                sl.add_game(
                    game_date_iso=date_iso,
                    minutes=float(mins),
                    started=started,
                    closed=closed,
                    usage_est=float(usage_est),
                )

    # Finalize PlayerMonthSplit objects.
    out: Dict[str, PlayerMonthSplit] = {}
    for pid, team_slices in tmp.items():
        out[str(pid)] = finalize_player_month_split(
            player_id=str(pid),
            month_key=mk,
            team_slices=team_slices,
            cfg=cfg,
        )

    return out

def finalize_player_month_split(
    *,
    player_id: str,
    month_key: str,
    team_slices: Mapping[str, TeamSlice],
    cfg: MonthContextConfig = DEFAULT_MONTH_CONTEXT_CONFIG,
) -> PlayerMonthSplit:
    """Finalize a PlayerMonthSplit from per-team slices.

    This helper exists so the service layer can synthesize month splits
    (e.g., DNP presence via schedule+transactions) without duplicating the
    primary-team policy and summary semantics.
    """
    pid = str(player_id or "")
    mk = norm_month_key(month_key)
    teams: Dict[str, TeamSlice] = {str(t).upper(): sl for t, sl in dict(team_slices or {}).items() if str(t)}

    # Compute totals
    total_games_present = sum(int(sl.games_present) for sl in teams.values())
    total_games_played = sum(int(sl.games_played) for sl in teams.values())
    total_minutes = sum(float(sl.minutes) for sl in teams.values())

    if total_games_present < int(cfg.min_games_for_primary):
        # Not enough evidence to assign a primary team.
        return PlayerMonthSplit(
            player_id=pid,
            month_key=mk,
            teams=dict(teams),
            team_last=None,
            team_dominant=None,
            primary_team=None,
            primary_reason="PRIMARY_NONE_INSUFFICIENT_GAMES",
            sample_games_present=0,
            sample_games_played=0,
            sample_minutes=0.0,
            sample_weight=0.0,
            total_games_present=int(total_games_present),
            total_games_played=int(total_games_played),
            total_minutes=float(total_minutes),
        )

    # Determine team_last and team_dominant with stable tie breaks.
    def _key_last(item: Tuple[str, TeamSlice]) -> Tuple[str, int, float, str]:
        tid, sl = item
        last = sl.last_date or "0000-00-00"
        return (last, int(sl.games_present), float(sl.minutes), str(tid))

    def _key_dom(item: Tuple[str, TeamSlice]) -> Tuple[int, float, str, str]:
        tid, sl = item
        last = sl.last_date or "0000-00-00"
        return (int(sl.games_present), float(sl.minutes), last, str(tid))

    team_last = max(teams.items(), key=_key_last)[0] if teams else None
    team_dom = max(teams.items(), key=_key_dom)[0] if teams else None

    # Pick primary team per policy
    primary_team, primary_reason = pick_primary_team(
        teams,
        team_last=team_last,
        team_dominant=team_dom,
        cfg=cfg,
    )

    primary_slice = teams.get(primary_team) if primary_team else None
    if primary_slice is None:
        sample_gp = 0
        sample_gpl = 0
        sample_min = 0.0
    else:
        sample_gp = int(primary_slice.games_present)
        sample_gpl = int(primary_slice.games_played)
        sample_min = float(primary_slice.minutes)

    full = max(1, int(cfg.full_weight_games))
    sample_weight = float(clamp01(sample_gp / float(full)))

    return PlayerMonthSplit(
        player_id=pid,
        month_key=mk,
        teams=dict(teams),
        team_last=team_last,
        team_dominant=team_dom,
        primary_team=primary_team,
        primary_reason=primary_reason,
        sample_games_present=sample_gp,
        sample_games_played=sample_gpl,
        sample_minutes=sample_min,
        sample_weight=sample_weight,
        total_games_present=int(total_games_present),
        total_games_played=int(total_games_played),
        total_minutes=float(total_minutes),
    )

# ---------------------------------------------------------------------------
# Primary team policy
# ---------------------------------------------------------------------------


def pick_primary_team(
    team_slices: Mapping[str, TeamSlice],
    *,
    team_last: Optional[str],
    team_dominant: Optional[str],
    cfg: MonthContextConfig = DEFAULT_MONTH_CONTEXT_CONFIG,
) -> Tuple[Optional[str], str]:
    """Pick a primary team for the month given per-team slices.

    Returns:
        (primary_team_id or None, reason_code)
    """
    if not team_slices:
        return None, "PRIMARY_NONE_NO_SLICES"

    last = str(team_last or "").upper() or None
    dom = str(team_dominant or "").upper() or None

    if last is None and dom is None:
        # Pick a deterministic fallback team id
        tid = sorted([str(t).upper() for t in team_slices.keys() if str(t)])[0] if team_slices else None
        return tid, "PRIMARY_FALLBACK_ONLY_TEAM"

    if last is None:
        return dom, "PRIMARY_DOMINANT_ONLY"
    if dom is None:
        return last, "PRIMARY_LAST_ONLY"

    if last == dom:
        return last, "PRIMARY_SAME_LAST_DOMINANT"

    # Policy: if the last-team sample is meaningful, treat it as primary.
    min_last = max(1, int(cfg.min_games_last_team))
    games_last = int(team_slices.get(last).games_present if team_slices.get(last) else 0)

    if games_last >= min_last:
        return last, "PRIMARY_LAST_TEAM_MEANINGFUL"

    return dom, "PRIMARY_DOMINANT_TEAM"


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------


def players_by_team_from_splits(
    splits: Mapping[str, PlayerMonthSplit],
    *,
    min_games_present: int = 1,
) -> Dict[str, list[str]]:
    """Build mapping team_id -> list[player_id] from splits.

    Players are included for a team if they appeared (games_present) at least
    min_games_present times for that team in the month.

    This helper is typically used to build a month roster for expectation
    computation (role/leverage) in the agency service layer.
    """
    out: Dict[str, list[str]] = {}
    th = max(1, int(min_games_present))
    for pid, sp in (splits or {}).items():
        if not isinstance(sp, PlayerMonthSplit):
            continue
        for tid, sl in (sp.teams or {}).items():
            if not tid:
                continue
            if int(sl.games_present) < th:
                continue
            tid_u = str(tid).upper()
            out.setdefault(tid_u, []).append(str(pid))
    # Deduplicate while keeping deterministic order
    for tid in list(out.keys()):
        uniq = sorted(set(out[tid]), key=lambda x: str(x))
        out[tid] = uniq
    return out


def build_split_summary(split: PlayerMonthSplit, *, max_teams: int = 4) -> Dict[str, Any]:
    """Compact, UI-friendly summary dict."""
    if not isinstance(split, PlayerMonthSplit):
        return {}
    return split.to_summary(max_teams=max_teams)
