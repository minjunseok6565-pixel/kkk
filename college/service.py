from __future__ import annotations

import datetime as _dt
import json
import math
import random
import game_time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Set

from league_repo import LeagueRepo
from ratings_2k import validate_attrs

from . import config
from .declarations import declare_probability, estimate_draft_score
from .generation import build_college_teams, generate_initial_world_players, generate_players_for_team_class, sample_class_strength
from .names import build_used_name_keys
from .sim import simulate_college_season
from .types import (
    CollegePlayer,
    CollegeSeasonStats,
    CollegeTeam,
    CollegeTeamSeasonStats,
    DraftEntryDecisionTrace,
    json_dumps,
    json_loads,
)

def _stable_seed(*parts: object) -> int:
    """
    Stable seed across runs, independent of Python's hash randomization.
    """
    s = "|".join(str(p) for p in parts)
    h = 2166136261
    for ch in s.encode("utf-8"):
        h ^= ch
        h = (h * 16777619) & 0xFFFFFFFF
    return int(h)


# ----------------------------
# meta helpers
# ----------------------------

def _get_meta(repo: LeagueRepo, key: str) -> Optional[str]:
    row = repo._conn.execute("SELECT value FROM meta WHERE key=?;", (key,)).fetchone()
    if not row:
        return None
    return str(row[0]) if row[0] is not None else None


def _set_meta(repo: LeagueRepo, key: str, value: str, cur=None) -> None:
    """Write meta within an explicit transaction cursor.

    IMPORTANT: Avoid repo._conn.execute() for writes because sqlite3 will start an implicit
    transaction that can conflict with LeagueRepo.transaction()'s BEGIN.

    Policy:
      - If cur is provided, execute using that cursor.
      - Else if the connection is already inside a transaction, execute via a fresh cursor
        (do NOT start a nested BEGIN).
      - Else open a short repo.transaction() and execute inside.
    """

    sql = (
        "INSERT INTO meta(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value;"
    )
    params = (key, value)

    if cur is not None:
        cur.execute(sql, params)
        return

    # If caller already started a transaction but didn't pass cur, avoid nested BEGIN.
    if bool(getattr(repo._conn, "in_transaction", False)):
        c = repo._conn.cursor()
        try:
            c.execute(sql, params)
        finally:
            c.close()
        return

    with repo.transaction() as cur2:
        cur2.execute(sql, params)


# ----------------------------
# name uniqueness helpers (DB-aware)
# ----------------------------

def _collect_used_name_keys(repo: LeagueRepo, *, cur=None) -> Set[str]:
    """Collect used full-name keys (casefolded) from BOTH NBA and College.

    Reads:
      - players.name
      - college_players.name

    `cur` should be the active transaction cursor when called inside a write transaction.
    """
    ex = cur.execute if cur is not None else repo._conn.execute
    names: List[str] = []

    rows = ex("SELECT name FROM players WHERE name IS NOT NULL AND TRIM(name) != '';").fetchall()
    for (name,) in rows:
        if name is None:
            continue
        names.append(str(name))

    rows = ex("SELECT name FROM college_players WHERE name IS NOT NULL AND TRIM(name) != '';").fetchall()
    for (name,) in rows:
        if name is None:
            continue
        names.append(str(name))

    return build_used_name_keys(names)


# ----------------------------
# player_id allocation (DB-aware, collision-safe)
# ----------------------------

def _compute_max_player_num(repo: LeagueRepo) -> int:
    """
    Max numeric suffix among Pxxxxxx ids across BOTH NBA players and college_players.
    This avoids collisions even before we refactor draft/apply allocator.
    """
    # Ensure tables exist (players is created by repo.init_db; college by ensure_college_schema)
    max_n = 0

    # players table
    rows = repo._conn.execute("SELECT player_id FROM players WHERE player_id LIKE 'P%';").fetchall()
    for (pid,) in rows:
        s = str(pid)
        if len(s) >= 2 and s[1:].isdigit():
            max_n = max(max_n, int(s[1:]))

    # college_players table
    rows = repo._conn.execute("SELECT player_id FROM college_players WHERE player_id LIKE 'P%';").fetchall()
    for (pid,) in rows:
        s = str(pid)
        if len(s) >= 2 and s[1:].isdigit():
            max_n = max(max_n, int(s[1:]))

    return int(max_n)


def allocate_player_ids(repo: LeagueRepo, *, count: int, cur=None) -> List[str]:
    """
    Allocate sequential player_id values: P000001, P000002, ...

    Uses meta('seq_player_id') as the primary source for speed,
    and falls back to scanning tables to ensure collision-free init.
    """
    n = int(count)
    if n <= 0:
        return []

    key = "seq_player_id"

    def _allocate_player_ids_in_tx(*, count: int, cur) -> List[str]:
        """Allocate ids and update meta within the provided transaction cursor."""
        cur_val = _get_meta(repo, key)
        if cur_val is None:
            # initialize from max among players and college_players
            max_n = _compute_max_player_num(repo)
            _set_meta(repo, key, str(max_n), cur=cur)
            cur_n = max_n
        else:
            try:
                cur_n = int(cur_val)
            except Exception:
                cur_n = _compute_max_player_num(repo)
                _set_meta(repo, key, str(cur_n), cur=cur)

        ids: List[str] = []
        for _ in range(int(count)):
            cur_n += 1
            ids.append(f"P{cur_n:06d}")

        _set_meta(repo, key, str(cur_n), cur=cur)
        return ids

    # If caller provides a cursor, we are already inside an explicit transaction.
    if cur is not None:
        return _allocate_player_ids_in_tx(count=n, cur=cur)

    # If caller already started a transaction but didn't pass cur, avoid nested BEGIN.
    if bool(getattr(repo._conn, "in_transaction", False)):
        c = repo._conn.cursor()
        try:
            return _allocate_player_ids_in_tx(count=n, cur=c)
        finally:
            c.close()

    with repo.transaction() as cur2:
        return _allocate_player_ids_in_tx(count=n, cur=cur2)


# ----------------------------
# class strength
# ----------------------------

def get_or_create_class_strength(repo: LeagueRepo, *, draft_year: int, seed_salt: str, cur=None) -> float:
    """
    Fetch class strength from DB or create it deterministically if missing.
    """
    dy = int(draft_year)
    row = repo._conn.execute("SELECT strength FROM draft_class_strength WHERE draft_year=?;", (dy,)).fetchone()
    if row and row[0] is not None:
        return float(row[0])

    seed = _stable_seed("class_strength", dy, seed_salt)
    rng = random.Random(seed)
    strength = float(sample_class_strength(rng))
    lo, hi = config.CLASS_STRENGTH_CLAMP
    strength = float(max(lo, min(hi, strength)))

    sql = "INSERT INTO draft_class_strength(draft_year, strength, seed, created_at) VALUES (?, ?, ?, ?);"
    params = (dy, float(strength), int(seed), game_time.now_utc_like_iso())
    if cur is not None:
        cur.execute(sql, params)
    elif bool(getattr(repo._conn, "in_transaction", False)):
        c = repo._conn.cursor()
        try:
            c.execute(sql, params)
        finally:
            c.close()
    else:
        with repo.transaction() as cur2:
            cur2.execute(sql, params)
    return float(strength)


# ----------------------------
# world bootstrap
# ----------------------------

def ensure_world_bootstrapped(db_path: str, season_year: int) -> None:
    """
    Ensure college teams + initial (1~4 class years) players exist.

    Intended call site:
      state.startup_init_state() after NBA season/year is established.

    Idempotent: safe to call multiple times.

    Upgrade-safe:
      - If COLLEGE_TEAM_COUNT increases, missing teams will be inserted.
      - If some teams exist but have no players (e.g., newly inserted teams),
        bootstrap players will be created only for those teams.
    """
    sy = int(season_year)
    with LeagueRepo(db_path) as repo:
        repo.init_db()

        marker_key = "college_bootstrap_season_year"
        marker = _get_meta(repo, marker_key)

        # Ensure teams (insert missing). Do NOT gate this on marker/meta so that
        # increasing COLLEGE_TEAM_COUNT is naturally supported.
        seed_teams = build_college_teams()
        with repo.transaction() as cur:
            for t in seed_teams:
                cur.execute(
                    "INSERT OR IGNORE INTO college_teams(college_team_id, name, conference, meta_json) VALUES (?, ?, ?, ?);",
                    (t.college_team_id, t.name, t.conference, json_dumps(t.meta)),
                )

        # Load teams (ordered)
        teams = _load_teams(repo)
        if not teams:
            # Defensive: schema exists but teams are missing for some reason.
            return

        # Fast skip: if marker matches AND every team has at least one player.
        if marker == str(sy):
            missing = repo._conn.execute(
                """
                SELECT t.college_team_id
                FROM college_teams t
                LEFT JOIN (SELECT DISTINCT college_team_id FROM college_players) p
                  ON p.college_team_id = t.college_team_id
                WHERE p.college_team_id IS NULL
                LIMIT 1;
                """
            ).fetchone()
            if missing is None:
                return

        # Strength provider: use draft_year = entry_season_year + 1 as the cohort's "expected draft year"
        def strength_for_entry(entry_season_year: int) -> float:
            dy = int(entry_season_year) + 1
            return get_or_create_class_strength(repo, draft_year=dy, seed_salt=f"bootstrap@{sy}")

        # Ensure initial players
        existing_player_count = repo._conn.execute("SELECT COUNT(*) FROM college_players;").fetchone()[0]
        created_players = False
        if int(existing_player_count) <= 0:
            rng = random.Random(_stable_seed("college_bootstrap_players", sy))
            used_name_keys = _collect_used_name_keys(repo)
            tmp_players = generate_initial_world_players(
                rng,
                season_year=sy,
                teams=teams,
                class_strength_for_entry_season=strength_for_entry,
                used_name_keys=used_name_keys,
            )

            with repo.transaction() as cur:
                new_ids = allocate_player_ids(repo, count=len(tmp_players), cur=cur)
                for pid, p in zip(new_ids, tmp_players):
                    cur.execute(
                        """
                        INSERT INTO college_players(
                            player_id, college_team_id, class_year, entry_season_year, status,
                            name, pos, age, height_in, weight_lb, ovr, attrs_json
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                        """,
                        (
                            pid,
                            p.college_team_id,
                            int(p.class_year),
                            int(p.entry_season_year),
                            str(p.status),
                            p.name,
                            p.pos,
                            int(p.age),
                            int(p.height_in),
                            int(p.weight_lb),
                            int(p.ovr),
                            json_dumps(p.attrs),
                        ),
                    )

                _set_meta(repo, marker_key, str(sy), cur=cur)
                created_players = True

        else:
            # Only bootstrap players for teams that currently have none.
            rows = repo._conn.execute(
                """
                SELECT t.college_team_id
                FROM college_teams t
                LEFT JOIN (SELECT DISTINCT college_team_id FROM college_players) p
                  ON p.college_team_id = t.college_team_id
                WHERE p.college_team_id IS NULL
                ORDER BY t.college_team_id ASC;
                """
            ).fetchall()
            missing_team_ids = [str(r[0]) for r in rows]

            if missing_team_ids:
                used_name_keys = _collect_used_name_keys(repo)
                team_by_id = {t.college_team_id: t for t in teams}
                tmp_players = []
                for tid in missing_team_ids:
                    t = team_by_id.get(tid)
                    if t is None:
                        continue
                    rng = random.Random(_stable_seed("college_bootstrap_players", sy, tid))
                    tmp_players.extend(
                        generate_initial_world_players(
                            rng,
                            season_year=sy,
                            teams=[t],
                            class_strength_for_entry_season=strength_for_entry,
                            used_name_keys=used_name_keys,
                        )
                    )

                if tmp_players:
                    with repo.transaction() as cur:
                        new_ids = allocate_player_ids(repo, count=len(tmp_players), cur=cur)
                        for pid, p in zip(new_ids, tmp_players):
                            cur.execute(
                                """
                                INSERT INTO college_players(
                                    player_id, college_team_id, class_year, entry_season_year, status,
                                    name, pos, age, height_in, weight_lb, ovr, attrs_json
                                )
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                                """,
                                (
                                    pid,
                                    p.college_team_id,
                                    int(p.class_year),
                                    int(p.entry_season_year),
                                    str(p.status),
                                    p.name,
                                    p.pos,
                                    int(p.age),
                                    int(p.height_in),
                                    int(p.weight_lb),
                                    int(p.ovr),
                                    json_dumps(p.attrs),
                                ),
                            )

                        _set_meta(repo, marker_key, str(sy), cur=cur)
                        created_players = True
        # If players already existed but marker was missing/outdated, set it now.
        if not created_players:
            _set_meta(repo, marker_key, str(sy))


# ----------------------------
# season finalize + draft entries
# ----------------------------

def _load_active_players(repo: LeagueRepo) -> List[CollegePlayer]:
    rows = repo._conn.execute(
        """
        SELECT
            player_id, name, pos, age, height_in, weight_lb, ovr,
            college_team_id, class_year, entry_season_year, status, attrs_json
        FROM college_players
        WHERE status IN ('ACTIVE','DECLARED')
        ORDER BY college_team_id ASC, class_year ASC, ovr DESC, player_id ASC;
        """
    ).fetchall()
    out: List[CollegePlayer] = []
    for r in rows:
        out.append(
            CollegePlayer(
                player_id=str(r[0]),
                name=str(r[1]),
                pos=str(r[2]),
                age=int(r[3]),
                height_in=int(r[4]),
                weight_lb=int(r[5]),
                ovr=int(r[6]),
                college_team_id=str(r[7]),
                class_year=int(r[8]),
                entry_season_year=int(r[9]),
                status=str(r[10]),
                attrs=(
                    (lambda a: (validate_attrs(a, strict=True), a)[1])(json_loads(str(r[11])) or {})
                    if isinstance(json_loads(str(r[11])) or {}, dict) else {}
                ),
            )
        )
    return out


def _load_teams(repo: LeagueRepo) -> List[CollegeTeam]:
    rows = repo._conn.execute(
        "SELECT college_team_id, name, conference, meta_json FROM college_teams ORDER BY college_team_id ASC;"
    ).fetchall()
    teams: List[CollegeTeam] = []
    for r in rows:
        teams.append(
            CollegeTeam(
                college_team_id=str(r[0]),
                name=str(r[1]),
                conference=str(r[2]),
                meta=json_loads(str(r[3])) or {},
            )
        )
    return teams



def _compute_relative_draft_ranks(
    *,
    players: Sequence[CollegePlayer],
    stats_by_pid: Dict[str, CollegeSeasonStats],
    class_strength: float,
) -> Tuple[Dict[str, int], int]:
    """Compute eligible-pool relative ranks for draft declaration logic.

    Returns:
      - rank_by_pid: player_id -> 1..N rank within eligible pool (lower is better)
      - eligible_count: N

    Deterministic:
      - Sort by (-draft_score, player_id) to break ties stably.
    """
    scored: List[Tuple[str, float]] = []
    for p in players:
        if int(p.age) < config.MIN_DRAFT_ELIGIBLE_AGE:
            continue
        potential_grade = str(p.attrs.get("Potential") or "C-")
        season_stats = stats_by_pid.get(p.player_id)

        score = estimate_draft_score(
            ovr=int(p.ovr),
            age=int(p.age),
            class_year=int(p.class_year),
            potential_grade=potential_grade,
            season_stats=season_stats,
            class_strength=float(class_strength),
        )
        scored.append((str(p.player_id), float(score)))

    scored.sort(key=lambda t: (-float(t[1]), str(t[0])))

    rank_by_pid: Dict[str, int] = {}
    for i, (pid, _score) in enumerate(scored):
        rank_by_pid[str(pid)] = int(i + 1)

    return rank_by_pid, int(len(scored))


def finalize_season_and_generate_entries(db_path: str, season_year: int, draft_year: int) -> None:
    """
    1) Simulate college season stats for season_year (fast)
    2) Persist team/player season stats
    3) Generate draft declarations for draft_year (usually season_year+1)

    Idempotent behavior:
    - If draft entries already exist for draft_year, we skip re-generate.

    IMPORTANT (2차 월별 스냅샷):
    - 월별 스냅샷이 college_*_season_stats 를 미리 채워둘 수 있으므로,
      시즌 마감(finalize)에서는 "stats가 이미 있다"는 이유로 스킵하면 안 된다.
      finalize는 항상 최종 스탯을 재시뮬/덮어써서 SSOT를 안정화한다.
    """
    sy = int(season_year)
    dy = int(draft_year)

    with LeagueRepo(db_path) as repo:
        repo.init_db()

        # Ensure class strength exists for this draft year
        strength = get_or_create_class_strength(repo, draft_year=dy, seed_salt=f"entries@{sy}")

        players = _load_active_players(repo)
        teams = _load_teams(repo)

        # FINALIZE: Always re-sim and overwrite season stats (monthly snapshots may have populated partial stats).
        rng = random.Random(_stable_seed("college_season_sim", sy))
        team_stats, player_stats = simulate_college_season(rng, season_year=sy, teams=teams, players=players)

        finalized_key = f"college_finalized_season_year:{sy}"
        with repo.transaction() as cur:
            for ts in team_stats:
                cur.execute(
                    """
                    INSERT OR REPLACE INTO college_team_season_stats(
                        season_year, college_team_id, wins, losses, srs, pace, off_ppg, def_ppg, meta_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        int(ts.season_year),
                        ts.college_team_id,
                        int(ts.wins),
                        int(ts.losses),
                        float(ts.srs),
                        float(ts.pace),
                        float(ts.off_ppg),
                        float(ts.def_ppg),
                        json_dumps(ts.meta),
                    ),
                )

            for ps in player_stats:
                cur.execute(
                    """
                    INSERT OR REPLACE INTO college_player_season_stats(
                        season_year, player_id, college_team_id, stats_json
                    ) VALUES (?, ?, ?, ?);
                    """,
                    (
                        int(ps.season_year),
                        ps.player_id,
                        ps.college_team_id,
                        json_dumps(ps.to_json_dict()),
                    ),
                )

            # Mark finalized to help debug / protect against misreads (not used for gating)
            _set_meta(repo, finalized_key, "1", cur=cur)

        # Check if entries already exist
        entries_exist = repo._conn.execute(
            "SELECT 1 FROM college_draft_entries WHERE draft_year=? LIMIT 1;", (dy,)
        ).fetchone() is not None
        if entries_exist:
            return

        # Load season stats map (for declarations)
        rows = repo._conn.execute(
            "SELECT player_id, stats_json FROM college_player_season_stats WHERE season_year=?;",
            (sy,),
        ).fetchall()
        stats_by_pid: Dict[str, CollegeSeasonStats] = {}
        for pid, sjson in rows:
            d = json_loads(str(sjson)) or {}
            # stats_json에는 직렬화 버전 키(__v)가 포함될 수 있으므로,
            # dataclass 생성 전에 제거해서 slots/필드 불일치로 인한 TypeError를 방지한다.
            if isinstance(d, dict):
                d.pop("__v", None)
            else:
                d = {}
            stats_by_pid[str(pid)] = CollegeSeasonStats(**d)

        # Compute relative draft ranks (eligible pool) for declaration logic (P0-2 fix)
        rank_by_pid, eligible_n = _compute_relative_draft_ranks(
            players=players,
            stats_by_pid=stats_by_pid,
            class_strength=float(strength),
        )

        # Decide declarations
        now = game_time.now_utc_like_iso()
        declared: List[DraftEntryDecisionTrace] = []

        for p in players:
            # Eligibility gate (simple; can be extended)
            if int(p.age) < config.MIN_DRAFT_ELIGIBLE_AGE:
                continue

            potential_grade = str(p.attrs.get("Potential") or "C-")
            season_stats = stats_by_pid.get(p.player_id)

            rank = rank_by_pid.get(p.player_id)
            if rank is None:
                # Defensive: should not happen if eligibility gate and rank computation match.
                continue

            # stable per-player RNG for reproducibility
            rng = random.Random(_stable_seed("declare", dy, p.player_id))

            trace = declare_probability(
                rng,
                player_id=p.player_id,
                draft_year=dy,
                ovr=int(p.ovr),
                age=int(p.age),
                class_year=int(p.class_year),
                potential_grade=potential_grade,
                season_stats=season_stats,
                class_strength=float(strength),
                projected_pick=int(rank),
                eligible_pool_size=int(eligible_n),
            )
            if trace.declared:
                declared.append(trace)

        # Persist entries and update statuses
        with repo.transaction() as cur:
            for tr in declared:
                cur.execute(
                    """
                    INSERT INTO college_draft_entries(draft_year, player_id, declared_at, decision_json)
                    VALUES (?, ?, ?, ?);
                    """,
                    (int(dy), tr.player_id, now, json_dumps(tr.to_json_dict())),
                )
                cur.execute(
                    "UPDATE college_players SET status='DECLARED' WHERE player_id=?;",
                    (tr.player_id,),
                )


# ----------------------------
# monthly stats snapshots (in-season)
# ----------------------------

def _clamp(x: float, lo: float, hi: float) -> float:
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x


def compute_games_played_for_period(season_year: int, period_key: str) -> int:
    """Return coarse cumulative games played per team for a month checkpoint.

    period_key: "YYYY-MM"
    - We intentionally model only the NCAA window months defined in config.
    - Months not in config.COLLEGE_GAMES_BY_CHECKPOINT_MONTH -> 0 games (no snapshot changes).
    """
    _ = int(season_year)  # keep signature stable for future refinements
    pk = str(period_key or "").strip()
    if len(pk) != 7 or pk[4] != "-":
        raise ValueError(f"period_key must be YYYY-MM: got {period_key!r}")
    try:
        m = int(pk[5:7])
    except Exception as e:
        raise ValueError(f"period_key must be YYYY-MM: got {period_key!r}") from e

    g = int(getattr(config, "COLLEGE_GAMES_BY_CHECKPOINT_MONTH", {}).get(m, 0))
    season_games = int(getattr(config, "COLLEGE_SEASON_GAMES_PER_TEAM", 34))
    return int(_clamp(float(g), 0.0, float(season_games)))


def recompute_college_monthly_stats_snapshot(
    db_path: str,
    *,
    season_year: int,
    period_key: str,
    as_of_date: str,
    force: bool = False,
) -> Dict[str, Any]:
    """Recompute (or skip) an in-season monthly stats snapshot for college.

    Writes:
      - college_player_season_stats (season_year, player_id)  -> snapshot payload
      - college_team_season_stats   (season_year, team_id)    -> snapshot payload
      - meta keys for idempotence

    Snapshot model:
      - baseline "truth" = simulate_college_season(seed="college_season_truth", season_year)
      - snapshot variance scales by 1/sqrt(max(games_to_date,1))
      - games_to_date is coarse per-month (config.COLLEGE_GAMES_BY_CHECKPOINT_MONTH)
      - deterministic per (season_year, period_key, player_id) seeds
    """
    sy = int(season_year)
    pk = str(period_key or "").strip()
    as_of = str(as_of_date or "").strip()
    if sy <= 0:
        raise ValueError(f"invalid season_year: {season_year}")
    if len(pk) != 7 or pk[4] != "-":
        raise ValueError(f"period_key must be YYYY-MM: got {period_key!r}")
    if len(as_of) < 7:
        raise ValueError(f"as_of_date must be YYYY-MM-DD (or at least YYYY-MM): got {as_of_date!r}")

    done_key = f"college_monthly_stats_done:{sy}:{pk}"
    now = game_time.now_utc_like_iso()

    games_to_date = int(compute_games_played_for_period(sy, pk))
    season_games = int(getattr(config, "COLLEGE_SEASON_GAMES_PER_TEAM", 34))
    frac = float(games_to_date) / float(season_games if season_games > 0 else 34)
    frac = _clamp(frac, 0.0, 1.0)

    # Noise + clamps from config (defaults are conservative)
    noise_std: Dict[str, float] = dict(getattr(config, "COLLEGE_SNAPSHOT_NOISE_STD", {}) or {})
    clamps: Dict[str, Tuple[float, float]] = dict(getattr(config, "COLLEGE_SNAPSHOT_CLAMPS", {}) or {})

    def _std_for(k: str, default: float) -> float:
        try:
            return float(noise_std.get(k, default))
        except Exception:
            return float(default)

    def _clamp_for(k: str, v: float) -> float:
        rng = clamps.get(k)
        if not rng:
            return float(v)
        return float(_clamp(float(v), float(rng[0]), float(rng[1])))

    # Reliability scaling (bigger sample -> smaller noise)
    denom = math.sqrt(float(max(games_to_date, 1)))

    with LeagueRepo(db_path) as repo:
        repo.init_db()

        if _get_meta(repo, done_key) == "1" and not bool(force):
            return {
                "ok": True,
                "season_year": sy,
                "period_key": pk,
                "as_of_date": as_of,
                "games_to_date": games_to_date,
                "skipped": True,
                "written_players": 0,
                "written_teams": 0,
                "created_at": now,
            }

        players = _load_active_players(repo)
        teams = _load_teams(repo)

        # Baseline "truth" season
        rng_truth = random.Random(_stable_seed("college_season_truth", sy))
        base_team_stats, base_player_stats = simulate_college_season(
            rng_truth, season_year=sy, teams=teams, players=players
        )
        base_ps_by_pid: Dict[str, CollegeSeasonStats] = {ps.player_id: ps for ps in base_player_stats}
        base_ts_by_tid: Dict[str, CollegeTeamSeasonStats] = {ts.college_team_id: ts for ts in base_team_stats}

        written_players = 0
        written_teams = 0

        with repo.transaction() as cur:
            # Teams snapshot
            for t in teams:
                base_ts = base_ts_by_tid.get(t.college_team_id)
                if base_ts is None:
                    # Safe fallback
                    base_ts = CollegeTeamSeasonStats(
                        season_year=sy,
                        college_team_id=t.college_team_id,
                        wins=0,
                        losses=0,
                        srs=0.0,
                        pace=70.0,
                        off_ppg=70.0,
                        def_ppg=70.0,
                        meta={},
                    )

                # deterministic noise per team/month
                rng = random.Random(_stable_seed("college_monthly_team_snapshot", sy, pk, t.college_team_id))

                g = games_to_date
                wins = int(round(float(base_ts.wins) * frac)) if g > 0 else 0
                wins = int(_clamp(float(wins), 0.0, float(g)))
                losses = int(g - wins)

                pace = float(base_ts.pace) + rng.gauss(0.0, 1.2 / denom)
                off_ppg = float(base_ts.off_ppg) + rng.gauss(0.0, 2.0 / denom)
                def_ppg = float(base_ts.def_ppg) + rng.gauss(0.0, 2.0 / denom)
                srs = float(base_ts.srs) + rng.gauss(0.0, 1.0 / denom)

                meta = {
                    "kind": "MONTHLY",
                    "period_key": pk,
                    "as_of_date": as_of,
                    "games_to_date": int(g),
                }
                cur.execute(
                    """
                    INSERT OR REPLACE INTO college_team_season_stats(
                        season_year, college_team_id, wins, losses, srs, pace, off_ppg, def_ppg, meta_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        int(sy),
                        t.college_team_id,
                        int(wins),
                        int(losses),
                        float(srs),
                        float(_clamp_for("pace", pace)),
                        float(off_ppg),
                        float(def_ppg),
                        json_dumps(meta),
                    ),
                )
                written_teams += 1

            # Players snapshot
            for p in players:
                base_ps = base_ps_by_pid.get(p.player_id)
                if base_ps is None:
                    # Safe fallback (should not happen)
                    base_ps = CollegeSeasonStats(
                        season_year=sy,
                        player_id=p.player_id,
                        college_team_id=p.college_team_id,
                        games=season_games,
                        mpg=20.0,
                        pts=6.0,
                        reb=3.0,
                        ast=2.0,
                        stl=0.6,
                        blk=0.4,
                        tov=1.5,
                        pf=2.0,
                        fg_pct=0.45,
                        tp_pct=0.33,
                        ft_pct=0.72,
                        usg=0.18,
                        ts_pct=0.53,
                        pace=70.0,
                        meta={},
                    )

                rng = random.Random(_stable_seed("college_monthly_snapshot", sy, pk, p.player_id))

                g = int(games_to_date)
                if g <= 0:
                    # Early months: no reliable box score yet
                    mpg = 0.0
                    pts = reb = ast = stl = blk = tov = pf = 0.0
                    fg_pct = tp_pct = ft_pct = 0.0
                    usg = 0.0
                    ts_pct = 0.0
                    pace = 0.0
                else:
                    # Per-game stats with noise (variance shrinks as games accumulate)
                    mpg = float(base_ps.mpg) + rng.gauss(0.0, _std_for("mpg", 2.2) / denom)
                    pts = float(base_ps.pts) + rng.gauss(0.0, _std_for("pts", 3.2) / denom)
                    reb = float(base_ps.reb) + rng.gauss(0.0, _std_for("reb", 1.4) / denom)
                    ast = float(base_ps.ast) + rng.gauss(0.0, _std_for("ast", 1.3) / denom)
                    stl = float(base_ps.stl) + rng.gauss(0.0, _std_for("stl", 0.25) / denom)
                    blk = float(base_ps.blk) + rng.gauss(0.0, _std_for("blk", 0.25) / denom)
                    tov = float(base_ps.tov) + rng.gauss(0.0, _std_for("tov", 0.55) / denom)
                    pf = float(base_ps.pf) + rng.gauss(0.0, _std_for("pf", 0.45) / denom)

                    fg_pct = float(base_ps.fg_pct) + rng.gauss(0.0, _std_for("fg_pct", 0.040) / denom)
                    tp_pct = float(base_ps.tp_pct) + rng.gauss(0.0, _std_for("tp_pct", 0.060) / denom)
                    ft_pct = float(base_ps.ft_pct) + rng.gauss(0.0, _std_for("ft_pct", 0.040) / denom)

                    # Flavor metrics: keep close to baseline (small noise)
                    usg = float(base_ps.usg) + rng.gauss(0.0, 0.030 / denom)
                    ts_pct = float(base_ps.ts_pct) + rng.gauss(0.0, 0.030 / denom)
                    pace = float(base_ps.pace) + rng.gauss(0.0, 2.0 / denom)

                mpg = _clamp_for("mpg", float(mpg))
                pts = _clamp_for("pts", float(pts))
                reb = _clamp_for("reb", float(reb))
                ast = _clamp_for("ast", float(ast))
                stl = _clamp_for("stl", float(stl))
                blk = _clamp_for("blk", float(blk))
                tov = float(max(0.0, tov))
                pf = float(max(0.0, pf))
                fg_pct = _clamp_for("fg_pct", float(fg_pct))
                tp_pct = _clamp_for("tp_pct", float(tp_pct))
                ft_pct = _clamp_for("ft_pct", float(ft_pct))
                usg = _clamp_for("usg", float(usg))
                ts_pct = _clamp_for("ts_pct", float(ts_pct))
                pace = _clamp_for("pace", float(pace))

                meta = {
                    "kind": "MONTHLY",
                    "period_key": pk,
                    "as_of_date": as_of,
                    "games_to_date": int(g),
                }

                snap = CollegeSeasonStats(
                    season_year=sy,
                    player_id=p.player_id,
                    college_team_id=p.college_team_id,
                    games=int(g),
                    mpg=float(mpg),
                    pts=float(pts),
                    reb=float(reb),
                    ast=float(ast),
                    stl=float(stl),
                    blk=float(blk),
                    tov=float(tov),
                    pf=float(pf),
                    fg_pct=float(fg_pct),
                    tp_pct=float(tp_pct),
                    ft_pct=float(ft_pct),
                    usg=float(usg),
                    ts_pct=float(ts_pct),
                    pace=float(pace),
                    meta=meta,
                )

                cur.execute(
                    """
                    INSERT OR REPLACE INTO college_player_season_stats(
                        season_year, player_id, college_team_id, stats_json
                    ) VALUES (?, ?, ?, ?);
                    """,
                    (int(sy), snap.player_id, snap.college_team_id, json_dumps(snap.to_json_dict())),
                )
                written_players += 1

            # idempotence markers
            _set_meta(repo, done_key, "1", cur=cur)
            _set_meta(repo, f"college_monthly_stats_last_period:{sy}", pk, cur=cur)

        return {
            "ok": True,
            "season_year": sy,
            "period_key": pk,
            "as_of_date": as_of,
            "games_to_date": games_to_date,
            "skipped": False,
            "written_players": int(written_players),
            "written_teams": int(written_teams),
            "created_at": now,
        }


def _infer_season_year_from_date(d: _dt.date) -> int:
    # College season assumed to start in October.
    return int(d.year) if int(d.month) >= 10 else int(d.year - 1)


def _month_floor(d: _dt.date) -> _dt.date:
    return _dt.date(int(d.year), int(d.month), 1)


def _add_one_month(d: _dt.date) -> _dt.date:
    y = int(d.year)
    m = int(d.month) + 1
    if m == 13:
        return _dt.date(y + 1, 1, 1)
    return _dt.date(y, m, 1)


def run_monthly_watch_and_stats_checkpoints(
    db_path: str,
    *,
    from_date: str,
    to_date: str,
    min_inclusion_prob: float = 0.35,
) -> Dict[str, Any]:
    """Run monthly checkpoints between two dates (inclusive, month-granular).

    For each month period_key in [from_date..to_date]:
      1) recompute_college_monthly_stats_snapshot(season_year inferred from month)
      2) recompute_draft_watch_run(draft_year=season_year+1, season_year=season_year, force=True)

    Months outside COLLEGE_GAMES_BY_CHECKPOINT_MONTH are skipped (no-op).
    """
    try:
        d0 = _dt.date.fromisoformat(str(from_date)[:10])
        d1 = _dt.date.fromisoformat(str(to_date)[:10])
    except Exception as e:
        raise ValueError("from_date/to_date must be ISO YYYY-MM-DD") from e

    if d1 < d0:
        d0, d1 = d1, d0

    cur = _month_floor(d0)
    end = _month_floor(d1)

    handled: List[Dict[str, Any]] = []
    skipped_months: List[str] = []

    while cur <= end:
        pk = f"{cur.year:04d}-{cur.month:02d}"
        # Only run for defined checkpoint months (others are no-op)
        if int(cur.month) not in getattr(config, "COLLEGE_GAMES_BY_CHECKPOINT_MONTH", {}):
            skipped_months.append(pk)
            cur = _add_one_month(cur)
            continue

        sy = _infer_season_year_from_date(cur)
        dy = int(sy + 1)
        as_of = f"{pk}-01"

        snap = recompute_college_monthly_stats_snapshot(
            db_path,
            season_year=int(sy),
            period_key=pk,
            as_of_date=as_of,
            force=False,
        )

        watch = recompute_draft_watch_run(
            db_path,
            draft_year=int(dy),
            as_of_date=as_of,
            period_key=pk,
            season_year=int(sy),
            min_inclusion_prob=float(min_inclusion_prob),
            force=True,  # ensure watch always reflects the latest snapshot stats
        )

        handled.append(
            {
                "period_key": pk,
                "season_year": int(sy),
                "draft_year": int(dy),
                "snapshot": snap,
                "watch": watch,
            }
        )

        cur = _add_one_month(cur)

    return {
        "ok": True,
        "from_date": str(from_date),
        "to_date": str(to_date),
        "handled": handled,
        "skipped_months": skipped_months,
    }


# ----------------------------
# draft watch snapshots (pre-declaration)
# ----------------------------

def recompute_draft_watch_run(
    db_path: str,
    *,
    draft_year: int,
    as_of_date: str,
    period_key: Optional[str] = None,
    season_year: Optional[int] = None,
    min_inclusion_prob: float = 0.35,
    force: bool = False,
) -> Dict[str, object]:
    """
    Create (or fetch) a monthly "watch" snapshot run for a draft year.

    This computes declare probability for eligible college players and persists:
      - draft_watch_runs (run metadata)
      - draft_watch_probs (per player probabilities + decision trace JSON)

    Idempotent:
      - If a run for (draft_year, period_key) already exists and force=False, this returns it.
      - If force=True, it rewrites the run + all probabilities for that period.
    """
    dy = int(draft_year)
    as_of = str(as_of_date or "")
    if len(as_of) < 7:
        raise ValueError(f"as_of_date must be YYYY-MM-DD (or at least YYYY-MM): got {as_of_date!r}")

    pk = str(period_key or as_of[:7])  # YYYY-MM
    if len(pk) != 7 or pk[4] != "-":
        raise ValueError(f"period_key must be YYYY-MM: got {pk!r}")

    run_id = f"DY{dy}@{pk}"
    sy = int(season_year) if season_year is not None else int(dy - 1)
    prob_floor = float(min_inclusion_prob)
    now = game_time.now_utc_like_iso()

    with LeagueRepo(db_path) as repo:
        repo.init_db()

        # If already computed for this month, return it (unless forced)
        row = repo._conn.execute(
            """
            SELECT run_id, as_of_date, season_year, min_inclusion_prob, created_at
            FROM draft_watch_runs
            WHERE draft_year=? AND period_key=?
            LIMIT 1;
            """,
            (dy, pk),
        ).fetchone()
        if row and not bool(force):
            return {
                "ok": True,
                "draft_year": dy,
                "run_id": str(row[0]),
                "period_key": pk,
                "as_of_date": str(row[1]),
                "season_year_used": int(row[2]),
                "min_inclusion_prob": float(row[3] if row[3] is not None else 0.35),
                "created_at": str(row[4]),
                "skipped": True,
                "players_total": 0,
                "written": 0,
            }

        # Ensure class strength exists for this draft year (stable across runs)
        strength = get_or_create_class_strength(repo, draft_year=dy, seed_salt=f"watch@{pk}")

        # Load season stats map (same pattern used in finalize_season_and_generate_entries)
        rows = repo._conn.execute(
            "SELECT player_id, stats_json FROM college_player_season_stats WHERE season_year=?;",
            (sy,),
        ).fetchall()
        stats_by_pid: Dict[str, CollegeSeasonStats] = {}
        for pid, sjson in rows:
            d = json_loads(str(sjson)) or {}
            # stats_json may include a version key (__v); strip it to avoid dataclass field mismatch.
            if isinstance(d, dict):
                d.pop("__v", None)
            else:
                d = {}
            stats_by_pid[str(pid)] = CollegeSeasonStats(**d)

        players = _load_active_players(repo)

        # Compute relative draft ranks (eligible pool) for this watch run (P0-2 fix)
        rank_by_pid, eligible_n = _compute_relative_draft_ranks(
            players=players,
            stats_by_pid=stats_by_pid,
            class_strength=float(strength),
        )

        written = 0
        # Persist run + probabilities
        with repo.transaction() as cur:
            # Upsert the run metadata
            cur.execute(
                """
                INSERT INTO draft_watch_runs(
                    run_id, draft_year, period_key, as_of_date, season_year, min_inclusion_prob, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    draft_year=excluded.draft_year,
                    period_key=excluded.period_key,
                    as_of_date=excluded.as_of_date,
                    season_year=excluded.season_year,
                    min_inclusion_prob=excluded.min_inclusion_prob,
                    created_at=excluded.created_at;
                """,
                (run_id, dy, pk, as_of, sy, prob_floor, now),
            )

            # If forcing, rewrite all probabilities for this run_id
            if row and bool(force):
                cur.execute("DELETE FROM draft_watch_probs WHERE run_id=?;", (run_id,))

            for p in players:
                # Eligibility gate consistent with declaration generation
                if int(p.age) < config.MIN_DRAFT_ELIGIBLE_AGE:
                    continue

                potential_grade = str(p.attrs.get("Potential") or "C-")
                season_stats = stats_by_pid.get(p.player_id)

                rank = rank_by_pid.get(p.player_id)
                if rank is None:
                    # Defensive: should not happen if eligibility gate and rank computation match.
                    continue

                # Stable per-run RNG for reproducibility
                rng = random.Random(_stable_seed("draft_watch", run_id, p.player_id))

                trace = declare_probability(
                    rng,
                    player_id=p.player_id,
                    draft_year=dy,
                    ovr=int(p.ovr),
                    age=int(p.age),
                    class_year=int(p.class_year),
                    potential_grade=potential_grade,
                    season_stats=season_stats,
                    class_strength=float(strength),
                    projected_pick=int(rank),
                    eligible_pool_size=int(eligible_n),
                )

                cur.execute(
                    """
                    INSERT OR REPLACE INTO draft_watch_probs(
                        run_id, player_id, declare_prob, projected_pick, decision_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?);
                    """,
                    (
                        run_id,
                        trace.player_id,
                        float(trace.declare_prob),
                        int(trace.projected_pick) if trace.projected_pick is not None else None,
                        json_dumps(trace.to_json_dict()),
                        now,
                    ),
                )
                written += 1

        return {
            "ok": True,
            "draft_year": dy,
            "run_id": run_id,
            "period_key": pk,
            "as_of_date": as_of,
            "season_year_used": sy,
            "min_inclusion_prob": prob_floor,
            "created_at": now,
            "skipped": False,
            "players_total": int(len(players)),
            "written": int(written),
        }


# ----------------------------
# offseason advance (grade bump + freshmen)
# ----------------------------

def advance_offseason(db_path: str, from_season_year: int, to_season_year: int) -> None:
    """
    Advance college world from from_season_year -> to_season_year:
    - NOTE: DECLARED players are expected to be resolved *before* this step:
        * Underclass withdrawals (return-to-school) should flip status back to ACTIVE.
        * Post-draft undrafted should be routed to pro/retirement (not return-to-school).
    - Increment class_year (+ age)
    - Graduate/remove players beyond 4
    - Always add a fixed number of freshmen per team
    - After freshmen, if roster is still below a minimum threshold, top-up from 2nd/3rd year
    - Enforce a hard cap via trim (lowest OVR first)

    Idempotent safety:
      - Guard against double-running the same to_season_year.
    """
    fy = int(from_season_year)
    ty = int(to_season_year)
    if ty != fy + 1:
        # Keep strict to avoid accidental skipping (commercial stability)
        raise ValueError(f"advance_offseason expects consecutive years: {fy} -> {ty}")

    with LeagueRepo(db_path) as repo:
        repo.init_db()

        meta_key = f"college_advanced_to_{ty}"
        if _get_meta(repo, meta_key) == "1":
            return

        # Fail-loud guard: by this stage there should be no DECLARED players left.
        # (Withdrawals step returns underclassmen to ACTIVE; undrafted step removes/promotes.)
        # We prefer a loud error over silently "return everyone", because that can explode
        # declaration counts and breaks NBA-like realism.
        row = repo._conn.execute("SELECT COUNT(1) AS n FROM college_players WHERE status='DECLARED';").fetchone()
        declared_left = int(row["n"] if row is not None else 0)
        if declared_left > 0:
            raise ValueError(
                "college.advance_offseason: DECLARED players still exist. "
                "Run draft withdrawals + draft apply (with undrafted resolution) before advancing college offseason. "
                f"declared_left={declared_left} to_season_year={ty}"
            )

        # Load teams (ordered)
        teams = _load_teams(repo)
        team_ids = [t.college_team_id for t in teams]

        roster_cap = int(config.COLLEGE_ROSTER_SIZE)
        hard_cap = int(getattr(config, "OFFSEASON_HARD_CAP", roster_cap))
        freshmen_per_team = int(getattr(config, "OFFSEASON_FRESHMEN_PER_TEAM", 4))
        min_roster = int(getattr(config, "OFFSEASON_MIN_ROSTER", 14))

        if min_roster < 0:
            raise ValueError("OFFSEASON_MIN_ROSTER must be >= 0")
        if hard_cap <= 0:
            raise ValueError("OFFSEASON_HARD_CAP must be > 0")
        if min_roster > hard_cap:
            raise ValueError("OFFSEASON_MIN_ROSTER must be <= OFFSEASON_HARD_CAP")
        if freshmen_per_team < 0:
            raise ValueError("OFFSEASON_FRESHMEN_PER_TEAM must be >= 0")

        with repo.transaction() as cur:
            # (1) Progress class year + age for ACTIVE
            cur.execute(
                """
                UPDATE college_players
                SET class_year = class_year + 1,
                    age = age + 1
                WHERE status='ACTIVE';
                """
            )

            # (2) Graduate: remove those now beyond 4
            cur.execute("DELETE FROM college_players WHERE status='ACTIVE' AND class_year > 4;")

            # (3) Safety trim (pre-add): if any team is over hard_cap, trim lowest OVR first.
            for tid in team_ids:
                row = repo._conn.execute(
                    "SELECT COUNT(*) FROM college_players WHERE status='ACTIVE' AND college_team_id=?;",
                    (tid,),
                ).fetchone()
                total = int(row[0] or 0)
                if total <= hard_cap:
                    continue
                excess = int(total - hard_cap)
                pid_rows = repo._conn.execute(
                    """
                    SELECT player_id
                    FROM college_players
                    WHERE status='ACTIVE' AND college_team_id=?
                    ORDER BY ovr ASC, player_id ASC
                    LIMIT ?;
                    """,
                    (tid, excess),
                ).fetchall()
                pids = [str(r[0]) for r in pid_rows]
                for pid in pids:
                    cur.execute("DELETE FROM college_player_season_stats WHERE player_id=?;", (pid,))
                    cur.execute("DELETE FROM college_players WHERE player_id=?;", (pid,))

            # (4) Aggregate current ACTIVE totals by team (after bump+graduation+pre-trim)
            rows = repo._conn.execute(
                """
                SELECT college_team_id, COUNT(*) AS cnt
                FROM college_players
                WHERE status='ACTIVE'
                GROUP BY college_team_id;
                """
            ).fetchall()

            total_by_team: Dict[str, int] = {tid: 0 for tid in team_ids}
            for team_id, cnt in rows:
                total_by_team[str(team_id)] = int(cnt)

            # class_strength cache by expected draft_year (= entry_season_year + 1)
            strength_cache: Dict[int, float] = {}

            def _strength_for_entry(entry_season_year: int, *, seed_tag: str) -> float:
                dy = int(entry_season_year) + 1
                v = strength_cache.get(dy)
                if v is None:
                    v = float(get_or_create_class_strength(repo, draft_year=dy, seed_salt=f"{seed_tag}@{ty}", cur=cur))
                    strength_cache[dy] = v
                return float(v)

            # (6) Build offseason additions:
            used_name_keys = _collect_used_name_keys(repo, cur=cur)
            #  - Always add freshmen_per_team of class_year=1 (entry=ty)
            #  - After freshmen, if total < min_roster, top-up from class_year=2 then 3:
            #       d = min_roster - (total + freshmen_per_team)
            #       add3 = d // 2
            #       add2 = d - add3
            tmp_new: List[CollegePlayer] = []

            for tid in team_ids:
                pre_total = int(total_by_team.get(tid, 0))

                # 6-A) Freshmen (always)
                if freshmen_per_team > 0:
                    rng_f = random.Random(_stable_seed("college_offseason_freshmen", ty, tid))
                    cs_f = _strength_for_entry(ty, seed_tag="freshmen")
                    tmp_new.extend(
                        generate_players_for_team_class(
                            rng_f,
                            college_team_id=tid,
                            class_year=1,
                            entry_season_year=ty,
                            class_strength=cs_f,
                            count=freshmen_per_team,
                            used_name_keys=used_name_keys,
                        )
                    )

                # 6-B) Top-up if still below min_roster after freshmen
                post_fresh_total = pre_total + int(freshmen_per_team)
                if post_fresh_total < min_roster:
                    d = int(min_roster - post_fresh_total)
                    add3 = int(d // 2)
                    add2 = int(d - add3)

                    # Sophomores first (entry=ty-1)
                    if add2 > 0:
                        rng_2 = random.Random(_stable_seed("college_offseason_topup", ty, tid, 2))
                        cs_2 = _strength_for_entry(ty - 1, seed_tag="topup2")
                        tmp_new.extend(
                            generate_players_for_team_class(
                                rng_2,
                                college_team_id=tid,
                                class_year=2,
                                entry_season_year=ty - 1,
                                class_strength=cs_2,
                                count=add2,
                                used_name_keys=used_name_keys,
                            )
                        )

                    # Then juniors (entry=ty-2)
                    if add3 > 0:
                        rng_3 = random.Random(_stable_seed("college_offseason_topup", ty, tid, 3))
                        cs_3 = _strength_for_entry(ty - 2, seed_tag="topup3")
                        tmp_new.extend(
                            generate_players_for_team_class(
                                rng_3,
                                college_team_id=tid,
                                class_year=3,
                                entry_season_year=ty - 2,
                                class_strength=cs_3,
                                count=add3,
                                used_name_keys=used_name_keys,
                            )
                        )

            # (7) Insert new players (single id allocation for collision safety)
            if tmp_new:
                new_ids = allocate_player_ids(repo, count=len(tmp_new), cur=cur)
                for pid, p in zip(new_ids, tmp_new):
                    cur.execute(
                        """
                        INSERT INTO college_players(
                            player_id, college_team_id, class_year, entry_season_year, status,
                            name, pos, age, height_in, weight_lb, ovr, attrs_json
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                        """,
                        (
                            pid,
                            p.college_team_id,
                            int(p.class_year),
                            int(p.entry_season_year),
                            "ACTIVE",
                            p.name,
                            p.pos,
                            int(p.age),
                            int(p.height_in),
                            int(p.weight_lb),
                            int(p.ovr),
                            json_dumps(p.attrs),
                        ),
                    )

            # (7.5) Final hard-cap trim AFTER additions (trim lowest OVR first)
            for tid in team_ids:
                row = repo._conn.execute(
                    "SELECT COUNT(*) FROM college_players WHERE status='ACTIVE' AND college_team_id=?;",
                    (tid,),
                ).fetchone()
                total = int(row[0] or 0)
                if total <= hard_cap:
                    continue
                excess = int(total - hard_cap)
                pid_rows = repo._conn.execute(
                    """
                    SELECT player_id
                    FROM college_players
                    WHERE status='ACTIVE' AND college_team_id=?
                    ORDER BY ovr ASC, player_id ASC
                    LIMIT ?;
                    """,
                    (tid, excess),
                ).fetchall()
                for (pid,) in pid_rows:
                    cur.execute("DELETE FROM college_player_season_stats WHERE player_id=?;", (str(pid),))
                    cur.execute("DELETE FROM college_players WHERE player_id=?;", (str(pid),))

            _set_meta(repo, meta_key, "1", cur=cur)


# ----------------------------
# draft promotion cleanup
# ----------------------------

def remove_drafted_player(db_path: str, player_id: str) -> None:
    """
    Called at the moment a player is drafted into NBA.
    College stats are ephemeral by design; we remove college-side records.

    This prevents duplicated player records and keeps DB lean.
    """
    pid = str(player_id)
    with LeagueRepo(db_path) as repo:
        repo.init_db()
        with repo.transaction() as cur:
            cur.execute("DELETE FROM college_player_season_stats WHERE player_id=?;", (pid,))
            cur.execute("DELETE FROM college_draft_entries WHERE player_id=?;", (pid,))
            cur.execute("DELETE FROM college_players WHERE player_id=?;", (pid,))
