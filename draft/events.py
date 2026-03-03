from __future__ import annotations

"""draft.events

Draft Combine / Team Workouts (DB-backed).

목적(게임 체감):
- 대학 스탯/레팅만으로 보이는 드래프트 평가를, 현실 NBA처럼
  '컴바인 측정/드릴' + '팀 워크아웃(스킬/인터뷰/메디컬 인상)'으로 한 단계 더 입체화.
- 개발 단계에서는 고품질 경기 시뮬이 없어도, 레이팅(2K SSOT)과 피지컬(키/몸무게/포지션)을
  기반으로 현실적인 분포를 생성해 UI/스카우팅에 활용할 수 있게 한다.

SSOT:
- draft_combine_results (draft_year, prospect_temp_id) -> result_json
- draft_workout_results (draft_year, team_id, prospect_temp_id) -> result_json

Idempotency:
- 이미 결과가 존재하면 같은 key에 대해 다시 생성하지 않는다(ON CONFLICT DO NOTHING).
  (개발/테스트에서 버튼을 여러 번 눌러도 결과가 바뀌지 않게)

Notes:
- prospect_temp_id == college player_id (draft.pool.load_pool_from_db와 동일)
- 이 모듈은 '선언자(= college_draft_entries)'가 존재해야 동작한다.
"""

import json
import math
import random
import sqlite3
import zlib

import game_time
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .types import norm_team_id


# ----------------------------
# JSON helpers
# ----------------------------

def _json_loads(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str)


def _clamp(x: float, lo: float, hi: float) -> float:
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x


def _pct(v: float) -> str:
    return f"{v:.1f}%"


def _stable_u32(*parts: Any) -> int:
    """Stable 32-bit hash for deterministic RNG seeding.

    Do NOT use Python's built-in hash() here because it is process-randomized.
    """
    s = "|".join(str(p) for p in parts)
    return int(zlib.crc32(s.encode("utf-8")) & 0xFFFFFFFF)


# ----------------------------
# Ratings helpers
# ----------------------------

def _r(attrs: Mapping[str, Any], key: str, default: float = 50.0) -> float:
    try:
        v = attrs.get(key, default)
        if v is None:
            return float(default)
        return float(v)
    except Exception:
        return float(default)


def _pos_bucket(pos: str) -> str:
    p = str(pos or "").upper().strip()
    if p in {"PG", "SG"}:
        return "G"
    if p in {"SF"}:
        return "W"
    if p in {"PF", "C"}:
        return "B"
    # fallback: first char heuristic
    if p.startswith("P") or p.startswith("S"):
        return "G"
    if p.startswith("C"):
        return "B"
    return "W"


# ----------------------------
# Prospect loading (declared entries)
# ----------------------------

def _load_declared_prospects(db_path: str, draft_year: int) -> List[Dict[str, Any]]:
    from league_repo import LeagueRepo

    dy = int(draft_year)
    sql = """
SELECT
  e.player_id       AS prospect_temp_id,
  e.declared_at     AS declared_at,
  e.decision_json   AS decision_json,
  p.name            AS name,
  p.pos             AS pos,
  p.age             AS age,
  p.height_in       AS height_in,
  p.weight_lb       AS weight_lb,
  p.ovr             AS ovr,
  p.attrs_json      AS attrs_json
FROM college_draft_entries e
JOIN college_players p
  ON p.player_id = e.player_id
WHERE e.draft_year = ?
ORDER BY p.ovr DESC, p.age ASC, e.player_id ASC;
""".strip()

    with LeagueRepo(str(db_path)) as repo:
        repo.init_db()
        rows = repo._conn.execute(sql, (dy,)).fetchall()

    out: List[Dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        d["prospect_temp_id"] = str(d.get("prospect_temp_id") or "").strip()
        if not d["prospect_temp_id"]:
            continue
        out.append(d)

    if not out:
        raise ValueError(
            f"No declared prospects found for draft_year={dy}. "
            f"Run college.finalize_season_and_generate_entries(season_year={dy-1}, draft_year={dy}) first."
        )
    return out


# ----------------------------
# Combine model (measurements + drills)
# ----------------------------

def _normal(rng: random.Random, mu: float, sigma: float) -> float:
    # Box-Muller (avoid numpy)
    u1 = max(1e-9, rng.random())
    u2 = max(1e-9, rng.random())
    z = math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)
    return mu + z * sigma


def _combine_measurements(
    rng: random.Random,
    *,
    pos: str,
    age: int,
    height_in: int,
    weight_lb: int,
    attrs: Mapping[str, Any],
) -> Dict[str, Any]:
    b = _pos_bucket(pos)

    height_shoes = int(height_in)
    height_noshoes = int(round(float(height_in) - _clamp(_normal(rng, 1.0, 0.35), 0.3, 1.8)))

    # Wingspan (in): position-dependent offset + small skill/athletic nudges
    base_extra = 3.0 if b == "G" else (5.0 if b == "W" else 4.0)
    block = _r(attrs, "Block", 50.0)
    reb = (_r(attrs, "Defensive Rebound", 50.0) + _r(attrs, "Offensive Rebound", 50.0)) * 0.5
    extra = base_extra + 0.02 * (block - 50.0) + 0.01 * (reb - 50.0) + _normal(rng, 0.0, 1.1)
    wingspan = int(round(_clamp(float(height_noshoes) + extra, float(height_noshoes) + 1.0, float(height_noshoes) + 10.0)))

    # Standing reach: height + long-arm contribution (roughly 24~30 in offset)
    reach_offset = 24.0 + 0.45 * float(wingspan - height_noshoes) + (1.5 if b == "B" else 0.0)
    standing_reach = int(round(_clamp(float(height_noshoes) + reach_offset + _normal(rng, 0.0, 0.9), 95.0, 115.0)))

    # Hand size (in)
    hand_length = _clamp(7.7 + 0.05 * float(height_noshoes - 72) + _normal(rng, 0.0, 0.25), 7.0, 10.5)
    hand_width = _clamp(8.2 + 0.04 * float(height_noshoes - 72) + _normal(rng, 0.0, 0.25), 7.5, 11.5)

    # Body fat: crude size-based estimate (3~18%)
    expected_w = 160.0 + 4.0 * float(height_noshoes - 60)  # 78in -> 232lb
    delta = float(weight_lb) - expected_w
    body_fat = _clamp(6.0 + 0.035 * delta + _normal(rng, 0.0, 1.0), 3.0, 18.0)

    return {
        "age": int(age),
        "height_shoes_in": int(height_shoes),
        "height_noshoes_in": int(height_noshoes),
        "weight_lb": int(weight_lb),
        "wingspan_in": int(wingspan),
        "standing_reach_in": int(standing_reach),
        "hand_length_in": round(float(hand_length), 2),
        "hand_width_in": round(float(hand_width), 2),
        "body_fat_pct": round(float(body_fat), 2),
    }


def _combine_drills(
    rng: random.Random,
    *,
    pos: str,
    height_in: int,
    weight_lb: int,
    attrs: Mapping[str, Any],
) -> Dict[str, Any]:

    speed = _r(attrs, "Speed", 50.0)
    agil = _r(attrs, "Agility", 50.0)
    vert = _r(attrs, "Vertical", 50.0)
    strength = _r(attrs, "Strength", 50.0)

    # Size penalty (bigs are slower on average)
    size = (float(height_in) - 78.0) / 12.0 + (float(weight_lb) - 210.0) / 60.0
    size = _clamp(size, -1.0, 2.0)

    # 3/4 court sprint: ~3.1 to 3.7
    sprint = 3.70 - 0.006 * (speed - 50.0) + 0.030 * size + _normal(rng, 0.0, 0.030)
    sprint = _clamp(sprint, 3.05, 3.75)

    # Lane agility: ~10.3 to 12.5
    lane = 11.70 - 0.035 * (agil - 50.0) + 0.120 * size + _normal(rng, 0.0, 0.080)
    lane = _clamp(lane, 10.10, 12.80)

    # Shuttle: ~2.9 to 3.8
    shuttle = 3.40 - 0.010 * (agil - 50.0) + 0.040 * size + _normal(rng, 0.0, 0.045)
    shuttle = _clamp(shuttle, 2.85, 3.95)

    # Vertical: stand/max
    vert_max = 28.0 + 0.25 * (vert - 50.0) - 1.2 * size + _normal(rng, 0.0, 1.0)
    vert_max = _clamp(vert_max, 20.0, 46.0)
    stand_gap = _clamp(_normal(rng, 5.2, 1.0), 3.0, 8.0)
    vert_stand = _clamp(vert_max - stand_gap, 18.0, 40.0)

    # Bench reps: modern NBA doesn't always do this, but it's a useful proxy.
    reps = 2.0 + 0.22 * (strength - 50.0) + _normal(rng, 0.0, 1.2)
    reps = int(round(_clamp(reps, 0.0, 27.0)))

    # Athletic score (0..100-ish)
    athletic = 50.0 + 0.42 * (speed - 50.0) + 0.38 * (agil - 50.0) + 0.28 * (vert - 50.0) + 0.18 * (strength - 50.0) - 6.0 * size
    athletic += _normal(rng, 0.0, 3.0)
    athletic = _clamp(athletic, 1.0, 99.0)

    return {
        "sprint_3q_sec": round(float(sprint), 3),
        "lane_agility_sec": round(float(lane), 3),
        "shuttle_sec": round(float(shuttle), 3),
        "vertical_max_in": round(float(vert_max), 1),
        "vertical_standing_in": round(float(vert_stand), 1),
        "bench_reps_185": int(reps),
        "athletic_score": round(float(athletic), 1),
    }


def _grade_letter(score: float) -> str:
    # Simple A+..F mapping tuned for UI readability
    s = float(score)
    if s >= 92:
        return "A+"
    if s >= 88:
        return "A"
    if s >= 84:
        return "A-"
    if s >= 80:
        return "B+"
    if s >= 76:
        return "B"
    if s >= 72:
        return "B-"
    if s >= 68:
        return "C+"
    if s >= 64:
        return "C"
    if s >= 60:
        return "C-"
    if s >= 55:
        return "D+"
    if s >= 50:
        return "D"
    return "F"


# ----------------------------
# Public API
# ----------------------------

def run_combine(db_path: str, draft_year: int, *, rng_seed: Optional[int] = None) -> Dict[str, Any]:
    """Generate combine results for declared prospects and persist them to DB.

    Returns a small summary dict suitable for UI/debugging:
      {"draft_year": ..., "count": inserted_or_existing, "inserted": ..., "skipped": ..., "summary": {...}}
    """
    from league_repo import LeagueRepo

    dy = int(draft_year)
    prospects = _load_declared_prospects(str(db_path), dy)

    seed = int(rng_seed) if rng_seed is not None else (dy * 1009 + 17)

    now = game_time.now_utc_like_iso()
    inserted = 0
    skipped = 0
    athletic_scores: List[float] = []

    sql_ins = """
INSERT INTO draft_combine_results(draft_year, prospect_temp_id, result_json, created_at, updated_at)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT(draft_year, prospect_temp_id) DO NOTHING;
""".strip()

    with LeagueRepo(str(db_path)) as repo:
        repo.init_db()
        with repo.transaction() as cur:
            # Ensure table exists (helpful error in dev stage)
            try:
                cur.execute("SELECT 1 FROM draft_combine_results LIMIT 1;")
            except sqlite3.OperationalError as e:
                raise RuntimeError("draft_combine_results table missing. Apply db_schema/draft.py patch first.") from e

            for p in prospects:
                tid = str(p.get("prospect_temp_id") or "").strip()
                if not tid:
                    continue

                attrs = _json_loads(p.get("attrs_json"), default={})
                if not isinstance(attrs, dict):
                    attrs = {}

                # Per-prospect deterministic RNG (do not depend on insertion order).
                prng = random.Random(_stable_u32("combine_v1", seed, dy, tid))

                meas = _combine_measurements(
                    prng,
                    pos=str(p.get("pos") or "G"),
                    age=int(p.get("age") or 19),
                    height_in=int(p.get("height_in") or 78),
                    weight_lb=int(p.get("weight_lb") or 210),
                    attrs=attrs,
                )
                drills = _combine_drills(
                    prng,
                    pos=str(p.get("pos") or "G"),
                    height_in=int(p.get("height_in") or 78),
                    weight_lb=int(p.get("weight_lb") or 210),
                    attrs=attrs,
                )

                athletic_scores.append(float(drills.get("athletic_score") or 0.0))

                payload = {
                    "draft_year": dy,
                    "prospect_temp_id": tid,
                    "name": str(p.get("name") or "Unknown"),
                    "pos": str(p.get("pos") or "G"),
                    "generated": {
                        "seed": int(seed),
                        "generated_at": now,
                        "model": "combine_v1",
                    },
                    "measurements": meas,
                    "drills": drills,
                    "grades": {
                        "athletic": _grade_letter(float(drills.get("athletic_score") or 0.0)),
                    },
                }

                cur.execute(sql_ins, (dy, tid, _json_dumps(payload), now, now))
                if cur.rowcount and int(cur.rowcount) > 0:
                    inserted += 1
                else:
                    skipped += 1

    summary: Dict[str, Any] = {}
    if athletic_scores:
        athletic_scores_sorted = sorted(athletic_scores)
        n = len(athletic_scores_sorted)
        summary = {
            "athletic_avg": round(sum(athletic_scores_sorted) / float(n), 2),
            "athletic_p10": round(athletic_scores_sorted[max(0, int(0.10 * (n - 1)))], 2),
            "athletic_p50": round(athletic_scores_sorted[max(0, int(0.50 * (n - 1)))], 2),
            "athletic_p90": round(athletic_scores_sorted[max(0, int(0.90 * (n - 1)))], 2),
        }

    return {
        "ok": True,
        "draft_year": dy,
        "count": int(len(prospects)),
        "inserted": int(inserted),
        "skipped": int(skipped),
        "summary": summary,
    }



def ensure_combine_results(
    db_path: str,
    draft_year: int,
    *,
    rng_seed: Optional[int] = None,
) -> Dict[str, Any]:
    """Ensure combine results exist for all declared prospects.

    This is intended for "public" flows (e.g., draft bundle view) where combine is
    league-wide and should be available without requiring a separate button press.
    """
    from league_repo import LeagueRepo

    dy = int(draft_year)
    try:
        prospects = _load_declared_prospects(str(db_path), dy)
    except ValueError:
        return {"ok": True, "draft_year": dy, "expected": 0, "existing": 0, "already_complete": True}
    expected = int(len(prospects))

    sql_cnt = "SELECT COUNT(1) AS n FROM draft_combine_results WHERE draft_year = ?;"
    with LeagueRepo(str(db_path)) as repo:
        repo.init_db()
        try:
            row = repo._conn.execute(sql_cnt, (dy,)).fetchone()
            existing = int(row["n"] if row is not None else 0)
        except sqlite3.OperationalError as e:
            raise RuntimeError("draft_combine_results table missing. Apply db_schema/draft.py patch first.") from e

    if existing >= expected and expected > 0:
        return {"ok": True, "draft_year": dy, "expected": expected, "existing": existing, "already_complete": True}

    res = run_combine(str(db_path), dy, rng_seed=rng_seed)
    return {"ok": True, "draft_year": dy, "expected": expected, "existing": existing, "already_complete": False, "combine": res}


def _load_combine_map(db_path: str, draft_year: int) -> Dict[str, Dict[str, Any]]:
    """Load combine results keyed by prospect_temp_id. Missing is allowed."""
    from league_repo import LeagueRepo

    dy = int(draft_year)
    sql = """
SELECT prospect_temp_id, result_json
FROM draft_combine_results
WHERE draft_year = ?;
""".strip()

    out: Dict[str, Dict[str, Any]] = {}
    with LeagueRepo(str(db_path)) as repo:
        repo.init_db()
        try:
            rows = repo._conn.execute(sql, (dy,)).fetchall()
        except sqlite3.OperationalError:
            return out
    for r in rows:
        pid = str(r["prospect_temp_id"] or "")
        payload = _json_loads(r["result_json"], default={})
        if isinstance(payload, dict) and pid:
            # Defensive: legacy payloads may have included sensitive keys.
            if "ovr" in payload:
                payload = dict(payload)
                payload.pop("ovr", None)
            out[pid] = payload
    return out


def run_workouts(
    db_path: str,
    draft_year: int,
    *,
    team_id: str,
    invited_prospect_temp_ids: Sequence[str],
    max_invites: int,
    rng_seed: Optional[int] = None,
) -> Dict[str, Any]:
    """Generate workouts ONLY for the specified team and invited prospects."""
    from league_repo import LeagueRepo

    dy = int(draft_year)
    prospects = _load_declared_prospects(str(db_path), dy)
    combine_map = _load_combine_map(str(db_path), dy)

    t = norm_team_id(team_id)
    if not t:
        raise ValueError(f"invalid team_id: {team_id}")

    mi = int(max_invites)
    if mi <= 0 or mi > 60:
        raise ValueError(f"max_invites out of range (1..60): {mi}")

    raw = [str(x or "").strip() for x in (invited_prospect_temp_ids or []) if str(x or "").strip()]
    seen: set[str] = set()
    invited: List[str] = []
    for pid in raw:
        if pid in seen:
            continue
        invited.append(pid)
        seen.add(pid)
    if not invited:
        return {"ok": True, "draft_year": dy, "team_id": t, "skipped": True, "reason": "NO_INVITES", "count": 0, "inserted": 0, "skipped_existing": 0, "missing_invites": [], "trimmed_from": None}

    trimmed_from: Optional[int] = None
    if len(invited) > mi:
        trimmed_from = len(invited)
        invited = invited[:mi]

    by_id: Dict[str, Dict[str, Any]] = {}
    declared_ids: set[str] = set()
    for p in prospects:
        pid = str(p.get("prospect_temp_id") or "").strip()
        if pid:
            declared_ids.add(pid)
            by_id[pid] = p

    missing_invites: List[str] = []
    targets: List[str] = []
    for pid in invited:
        if pid in declared_ids:
            targets.append(pid)
        else:
            missing_invites.append(pid)
    if not targets:
        return {"ok": True, "draft_year": dy, "team_id": t, "skipped": True, "reason": "NO_VALID_INVITES", "count": 0, "inserted": 0, "skipped_existing": 0, "missing_invites": missing_invites, "trimmed_from": trimmed_from}

    seed = int(rng_seed) if rng_seed is not None else (dy * 2027 + 31)
    now = game_time.now_utc_like_iso()
    inserted = 0
    skipped_existing = 0

    sql_ins = """
INSERT INTO draft_workout_results(draft_year, team_id, prospect_temp_id, result_json, created_at, updated_at)
VALUES (?, ?, ?, ?, ?, ?)
ON CONFLICT(draft_year, team_id, prospect_temp_id) DO NOTHING;
""".strip()

    with LeagueRepo(str(db_path)) as repo:
        repo.init_db()
        with repo.transaction() as cur:
            try:
                cur.execute("SELECT 1 FROM draft_workout_results LIMIT 1;")
            except sqlite3.OperationalError as e:
                raise RuntimeError("draft_workout_results table missing. Apply db_schema/draft.py patch first.") from e

            for pid in targets:
                p = by_id.get(pid)
                if not p:
                    continue
                attrs = _json_loads(p.get("attrs_json"), default={})
                if not isinstance(attrs, dict):
                    attrs = {}
                derived_seed = _stable_u32("workout_v1", seed, dy, t, pid)
                prng = random.Random(derived_seed)

                shoot = (_r(attrs, "Three-Point Shot") * 0.55 + _r(attrs, "Mid-Range Shot") * 0.25 + _r(attrs, "Free Throw") * 0.20)
                handle = (_r(attrs, "Ball Handle") * 0.45 + _r(attrs, "Pass Accuracy") * 0.25 + _r(attrs, "Pass Vision") * 0.15 + _r(attrs, "Speed with Ball") * 0.15)
                hustle = _r(attrs, "Hustle")
                stamina = _r(attrs, "Stamina")
                defense = (
                    _r(attrs, "Perimeter Defense") * 0.33
                    + _r(attrs, "Interior Defense") * 0.19
                    + _r(attrs, "Steal") * 0.19
                    + _r(attrs, "Block") * 0.14
                    + _r(attrs, "Help Defense IQ") * 0.10
                    + hustle * 0.03
                    + stamina * 0.02
                )
                durability = _r(attrs, "Overall Durability")
                injury_freq = _clamp(_r(attrs, "I_InjuryFreq", 5.0), 1.0, 10.0)
                comb = combine_map.get(pid) or {}
                comb_drills = (comb.get("drills") or {}) if isinstance(comb.get("drills"), dict) else {}
                athletic = float(comb_drills.get("athletic_score") or 50.0)
                medical_risk_base = 55.0 - 0.55 * (durability - 50.0)
                medical_risk = medical_risk_base + 3.5 * (injury_freq - 5.5) + _normal(prng, 0.0, 8.0)
                medical_risk = _clamp(medical_risk, 0.0, 100.0)
                overall = 0.35 * shoot + 0.275 * handle + 0.25 * defense + 0.125 * athletic
                overall += _normal(prng, 0.0, 4.0)
                overall = _clamp(overall, 0.0, 100.0)
                notes: List[str] = []
                if shoot >= 85 and _normal(prng, 0.0, 1.0) > -0.2: notes.append("Shooting pop in drills")
                if defense >= 82 and _normal(prng, 0.0, 1.0) > -0.2: notes.append("Defensive motor stood out")
                if medical_risk >= 70 and _normal(prng, 0.0, 1.0) > -0.4: notes.append("Medical flagged (monitor)")
                if not notes: notes.append("Standard workout")

                payload = {
                    "draft_year": dy,
                    "team_id": t,
                    "prospect_temp_id": pid,
                    "name": str(p.get("name") or "Unknown"),
                    "pos": str(p.get("pos") or "G"),
                    "generated": {"seed": int(seed), "derived_seed": int(derived_seed), "generated_at": now, "model": "workout_v1"},
                    "scores": {
                        "shooting": round(float(_clamp(shoot + _normal(prng, 0.0, 3.0), 0.0, 100.0)), 1),
                        "ball_skills": round(float(_clamp(handle + _normal(prng, 0.0, 3.0), 0.0, 100.0)), 1),
                        "defense": round(float(_clamp(defense + _normal(prng, 0.0, 3.0), 0.0, 100.0)), 1),
                        "medical_risk": round(float(medical_risk), 1),
                        "overall": round(float(overall), 1),
                    },
                    "grades": {"overall": _grade_letter(float(overall)), "medical": _grade_letter(100.0 - float(medical_risk))},
                    "notes": notes[:4],
                }
                cur.execute(sql_ins, (dy, t, pid, _json_dumps(payload), now, now))
                if cur.rowcount and int(cur.rowcount) > 0:
                    inserted += 1
                else:
                    skipped_existing += 1

    return {"ok": True, "draft_year": dy, "team_id": t, "skipped": False, "reason": None, "count": int(len(targets)), "inserted": int(inserted), "skipped_existing": int(skipped_existing), "missing_invites": missing_invites, "trimmed_from": trimmed_from}

