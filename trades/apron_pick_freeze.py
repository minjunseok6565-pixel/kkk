from __future__ import annotations

import sqlite3
from typing import Any, Dict, Mapping, Optional


_SECOND_APRON_LOCK_REASON = "SECOND_APRON_FROZEN_PICK"


def _coerce_int(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        return int(value)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if s in {"1", "true", "t", "yes", "y", "on"}:
        return True
    if s in {"0", "false", "f", "no", "n", "off"}:
        return False
    return default


def ensure_second_apron_frozen_picks(
    *,
    db_path: str,
    season_year: int,
    draft_year: int,
    trade_rules: Mapping[str, Any],
    now_iso: str,
) -> Dict[str, Any]:
    enabled = _coerce_bool(trade_rules.get("second_apron_frozen_picks_enabled"), True)
    start_season_year = _coerce_int(
        trade_rules.get("second_apron_frozen_picks_start_season_year"), 2025
    )
    if not enabled or int(season_year) < int(start_season_year):
        return {
            "ok": True,
            "skipped": True,
            "season_year": int(season_year),
            "draft_year": int(draft_year),
        }

    second_apron = _coerce_int(trade_rules.get("second_apron"), 0)
    if second_apron <= 0:
        return {
            "ok": True,
            "skipped": True,
            "season_year": int(season_year),
            "draft_year": int(draft_year),
        }

    target_years_out = _coerce_int(
        trade_rules.get("second_apron_frozen_picks_target_years_out"), 7
    )
    window_years = _coerce_int(trade_rules.get("second_apron_frozen_picks_window_years"), 4)
    unfreeze_below_years = _coerce_int(
        trade_rules.get("second_apron_frozen_picks_unfreeze_below_years"), 3
    )
    if target_years_out < 1:
        target_years_out = 1
    if window_years < 1:
        window_years = 1
    if unfreeze_below_years < 1:
        unfreeze_below_years = 1

    meta_key = f"second_apron_frozen_picks_done_{int(season_year)}"

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    cur = conn.cursor()

    locked_picks: list[str] = []
    unlocked_picks: list[str] = []
    escalated_picks: list[str] = []

    try:
        cur.execute("BEGIN;")

        row = cur.execute("SELECT value FROM meta WHERE key=?;", (meta_key,)).fetchone()
        if row is not None and str(row["value"]).strip() not in {"", "0", "false", "False"}:
            conn.commit()
            return {
                "ok": True,
                "already_done": True,
                "season_year": int(season_year),
                "draft_year": int(draft_year),
                "locked_picks": [],
                "unlocked_picks": [],
                "escalated_picks": [],
                "teams_above_second_apron": [],
            }

        team_ids: list[str] = []
        try:
            team_rows = cur.execute(
                "SELECT team_id FROM gm_profiles ORDER BY team_id;"
            ).fetchall()
            team_ids = [str(r["team_id"]).upper() for r in team_rows if r["team_id"] is not None]
        except sqlite3.OperationalError:
            team_ids = []

        team_ids = [tid for tid in team_ids if tid and tid != "FA"]

        if not team_ids:
            try:
                team_rows = cur.execute(
                    "SELECT DISTINCT team_id FROM roster WHERE status='active' ORDER BY team_id;"
                ).fetchall()
                team_ids = [str(r["team_id"]).upper() for r in team_rows if r["team_id"] is not None]
            except sqlite3.OperationalError:
                team_ids = []

            team_ids = [tid for tid in team_ids if tid and tid != "FA"]

        if not team_ids:
            try:
                from config import ALL_TEAM_IDS

                team_ids = [str(t).upper() for t in ALL_TEAM_IDS if t is not None]
            except Exception:
                team_ids = []

        team_ids = sorted({tid for tid in team_ids if tid and tid != "FA"})

        if not team_ids:
            conn.commit()
            return {
                "ok": True,
                "skipped": True,
                "season_year": int(season_year),
                "draft_year": int(draft_year),
            }

        payroll_by_team: Dict[str, int] = {}
        try:
            rows = cur.execute(
                """
                SELECT team_id, SUM(COALESCE(salary_amount, 0)) AS payroll
                FROM roster
                WHERE status='active' AND UPPER(team_id) != 'FA'
                GROUP BY team_id;
                """
            ).fetchall()
            for r in rows:
                tid = str(r["team_id"]).upper()
                payroll_by_team[tid] = _coerce_int(r["payroll"], 0)
        except sqlite3.OperationalError:
            conn.commit()
            return {
                "ok": True,
                "skipped": True,
                "season_year": int(season_year),
                "draft_year": int(draft_year),
            }

        if not payroll_by_team:
            conn.commit()
            return {
                "ok": True,
                "skipped": True,
                "season_year": int(season_year),
                "draft_year": int(draft_year),
            }

        above_second_apron: Dict[str, bool] = {}
        for tid in team_ids:
            above_second_apron[tid] = payroll_by_team.get(tid, 0) >= int(second_apron)

        eval_rows = cur.execute(
            """
            SELECT
                pick_id,
                original_team,
                trade_lock_start_season_year,
                trade_lock_eval_seasons,
                trade_lock_below_count
            FROM draft_picks
            WHERE trade_locked=1
              AND trade_lock_reason=?
              AND trade_lock_escalated=0
              AND trade_lock_start_season_year IS NOT NULL
              AND trade_lock_start_season_year < ?
              AND trade_lock_eval_seasons < ?;
            """,
            (_SECOND_APRON_LOCK_REASON, int(season_year), int(window_years)),
        ).fetchall()

        for r in eval_rows:
            pick_id = str(r["pick_id"])
            penalized_team = str(r["original_team"]).upper()
            eval_seasons = _coerce_int(r["trade_lock_eval_seasons"], 0) + 1
            below_count = _coerce_int(r["trade_lock_below_count"], 0)
            if not above_second_apron.get(penalized_team, False):
                below_count += 1

            if below_count >= int(unfreeze_below_years):
                cur.execute(
                    """
                    UPDATE draft_picks
                    SET trade_locked=0,
                        trade_lock_reason=NULL,
                        trade_lock_start_season_year=NULL,
                        trade_lock_eval_seasons=0,
                        trade_lock_below_count=0,
                        trade_lock_escalated=0,
                        updated_at=?
                    WHERE pick_id=?;
                    """,
                    (str(now_iso), pick_id),
                )
                unlocked_picks.append(pick_id)
                continue

            escalated = 0
            if eval_seasons >= int(window_years) and below_count < int(unfreeze_below_years):
                escalated = 1

            cur.execute(
                """
                UPDATE draft_picks
                SET trade_locked=1,
                    trade_lock_reason=?,
                    trade_lock_eval_seasons=?,
                    trade_lock_below_count=?,
                    trade_lock_escalated=?,
                    updated_at=?
                WHERE pick_id=?;
                """,
                (
                    _SECOND_APRON_LOCK_REASON,
                    int(eval_seasons),
                    int(below_count),
                    int(escalated),
                    str(now_iso),
                    pick_id,
                ),
            )
            if escalated:
                escalated_picks.append(pick_id)

        teams_above = [tid for tid in team_ids if above_second_apron.get(tid, False)]
        teams_above.sort()

        target_pick_year = int(draft_year) + int(target_years_out)
        for tid in teams_above:
            pid = f"{target_pick_year}_R1_{tid}"
            cur.execute(
                """
                INSERT OR IGNORE INTO draft_picks(
                    pick_id, year, round, original_team, owner_team, protection_json, created_at, updated_at
                )
                VALUES (?, ?, 1, ?, ?, NULL, ?, ?);
                """,
                (pid, int(target_pick_year), tid, tid, str(now_iso), str(now_iso)),
            )
            cur.execute(
                """
                UPDATE draft_picks
                SET trade_locked=1,
                    trade_lock_reason=?,
                    trade_lock_start_season_year=?,
                    trade_lock_eval_seasons=0,
                    trade_lock_below_count=0,
                    trade_lock_escalated=0,
                    updated_at=?
                WHERE pick_id=?;
                """,
                (_SECOND_APRON_LOCK_REASON, int(season_year), str(now_iso), pid),
            )
            locked_picks.append(pid)

        cur.execute(
            """
            INSERT INTO meta(key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value;
            """,
            (meta_key, "1"),
        )

        conn.commit()
        return {
            "ok": True,
            "season_year": int(season_year),
            "draft_year": int(draft_year),
            "second_apron": int(second_apron),
            "teams_above_second_apron": teams_above,
            "locked_picks": locked_picks,
            "unlocked_picks": unlocked_picks,
            "escalated_picks": escalated_picks,
            "meta_key": meta_key,
        }
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            cur.close()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass
