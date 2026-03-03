from __future__ import annotations

"""draft.undrafted

Resolve "undrafted" declared prospects into NBA-like pro routes (P0-3 fix).

Previous behavior:
  - The college offseason advance step naively reset *all* DECLARED players back to ACTIVE,
    implying every undrafted player returns to school.

New behavior:
  - After the draft is applied, any remaining `college_draft_entries(draft_year=...)` rows are
    considered undrafted.
  - Undrafted prospects are routed into:
      * FA pool (camp invites / summer league / two-way proxy)
      * Retirement (leaves the basketball ecosystem)
    (In future: overseas league / G-league rights can be added here.)

Persistence:
  - Writes one row per player into `draft_undrafted_outcomes`
  - For FA outcomes:
      * Promotes player_id from college_players -> players + roster(team_id='FA')
      * Deletes college tables for that player
  - For RETIRED outcomes:
      * Deletes college tables for that player
  - Sets meta key `draft_undrafted_resolved_{draft_year}` = "1" for idempotence

Design notes:
  - This step is designed to be called from the server draft apply endpoint.
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
from ratings_2k import REQUIRED_KEYS, potential_grade_to_scalar, validate_attrs


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
    x = float(_clamp(float(x), -20.0, 20.0))
    return 1.0 / (1.0 + math.exp(-x))


def _prod_score(stats: Mapping[str, Any] | None) -> float:
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


def _load_undrafted_rows(repo: LeagueRepo, *, draft_year: int, season_year: int) -> List[Dict[str, Any]]:
    """Undrafted candidates are remaining entries after draft apply."""
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


def resolve_undrafted_to_pro(
    *,
    db_path: str,
    draft_year: int,
    tx_date_iso: Optional[str] = None,
    rng_seed: Optional[int] = None,
    fa_target: Optional[int] = None,
) -> Dict[str, Any]:
    """Resolve remaining declared prospects into FA / retirement.

    Args:
        db_path: sqlite path
        draft_year: target draft year
        tx_date_iso: in-game date (ISO) for transaction log entries
        rng_seed: optional override seed
        fa_target: optional target count of undrafted players to promote into FA.
                   If None, uses a conservative default (~2 per team).
    """
    dy = int(draft_year)
    if dy <= 0:
        raise ValueError(f"invalid draft_year: {draft_year}")
    season_year = dy - 1
    if season_year <= 0:
        raise ValueError(f"invalid inferred season_year: {season_year}")

    meta_key = f"draft_undrafted_resolved_{dy}"
    seed = int(rng_seed) if rng_seed is not None else (dy * 1307 + 23)
    now = game_time.now_utc_like_iso()

    # Default FA target: roughly 2 camp invites per NBA team.
    if fa_target is None:
        try:
            from config import ALL_TEAM_IDS

            fa_target_i = max(10, int(len(ALL_TEAM_IDS)) * 2)
        except Exception:
            fa_target_i = 60
    else:
        fa_target_i = max(0, int(fa_target))

    signed_date_iso = str(tx_date_iso or "").strip() or now[:10]

    with LeagueRepo(str(db_path)) as repo:
        repo.init_db()

        if _get_meta(repo, meta_key) == "1":
            try:
                row = repo._conn.execute(
                    "SELECT COUNT(1) AS n FROM draft_undrafted_outcomes WHERE draft_year=?;",
                    (dy,),
                ).fetchone()
                n = int(row["n"] if row is not None else 0)
            except sqlite3.OperationalError:
                n = 0
            return {"ok": True, "draft_year": dy, "already_complete": True, "resolved": int(n)}

        rows = _load_undrafted_rows(repo, draft_year=dy, season_year=season_year)
        undrafted_n = int(len(rows))
        if undrafted_n <= 0:
            with repo.transaction() as cur:
                _set_meta(repo, meta_key, "1", cur=cur)
            return {"ok": True, "draft_year": dy, "already_complete": False, "undrafted": 0, "fa": 0, "retired": 0}

        # Score undrafted candidates to decide who earns FA invites.
        scored: List[Tuple[Tuple, Dict[str, Any]]] = []
        for r in rows:
            pid = str(r.get("player_id") or "").strip()
            if not pid:
                continue
            try:
                ovr = int(r.get("ovr") or 60)
            except Exception:
                ovr = 60
            try:
                age = int(r.get("age") or 19)
            except Exception:
                age = 19
            try:
                class_year = int(r.get("class_year") or 1)
            except Exception:
                class_year = 1

            ratings = _json_loads(r.get("attrs_json"), default={})
            if not isinstance(ratings, dict):
                ratings = {}
            pot_grade = str(ratings.get("Potential") or "C-")
            pot_points = int(_potential_points_from_grade(pot_grade))

            season_stats = _json_loads(r.get("stats_json"), default=None)
            if season_stats is not None and not isinstance(season_stats, dict):
                season_stats = None
            prod = float(_prod_score(season_stats))

            youth_bonus = float(_clamp(21 - float(age), -2.0, 3.0))
            class_penalty = 0.25 * float(max(0, class_year - 1))

            # Undrafted quality score (higher = more likely to get camp invite)
            score = (
                1.00 * (float(ovr) - 60.0)
                + 0.65 * (float(pot_points) - 70.0)
                + 0.45 * float(prod)
                + 0.55 * float(youth_bonus)
                - 0.35 * float(class_penalty)
            )

            sort_key = (-float(score), int(age), int(class_year), pid)
            scored.append((sort_key, {**r, "_score": float(score), "_pot_points": int(pot_points), "_prod": float(prod)}))

        scored.sort(key=lambda t: t[0])
        ranked_rows = [row for _, row in scored]
        if not ranked_rows:
            with repo.transaction() as cur:
                _set_meta(repo, meta_key, "1", cur=cur)
            return {"ok": True, "draft_year": dy, "already_complete": False, "undrafted": 0, "fa": 0, "retired": 0}

        # Pick a target number into FA with soft randomness around the cutoff.
        fa_target_eff = int(min(int(fa_target_i), int(len(ranked_rows))))
        if fa_target_eff < 0:
            fa_target_eff = 0

        cutoff_score: float
        if fa_target_eff == 0:
            cutoff_score = float("inf")
        elif fa_target_eff >= len(ranked_rows):
            cutoff_score = float(ranked_rows[-1].get("_score") or 0.0)
        else:
            cutoff_score = float(ranked_rows[fa_target_eff - 1].get("_score") or 0.0)

        selected_fa: List[Dict[str, Any]] = []
        for i, r in enumerate(ranked_rows):
            pid = str(r.get("player_id") or "").strip()
            if not pid:
                continue
            score = float(r.get("_score") or 0.0)
            prng = random.Random(_stable_u32("udfa_v1", seed, dy, pid))
            # Probability centered on cutoff.
            p = float(_sigmoid((float(score) - float(cutoff_score)) / 3.0))
            # Slight positional randomness: top of the list still almost always makes it.
            if i < max(1, fa_target_eff // 3):
                p = max(p, 0.85)
            if prng.random() < p:
                selected_fa.append({"player_id": pid, "score": score, "rank": int(i + 1), "p": p})

        # Enforce exact-ish target by trimming/filling.
        if len(selected_fa) > fa_target_eff:
            selected_fa.sort(key=lambda d: (-float(d.get("score") or 0.0), int(d.get("rank") or 9999), str(d.get("player_id") or "")))
            selected_fa = selected_fa[:fa_target_eff]
        elif len(selected_fa) < fa_target_eff:
            selected_ids = {str(d.get("player_id")) for d in selected_fa}
            for i, r in enumerate(ranked_rows):
                if len(selected_fa) >= fa_target_eff:
                    break
                pid = str(r.get("player_id") or "").strip()
                if not pid or pid in selected_ids:
                    continue
                selected_fa.append({"player_id": pid, "score": float(r.get("_score") or 0.0), "rank": int(i + 1), "p": 1.0})
                selected_ids.add(pid)

        fa_ids = {str(d.get("player_id")) for d in selected_fa if d.get("player_id")}

        fa_written = 0
        retired_written = 0
        tx_entries: List[Dict[str, Any]] = []

        with repo.transaction() as cur:
            # Ensure outcome table exists
            try:
                cur.execute("SELECT 1 FROM draft_undrafted_outcomes LIMIT 1;")
            except sqlite3.OperationalError as e:
                raise RuntimeError("draft_undrafted_outcomes table missing. Apply db_schema/draft.py patch first.") from e

            for r in ranked_rows:
                pid = str(r.get("player_id") or "").strip()
                if not pid:
                    continue

                # Defensive idempotency: if we've already resolved this player (row exists), skip.
                try:
                    ex = repo._conn.execute(
                        "SELECT outcome FROM draft_undrafted_outcomes WHERE draft_year=? AND player_id=?;",
                        (dy, pid),
                    ).fetchone()
                except sqlite3.OperationalError:
                    ex = None
                if ex is not None:
                    continue

                outcome = "FA" if pid in fa_ids else "RETIRED"

                # Decision payload for telemetry/tuning.
                decision_payload = {
                    "draft_year": dy,
                    "player_id": pid,
                    "model": "udfa_v1",
                    "seed": int(seed),
                    "inputs": {
                        "score": float(r.get("_score") or 0.0),
                        "pot_points": int(r.get("_pot_points") or 0),
                        "prod": float(r.get("_prod") or 0.0),
                        "ovr": int(r.get("ovr") or 0),
                        "age": int(r.get("age") or 0),
                        "class_year": int(r.get("class_year") or 0),
                        "fa_target": int(fa_target_eff),
                    },
                    "outputs": {"outcome": outcome},
                    "created_at": now,
                }

                cur.execute(
                    """
                    INSERT INTO draft_undrafted_outcomes(draft_year, player_id, outcome, decided_at, decision_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?);
                    """,
                    (dy, pid, outcome, now, _json_dumps(decision_payload), now),
                )

                if outcome == "FA":
                    # Promote into NBA players + roster(FA)
                    ratings = _json_loads(r.get("attrs_json"), default={})
                    if not isinstance(ratings, dict):
                        ratings = {}
                    validate_attrs(ratings, strict=True)
                    ratings = {k: ratings[k] for k in REQUIRED_KEYS}

                    # Insert/Upsert players
                    cur.execute(
                        """
                        INSERT INTO players(player_id, name, pos, age, height_in, weight_lb, ovr, attrs_json, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(player_id) DO UPDATE SET
                            name=excluded.name,
                            pos=excluded.pos,
                            age=excluded.age,
                            height_in=excluded.height_in,
                            weight_lb=excluded.weight_lb,
                            ovr=excluded.ovr,
                            attrs_json=excluded.attrs_json,
                            updated_at=excluded.updated_at;
                        """,
                        (
                            pid,
                            str(r.get("name") or "Unknown"),
                            str(r.get("pos") or "G"),
                            int(r.get("age") or 19),
                            int(r.get("height_in") or 78),
                            int(r.get("weight_lb") or 210),
                            int(r.get("ovr") or 60),
                            _json_dumps(ratings),
                            now,
                            now,
                        ),
                    )

                    # Roster entry as FA (no contract)
                    cur.execute(
                        """
                        INSERT INTO roster(player_id, team_id, salary_amount, status, updated_at)
                        VALUES (?, 'FA', ?, 'active', ?)
                        ON CONFLICT(player_id) DO UPDATE SET
                            team_id=excluded.team_id,
                            salary_amount=excluded.salary_amount,
                            status=excluded.status,
                            updated_at=excluded.updated_at;
                        """,
                        (pid, 0, now),
                    )

                    # Cleanup college SSOT rows
                    cur.execute("DELETE FROM college_player_season_stats WHERE player_id=?;", (pid,))
                    cur.execute("DELETE FROM college_draft_entries WHERE player_id=?;", (pid,))
                    cur.execute("DELETE FROM college_players WHERE player_id=?;", (pid,))

                    fa_written += 1

                    tx_entries.append(
                        {
                            "type": "undrafted_to_fa",
                            "source": "draft_undrafted",
                            "date": signed_date_iso,
                            "season_year": int(season_year),
                            "teams": ["FA"],
                            "draft_year": int(dy),
                            "player": {
                                "player_id": pid,
                                "name": str(r.get("name") or "Unknown"),
                                "pos": str(r.get("pos") or "G"),
                                "age": int(r.get("age") or 19),
                                "ovr": int(r.get("ovr") or 60),
                            },
                            "meta": {"outcome": "FA"},
                        }
                    )
                else:
                    # RETIRED: remove from college world entirely.
                    cur.execute("DELETE FROM college_player_season_stats WHERE player_id=?;", (pid,))
                    cur.execute("DELETE FROM college_draft_entries WHERE player_id=?;", (pid,))
                    cur.execute("DELETE FROM college_players WHERE player_id=?;", (pid,))
                    retired_written += 1

                    tx_entries.append(
                        {
                            "type": "undrafted_retired",
                            "source": "draft_undrafted",
                            "date": signed_date_iso,
                            "season_year": int(season_year),
                            "teams": [],
                            "draft_year": int(dy),
                            "player": {"player_id": pid, "name": str(r.get("name") or "Unknown")},
                            "meta": {"outcome": "RETIRED"},
                        }
                    )

            # Transactions log (nested SAVEPOINT)
            if tx_entries:
                repo.insert_transactions(tx_entries)

            _set_meta(repo, meta_key, "1", cur=cur)

        sample_fa = [d for d in selected_fa[:20]]
        return {
            "ok": True,
            "draft_year": dy,
            "already_complete": False,
            "undrafted": int(undrafted_n),
            "fa": int(fa_written),
            "retired": int(retired_written),
            "fa_target": int(fa_target_eff),
            "fa_sample": sample_fa,
            "notes": {"model": "udfa_v1", "seed": int(seed)},
        }
