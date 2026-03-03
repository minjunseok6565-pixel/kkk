from __future__ import annotations

"""draft.withdrawals

Implements a NBA-like "test the waters" withdrawal step for declared college prospects.

High-level behavior (P0-3 fix):
  - Only underclassmen (class_year 1..3) can withdraw and return to school.
  - Seniors (class_year 4) cannot withdraw.
  - Withdrawal decisions are driven by an updated draft stock estimate (soft probability),
    optionally incorporating combine/workout signals when present.

Persistence:
  - Deletes withdrawn players from `college_draft_entries` for that draft_year
  - Sets `college_players.status='ACTIVE'` for withdrawn players
  - Writes a trace row into `draft_withdrawals` (for tuning/telemetry)
  - Sets meta key `draft_withdrawals_done_{draft_year}` = "1" for idempotence

Design notes:
  - This step is intended to be called AFTER interviews and BEFORE selections.
  - Idempotent: safe to run multiple times; will no-op once meta key is set.
"""

import hashlib
import json
import math
import random
import sqlite3
from typing import Any, Dict, List, Mapping, Optional, Tuple

import game_time
from league_repo import LeagueRepo
from ratings_2k import potential_grade_to_scalar


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str)


def _json_loads(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        s = value.decode("utf-8") if isinstance(value, (bytes, bytearray)) else str(value)
        if not s:
            return default
        return json.loads(s)
    except Exception:
        return default


def _stable_u32(*parts: Any) -> int:
    s = ":".join(str(p) for p in parts)
    h = hashlib.blake2b(s.encode("utf-8"), digest_size=4).digest()
    return int.from_bytes(h, "big")


def _clamp(x: float, lo: float, hi: float) -> float:
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x


def _sigmoid(x: float) -> float:
    # Avoid overflow in exp for large values
    x = float(_clamp(float(x), -20.0, 20.0))
    return 1.0 / (1.0 + math.exp(-x))


def _prod_score(stats: Mapping[str, Any] | None) -> float:
    """Compact production score (college per-game) used only for ranking/withdrawal heuristics."""
    if not stats or not isinstance(stats, Mapping):
        return 0.0
    try:
        pts = float(stats.get("pts") or 0.0)
        reb = float(stats.get("reb") or 0.0)
        ast = float(stats.get("ast") or 0.0)
        stl = float(stats.get("stl") or 0.0)
        blk = float(stats.get("blk") or 0.0)
        tov = float(stats.get("tov") or 0.0)
        usg = float(stats.get("usg") or 0.0)
    except Exception:
        return 0.0

    prod = 0.35 * pts + 0.18 * reb + 0.22 * ast + 0.12 * stl + 0.10 * blk - 0.35 * tov
    prod += 3.0 * (usg - 0.18)
    return float(_clamp(prod, -8.0, 22.0))


def _potential_points_from_grade(potential_grade: str) -> int:
    g = str(potential_grade or "C-")
    scalar = float(potential_grade_to_scalar(g))
    pot_points = 60.0 + (scalar - 0.40) * (37.0 / 0.60)
    pot_points = float(_clamp(pot_points, 60.0, 97.0))
    return int(round(pot_points))


def _load_declared_rows(repo: LeagueRepo, *, draft_year: int, season_year: int) -> List[Dict[str, Any]]:
    sql = """
SELECT
  e.player_id              AS player_id,
  e.declared_at            AS declared_at,
  e.decision_json          AS decision_json,

  p.college_team_id        AS college_team_id,
  p.class_year             AS class_year,
  p.entry_season_year      AS entry_season_year,
  p.status                 AS status,

  p.name                   AS name,
  p.pos                    AS pos,
  p.age                    AS age,
  p.height_in              AS height_in,
  p.weight_lb              AS weight_lb,
  p.ovr                    AS ovr,
  p.attrs_json             AS attrs_json,

  ps.stats_json            AS stats_json
FROM college_draft_entries e
JOIN college_players p
  ON p.player_id = e.player_id
LEFT JOIN college_player_season_stats ps
  ON ps.player_id = e.player_id
 AND ps.season_year = ?
WHERE e.draft_year = ?
ORDER BY p.ovr DESC, p.player_id ASC;
""".strip()

    try:
        rows = repo._conn.execute(sql, (int(season_year), int(draft_year))).fetchall()
    except sqlite3.OperationalError:
        return []

    out: List[Dict[str, Any]] = []
    for r in rows:
        try:
            out.append(dict(r))
        except Exception:
            d: Dict[str, Any] = {}
            try:
                for k in getattr(r, "keys", lambda: [])():
                    d[str(k)] = r[k]
            except Exception:
                d = {}
            if d:
                out.append(d)
    return out


def _load_combine_athletic_map(repo: LeagueRepo, *, draft_year: int) -> Dict[str, float]:
    """Return prospect_temp_id -> athletic_score (default 50.0 when missing)."""
    sql = "SELECT prospect_temp_id, result_json FROM draft_combine_results WHERE draft_year = ?;"
    out: Dict[str, float] = {}
    try:
        rows = repo._conn.execute(sql, (int(draft_year),)).fetchall()
    except sqlite3.OperationalError:
        return out
    for r in rows:
        pid = str(r["prospect_temp_id"] or "").strip()
        if not pid:
            continue
        payload = _json_loads(r["result_json"], default={})
        if not isinstance(payload, dict):
            continue
        drills = payload.get("drills")
        if not isinstance(drills, dict):
            drills = {}
        try:
            athletic = float(drills.get("athletic_score") or 50.0)
        except Exception:
            athletic = 50.0
        out[pid] = float(_clamp(athletic, 0.0, 100.0))
    return out


def _load_workout_overall_avg_map(repo: LeagueRepo, *, draft_year: int) -> Dict[str, float]:
    """Return prospect_temp_id -> avg workout overall score across all teams (0..100)."""
    sql = "SELECT prospect_temp_id, result_json FROM draft_workout_results WHERE draft_year = ?;"
    sums: Dict[str, float] = {}
    cnts: Dict[str, int] = {}
    try:
        rows = repo._conn.execute(sql, (int(draft_year),)).fetchall()
    except sqlite3.OperationalError:
        return {}
    for r in rows:
        pid = str(r["prospect_temp_id"] or "").strip()
        if not pid:
            continue
        payload = _json_loads(r["result_json"], default={})
        if not isinstance(payload, dict):
            continue
        scores = payload.get("scores")
        if not isinstance(scores, dict):
            continue
        try:
            ov = float(scores.get("overall"))
        except Exception:
            continue
        sums[pid] = float(sums.get(pid, 0.0)) + float(ov)
        cnts[pid] = int(cnts.get(pid, 0)) + 1
    out: Dict[str, float] = {}
    for pid, s in sums.items():
        c = int(cnts.get(pid, 0))
        if c <= 0:
            continue
        out[pid] = float(_clamp(float(s) / float(c), 0.0, 100.0))
    return out


def _get_meta(repo: LeagueRepo, key: str) -> Optional[str]:
    row = repo._conn.execute("SELECT value FROM meta WHERE key=?;", (str(key),)).fetchone()
    if not row:
        return None
    try:
        return str(row["value"])
    except Exception:
        try:
            return str(row[0])
        except Exception:
            return None


def _set_meta(repo: LeagueRepo, key: str, value: str, *, cur: sqlite3.Cursor | None = None) -> None:
    sql = (
        "INSERT INTO meta(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value;"
    )
    if cur is not None:
        cur.execute(sql, (str(key), str(value)))
        return
    repo._conn.execute(sql, (str(key), str(value)))
    repo._conn.commit()


def run_withdrawals(
    db_path: str,
    draft_year: int,
    *,
    rng_seed: Optional[int] = None,
    min_remaining_declared: int = 80,
) -> Dict[str, Any]:
    """Execute the withdrawal step for a given draft year.

    Args:
        db_path: sqlite path
        draft_year: target draft year
        rng_seed: optional override seed
        min_remaining_declared: safety guard so withdrawals cannot shrink the pool below this.
                               (Must be >= 60 to keep the draft session feasible.)
    """
    dy = int(draft_year)
    if dy <= 0:
        raise ValueError(f"invalid draft_year: {draft_year}")

    min_rem = int(min_remaining_declared)
    if min_rem < 60:
        raise ValueError("min_remaining_declared must be >= 60")

    meta_key = f"draft_withdrawals_done_{dy}"
    season_year = dy - 1
    if season_year <= 0:
        raise ValueError(f"invalid inferred season_year: {season_year}")

    seed = int(rng_seed) if rng_seed is not None else (dy * 1109 + 19)
    now = game_time.now_utc_like_iso()

    with LeagueRepo(str(db_path)) as repo:
        repo.init_db()

        if _get_meta(repo, meta_key) == "1":
            # Idempotent early exit
            try:
                row = repo._conn.execute(
                    "SELECT COUNT(1) AS n FROM draft_withdrawals WHERE draft_year=?;",
                    (dy,),
                ).fetchone()
                withdrawn_n = int(row["n"] if row is not None else 0)
            except sqlite3.OperationalError:
                withdrawn_n = 0
            return {
                "ok": True,
                "draft_year": dy,
                "already_complete": True,
                "withdrawn": int(withdrawn_n),
            }

        declared_rows = _load_declared_rows(repo, draft_year=dy, season_year=season_year)
        declared_n = int(len(declared_rows))
        if declared_n <= 0:
            # Nothing to do; still mark step done to keep pipeline deterministic.
            with repo.transaction() as cur:
                _set_meta(repo, meta_key, "1", cur=cur)
            return {"ok": True, "draft_year": dy, "already_complete": False, "declared": 0, "withdrawn": 0}

        combine_ath = _load_combine_athletic_map(repo, draft_year=dy)
        workout_avg = _load_workout_overall_avg_map(repo, draft_year=dy)

        # Build updated stock score for ranking within the DECLARED pool.
        scored: List[Tuple[Tuple, Dict[str, Any]]] = []
        for r in declared_rows:
            pid = str(r.get("player_id") or "").strip()
            if not pid:
                continue

            try:
                class_year = int(r.get("class_year") or 1)
            except Exception:
                class_year = 1
            try:
                age = int(r.get("age") or 19)
            except Exception:
                age = 19
            try:
                ovr = int(r.get("ovr") or 60)
            except Exception:
                ovr = 60

            ratings = _json_loads(r.get("attrs_json"), default={})
            if not isinstance(ratings, dict):
                ratings = {}
            pot_grade = str(ratings.get("Potential") or "C-")
            pot_points = int(_potential_points_from_grade(pot_grade))

            season_stats = _json_loads(r.get("stats_json"), default=None)
            if season_stats is not None and not isinstance(season_stats, dict):
                season_stats = None
            prod = float(_prod_score(season_stats))

            athletic = float(combine_ath.get(pid, 50.0))
            wavg = workout_avg.get(pid)

            youth_bonus = float(_clamp(21 - float(age), -2.0, 3.0))
            class_penalty = 0.35 * float(max(0, class_year - 1))

            # Updated stock score (higher = better)
            score = (
                1.00 * (float(ovr) - 60.0)
                + 0.55 * (float(pot_points) - 70.0)
                + 0.55 * float(prod)
                + 0.75 * float(youth_bonus)
                - 0.45 * float(class_penalty)
            )

            # Combine/Workout influence (small but noticeable)
            score += 0.18 * ((float(athletic) - 50.0) / 10.0)
            if wavg is not None:
                score += 0.22 * ((float(wavg) - 50.0) / 10.0)

            # Stable tiebreak key
            sort_key = (-float(score), int(age), int(class_year), pid)
            scored.append((sort_key, {**r, "_score": float(score), "_pot_points": int(pot_points), "_prod": float(prod), "_athletic": float(athletic), "_workout_avg": (float(wavg) if wavg is not None else None)}))

        scored.sort(key=lambda t: t[0])
        ranked_rows = [row for _, row in scored]
        rank_by_pid: Dict[str, int] = {str(row["player_id"]): int(i + 1) for i, row in enumerate(ranked_rows) if row.get("player_id")}

        # Compute soft probabilities from rank (smooth around cutoffs).
        drafted_k = 8.0
        first_k = 6.0
        draft_slots = 60
        first_slots = 30

        # Candidate withdrawal decisions
        candidates: List[Dict[str, Any]] = []
        selected: List[Dict[str, Any]] = []
        for row in ranked_rows:
            pid = str(row.get("player_id") or "").strip()
            if not pid:
                continue
            try:
                class_year = int(row.get("class_year") or 1)
            except Exception:
                class_year = 1
            if class_year >= 4:
                continue  # seniors cannot withdraw

            rank = int(rank_by_pid.get(pid) or 9999)
            p_drafted = float(_sigmoid(((float(draft_slots) + 0.5) - float(rank)) / float(drafted_k)))
            p_first = float(_sigmoid(((float(first_slots) + 0.5) - float(rank)) / float(first_k)))
            if p_first > p_drafted:
                p_first = float(p_drafted)

            # Withdrawal probability model:
            # - lower p_drafted => more likely to return
            # - younger class => more likely to return
            # - slight noise for realism
            base = -1.05
            class_term = 0.60 * float(3 - int(class_year))
            draft_term = 3.60 * float(0.45 - float(p_drafted))
            first_term = 1.40 * float(0.12 - float(p_first))

            prng = random.Random(_stable_u32("withdraw_v1", seed, dy, pid))
            noise = float(prng.normalvariate(0.0, 0.22))
            logit = float(base + class_term + draft_term + first_term + noise)
            p_withdraw = float(_sigmoid(logit))

            cand = {
                "player_id": pid,
                "class_year": int(class_year),
                "rank_declared": int(rank),
                "p_drafted": float(p_drafted),
                "p_first_round": float(p_first),
                "withdraw_prob": float(p_withdraw),
                "logit": float(logit),
                "score": float(row.get("_score") or 0.0),
                "pot_points": int(row.get("_pot_points") or 0),
                "prod": float(row.get("_prod") or 0.0),
                "combine_athletic": float(row.get("_athletic") or 50.0),
                "workout_overall_avg": row.get("_workout_avg"),
            }
            candidates.append(cand)

            if prng.random() < float(p_withdraw):
                selected.append(cand)

        # Safety: do not shrink declared pool below min_rem.
        min_rem_eff = int(min_rem)
        if min_rem_eff < 60:
            min_rem_eff = 60
        max_withdraw = max(0, int(declared_n - min_rem_eff))
        if max_withdraw <= 0:
            selected = []
        elif len(selected) > max_withdraw:
            selected.sort(key=lambda d: (-float(d.get("withdraw_prob") or 0.0), int(d.get("rank_declared") or 9999), str(d.get("player_id") or "")))
            selected = selected[:max_withdraw]

        withdrawn_ids = [str(d["player_id"]) for d in selected if d.get("player_id")]
        withdrawn_set = set(withdrawn_ids)

        # Persist
        written = 0
        with repo.transaction() as cur:
            # Ensure table exists
            try:
                cur.execute("SELECT 1 FROM draft_withdrawals LIMIT 1;")
            except sqlite3.OperationalError as e:
                raise RuntimeError("draft_withdrawals table missing. Apply db_schema/draft.py patch first.") from e

            for d in selected:
                pid = str(d.get("player_id") or "").strip()
                if not pid:
                    continue

                decision_payload = {
                    "draft_year": dy,
                    "player_id": pid,
                    "model": "withdraw_v1",
                    "seed": int(seed),
                    "inputs": {
                        "rank_declared": int(d.get("rank_declared") or 0),
                        "p_drafted": float(d.get("p_drafted") or 0.0),
                        "p_first_round": float(d.get("p_first_round") or 0.0),
                        "score": float(d.get("score") or 0.0),
                        "pot_points": int(d.get("pot_points") or 0),
                        "prod": float(d.get("prod") or 0.0),
                        "combine_athletic": float(d.get("combine_athletic") or 50.0),
                        "workout_overall_avg": d.get("workout_overall_avg"),
                    },
                    "outputs": {
                        "withdraw_prob": float(d.get("withdraw_prob") or 0.0),
                        "logit": float(d.get("logit") or 0.0),
                        "withdrawn": True,
                    },
                    "created_at": now,
                }

                cur.execute(
                    """
                    INSERT INTO draft_withdrawals(draft_year, player_id, withdrawn_at, decision_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(draft_year, player_id) DO UPDATE SET
                        withdrawn_at=excluded.withdrawn_at,
                        decision_json=excluded.decision_json,
                        updated_at=excluded.updated_at;
                    """,
                    (dy, pid, now, _json_dumps(decision_payload), now, now),
                )

                cur.execute(
                    "DELETE FROM college_draft_entries WHERE draft_year=? AND player_id=?;",
                    (dy, pid),
                )
                cur.execute(
                    "UPDATE college_players SET status='ACTIVE' WHERE player_id=?;",
                    (pid,),
                )
                written += 1

            _set_meta(repo, meta_key, "1", cur=cur)

        remaining = int(declared_n - written)

        # For UI/debugging, return a compact sample of withdrawals.
        sample = [
            {
                "player_id": d.get("player_id"),
                "rank_declared": d.get("rank_declared"),
                "p_drafted": round(float(d.get("p_drafted") or 0.0), 3),
                "withdraw_prob": round(float(d.get("withdraw_prob") or 0.0), 3),
            }
            for d in selected[:20]
        ]

        return {
            "ok": True,
            "draft_year": dy,
            "already_complete": False,
            "declared_before": int(declared_n),
            "candidates": int(len(candidates)),
            "withdrawn": int(written),
            "declared_remaining_est": int(remaining),
            "min_remaining_declared": int(min_rem),
            "withdrawn_sample": sample,
            "notes": {
                "model": "withdraw_v1",
                "seed": int(seed),
                "draft_slots": int(draft_slots),
                "first_round_slots": int(first_slots),
            },
        }
