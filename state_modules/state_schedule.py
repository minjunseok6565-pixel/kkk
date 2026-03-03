from __future__ import annotations

import math
import random
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from config import (
    ALL_TEAM_IDS,
    DIVISIONS,
    MAX_GAMES_PER_DAY,
    SEASON_LENGTH_DAYS,
    SEASON_START_DAY,
    SEASON_START_MONTH,
    TEAM_TO_CONF_DIV,
)
from .state_constants import _ALLOWED_SCHEDULE_STATUSES
from schema import season_id_from_year as _schema_season_id_from_year


def _season_id_from_year(season_year: int) -> str:
    """시즌 시작 연도(int) -> season_id 문자열. 예: 2025 -> '2025-26'"""
    return str(_schema_season_id_from_year(int(season_year)))


# - 이 모듈은 "master_schedule 생성/인덱싱/업데이트"만 담당한다.
# - 시즌 전환(archive/reset), active_season_id 설정, season_history 작성,
#   DB seed/cap 모델 적용 등 라이프사이클은 **반드시 state.py(facade)에서만** 수행한다.
#   (레거시 경로 재발 방지)


def validate_master_schedule_entry(entry: Dict[str, Any], *, path: str = "master_schedule.entry") -> None:
    """
    master_schedule.games[*]에서 실제로 "사용되는 필드만" 최소 계약으로 고정한다.

    Required:
      - game_id: str (non-empty)
      - home_team_id: str (non-empty)
      - away_team_id: str (non-empty)
      - status: str (allowed set)

    Optional (if present, must be correct type):
      - date: str (ISO-like recommended)
      - season_id: str
      - phase: str
      - home_score/away_score: int|None
      - home_tactics/away_tactics/tactics: dict|None  (프로젝트별로 사용하는 키가 달라도 안전하게 수용)
    """
    if not isinstance(entry, dict):
        raise ValueError(f"MasterScheduleEntry invalid: '{path}' must be a dict")

    for k in ("game_id", "home_team_id", "away_team_id", "status"):
        if k not in entry:
            raise ValueError(f"MasterScheduleEntry invalid: missing {path}.{k}")

    game_id = entry.get("game_id")
    if not isinstance(game_id, str) or not game_id.strip():
        raise ValueError(f"MasterScheduleEntry invalid: {path}.game_id must be a non-empty string")

    for k in ("home_team_id", "away_team_id"):
        v = entry.get(k)
        if not isinstance(v, str) or not v.strip():
            raise ValueError(f"MasterScheduleEntry invalid: {path}.{k} must be a non-empty string")

    status = entry.get("status")
    if not isinstance(status, str) or status not in _ALLOWED_SCHEDULE_STATUSES:
        raise ValueError(
            f"MasterScheduleEntry invalid: {path}.status must be one of {sorted(_ALLOWED_SCHEDULE_STATUSES)}"
        )

    # Optional: tactics payload(s)
    for tk in ("tactics", "home_tactics", "away_tactics"):
        if tk in entry and entry[tk] is not None and not isinstance(entry[tk], dict):
            raise ValueError(f"MasterScheduleEntry invalid: {path}.{tk} must be a dict if present")

    # Optional: date (string)
    if "date" in entry and entry["date"] is not None and not isinstance(entry["date"], str):
        raise ValueError(f"MasterScheduleEntry invalid: {path}.date must be a string if present")

    # Optional: scores
    for sk in ("home_score", "away_score"):
        if sk in entry and entry[sk] is not None and not isinstance(entry[sk], int):
            raise ValueError(f"MasterScheduleEntry invalid: {path}.{sk} must be int or None if present")


def ensure_master_schedule_indices(master_schedule: dict) -> None:
    """master_schedule의 최소 계약 검증 + by_id 인덱스를 보장한다."""
    if not isinstance(master_schedule, dict):
        raise ValueError("master_schedule must be a dict")
        
    games = master_schedule.get("games") or []
    if not isinstance(games, list):
        raise ValueError("master_schedule.games must be a list")
        
    # Contract check: master_schedule entries must satisfy the minimal schema.
    for i, g in enumerate(games):
        validate_master_schedule_entry(g, path=f"master_schedule.games[{i}]")
    by_id = master_schedule.get("by_id")
    if not isinstance(by_id, dict) or len(by_id) != len(games):
        master_schedule["by_id"] = {g.get("game_id"): g for g in games if isinstance(g, dict) and g.get("game_id")}



@dataclass(frozen=True)
class _GameStub:
    """A date-less regular-season game stub.

    The home/away direction is *already fixed* at template-build time so that
    each team ends up with exactly 41 home and 41 away games.
    """

    home_team_id: str
    away_team_id: str
    opp_key: tuple[str, str]


# ---------------------------------------------------------------------------
# NBA-like schedule parameters (tunable)
# ---------------------------------------------------------------------------

# Hard caps (enforced)
_B2B_MAX = 16  # back-to-backs per team (hard cap)

# Soft targets (optimized)
_B2B_TARGET = 14
_MIN_REMATCH_GAP_DAYS = 7
_SOFT_STREAK_LIMIT = 6  # home/away streak length starts to be penalized above this

# Search knobs
_DAY_BUILD_ATTEMPTS = 6
_FULL_SCHEDULE_ATTEMPTS = 30
_REPAIR_ITERS = 1200


def _third_sunday_of_feb(year: int) -> date:
    """Return the 3rd Sunday of February for the given year."""
    d = date(int(year), 2, 1)
    # weekday(): Monday=0 .. Sunday=6
    while d.weekday() != 6:
        d += timedelta(days=1)
    return d + timedelta(days=14)


def _compute_blackout_day_indices(*, season_year: int, season_start: date) -> set[int]:
    """Compute blackout day indices (0-based from season_start).

    We model a simplified NBA-like All-Star break window and one holiday blackout.
    """
    blocked: set[int] = set()

    # All-Star break (next calendar year, 3rd Sunday of Feb).
    all_star_year = int(season_year) + 1
    all_star_sun = _third_sunday_of_feb(all_star_year)

    # Simplified: Thu -> Tue around the All-Star Sunday (6 days)
    start = all_star_sun - timedelta(days=3)
    end = all_star_sun + timedelta(days=2)
    cur = start
    while cur <= end:
        idx = (cur - season_start).days
        if 0 <= idx < SEASON_LENGTH_DAYS:
            blocked.add(int(idx))
        cur += timedelta(days=1)

    # Christmas Eve: no games (optional realism sugar)
    try:
        xmas_eve = date(int(season_year), 12, 24)
        idx = (xmas_eve - season_start).days
        if 0 <= idx < SEASON_LENGTH_DAYS:
            blocked.add(int(idx))
    except Exception:
        pass

    return blocked


_WEEKDAY_WEIGHTS: Dict[int, float] = {
    0: 0.95,  # Mon
    1: 1.00,  # Tue
    2: 1.05,  # Wed
    3: 1.00,  # Thu
    4: 1.10,  # Fri
    5: 1.15,  # Sat
    6: 1.05,  # Sun
}


def _weekday_weight(d: date) -> float:
    # Small holiday bumps (NBA-style showcase days)
    if int(d.month) == 12 and int(d.day) == 25:
        return 1.20
    return float(_WEEKDAY_WEIGHTS.get(int(d.weekday()), 1.0))


def _build_nba_matchup_stubs(*, season_year: int) -> List[_GameStub]:
    """Build an NBA-like 82-game template with exact 41H/41A per team.

    Structure per team:
      - Same division (4 opponents): 4 games each => 16
      - Same conference other divisions (10 opponents): 6 opponents x4 games + 4 opponents x3 games => 36
      - Other conference (15 opponents): 2 games each => 30
      Total = 82

    The 3-game opponents rotate deterministically by season_year and division-pair index.
    """
    teams = list(ALL_TEAM_IDS)
    stubs: List[_GameStub] = []

    # Conference team lists.
    east = [t for t in teams if (TEAM_TO_CONF_DIV.get(t) or {}).get('conference') == 'East']
    west = [t for t in teams if (TEAM_TO_CONF_DIV.get(t) or {}).get('conference') == 'West']

    # 1) Inter-conference: 2 games (1 home each)
    for e in east:
        for w in west:
            key = tuple(sorted((e, w)))
            stubs.append(_GameStub(home_team_id=e, away_team_id=w, opp_key=key))
            stubs.append(_GameStub(home_team_id=w, away_team_id=e, opp_key=key))

    # 2) Intra-division: 4 games (2 home each)
    for conf_name, divs in DIVISIONS.items():
        for _div_name, div_teams in (divs or {}).items():
            div_list = list(div_teams or [])
            if len(div_list) < 2:
                continue
            for i in range(len(div_list)):
                for j in range(i + 1, len(div_list)):
                    t1 = str(div_list[i])
                    t2 = str(div_list[j])
                    key = tuple(sorted((t1, t2)))
                    stubs.append(_GameStub(home_team_id=t1, away_team_id=t2, opp_key=key))
                    stubs.append(_GameStub(home_team_id=t1, away_team_id=t2, opp_key=key))
                    stubs.append(_GameStub(home_team_id=t2, away_team_id=t1, opp_key=key))
                    stubs.append(_GameStub(home_team_id=t2, away_team_id=t1, opp_key=key))

    # 3) Cross-division within conference: 3 or 4 games.
    # We construct, for each division pair (A,B), a 2-regular bipartite graph of "3-game" pairings.
    # Mapping:
    #   A[i] pairs (3 games) with B[(i+rot)%5]  and  B[(i+rot+1)%5]
    # This ensures every team has exactly 2 "3-game" opponents in the other division.
    # For home/away balance:
    #   - Edge type0 (to B[(i+rot)%5]) gives advantage to A-side team (2 home / 1 away)
    #   - Edge type1 (to B[(i+rot+1)%5]) gives advantage to B-side team
    # A seasonal flip toggles which edge type gives advantage, rotating the 2H/1H direction.
    for conf_name, divs in DIVISIONS.items():
        div_items = list((divs or {}).items())
        if len(div_items) < 2:
            continue

        pair_idx = 0
        for i in range(len(div_items)):
            for j in range(i + 1, len(div_items)):
                _a_name, a_teams = div_items[i]
                _b_name, b_teams = div_items[j]
                A = list(a_teams or [])
                B = list(b_teams or [])
                if len(A) != 5 or len(B) != 5:
                    # Defensive: our NBA config expects 5-team divisions.
                    continue

                rot = (int(season_year) + int(pair_idx)) % 5
                flip = ((int(season_year) + int(pair_idx)) % 2) == 1

                three_adv: Dict[tuple[str, str], str] = {}
                for k in range(5):
                    a = str(A[k])
                    b0 = str(B[(k + rot) % 5])
                    b1 = str(B[(k + rot + 1) % 5])
                    key0 = tuple(sorted((a, b0)))
                    key1 = tuple(sorted((a, b1)))

                    if not flip:
                        three_adv[key0] = a
                        three_adv[key1] = b1
                    else:
                        three_adv[key0] = b0
                        three_adv[key1] = a

                for a in A:
                    for b in B:
                        a = str(a)
                        b = str(b)
                        key = tuple(sorted((a, b)))
                        adv = three_adv.get(key)
                        if adv is None:
                            # 4 games (2H/2A)
                            stubs.append(_GameStub(home_team_id=a, away_team_id=b, opp_key=key))
                            stubs.append(_GameStub(home_team_id=a, away_team_id=b, opp_key=key))
                            stubs.append(_GameStub(home_team_id=b, away_team_id=a, opp_key=key))
                            stubs.append(_GameStub(home_team_id=b, away_team_id=a, opp_key=key))
                        else:
                            # 3 games (2H/1A for adv team)
                            if adv == a:
                                stubs.append(_GameStub(home_team_id=a, away_team_id=b, opp_key=key))
                                stubs.append(_GameStub(home_team_id=a, away_team_id=b, opp_key=key))
                                stubs.append(_GameStub(home_team_id=b, away_team_id=a, opp_key=key))
                            else:
                                stubs.append(_GameStub(home_team_id=b, away_team_id=a, opp_key=key))
                                stubs.append(_GameStub(home_team_id=b, away_team_id=a, opp_key=key))
                                stubs.append(_GameStub(home_team_id=a, away_team_id=b, opp_key=key))

                pair_idx += 1

    # Validate: totals must match NBA regular season.
    if len(stubs) != 1230:
        raise RuntimeError(f"NBA template build failed: expected 1230 games, got {len(stubs)}")

    home_cnt: Dict[str, int] = {t: 0 for t in teams}
    away_cnt: Dict[str, int] = {t: 0 for t in teams}
    for s in stubs:
        home_cnt[s.home_team_id] = int(home_cnt.get(s.home_team_id, 0) + 1)
        away_cnt[s.away_team_id] = int(away_cnt.get(s.away_team_id, 0) + 1)

    bad = []
    for t in teams:
        if int(home_cnt.get(t, 0)) != 41 or int(away_cnt.get(t, 0)) != 41:
            bad.append((t, int(home_cnt.get(t, 0)), int(away_cnt.get(t, 0))))
    if bad:
        raise RuntimeError(f"NBA template home/away not balanced: {bad[:5]} ...")

    return stubs


def _count_recent_games(days: set[int], *, start: int, end: int) -> int:
    """Count days in [start, end] (inclusive) contained in the set."""
    c = 0
    for d in range(int(start), int(end) + 1):
        if d in days:
            c += 1
    return int(c)


def _team_can_play_today(
    team_id: str,
    day: int,
    *,
    team_days: Dict[str, set[int]],
    team_b2b: Dict[str, int],
) -> bool:
    days = team_days[team_id]
    d = int(day)

    # Already playing today?
    if d in days:
        return False

    # Hard: no 3 consecutive game days.
    if (d - 1) in days and (d - 2) in days:
        return False

    # Hard: no 4 games in 5 days.
    if _count_recent_games(days, start=d - 4, end=d - 1) >= 3:
        return False

    # Hard: B2B cap.
    if (d - 1) in days and int(team_b2b.get(team_id, 0)) >= int(_B2B_MAX):
        return False

    return True


def _b2b_feasible_after_play(
    team_id: str,
    day: int,
    *,
    team_days: Dict[str, set[int]],
    team_games: Dict[str, int],
    team_b2b: Dict[str, int],
) -> bool:
    """Lightweight feasibility guard so we don't burn all B2Bs too early."""
    d = int(day)
    days = team_days[team_id]

    b2b_after = int(team_b2b.get(team_id, 0)) + (1 if (d - 1) in days else 0)
    if b2b_after > int(_B2B_MAX):
        return False

    # Remaining days in the season after today.
    remaining_days = int(SEASON_LENGTH_DAYS - 1 - d)

    # Remaining games after playing today.
    remaining_games = 82 - int(team_games.get(team_id, 0) + 1)
    if remaining_games <= 0:
        return True

    # Lower-bound on required back-to-backs if we try to keep at least one rest day between games.
    # If we need R games in D days, and without B2B we need (2R-1) days, then the shortfall
    # is a minimum B2B requirement.
    min_additional_b2b = max(0, (2 * int(remaining_games) - 1) - int(remaining_days))

    return (b2b_after + int(min_additional_b2b)) <= int(_B2B_MAX)


def _rest_days(last_day: Optional[int], day: int) -> int:
    if last_day is None:
        return 3
    return max(0, int(day) - int(last_day) - 1)


def _placement_penalty(
    stub: _GameStub,
    day: int,
    *,
    team_days: Dict[str, set[int]],
    team_last_day: Dict[str, Optional[int]],
    team_last_loc: Dict[str, Optional[str]],
    team_home_streak: Dict[str, int],
    team_away_streak: Dict[str, int],
    team_opp_last_day: Dict[str, Dict[str, int]],
) -> float:
    """Soft penalty for placing a particular game on a given day."""
    d = int(day)
    home = stub.home_team_id
    away = stub.away_team_id

    # Weights (tunable)
    W_B2B = 6.0
    W_3IN4 = 3.0
    W_REST = 0.8
    W_STREAK = 1.2
    W_REMATCH = 1.5

    p = 0.0

    # Back-to-back softness.
    if (d - 1) in team_days[home]:
        p += W_B2B
    if (d - 1) in team_days[away]:
        p += W_B2B

    # 3-in-4 softness.
    if _count_recent_games(team_days[home], start=d - 3, end=d - 1) >= 2:
        p += W_3IN4
    if _count_recent_games(team_days[away], start=d - 3, end=d - 1) >= 2:
        p += W_3IN4

    # Rest mismatch softness.
    rest_h = _rest_days(team_last_day.get(home), d)
    rest_a = _rest_days(team_last_day.get(away), d)
    p += W_REST * float(abs(int(rest_h) - int(rest_a)))

    # Home/away streak softness.
    # home team
    if team_last_loc.get(home) == 'H':
        new_home_streak = int(team_home_streak.get(home, 0) + 1)
    else:
        new_home_streak = 1
    if new_home_streak > 4:
        p += W_STREAK * float((new_home_streak - 4) ** 2)

    # away team
    if team_last_loc.get(away) == 'A':
        new_away_streak = int(team_away_streak.get(away, 0) + 1)
    else:
        new_away_streak = 1
    if new_away_streak > 4:
        p += W_STREAK * float((new_away_streak - 4) ** 2)

    # Rematch gap softness.
    last_meet_h = (team_opp_last_day.get(home) or {}).get(away)
    if last_meet_h is not None:
        gap = int(d - int(last_meet_h))
        if gap < int(_MIN_REMATCH_GAP_DAYS):
            p += W_REMATCH * float(int(_MIN_REMATCH_GAP_DAYS) - gap)

    return float(p)


def _apply_game_to_team_state(
    team_id: str,
    *,
    day: int,
    is_home: bool,
    opponent: str,
    team_days: Dict[str, set[int]],
    team_games: Dict[str, int],
    team_b2b: Dict[str, int],
    team_last_day: Dict[str, Optional[int]],
    team_last_loc: Dict[str, Optional[str]],
    team_home_streak: Dict[str, int],
    team_away_streak: Dict[str, int],
    team_opp_last_day: Dict[str, Dict[str, int]],
) -> None:
    d = int(day)
    days = team_days[team_id]

    # B2B count update
    if (d - 1) in days:
        team_b2b[team_id] = int(team_b2b.get(team_id, 0) + 1)

    days.add(d)
    team_games[team_id] = int(team_games.get(team_id, 0) + 1)

    # Streak update
    if is_home:
        if team_last_loc.get(team_id) == 'H':
            team_home_streak[team_id] = int(team_home_streak.get(team_id, 0) + 1)
        else:
            team_home_streak[team_id] = 1
        team_away_streak[team_id] = 0
        team_last_loc[team_id] = 'H'
    else:
        if team_last_loc.get(team_id) == 'A':
            team_away_streak[team_id] = int(team_away_streak.get(team_id, 0) + 1)
        else:
            team_away_streak[team_id] = 1
        team_home_streak[team_id] = 0
        team_last_loc[team_id] = 'A'

    team_last_day[team_id] = d

    # Opponent last-played day update (for rematch gap penalties)
    m = team_opp_last_day.get(team_id)
    if m is None:
        m = {}
        team_opp_last_day[team_id] = m
    m[str(opponent)] = d


def _attempt_assign_dates(
    stubs: List[_GameStub],
    *,
    teams: List[str],
    season_start: date,
    blackout_days: set[int],
    max_games_per_day: int,
    rng: random.Random,
) -> Optional[tuple[List[List[int]], List[int]]]:
    """Try to assign all games to dates using hard constraints + soft penalties.

    Returns (day_to_game_idxs, game_day) on success, otherwise None.
    """
    n_games = len(stubs)
    unscheduled = set(range(n_games))

    team_to_games: Dict[str, List[int]] = defaultdict(list)
    for idx, s in enumerate(stubs):
        team_to_games[s.home_team_id].append(int(idx))
        team_to_games[s.away_team_id].append(int(idx))

    day_to_games: List[List[int]] = [[] for _ in range(SEASON_LENGTH_DAYS)]
    game_day: List[int] = [-1 for _ in range(n_games)]

    team_days: Dict[str, set[int]] = {t: set() for t in teams}
    team_games: Dict[str, int] = {t: 0 for t in teams}
    team_b2b: Dict[str, int] = {t: 0 for t in teams}
    team_last_day: Dict[str, Optional[int]] = {t: None for t in teams}
    team_last_loc: Dict[str, Optional[str]] = {t: None for t in teams}
    team_home_streak: Dict[str, int] = {t: 0 for t in teams}
    team_away_streak: Dict[str, int] = {t: 0 for t in teams}
    team_opp_last_day: Dict[str, Dict[str, int]] = {t: {} for t in teams}

    # Precompute playable-day prefix for pace shaping.
    playable_prefix: List[int] = [0 for _ in range(SEASON_LENGTH_DAYS)]
    playable_total = 0
    for d in range(SEASON_LENGTH_DAYS):
        if d not in blackout_days:
            playable_total += 1
        playable_prefix[d] = int(playable_total)
    playable_total = max(1, int(playable_total))

    def _expected_games_by_day(d: int) -> float:
        # Expected games through day d (inclusive) if we spread evenly across playable days.
        return 82.0 * (float(playable_prefix[d]) / float(playable_total))

    for day in range(SEASON_LENGTH_DAYS):
        if day in blackout_days:
            continue
        if not unscheduled:
            break

        # Dynamic target: remaining games / remaining playable days, scaled by weekday weight.
        remaining_playable_days = playable_total - (playable_prefix[day - 1] if day > 0 else 0)
        remaining_playable_days = max(1, int(remaining_playable_days))
        avg_needed = float(len(unscheduled)) / float(remaining_playable_days)
        w = _weekday_weight(season_start + timedelta(days=int(day)))
        target = int(round(avg_needed * float(w)))
        target = max(0, min(int(max_games_per_day), int(target)))
        target = min(target, int(len(unscheduled)))

        # Build the best slate for this day via a few randomized greedy attempts.
        best_games: List[int] = []
        best_pen = math.inf

        for _ in range(int(_DAY_BUILD_ATTEMPTS)):
            teams_used: set[str] = set()
            today_games: List[int] = []
            today_pen = 0.0

            # Candidate ordering: teams that are behind pace first.
            cand = [
                t
                for t in teams
                if t not in teams_used and _team_can_play_today(t, day, team_days=team_days, team_b2b=team_b2b)
            ]
            cand.sort(
                key=lambda t: (
                    -(float(_expected_games_by_day(day)) - float(team_games.get(t, 0))),
                    rng.random(),
                )
            )

            for t in cand:
                if len(today_games) >= target:
                    break
                if t in teams_used:
                    continue
                if not _team_can_play_today(t, day, team_days=team_days, team_b2b=team_b2b):
                    continue

                # Choose best opponent/game for team t.
                best_gid = None
                best_gpen = math.inf

                for gid in team_to_games.get(t, []):
                    if gid not in unscheduled:
                        continue
                    stub = stubs[int(gid)]
                    opp = stub.away_team_id if stub.home_team_id == t else stub.home_team_id
                    if opp in teams_used:
                        continue
                    if not _team_can_play_today(opp, day, team_days=team_days, team_b2b=team_b2b):
                        continue

                    # B2B feasibility guard.
                    if not _b2b_feasible_after_play(t, day, team_days=team_days, team_games=team_games, team_b2b=team_b2b):
                        continue
                    if not _b2b_feasible_after_play(opp, day, team_days=team_days, team_games=team_games, team_b2b=team_b2b):
                        continue

                    gpen = _placement_penalty(
                        stub,
                        day,
                        team_days=team_days,
                        team_last_day=team_last_day,
                        team_last_loc=team_last_loc,
                        team_home_streak=team_home_streak,
                        team_away_streak=team_away_streak,
                        team_opp_last_day=team_opp_last_day,
                    )
                    if gpen < best_gpen:
                        best_gid = int(gid)
                        best_gpen = float(gpen)

                if best_gid is None:
                    continue

                stub = stubs[int(best_gid)]
                opp = stub.away_team_id if stub.home_team_id == t else stub.home_team_id

                today_games.append(int(best_gid))
                today_pen += float(best_gpen)
                teams_used.add(t)
                teams_used.add(opp)

            # Primary: maximize scheduled games today. Secondary: minimize penalties.
            if len(today_games) > len(best_games) or (len(today_games) == len(best_games) and today_pen < best_pen):
                best_games = list(today_games)
                best_pen = float(today_pen)

        # Commit best slate.
        for gid in best_games:
            if gid not in unscheduled:
                continue
            stub = stubs[int(gid)]
            home = stub.home_team_id
            away = stub.away_team_id

            if len(day_to_games[day]) >= int(max_games_per_day):
                # Shouldn't happen, but stay defensive.
                break
            if day in blackout_days:
                continue
            if day in team_days[home] or day in team_days[away]:
                continue
            if not _team_can_play_today(home, day, team_days=team_days, team_b2b=team_b2b):
                continue
            if not _team_can_play_today(away, day, team_days=team_days, team_b2b=team_b2b):
                continue

            # Hard B2B feasibility again (defensive)
            if not _b2b_feasible_after_play(home, day, team_days=team_days, team_games=team_games, team_b2b=team_b2b):
                continue
            if not _b2b_feasible_after_play(away, day, team_days=team_days, team_games=team_games, team_b2b=team_b2b):
                continue

            day_to_games[day].append(int(gid))
            game_day[int(gid)] = int(day)
            unscheduled.remove(int(gid))

            _apply_game_to_team_state(
                home,
                day=day,
                is_home=True,
                opponent=away,
                team_days=team_days,
                team_games=team_games,
                team_b2b=team_b2b,
                team_last_day=team_last_day,
                team_last_loc=team_last_loc,
                team_home_streak=team_home_streak,
                team_away_streak=team_away_streak,
                team_opp_last_day=team_opp_last_day,
            )
            _apply_game_to_team_state(
                away,
                day=day,
                is_home=False,
                opponent=home,
                team_days=team_days,
                team_games=team_games,
                team_b2b=team_b2b,
                team_last_day=team_last_day,
                team_last_loc=team_last_loc,
                team_home_streak=team_home_streak,
                team_away_streak=team_away_streak,
                team_opp_last_day=team_opp_last_day,
            )

    if unscheduled:
        return None

    # Final sanity: every game must be assigned a day.
    if any(d < 0 for d in game_day):
        return None

    return (day_to_games, game_day)


def _compute_team_days_maps(
    *,
    day_to_games: List[List[int]],
    game_day: List[int],
    stubs: List[_GameStub],
    teams: List[str],
) -> tuple[Dict[str, set[int]], Dict[str, Dict[int, int]]]:
    team_days: Dict[str, set[int]] = {t: set() for t in teams}
    team_day_game: Dict[str, Dict[int, int]] = {t: {} for t in teams}

    for gid, d in enumerate(game_day):
        day = int(d)
        if day < 0:
            continue
        stub = stubs[int(gid)]
        for tid in (stub.home_team_id, stub.away_team_id):
            team_days[tid].add(day)
            team_day_game[tid][day] = int(gid)

    return team_days, team_day_game


def _team_b2b_count(days: set[int]) -> int:
    return int(sum(1 for d in days if (int(d) - 1) in days))


def _validate_team_hard_constraints(days: set[int]) -> bool:
    # No 3 consecutive (checked on the last day of any triple).
    for d in days:
        if (int(d) - 1) in days and (int(d) - 2) in days:
            return False
        # No 4-in-5 (checked on the last day of any violating window).
        if _count_recent_games(days, start=int(d) - 4, end=int(d)) >= 4:
            return False
    return True


def _score_schedule_assignment(
    *,
    day_to_games: List[List[int]],
    game_day: List[int],
    stubs: List[_GameStub],
    teams: List[str],
) -> float:
    """Composite soft score (lower is better). Hard violations => +inf."""
    team_days, _ = _compute_team_days_maps(day_to_games=day_to_games, game_day=game_day, stubs=stubs, teams=teams)

    # Hard checks
    for t in teams:
        if not _validate_team_hard_constraints(team_days[t]):
            return math.inf
        if _team_b2b_count(team_days[t]) > int(_B2B_MAX):
            return math.inf

    # Soft score components
    score = 0.0

    # Team-level cadence
    for t in teams:
        days = team_days[t]
        b2b = _team_b2b_count(days)
        score += 4.0 * float(max(0, b2b - int(_B2B_TARGET)) ** 2)

        # 3-in-4 count proxy: count game days where the last-4-days window has >=3 games.
        three_in_four = 0
        for d in days:
            if _count_recent_games(days, start=int(d) - 3, end=int(d)) >= 3:
                three_in_four += 1
        score += 1.5 * float(three_in_four)

    # Game-level rest mismatch + rematch gap + streak penalties
    # Process games in chronological order.
    games_by_day: List[List[int]] = day_to_games

    prev_day: Dict[str, Optional[int]] = {t: None for t in teams}
    last_meeting: Dict[tuple[str, str], int] = {}
    last_loc: Dict[str, Optional[str]] = {t: None for t in teams}
    home_streak: Dict[str, int] = {t: 0 for t in teams}
    away_streak: Dict[str, int] = {t: 0 for t in teams}

    for day in range(SEASON_LENGTH_DAYS):
        for gid in games_by_day[day]:
            stub = stubs[int(gid)]
            home = stub.home_team_id
            away = stub.away_team_id

            rh = _rest_days(prev_day.get(home), day)
            ra = _rest_days(prev_day.get(away), day)
            score += 0.6 * float(abs(int(rh) - int(ra)))

            # Rematch softness
            key = tuple(sorted((home, away)))
            lm = last_meeting.get(key)
            if lm is not None:
                gap = int(day - int(lm))
                if gap < int(_MIN_REMATCH_GAP_DAYS):
                    score += 1.2 * float(int(_MIN_REMATCH_GAP_DAYS) - gap)
            last_meeting[key] = int(day)

            # Streak softness (beyond _SOFT_STREAK_LIMIT)
            # home team
            if last_loc.get(home) == 'H':
                home_streak[home] = int(home_streak.get(home, 0) + 1)
            else:
                home_streak[home] = 1
            away_streak[home] = 0
            last_loc[home] = 'H'

            # away team
            if last_loc.get(away) == 'A':
                away_streak[away] = int(away_streak.get(away, 0) + 1)
            else:
                away_streak[away] = 1
            home_streak[away] = 0
            last_loc[away] = 'A'

            if int(home_streak.get(home, 0)) > int(_SOFT_STREAK_LIMIT):
                score += 0.7 * float(int(home_streak[home]) - int(_SOFT_STREAK_LIMIT)) ** 2
            if int(away_streak.get(away, 0)) > int(_SOFT_STREAK_LIMIT):
                score += 0.7 * float(int(away_streak[away]) - int(_SOFT_STREAK_LIMIT)) ** 2

            prev_day[home] = int(day)
            prev_day[away] = int(day)

    return float(score)


def _collect_b2b_games(
    *,
    game_day: List[int],
    stubs: List[_GameStub],
    team_days: Dict[str, set[int]],
) -> List[int]:
    out: List[int] = []
    for gid, d in enumerate(game_day):
        day = int(d)
        if day <= 0:
            continue
        stub = stubs[int(gid)]
        if (day - 1) in team_days[stub.home_team_id] or (day - 1) in team_days[stub.away_team_id]:
            out.append(int(gid))
    return out


def _repair_schedule_assignment(
    *,
    day_to_games: List[List[int]],
    game_day: List[int],
    stubs: List[_GameStub],
    teams: List[str],
    blackout_days: set[int],
    max_games_per_day: int,
    rng: random.Random,
) -> None:
    """Local-search repair to reduce soft score without breaking hard constraints."""
    if int(_REPAIR_ITERS) <= 0:
        return

    # Build quick maps.
    team_days, team_day_game = _compute_team_days_maps(day_to_games=day_to_games, game_day=game_day, stubs=stubs, teams=teams)

    cur_score = _score_schedule_assignment(day_to_games=day_to_games, game_day=game_day, stubs=stubs, teams=teams)
    if not math.isfinite(cur_score):
        return

    b2b_games = _collect_b2b_games(game_day=game_day, stubs=stubs, team_days=team_days)

    def _move_game(gid: int, new_day: int) -> bool:
        nonlocal cur_score, team_days, team_day_game, b2b_games

        gid = int(gid)
        new_day = int(new_day)
        old_day = int(game_day[gid])
        if new_day == old_day:
            return False
        if new_day < 0 or new_day >= SEASON_LENGTH_DAYS:
            return False
        if new_day in blackout_days:
            return False
        if len(day_to_games[new_day]) >= int(max_games_per_day):
            return False

        stub = stubs[gid]
        home = stub.home_team_id
        away = stub.away_team_id

        # Teams must be free on the target day.
        if new_day in team_days[home] or new_day in team_days[away]:
            return False

        # Apply tentative move.
        if gid not in day_to_games[old_day]:
            return False

        day_to_games[old_day].remove(gid)
        day_to_games[new_day].append(gid)
        game_day[gid] = new_day

        # Update per-team day sets.
        team_days[home].remove(old_day)
        team_days[away].remove(old_day)
        team_days[home].add(new_day)
        team_days[away].add(new_day)
        team_day_game[home].pop(old_day, None)
        team_day_game[away].pop(old_day, None)
        team_day_game[home][new_day] = gid
        team_day_game[away][new_day] = gid

        # Hard validate only affected teams.
        ok = True
        for t in (home, away):
            if not _validate_team_hard_constraints(team_days[t]):
                ok = False
                break
            if _team_b2b_count(team_days[t]) > int(_B2B_MAX):
                ok = False
                break

        if not ok:
            # Revert
            day_to_games[new_day].remove(gid)
            day_to_games[old_day].append(gid)
            game_day[gid] = old_day

            team_days[home].remove(new_day)
            team_days[away].remove(new_day)
            team_days[home].add(old_day)
            team_days[away].add(old_day)
            team_day_game[home].pop(new_day, None)
            team_day_game[away].pop(new_day, None)
            team_day_game[home][old_day] = gid
            team_day_game[away][old_day] = gid
            return False

        # Score accept?
        new_score = _score_schedule_assignment(day_to_games=day_to_games, game_day=game_day, stubs=stubs, teams=teams)
        if new_score < cur_score:
            cur_score = float(new_score)
            return True

        # Revert if not improved.
        day_to_games[new_day].remove(gid)
        day_to_games[old_day].append(gid)
        game_day[gid] = old_day

        team_days[home].remove(new_day)
        team_days[away].remove(new_day)
        team_days[home].add(old_day)
        team_days[away].add(old_day)
        team_day_game[home].pop(new_day, None)
        team_day_game[away].pop(new_day, None)
        team_day_game[home][old_day] = gid
        team_day_game[away][old_day] = gid
        return False

    def _swap_games(gid1: int, gid2: int) -> bool:
        nonlocal cur_score, team_days, team_day_game
        gid1 = int(gid1)
        gid2 = int(gid2)
        if gid1 == gid2:
            return False

        d1 = int(game_day[gid1])
        d2 = int(game_day[gid2])
        if d1 == d2:
            return False
        if d1 in blackout_days or d2 in blackout_days:
            return False

        s1 = stubs[gid1]
        s2 = stubs[gid2]
        teams1 = {s1.home_team_id, s1.away_team_id}
        teams2 = {s2.home_team_id, s2.away_team_id}
        if teams1 & teams2:
            return False

        # Target-day occupancy: after swap, each team must be free on the new day.
        for t in teams1:
            if d2 in team_days[t]:
                return False
        for t in teams2:
            if d1 in team_days[t]:
                return False

        # Apply tentative swap.
        if gid1 not in day_to_games[d1] or gid2 not in day_to_games[d2]:
            return False

        day_to_games[d1].remove(gid1)
        day_to_games[d2].remove(gid2)
        day_to_games[d1].append(gid2)
        day_to_games[d2].append(gid1)
        game_day[gid1] = d2
        game_day[gid2] = d1

        # Update per-team day sets/maps.
        affected = list(teams1 | teams2)
        for t in affected:
            if d1 in team_days[t]:
                team_days[t].remove(d1)
                team_day_game[t].pop(d1, None)
            if d2 in team_days[t]:
                team_days[t].remove(d2)
                team_day_game[t].pop(d2, None)

        # Add new
        for t in teams1:
            team_days[t].add(d2)
            team_day_game[t][d2] = gid1
        for t in teams2:
            team_days[t].add(d1)
            team_day_game[t][d1] = gid2

        # Hard validate affected teams.
        ok = True
        for t in affected:
            if not _validate_team_hard_constraints(team_days[t]):
                ok = False
                break
            if _team_b2b_count(team_days[t]) > int(_B2B_MAX):
                ok = False
                break

        if not ok:
            # Revert
            day_to_games[d1].remove(gid2)
            day_to_games[d2].remove(gid1)
            day_to_games[d1].append(gid1)
            day_to_games[d2].append(gid2)
            game_day[gid1] = d1
            game_day[gid2] = d2

            # Rebuild maps for safety.
            team_days, team_day_game = _compute_team_days_maps(
                day_to_games=day_to_games, game_day=game_day, stubs=stubs, teams=teams
            )
            return False

        new_score = _score_schedule_assignment(day_to_games=day_to_games, game_day=game_day, stubs=stubs, teams=teams)
        if new_score < cur_score:
            cur_score = float(new_score)
            return True

        # Revert if not improved.
        day_to_games[d1].remove(gid2)
        day_to_games[d2].remove(gid1)
        day_to_games[d1].append(gid1)
        day_to_games[d2].append(gid2)
        game_day[gid1] = d1
        game_day[gid2] = d2

        team_days, team_day_game = _compute_team_days_maps(day_to_games=day_to_games, game_day=game_day, stubs=stubs, teams=teams)
        return False

    # Repair loop
    for it in range(int(_REPAIR_ITERS)):
        if it % 150 == 0:
            # Refresh B2B list periodically.
            b2b_games = _collect_b2b_games(game_day=game_day, stubs=stubs, team_days=team_days)

        if b2b_games and rng.random() < 0.70:
            gid = int(rng.choice(b2b_games))
        else:
            gid = int(rng.randrange(len(game_day)))

        cur_d = int(game_day[gid])
        if cur_d < 0:
            continue

        # Propose a nearby move to try to break B2Bs / long streaks.
        shift = rng.choice([-5, -4, -3, -2, -1, 1, 2, 3, 4, 5, 6])
        new_d = int(cur_d + int(shift))
        if new_d < 0 or new_d >= SEASON_LENGTH_DAYS:
            continue

        if new_d in blackout_days:
            continue

        # Prefer move when capacity allows; otherwise try swap.
        if len(day_to_games[new_d]) < int(max_games_per_day):
            _move_game(gid, new_d)
        else:
            # Swap with a random game on target day.
            candidates = list(day_to_games[new_d])
            if not candidates:
                continue
            rng.shuffle(candidates)
            for gid2 in candidates[:4]:
                if _swap_games(gid, int(gid2)):
                    break


def build_master_schedule(
    *,
    season_year: int,
    season_start: Optional[date] = None,
    rng_seed: Optional[int] = None,
) -> Dict[str, Any]:
    """
    정규시즌(regular) 마스터 스케줄을 **순수하게 생성**해서 반환한다.

    반환 포맷(league['master_schedule']에 그대로 넣을 수 있음):
      {
        "games": [...],
        "by_team": {team_id: [game_id, ...]},
        "by_date": {date_str: [game_id, ...]},
        "by_id": {game_id: entry},
      }

    스케줄 생성 정책(NEW):
    - NBA 82경기 구조를 반영 (디비전/컨퍼런스/타컨퍼런스 매치업)
    - 팀당 41홈/41원정 **정확히 보장**
    - 날짜 배치는 "남은 경기들을 날짜에 직접 배치" 방식
      - 하드 제약: 하루 1경기/팀, 3연전 금지, 4-in-5 금지, B2B 상한, 블랙아웃(올스타 브레이크 등)
      - 소프트 제약: B2B/3-in-4 최소화, 리매치 간격, 홈/원정 스트릭, 휴식 불리 최소화
      - 리페어/재시도: 여러 시도 중 최적 스코어 선택 + 로컬 리페어

    주의:
    - 이 함수는 state/DB/season_history/active_season_id에 접근하지 않는다.
    - 시즌 전환/오프시즌 처리/아카이브 등 라이프사이클은 facade(state.py)가 담당한다.
    """
    if season_start is None:
        season_start = date(int(season_year), SEASON_START_MONTH, SEASON_START_DAY)

    teams = list(ALL_TEAM_IDS)
    season_id = _season_id_from_year(int(season_year))
    phase = "regular"

    max_games_per_day = max(1, int(MAX_GAMES_PER_DAY))

    # 1) Matchup template (82 games/team) with exact 41H/41A.
    stubs = _build_nba_matchup_stubs(season_year=int(season_year))

    # 2) Calendar blackouts.
    blackout_days = _compute_blackout_day_indices(season_year=int(season_year), season_start=season_start)

    # Root RNG: ensure reproducibility when rng_seed is provided.
    root_rng = random.Random(rng_seed)

    best_assignment = None
    best_score = math.inf

    for attempt in range(int(_FULL_SCHEDULE_ATTEMPTS)):
        # Derive per-attempt RNG deterministically from the root.
        attempt_seed = root_rng.randrange(1_000_000_000)
        rng = random.Random(int(attempt_seed))

        res = _attempt_assign_dates(
            stubs,
            teams=teams,
            season_start=season_start,
            blackout_days=blackout_days,
            max_games_per_day=max_games_per_day,
            rng=rng,
        )
        if res is None:
            continue
        day_to_games, game_day = res

        # Repair pass (soft optimization)
        _repair_schedule_assignment(
            day_to_games=day_to_games,
            game_day=game_day,
            stubs=stubs,
            teams=teams,
            blackout_days=blackout_days,
            max_games_per_day=max_games_per_day,
            rng=rng,
        )

        score = _score_schedule_assignment(day_to_games=day_to_games, game_day=game_day, stubs=stubs, teams=teams)
        if not math.isfinite(score):
            continue
        if score < best_score:
            best_score = float(score)
            best_assignment = (day_to_games, game_day)

            # Early-exit heuristic: if we are "good enough", stop searching.
            if best_score < 2500.0:
                break

    if best_assignment is None:
        raise RuntimeError(
            "Failed to build a valid NBA-like schedule after retries. "
            "Try increasing _FULL_SCHEDULE_ATTEMPTS or relaxing constraints."
        )

    day_to_games, game_day = best_assignment

    # 3) Emit final master_schedule entries + indices.
    scheduled_games: List[Dict[str, Any]] = []
    by_date: Dict[str, List[str]] = {}
    by_team: Dict[str, List[str]] = {tid: [] for tid in teams}

    # Build entries in chronological order for UX/debug.
    for day in range(SEASON_LENGTH_DAYS):
        if not day_to_games[day]:
            continue
        game_date = season_start + timedelta(days=int(day))
        date_str = game_date.isoformat()
        by_date.setdefault(date_str, [])

        for gid in day_to_games[day]:
            stub = stubs[int(gid)]
            home_id = stub.home_team_id
            away_id = stub.away_team_id

            game_id = f"{date_str}_{home_id}_{away_id}"
            entry = {
                "game_id": game_id,
                "date": date_str,
                "home_team_id": home_id,
                "away_team_id": away_id,
                "status": "scheduled",
                "home_score": None,
                "away_score": None,
                "season_id": season_id,
                "phase": phase,
            }
            scheduled_games.append(entry)
            by_date[date_str].append(game_id)
            by_team[home_id].append(game_id)
            by_team[away_id].append(game_id)

    out = {
        "games": scheduled_games,
        "by_team": by_team,
        "by_date": by_date,
        "by_id": {g["game_id"]: g for g in scheduled_games},
    }

    # Minimal contract validation (fail-fast in dev)
    ensure_master_schedule_indices(out)

    return out



def mark_master_schedule_game_final(
    master_schedule: dict,
    *,
    game_id: str,
    game_date_str: str,
    home_id: str,
    away_id: str,
    home_score: int,
    away_score: int,
) -> None:
    """마스터 스케줄에 동일한 game_id가 있으면 결과를 반영한다.

    Fail-fast:
    - game_id가 스케줄에 없으면 예외를 발생시켜 상위 ingest가
      스케줄 비일관 상태를 만들지 않도록 한다.
    """
    if not isinstance(master_schedule, dict):
        raise ValueError("master_schedule must be a dict")
    games = master_schedule.get("games") or []
    by_id = master_schedule.get("by_id")
    if not isinstance(by_id, dict):
        raise ValueError("master_schedule.by_id must be a dict")
    entry = by_id.get(game_id)
    if entry:
        entry["status"] = "final"
        entry["date"] = game_date_str
        entry["home_score"] = home_score
        entry["away_score"] = away_score
        return

    for g in games:
        if isinstance(g, dict) and g.get("game_id") == game_id:
            g["status"] = "final"
            g["date"] = game_date_str
            g["home_score"] = home_score
            g["away_score"] = away_score
            by_id[game_id] = g
            return

    raise ValueError(f"master_schedule game_id not found: {game_id!r}")


def get_schedule_summary(master_schedule: dict) -> Dict[str, Any]:
    """마스터 스케줄 통계 요약을 반환한다.

    - 총 경기 수, 상태별 경기 수
    - 팀별 총 경기 수(82 보장 여부)와 홈/원정 분배

    주의:
    - 이 함수는 schedule을 생성하지 않는다.
    - schedule 생성/재생성은 facade(state.py)가 ensure_schedule_for_active_season()로 수행해야 한다.
    """
    # NOTE: This function must be PURE (no mutation). Do not call
    # ensure_master_schedule_indices() here.
    if not isinstance(master_schedule, dict):
        raise ValueError("master_schedule must be a dict")

    games = master_schedule.get("games") or []
    if not isinstance(games, list):
        games = []

    status_counts: Dict[str, int] = {}
    home_away: Dict[str, Dict[str, int]] = {tid: {"home": 0, "away": 0} for tid in ALL_TEAM_IDS}

    for g in games:
        if not isinstance(g, dict):
            continue
        status = g.get("status", "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1

        home_team_id = g.get("home_team_id")
        away_team_id = g.get("away_team_id")
        if home_team_id in home_away:
            home_away[home_team_id]["home"] += 1
        if away_team_id in home_away:
            home_away[away_team_id]["away"] += 1

    team_breakdown: Dict[str, Dict[str, Any]] = {}
    for tid in ALL_TEAM_IDS:
        # Compute from game scan (do not rely on by_team index).
        home_n = home_away.get(tid, {}).get("home", 0)
        away_n = home_away.get(tid, {}).get("away", 0)
        team_breakdown[tid] = {
            "games": int(home_n) + int(away_n),
            "home": int(home_n),
            "away": int(away_n),
        }
        team_breakdown[tid]["home_away_diff"] = team_breakdown[tid]["home"] - team_breakdown[tid]["away"]

    return {
        "total_games": len(games),
        "status_counts": status_counts,
        "teams": team_breakdown,
    }


def days_to_next_game(
    master_schedule: dict,
    *,
    team_id: str,
    date_iso: str,
    include_today: bool = True,
) -> Optional[int]:
    """Return number of days until the team's next non-canceled game.

    PURE helper:
      - Does not mutate master_schedule.
      - Prefers by_team/by_id indices when present; otherwise scans games list.

    Args:
      master_schedule: schedule dict (as stored under league['master_schedule']).
      team_id: canonical team id.
      date_iso: ISO date string (YYYY-MM-DD...).
      include_today: if True, a game on date_iso returns 0; else it must be strictly after.

    Returns:
      int days until next game, or None if not found.
    """
    if not isinstance(master_schedule, dict):
        return None

    tid = str(team_id).upper()
    try:
        base = date.fromisoformat(str(date_iso)[:10])
    except Exception:
        return None

    def _accept_delta(delta: int) -> bool:
        if include_today:
            return int(delta) >= 0
        return int(delta) > 0

    best: Optional[int] = None

    by_team = master_schedule.get("by_team")
    by_id = master_schedule.get("by_id")
    if isinstance(by_team, dict) and isinstance(by_id, dict):
        gids = by_team.get(tid)
        if isinstance(gids, list):
            for gid in gids:
                if not isinstance(gid, str):
                    continue
                entry = by_id.get(gid)
                if not isinstance(entry, dict):
                    continue
                status = str(entry.get("status") or "").lower()
                if status == "canceled":
                    continue
                ds = entry.get("date")
                if not ds:
                    continue
                try:
                    gd = date.fromisoformat(str(ds)[:10])
                except Exception:
                    continue
                delta = int((gd - base).days)
                if not _accept_delta(delta):
                    continue
                if best is None or int(delta) < int(best):
                    best = int(delta)
        if best is not None:
            return int(best)

    games = master_schedule.get("games") or []
    if not isinstance(games, list):
        games = []
    for entry in games:
        if not isinstance(entry, dict):
            continue
        h = str(entry.get("home_team_id") or "").upper()
        a = str(entry.get("away_team_id") or "").upper()
        if tid not in (h, a):
            continue
        status = str(entry.get("status") or "").lower()
        if status == "canceled":
            continue
        ds = entry.get("date")
        if not ds:
            continue
        try:
            gd = date.fromisoformat(str(ds)[:10])
        except Exception:
            continue
        delta = int((gd - base).days)
        if not _accept_delta(delta):
            continue
        if best is None or int(delta) < int(best):
            best = int(delta)

    return int(best) if best is not None else None
