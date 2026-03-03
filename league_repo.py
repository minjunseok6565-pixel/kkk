# league_repo.py
# Developer note:
# - SQLite DB is the single source of truth (SSOT) for persisted league data (tables managed here).
# - Excel files are import/export only (no runtime reads/writes).
# - player_id and team_id are canonical strings.
# - Never use DataFrame indices as IDs; always use schema.py normalization helpers.
"""
LeagueRepository: persisted-data SSOT (SQLite)

Goal:
- Excel is import/export only.
- All persisted league-data reads/writes go through SQLite (via LeagueRepo).
  (Runtime GameState / caches live in memory; see state_schema.py / state_modules/state_store.py.)

Usage (CLI):
  python league_repo.py init --db <db_path>
  python league_repo.py import_roster --db <db_path> --excel roster.xlsx
  python league_repo.py validate --db <db_path>
  python league_repo.py export_roster --db <db_path> --excel roster_export.xlsx

Python:
  from league_repo import LeagueRepo
  repo = LeagueRepo("<db_path>")
  repo.import_roster_excel("roster.xlsx", mode="replace")
  team = repo.get_team_roster("ATL")
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as _dt
import game_time
import hashlib
import json
import logging
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

# We strongly recommend keeping schema.py next to this file.
# It defines canonical IDs, stat keys, and normalization helpers.
try:
    from schema import (
        SCHEMA_VERSION,
        PlayerId,
        TeamId,
        normalize_player_id,
        normalize_team_id,
        season_id_from_year,
        assert_unique_ids,
        ROSTER_COL_PLAYER_ID,
        ROSTER_COL_TEAM_ID,
    )
except Exception as e:  # pragma: no cover
    raise ImportError(
        "schema.py is required. Put schema.py next to league_repo.py and retry.\n"
        f"Import error: {e}"
    )


# Contract SSOT codec (columns are SSOT; contract_json stores extras only)
from contract_codec import CONTRACT_SSOT_FIELDS, contract_from_row, contract_to_upsert_row
from contracts.options import normalize_option_record


# ----------------------------
# Helpers
# ----------------------------

_HEIGHT_RE = re.compile(r"^\s*(\d+)\s*'\s*(\d+)\s*\"?\s*$")
_WEIGHT_RE = re.compile(r"^\s*(\d+)\s*(?:lbs?)?\s*$", re.IGNORECASE)
_SALARY_YEAR_COL_RE = re.compile(r"^salary_(\d{4})$", re.IGNORECASE)

logger = logging.getLogger(__name__)
_WARN_COUNTS: Dict[str, int] = {}


def _warn_limited(code: str, msg: str, *, limit: int = 5) -> None:
    n = _WARN_COUNTS.get(code, 0)
    if n < limit:
        logger.warning("%s %s", code, msg, exc_info=True)
    _WARN_COUNTS[code] = n + 1


def _utc_now_iso() -> str:
    # Hard rule: never use the host OS clock.
    return game_time.now_utc_like_iso()




def _state_season_year_ssot() -> int:
    try:
        import state

        snap = state.get_league_context_snapshot() or {}
        y = snap.get("season_year")
        if y is None:
            raise KeyError("season_year missing")
        return int(y)
    except Exception:
        # Excel bootstrap fallback when state is unavailable (CLI import path).
        return int(_dt.date.today().year)

def _json_dumps(obj: Any) -> str:
    return json.dumps(
        obj,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    )

def _json_loads(value: Any, default: Any):
    """
    Safe JSON loader:
    - None -> default
    - already dict/list -> returns as-is
    - invalid JSON -> default
    """
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        _warn_limited("JSON_DECODE_FAILED", f"value_preview={repr(str(value))[:120]}", limit=3)
        return default

def parse_height_in(value: Any) -> Optional[int]:
    """Convert \"6' 5\"\" to inches. If unknown, return None."""
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() in {"nan", "none"}:
        return None
    if s == "--":
        return 0
    m = _HEIGHT_RE.match(s)
    if not m:
        return None
    feet = int(m.group(1))
    inches = int(m.group(2))
    return feet * 12 + inches


def parse_weight_lb(value: Any) -> Optional[int]:
    """Convert \"205 lbs\" to 205. If unknown, return None."""
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() in {"nan", "none"}:
        return None
    m = _WEIGHT_RE.match(s.replace(",", ""))
    if not m:
        return None
    return int(m.group(1))


def parse_salary_int(value: Any) -> Optional[int]:
    """
    Parse salary into integer dollars.
    Accepts: 15161800, "15,161,800", "$15,161,800", etc.
    Returns None for empty/invalid.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return int(value)
        except (TypeError, ValueError, OverflowError):
            _warn_limited("SALARY_INT_COERCE_FAILED", f"value={value!r}", limit=3)
            return None
    s = str(value).strip()
    if not s or s.lower() in {"nan", "none"}:
        return None
    s = s.replace("$", "").replace(",", "")
    if not re.fullmatch(r"-?\d+", s):
        return None
    try:
        return int(s)
    except (TypeError, ValueError, OverflowError):
        _warn_limited("SALARY_STR_COERCE_FAILED", f"value={value!r}", limit=3)
        return None


def _parse_salary_option_cell(value: Any) -> tuple[Optional[int], Optional[str]]:
    """Parse Excel salary cell with optional option marker.

    Supported examples:
      - 15161800
      - "15,161,800"
      - "15,161,800|TEAM" / "15161800|TO"
      - "15161800|PLAYER" / "15161800|PO"
    """
    if value is None:
        return (None, None)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        sal = parse_salary_int(value)
        return (sal, None)

    raw = str(value).strip()
    if not raw or raw.lower() in {"nan", "none"}:
        return (None, None)

    left = raw
    opt_raw = None
    if "|" in raw:
        left, opt_raw = raw.split("|", 1)

    sal = parse_salary_int(left)
    if sal is None:
        return (None, None)

    if opt_raw is None:
        return (sal, None)

    token = str(opt_raw).strip().upper().replace(" ", "").replace("-", "_")
    alias = {
        "TEAM": "TEAM",
        "TEAM_OPTION": "TEAM",
        "TO": "TEAM",
        "PLAYER": "PLAYER",
        "PLAYER_OPTION": "PLAYER",
        "PO": "PLAYER",
        "ETO": "ETO",
    }
    opt = alias.get(token)
    if not opt:
        _warn_limited("EXCEL_OPTION_TOKEN_INVALID", f"token={opt_raw!r}", limit=5)
        return (sal, None)
    return (sal, opt)


def _extract_salary_and_options_excel(row: Any, df_columns: Sequence[str]) -> tuple[Dict[str, float], List[Dict[str, Any]]]:
    """Extract salary_by_year + option records from salary_YYYY Excel columns."""
    salary_by_year: Dict[str, float] = {}
    option_rows: List[Dict[str, Any]] = []

    for col in df_columns:
        m = _SALARY_YEAR_COL_RE.match(str(col).strip())
        if not m:
            continue
        year_i = int(m.group(1))
        sal_i, opt_type = _parse_salary_option_cell(row.get(col, None))
        if sal_i is None:
            continue
        salary_by_year[str(year_i)] = float(sal_i)
        if opt_type:
            try:
                option_rows.append(
                    normalize_option_record(
                        {
                            "season_year": int(year_i),
                            "type": str(opt_type),
                            "status": "PENDING",
                            "decision_date": None,
                        }
                    )
                )
            except Exception as e:
                _warn_limited(
                    "EXCEL_OPTION_NORMALIZE_FAILED",
                    f"col={col!r} value={row.get(col, None)!r} exc_type={type(e).__name__}",
                    limit=5,
                )

    # de-dup by season_year (first wins)
    by_year: Dict[int, Dict[str, Any]] = {}
    for opt in option_rows:
        sy = int(opt.get("season_year") or 0)
        if sy not in by_year:
            by_year[sy] = opt

    options_sorted = [by_year[y] for y in sorted(by_year.keys())]
    return salary_by_year, options_sorted


def _require_columns(cols: Sequence[str], required: Sequence[str]) -> None:
    missing = [c for c in required if c not in cols]
    if missing:
        raise ValueError(f"Excel missing required columns: {missing}. Found: {list(cols)}")


# ----------------------------
# Data types
# ----------------------------

@dataclass(frozen=True)
class PlayerRow:
    player_id: str
    name: Optional[str]
    pos: Optional[str]
    age: Optional[int]
    height_in: Optional[int]
    weight_lb: Optional[int]
    ovr: Optional[int]
    attrs_json: str  # serialized dict


@dataclass(frozen=True)
class RosterRow:
    player_id: str
    team_id: str
    salary_amount: Optional[int]


# ----------------------------
# Repository
# ----------------------------

class LeagueRepo:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON;")
        self._conn.execute("PRAGMA journal_mode = WAL;")  # good safety for frequent writes
        # Nested transaction support (SAVEPOINT) for callers that compose repo methods.
        # (SQLite raises if BEGIN is issued while a transaction is already active.)
        self._savepoint_seq = 0

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass

    @contextlib.contextmanager
    def transaction(self):
        """
        Atomic transaction helper.

        Supports nesting via SAVEPOINT:
        - outermost: BEGIN ... COMMIT/ROLLBACK
        - nested: SAVEPOINT ... RELEASE (or ROLLBACK TO + RELEASE on error)
        """
        cur = self._conn.cursor()
        nested = bool(getattr(self._conn, "in_transaction", False))
        sp_name = None
        try:
            if nested:
                self._savepoint_seq += 1
                sp_name = f"sp_{self._savepoint_seq}"
                cur.execute(f"SAVEPOINT {sp_name};")
            else:
                self._conn.execute("BEGIN;")

            yield cur

            if nested and sp_name:
                cur.execute(f"RELEASE SAVEPOINT {sp_name};")
            else:
                self._conn.commit()
        except Exception:
            if nested and sp_name:
                # Roll back to the savepoint only; do NOT rollback the outer transaction here.
                try:
                    cur.execute(f"ROLLBACK TO SAVEPOINT {sp_name};")
                finally:
                    cur.execute(f"RELEASE SAVEPOINT {sp_name};")
            else:
                self._conn.rollback()
            raise
        finally:
            try:
                cur.close()
            except Exception:
                pass

    # ------------------------
    # Schema
    # ------------------------

    def _ensure_table_columns(self, cur: sqlite3.Cursor, table: str, columns: Mapping[str, str]) -> None:
        """SQLite에는 ADD COLUMN IF NOT EXISTS가 없어서 PRAGMA로 확인 후 추가한다."""
        rows = cur.execute(f"PRAGMA table_info({table});").fetchall()
        existing = {r["name"] for r in rows}
        for col, ddl in columns.items():
            if col in existing:
                continue
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl};")

    def init_db(self) -> None:
        """Apply SQLite schema (DDL + migrations) via db_schema."""
        now = _utc_now_iso()
        try:
            # db_schema is the extracted schema/migration layer (split out of league_repo.py).
            from db_schema import apply_schema
        except Exception as e:  # pragma: no cover
            raise ImportError(
                "db_schema package is required. Ensure db_schema/ is on PYTHONPATH.\n"
                f"Import error: {e}"
            ) from e

        with self.transaction() as cur:
            apply_schema(
                cur,
                now=now,
                schema_version=SCHEMA_VERSION,
                ensure_columns=self._ensure_table_columns,
            )

    # ------------------------
    # Draft Picks / Swaps / Fixed Assets
    # ------------------------

    def upsert_draft_picks(self, picks_by_id: Mapping[str, Any]) -> None:
        if not picks_by_id:
            return
        now = _utc_now_iso()
        # SSOT: ensure protection schema is canonical on writes.
        try:
            from trades.protection import normalize_protection_optional
            from trades.errors import TradeError as _TradeError
        except Exception as exc:  # pragma: no cover
            raise ImportError("trades.protection is required") from exc
        rows = []
        for pick_id, pick in picks_by_id.items():
            if not isinstance(pick, dict):
                continue
            pid = str(pick.get("pick_id") or pick_id)
            try:
                year = int(pick.get("year") or 0)
            except (TypeError, ValueError):
                _warn_limited("DRAFT_PICK_YEAR_COERCE_FAILED", f"pick_id={pid} value={pick.get('year')!r}")
                year = 0
            try:
                rnd = int(pick.get("round") or 0)
            except (TypeError, ValueError):
                _warn_limited("DRAFT_PICK_ROUND_COERCE_FAILED", f"pick_id={pid} value={pick.get('round')!r}")
                rnd = 0
            original = str(pick.get("original_team") or "").upper()
            owner = str(pick.get("owner_team") or "").upper()
            protection = pick.get("protection")
            # Allow protection to be passed as a JSON string in tooling paths.
            if isinstance(protection, str):
                s = protection.strip()
                if not s or s.lower() == "null":
                    protection = None
                else:
                    try:
                        protection = json.loads(s)
                    except Exception as exc:
                        raise ValueError(
                            f"draft_picks.protection invalid JSON string (pick_id={pid}): {exc}"
                        ) from exc
            try:
                protection = normalize_protection_optional(protection, pick_id=pid)
            except _TradeError as exc:
                raise ValueError(f"draft_picks.protection invalid schema (pick_id={pid}): {exc}") from exc

            rows.append(
                (
                    pid,
                    year,
                    rnd,
                    original,
                    owner,
                    _json_dumps(protection) if protection is not None else None,
                    now,
                    now,
                )
            )
        with self.transaction() as cur:
            cur.executemany(
                """
                INSERT INTO draft_picks(pick_id, year, round, original_team, owner_team, protection_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(pick_id) DO UPDATE SET
                    year=excluded.year,
                    round=excluded.round,
                    original_team=excluded.original_team,
                    owner_team=excluded.owner_team,
                    protection_json=excluded.protection_json,
                    updated_at=excluded.updated_at;
                """,
                rows,
            )

    def ensure_draft_picks_seeded(self, draft_year: int, team_ids: Iterable[str], *, years_ahead: int = 7) -> None:
        now = _utc_now_iso()
        team_ids = [str(normalize_team_id(t, strict=False)).upper() for t in team_ids]
        with self.transaction() as cur:
            for year in range(int(draft_year), int(draft_year) + int(years_ahead) + 1):
                for rnd in (1, 2):
                    for tid in team_ids:
                        pick_id = f"{year}_R{rnd}_{tid}"
                        cur.execute(
                            """
                            INSERT OR IGNORE INTO draft_picks(pick_id, year, round, original_team, owner_team, protection_json, created_at, updated_at)
                            VALUES (?, ?, ?, ?, ?, NULL, ?, ?);
                            """,
                            (pick_id, year, rnd, tid, tid, now, now),
                        )

    def upsert_swap_rights(self, swaps_by_id: Mapping[str, Any]) -> None:
        if not swaps_by_id:
            return
        now = _utc_now_iso()
        rows = []
        for sid, swap in swaps_by_id.items():
            if not isinstance(swap, dict):
                continue
            swap_id = str(swap.get("swap_id") or sid)
            rows.append(
                (
                    swap_id,
                    str(swap.get("pick_id_a") or ""),
                    str(swap.get("pick_id_b") or ""),
                    int(swap.get("year") or 0) if str(swap.get("year") or "").isdigit() else None,
                    int(swap.get("round") or 0) if str(swap.get("round") or "").isdigit() else None,
                    str(swap.get("owner_team") or "").upper(),
                    str(swap.get("originator_team") or "").upper() if swap.get("originator_team") else None,
                    int(swap.get("transfer_count") or 0),
                    1 if swap.get("active", True) else 0,
                    str(swap.get("created_by_deal_id") or "") if swap.get("created_by_deal_id") is not None else None,
                    str(swap.get("created_at") or now),
                    now,
                )
            )
        with self.transaction() as cur:
            cur.executemany(
                """
                INSERT INTO swap_rights(
                    swap_id, pick_id_a, pick_id_b, year, round, owner_team,
                    originator_team, transfer_count,
                    active, created_by_deal_id, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(swap_id) DO UPDATE SET
                    pick_id_a=excluded.pick_id_a,
                    pick_id_b=excluded.pick_id_b,
                    year=excluded.year,
                    round=excluded.round,
                    owner_team=excluded.owner_team,
                    originator_team=excluded.originator_team,
                    transfer_count=excluded.transfer_count,
                    active=excluded.active,
                    created_by_deal_id=excluded.created_by_deal_id,
                    updated_at=excluded.updated_at;
                """,
                rows,
            )

    def upsert_fixed_assets(self, assets_by_id: Mapping[str, Any]) -> None:
        if not assets_by_id:
            return
        now = _utc_now_iso()
        rows = []
        for aid, asset in assets_by_id.items():
            if not isinstance(asset, dict):
                continue
            asset_id = str(asset.get("asset_id") or aid)
            label = asset.get("label")
            value = asset.get("value")
            try:
                value_f = float(value) if value is not None else None
            except (TypeError, ValueError):
                _warn_limited("FIXED_ASSET_VALUE_COERCE_FAILED", f"asset_id={asset_id} value={value!r}")
                value_f = None
            owner = str(asset.get("owner_team") or "").upper()
            source_pick_id = asset.get("source_pick_id")
            draft_year = asset.get("draft_year")
            try:
                draft_year_i = int(draft_year) if draft_year is not None else None
            except (TypeError, ValueError):
                _warn_limited(
                    "FIXED_ASSET_DRAFT_YEAR_COERCE_FAILED",
                    f"asset_id={asset_id} draft_year={draft_year!r}",
                )
                draft_year_i = None
            attrs = dict(asset)
            rows.append((asset_id, str(label) if label is not None else None, value_f, owner, str(source_pick_id) if source_pick_id is not None else None, draft_year_i, _json_dumps(attrs), now, now))
        with self.transaction() as cur:
            cur.executemany(
                """
                INSERT INTO fixed_assets(asset_id, label, value, owner_team, source_pick_id, draft_year, attrs_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(asset_id) DO UPDATE SET
                    label=excluded.label,
                    value=excluded.value,
                    owner_team=excluded.owner_team,
                    source_pick_id=excluded.source_pick_id,
                    draft_year=excluded.draft_year,
                    attrs_json=excluded.attrs_json,
                    updated_at=excluded.updated_at;
                """,
                rows,
            )

    def _read_draft_picks_map(self, cur: sqlite3.Cursor) -> Dict[str, Dict[str, Any]]:
        # SSOT: ensure protection is canonical on reads.
        try:
            from trades.protection import normalize_protection_optional
            from trades.errors import TradeError as _TradeError
        except Exception as exc:  # pragma: no cover
            raise ImportError("trades.protection is required") from exc
        rows = cur.execute(
            """
            SELECT pick_id, year, round, original_team, owner_team, protection_json, trade_locked, trade_lock_reason, trade_lock_start_season_year, trade_lock_eval_seasons, trade_lock_below_count, trade_lock_escalated
            FROM draft_picks;
            """
        ).fetchall()
        out: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            protection_raw = _json_loads(r["protection_json"], None)
            try:
                protection = normalize_protection_optional(protection_raw, pick_id=str(r["pick_id"]))
            except _TradeError as exc:
                raise ValueError(
                    f"draft_picks.protection_json invalid schema (pick_id={r['pick_id']}): {exc}"
                ) from exc
            pick = {
                "pick_id": str(r["pick_id"]),
                "year": int(r["year"]),
                "round": int(r["round"]),
                "original_team": str(r["original_team"]).upper(),
                "owner_team": str(r["owner_team"]).upper(),
                "protection": protection,
                "trade_locked": bool(int(r["trade_locked"]) if r["trade_locked"] is not None else 0),
                "trade_lock_reason": str(r["trade_lock_reason"]) if r["trade_lock_reason"] else None,
                "trade_lock_start_season_year": int(r["trade_lock_start_season_year"]) if r["trade_lock_start_season_year"] is not None else None,
                "trade_lock_eval_seasons": int(r["trade_lock_eval_seasons"] or 0),
                "trade_lock_below_count": int(r["trade_lock_below_count"] or 0),
                "trade_lock_escalated": bool(int(r["trade_lock_escalated"]) if r["trade_lock_escalated"] is not None else 0),
            }
            out[pick["pick_id"]] = pick
        return out

    def _read_tradable_draft_picks_map(self, cur: sqlite3.Cursor) -> Dict[str, Dict[str, Any]]:
        """Read only *tradable* (unused) draft picks.

        SSOT:
        - `draft_results` is the SSOT for applied picks (idempotent/resumable draft execution).
        - A pick is tradable iff it exists in `draft_picks` AND has no row in `draft_results`.

        This keeps historical picks in `draft_picks` for auditing, while ensuring the trade
        system never treats already-used picks as assets.
        """
        # SSOT: ensure protection is canonical on reads.
        try:
            from trades.protection import normalize_protection_optional
            from trades.errors import TradeError as _TradeError
        except Exception as exc:  # pragma: no cover
            raise ImportError("trades.protection is required") from exc

        rows = cur.execute(
            """
            SELECT
                p.pick_id, p.year, p.round, p.original_team, p.owner_team, p.protection_json,
                p.trade_locked, p.trade_lock_reason, p.trade_lock_start_season_year,
                p.trade_lock_eval_seasons, p.trade_lock_below_count, p.trade_lock_escalated
            FROM draft_picks p
            LEFT JOIN draft_results r ON r.pick_id = p.pick_id
            WHERE r.pick_id IS NULL;
            """
        ).fetchall()

        out: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            protection_raw = _json_loads(r["protection_json"], None)
            try:
                protection = normalize_protection_optional(protection_raw, pick_id=str(r["pick_id"]))
            except _TradeError as exc:
                raise ValueError(
                    f"draft_picks.protection_json invalid schema (pick_id={r['pick_id']}): {exc}"
                ) from exc
            pick = {
                "pick_id": str(r["pick_id"]),
                "year": int(r["year"]),
                "round": int(r["round"]),
                "original_team": str(r["original_team"]).upper(),
                "owner_team": str(r["owner_team"]).upper(),
                "protection": protection,
                "trade_locked": bool(int(r["trade_locked"]) if r["trade_locked"] is not None else 0),
                "trade_lock_reason": str(r["trade_lock_reason"]) if r["trade_lock_reason"] else None,
                "trade_lock_start_season_year": int(r["trade_lock_start_season_year"]) if r["trade_lock_start_season_year"] is not None else None,
                "trade_lock_eval_seasons": int(r["trade_lock_eval_seasons"] or 0),
                "trade_lock_below_count": int(r["trade_lock_below_count"] or 0),
                "trade_lock_escalated": bool(int(r["trade_lock_escalated"]) if r["trade_lock_escalated"] is not None else 0),
            }
            out[pick["pick_id"]] = pick
        return out

    def _read_swap_rights_map(self, cur: sqlite3.Cursor) -> Dict[str, Dict[str, Any]]:
        rows = cur.execute(
            """
            SELECT
                swap_id, pick_id_a, pick_id_b, year, round,
                owner_team, originator_team, transfer_count,
                active, created_by_deal_id, created_at
            FROM swap_rights;
            """
        ).fetchall()
        out: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            swap = {
                "swap_id": str(r["swap_id"]),
                "pick_id_a": str(r["pick_id_a"]),
                "pick_id_b": str(r["pick_id_b"]),
                "year": int(r["year"]) if r["year"] is not None else None,
                "round": int(r["round"]) if r["round"] is not None else None,
                "owner_team": str(r["owner_team"]).upper(),
                "originator_team": str(r["originator_team"]).upper() if r["originator_team"] else None,
                "transfer_count": int(r["transfer_count"] or 0),
                "active": bool(int(r["active"]) if r["active"] is not None else 1),
                "created_by_deal_id": str(r["created_by_deal_id"]) if r["created_by_deal_id"] else None,
                "created_at": str(r["created_at"]) if r["created_at"] else None,
            }
            out[swap["swap_id"]] = swap
        return out

    def _read_fixed_assets_map(self, cur: sqlite3.Cursor) -> Dict[str, Dict[str, Any]]:
        rows = cur.execute(
            """
            SELECT
                asset_id, label, value, owner_team,
                source_pick_id, draft_year, attrs_json
            FROM fixed_assets;
            """
        ).fetchall()
        out: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            attrs = _json_loads(r["attrs_json"], {})
            if not isinstance(attrs, dict):
                attrs = {"value": attrs}
            asset = {
                "asset_id": str(r["asset_id"]),
                "label": str(r["label"]) if r["label"] is not None else None,
                "value": float(r["value"]) if r["value"] is not None else None,
                "owner_team": str(r["owner_team"]).upper(),
                "source_pick_id": str(r["source_pick_id"]) if r["source_pick_id"] else None,
                "draft_year": int(r["draft_year"]) if r["draft_year"] is not None else None,
                "attrs": attrs,
            }
            out[asset["asset_id"]] = asset
        return out

    def get_draft_picks_map(self) -> Dict[str, Dict[str, Any]]:
        cur = self._conn.cursor()
        return self._read_draft_picks_map(cur)

    def get_swap_rights_map(self) -> Dict[str, Dict[str, Any]]:
        cur = self._conn.cursor()
        return self._read_swap_rights_map(cur)

    def get_fixed_assets_map(self) -> Dict[str, Dict[str, Any]]:
        cur = self._conn.cursor()
        return self._read_fixed_assets_map(cur)

    def get_trade_assets_snapshot(self) -> Dict[str, Any]:
        """
        Read draft_picks / swap_rights / fixed_assets in one DB transaction
        so trade validation can use a consistent snapshot.
        """
        with self.transaction() as cur:
            return {
                "draft_picks": self._read_tradable_draft_picks_map(cur),
                "swap_rights": self._read_swap_rights_map(cur),
                "fixed_assets": self._read_fixed_assets_map(cur),
            }

    # ------------------------
    # Transactions log
    # ------------------------

    def insert_transactions(self, entries: Sequence[Mapping[str, Any]]) -> None:
        if not entries:
            return
        now = _utc_now_iso()
        rows = []
        for e in entries:
            if not isinstance(e, dict):
                continue
            # Store season_year as a first-class column when provided (else NULL).
            sy = e.get("season_year")
            try:
                season_year_i = int(sy) if sy is not None and str(sy) != "" else None
            except (TypeError, ValueError):
                _warn_limited("TX_SEASON_YEAR_COERCE_FAILED", f"value={sy!r}", limit=3)
                season_year_i = None
            payload = _json_dumps(e)
            tx_hash = hashlib.sha1(payload.encode("utf-8")).hexdigest()
            rows.append(
                (
                    tx_hash,
                    str(e.get("type") or "unknown"),
                    str(e.get("date") or "") if e.get("date") is not None else None,
                    season_year_i,
                    str(e.get("deal_id") or "") if e.get("deal_id") is not None else None,
                    str(e.get("source") or "") if e.get("source") is not None else None,
                    _json_dumps(e.get("teams") or []),
                    payload,
                    now,
                )
            )
        with self.transaction() as cur:
            cur.executemany(
                """
                INSERT OR IGNORE INTO transactions_log(
                    tx_hash, tx_type, tx_date, season_year, deal_id, source, teams_json, payload_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                rows,
            )

    def list_transactions(
        self,
        *,
        limit: int = 200,
        since_date: Optional[str] = None,
        season_year: Optional[int] = None,
        deal_id: Optional[str] = None,
        tx_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        limit_i = max(1, int(limit))
        where = []
        params: List[Any] = []
        if since_date:
            where.append("tx_date >= ?")
            params.append(str(since_date))
        if season_year is not None:
            where.append("season_year = ?")
            params.append(int(season_year))
        if deal_id:
            where.append("deal_id = ?")
            params.append(str(deal_id))
        if tx_type:
            where.append("tx_type = ?")
            params.append(str(tx_type))

        sql = "SELECT payload_json FROM transactions_log"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY COALESCE(tx_date,'') DESC, created_at DESC LIMIT ?"
        params.append(limit_i)

        rows = self._conn.execute(sql, params).fetchall()
        out: List[Dict[str, Any]] = []
        for r in rows:
            payload = _json_loads(r["payload_json"], None)
            if isinstance(payload, dict):
                out.append(payload)
            else:
                out.append({"value": payload})
        return out

    def get_transactions_for_players(
        self,
        player_ids: Sequence[str],
        *,
        types: Optional[Sequence[str]] = None,
        season_year: Optional[int] = None,
        up_to_date: Optional[Any] = None,
        limit: int = 5000,
    ) -> List[Dict[str, Any]]:
        """
        Fetch transactions relevant to the given players.

        Minimal Phase-2 support:
        - Filters by season_year / tx_type / up_to_date in SQL (fast).
        - Filters by player_ids in Python by parsing payload_json (because player_id is not a first-class column).

        NOTE / unresolved:
        - transactions_log does NOT have a player_id column (only payload_json), so we can't do SQL filtering by player.
          If this becomes slow, consider adding:
            (a) a player_id column for contract-type tx, and/or
            (b) a tx_players bridge table (tx_hash, player_id) for trade moves and contract actions.
        """
        pid_set = {str(p) for p in (player_ids or []) if p is not None and str(p).strip()}
        if not pid_set:
            return []

        def _normalize_up_to_iso(v: Any) -> Optional[str]:
            # We store tx_date as ISO strings. For date-only inputs, treat as end-of-day UTC.
            if v is None:
                return None
            if isinstance(v, _dt.datetime):
                dt = v
                if dt.tzinfo is not None:
                    dt = dt.astimezone(_dt.timezone.utc).replace(tzinfo=None)
                return dt.replace(microsecond=0).isoformat() + "Z"
            if isinstance(v, _dt.date):
                d = v
                return f"{d.isoformat()}T23:59:59Z"
            s = str(v).strip()
            if not s:
                return None
            # If user passes YYYY-MM-DD, interpret as end-of-day to include same-day tx with time.
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
                return f"{s}T23:59:59Z"
            return s

        up_to_iso = _normalize_up_to_iso(up_to_date)
        types_list = [str(t) for t in (types or []) if t is not None and str(t).strip()]

        where: List[str] = []
        params: List[Any] = []

        if season_year is not None:
            where.append("season_year = ?")
            params.append(int(season_year))

        if types_list:
            placeholders = ",".join(["?"] * len(types_list))
            where.append(f"tx_type IN ({placeholders})")
            params.extend(types_list)

        if up_to_iso:
            # Include NULL dates defensively (legacy); ideally tx_date is always set going forward.
            where.append("(tx_date IS NULL OR tx_date <= ?)")
            params.append(up_to_iso)

        sql = "SELECT payload_json FROM transactions_log"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY COALESCE(tx_date,'') DESC, created_at DESC LIMIT ?"
        params.append(max(1, int(limit)))

        rows = self._conn.execute(sql, params).fetchall()

        def _tx_mentions_player(tx: Dict[str, Any]) -> bool:
            # 1) Contract-type tx: top-level player_id
            pid = tx.get("player_id")
            if pid is not None and str(pid) in pid_set:
                return True

            # 2) Trade-type tx: player_moves list (Phase 2 normalized payload)
            pm = tx.get("player_moves")
            if isinstance(pm, list):
                for m in pm:
                    if isinstance(m, dict) and str(m.get("player_id")) in pid_set:
                        return True

            # 3) Legacy trade payload: assets summary might have team -> players lists
            assets = tx.get("assets")
            if isinstance(assets, dict):
                for _team, a in assets.items():
                    if isinstance(a, dict):
                        players = a.get("players")
                        if isinstance(players, list) and any(str(x) in pid_set for x in players):
                            return True

            return False

        out: List[Dict[str, Any]] = []
        for r in rows:
            payload = _json_loads(r["payload_json"], None)
            if isinstance(payload, dict):
                if _tx_mentions_player(payload):
                    out.append(payload)
            else:
                # keep non-dict payloads out; rules expect dict payload
                continue

        return out

    # ------------------------
    # Contracts ledger (legacy-compatible SSOT)
    # ------------------------

    def upsert_contract_records(self, contracts_by_id: Mapping[str, Any]) -> None:
        if not contracts_by_id:
            return
        now = _utc_now_iso()
        rows = []
        for cid, c in contracts_by_id.items():
            if not isinstance(c, dict):
                continue
            # SSOT write path:
            # - First-class columns are authoritative
            # - contract_json stores extras only (SSOT keys stripped)
            # - tuple order matches the INSERT statement below
            rows.append(
                contract_to_upsert_row(
                    c,
                    now_iso=now,
                    contract_id_fallback=str(cid),
                )
            )
        with self.transaction() as cur:
            cur.executemany(
                """
                INSERT INTO contracts(
                    contract_id, player_id, team_id, start_season_id, end_season_id,
                    salary_by_season_json, contract_type, is_active, created_at, updated_at,
                    signed_date, start_season_year, years, options_json, status, contract_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(contract_id) DO UPDATE SET
                    player_id=excluded.player_id,
                    team_id=excluded.team_id,
                    start_season_id=excluded.start_season_id,
                    end_season_id=excluded.end_season_id,
                    salary_by_season_json=excluded.salary_by_season_json,
                    contract_type=excluded.contract_type,
                    is_active=excluded.is_active,
                    updated_at=excluded.updated_at,
                    signed_date=excluded.signed_date,
                    start_season_year=excluded.start_season_year,
                    years=excluded.years,
                    options_json=excluded.options_json,
                    status=excluded.status,
                    contract_json=excluded.contract_json;
                """,
                rows,
            )

    def rebuild_contract_indices(self) -> None:
        """Rebuild derived index tables from SSOT sources.

        SSOT rules (as agreed):
          - free_agents: derived from roster.team_id == 'FA'
          - active_contracts: derived from contracts.is_active == 1
          - player_contracts: derived from contracts (player_id -> contract_id)

        Intended usage:
          - integrity repair / deterministic rebuilds
        """
        now = _utc_now_iso()
        with self.transaction() as cur:
            # 1) player_contracts: one row per (player_id, contract_id) found in contracts.
            cur.execute("DELETE FROM player_contracts;")
            cur.execute(
                """
                INSERT OR IGNORE INTO player_contracts(player_id, contract_id)
                SELECT player_id, contract_id
                FROM contracts
                WHERE player_id IS NOT NULL AND contract_id IS NOT NULL;
                """
            )

            # 2) active_contracts: one active contract per player, based on contracts.is_active.
            cur.execute("DELETE FROM active_contracts;")
            active_rows = cur.execute(
                """
                SELECT contract_id, player_id, COALESCE(updated_at, created_at, '') AS ts
                FROM contracts
                WHERE is_active=1 AND player_id IS NOT NULL AND contract_id IS NOT NULL;
                """
            ).fetchall()
            best: Dict[str, Tuple[str, str]] = {}
            for r in active_rows:
                pid = str(r["player_id"])
                cid = str(r["contract_id"])
                ts = str(r["ts"] or "")
                prev = best.get(pid)
                if prev is None:
                    best[pid] = (ts, cid)
                    continue
                # Prefer newest timestamp; tie-break by contract_id for determinism.
                if ts > prev[0] or (ts == prev[0] and cid > prev[1]):
                    best[pid] = (ts, cid)

            if best:
                cur.executemany(
                    "INSERT OR REPLACE INTO active_contracts(player_id, contract_id, updated_at) VALUES (?, ?, ?);",
                    [(pid, cid, now) for pid, (_, cid) in best.items()],
                )

            # 3) free_agents: derived from roster team assignment.
            cur.execute("DELETE FROM free_agents;")
            cur.execute(
                """
                INSERT OR REPLACE INTO free_agents(player_id, updated_at)
                SELECT player_id, ?
                FROM roster
                WHERE status='active' AND UPPER(team_id)='FA' AND player_id IS NOT NULL;
                """,
                (now,),
            )   

    def ensure_contracts_bootstrapped_from_roster(self, season_year: int) -> None:
        """state에 contract ledger를 만들지 않고, DB contracts만 최소로 보장한다.

        - roster.status='active' 이면서 team_id != 'FA' 인 선수에 대해
          ACTIVE contract가 없으면 BOOT_{season_id}_{player_id} 로 1년 계약 생성
        """
        now = _utc_now_iso()
        season_year = int(season_year)
        season_id = str(season_id_from_year(season_year))
        rows = self._conn.execute(
            "SELECT player_id, team_id, salary_amount FROM roster WHERE status='active';"
        ).fetchall()
        with self.transaction() as cur:
            for r in rows:
                pid = str(normalize_player_id(r["player_id"], strict=False, allow_legacy_numeric=True))
                tid = str(r["team_id"] or "").upper()
                if tid == "FA":
                    continue
                # 이미 ACTIVE가 있으면 스킵
                exists = cur.execute(
                    "SELECT 1 FROM contracts WHERE player_id=? AND is_active=1 LIMIT 1;",
                    (pid,),
                ).fetchone()
                if exists:
                    continue
                contract_id = f"BOOT_{season_id}_{pid}"
                salary = float(r["salary_amount"] or 0.0)
                salary_by_year = {str(season_year): salary}
                contract = {
                    "contract_id": contract_id,
                    "player_id": pid,
                    "team_id": tid,
                    "signed_date": "1900-01-01",
                    "start_season_year": season_year,
                    "years": 1,
                    "salary_by_year": salary_by_year,
                    "options": [],
                    "status": "ACTIVE",
                    "contract_type": "STANDARD",
                }
                row = contract_to_upsert_row(contract, now_iso=now, contract_id_fallback=contract_id)
                cur.execute(
                    """
                    INSERT OR IGNORE INTO contracts(
                        contract_id, player_id, team_id,
                        start_season_id, end_season_id,
                        salary_by_season_json, contract_type, is_active,
                        created_at, updated_at,
                        signed_date, start_season_year, years, options_json, status, contract_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    row,
                )  


    def _contract_row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        # Columns are SSOT; contract_json is treated as extras only.
        # This prevents stale/legacy contract_json from overriding canonical fields.
        return contract_from_row(row)

    def get_contracts_map(self, *, active_only: bool = False) -> Dict[str, Dict[str, Any]]:
        sql = "SELECT * FROM contracts"
        params: List[Any] = []
        if active_only:
            sql += " WHERE is_active=1"
        rows = self._conn.execute(sql, params).fetchall()
        out: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            c = self._contract_row_to_dict(r)
            out[str(c.get("contract_id"))] = c
        return out

    def get_player_contracts_map(self) -> Dict[str, List[str]]:
        rows = self._conn.execute(
            "SELECT player_id, contract_id FROM player_contracts;"
        ).fetchall()
        out: Dict[str, List[str]] = {}
        for r in rows:
            pid = str(r["player_id"])
            cid = str(r["contract_id"])
            out.setdefault(pid, []).append(cid)
        # deterministic ordering
        for pid in list(out.keys()):
            out[pid] = sorted(out[pid])
        return out

    def get_active_contract_id_by_player(self) -> Dict[str, str]:
        rows = self._conn.execute(
            "SELECT player_id, contract_id FROM active_contracts;"
        ).fetchall()
        if rows:
            return {str(r["player_id"]): str(r["contract_id"]) for r in rows}

        # Fallback: derive from contracts table
        rows2 = self._conn.execute(
            """
            SELECT player_id, contract_id
            FROM contracts
            WHERE is_active=1
            ORDER BY updated_at DESC;
            """
        ).fetchall()
        out: Dict[str, str] = {}
        for r in rows2:
            pid = str(r["player_id"])
            if pid in out:
                continue
            out[pid] = str(r["contract_id"])
        return out

    def list_free_agents(self, *, source: str = "roster") -> List[str]:
        src = (source or "roster").strip().lower()
        if src == "roster":
            rows = self._conn.execute(
                """
                SELECT player_id
                FROM roster
                WHERE status='active' AND UPPER(team_id)='FA';
                """
            ).fetchall()
            return [str(r["player_id"]) for r in rows]
        if src == "table":
            rows = self._conn.execute(
                "SELECT player_id FROM free_agents;"
            ).fetchall()
            return [str(r["player_id"]) for r in rows]
        raise ValueError(f"Unknown source for list_free_agents: {source}")

    def get_contract_ledger_snapshot(self) -> Dict[str, Any]:
        with self.transaction() as cur:
            # Use public methods for shape; reads happen under the same BEGIN snapshot.
            # (We intentionally call the public methods so any future normalization stays centralized.)
            return {
                "contracts": self.get_contracts_map(active_only=False),
                "player_contracts": self.get_player_contracts_map(),
                "active_contract_id_by_player": self.get_active_contract_id_by_player(),
                "free_agents": self.list_free_agents(source="roster"),
            }


    # ------------------------
    # Team Strategy (SSOT)
    # ------------------------

    def get_team_strategy_map(self, *, season_year: int) -> Dict[str, str]:
        """Return mapping team_id -> strategy for the given season_year."""
        sy = int(season_year)
        rows = self._conn.execute(
            "SELECT team_id, strategy FROM team_strategy WHERE season_year=?;",
            (sy,),
        ).fetchall()
        out: Dict[str, str] = {}
        for r in rows:
            tid = str(r["team_id"] or "").upper()
            if not tid:
                continue
            out[tid] = str(r["strategy"] or "BALANCED").upper()
        return out

    def get_team_strategy(
        self,
        team_id: str,
        *,
        season_year: int,
        default: str = "BALANCED",
    ) -> str:
        """Get strategy for (team_id, season_year), or default if missing."""
        tid = normalize_team_id(team_id, strict=False)
        sy = int(season_year)
        row = self._conn.execute(
            "SELECT strategy FROM team_strategy WHERE team_id=? AND season_year=?;",
            (tid, sy),
        ).fetchone()
        if row:
            return str(row["strategy"] or default).upper()
        return str(default).upper()

    def upsert_team_strategy(self, team_id: str, *, season_year: int, strategy: str) -> None:
        """Insert or update a team's declared strategy for a season."""
        tid = normalize_team_id(team_id, strict=False)
        sy = int(season_year)
        strat = str(strategy or "").upper().strip()
        allowed = {"WIN_NOW", "BALANCED", "DEVELOP", "REBUILD"}
        if strat not in allowed:
            raise ValueError(f"Invalid strategy: {strategy}. Allowed: {sorted(allowed)}")

        now = _utc_now_iso()
        with self.transaction() as cur:
            cur.execute(
                """
                INSERT INTO team_strategy(team_id, season_year, strategy, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(team_id, season_year) DO UPDATE SET
                    strategy=excluded.strategy,
                    updated_at=excluded.updated_at;
                """,
                (tid, sy, strat, now, now),
            )

    # ------------------------
    # GM Profiles (AI)
    # ------------------------

    def upsert_gm_profile(self, team_id: str, profile: Mapping[str, Any] | None) -> None:
        """Insert or update a single GM profile (stored as JSON)."""
        tid = normalize_team_id(team_id, strict=False)
        now = _utc_now_iso()
        payload = json.dumps(
            profile or {},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            default=str,
        )
        with self.transaction() as cur:
            cur.execute(
                """
                INSERT INTO gm_profiles(team_id, profile_json, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(team_id) DO UPDATE SET
                    profile_json=excluded.profile_json,
                    updated_at=excluded.updated_at;
                """,
                (str(tid), payload, now, now),
           )

    def upsert_gm_profiles(self, profiles_by_team: Mapping[str, Any]) -> None:
        """Bulk upsert GM profiles."""
        if not profiles_by_team:
            return
        now = _utc_now_iso()
        rows: List[Tuple[str, str, str, str]] = []
        for raw_team_id, raw_profile in profiles_by_team.items():
            tid = normalize_team_id(raw_team_id, strict=False)
            payload = json.dumps(
                raw_profile or {},
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
                default=str,
            )
            rows.append((str(tid), payload, now, now))

        with self.transaction() as cur:
            cur.executemany(
                """
                INSERT INTO gm_profiles(team_id, profile_json, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(team_id) DO UPDATE SET
                    profile_json=excluded.profile_json,
                    updated_at=excluded.updated_at;
                """,
                rows,
            )

    def get_gm_profile(self, team_id: str) -> Optional[Dict[str, Any]]:
        """Return the GM profile dict for a team, or None if missing."""
        tid = normalize_team_id(team_id, strict=False)
        row = self._conn.execute(
            "SELECT profile_json FROM gm_profiles WHERE team_id=?;", (str(tid),)
        ).fetchone()
        if not row:
            return None
        try:
            value = json.loads(row["profile_json"])
            return value if isinstance(value, dict) else {"value": value}
        except (json.JSONDecodeError, TypeError):
            _warn_limited("GM_PROFILE_JSON_DECODE_FAILED", f"team_id={team_id!r}", limit=3)
            # Defensive: if JSON is corrupted, don't crash the game loop.
            return None

    def get_all_gm_profiles(self) -> Dict[str, Dict[str, Any]]:
        """Return all GM profiles keyed by team_id."""
        out: Dict[str, Dict[str, Any]] = {}
        rows = self._conn.execute(
            "SELECT team_id, profile_json FROM gm_profiles;"
        ).fetchall()
        for r in rows:
            try:
                value = json.loads(r["profile_json"])
                out[str(r["team_id"])] = value if isinstance(value, dict) else {"value": value}
            except (json.JSONDecodeError, TypeError):
                _warn_limited("GM_PROFILE_JSON_DECODE_FAILED", f"team_id={r['team_id']!r}", limit=3)
                continue
        return out

    def ensure_gm_profiles_seeded(
        self,
        team_ids: Iterable[str],
        *,
        default_profile: Optional[Mapping[str, Any]] = None,
    ) -> None:
        """Ensure each team_id has a row in gm_profiles (idempotent)."""
        ids = [str(normalize_team_id(t, strict=False)) for t in team_ids]
        if not ids:
            return
        existing = {
            str(r["team_id"])
            for r in self._conn.execute(
                "SELECT team_id FROM gm_profiles WHERE team_id IN (%s);"
                % ",".join(["?"] * len(ids)),
                ids,
            ).fetchall()
        }
        missing = [tid for tid in ids if tid not in existing]
        if not missing:
            return
        now = _utc_now_iso()
        payload = json.dumps(
            default_profile or {},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
           default=str,
        )
        rows = [(tid, payload, now, now) for tid in missing]
        with self.transaction() as cur:
            cur.executemany(
                """
                INSERT OR IGNORE INTO gm_profiles(team_id, profile_json, created_at, updated_at)
                VALUES (?, ?, ?, ?);
                """,
                rows,
            )

    # ------------------------
    # Import / Export (Excel)
    # ------------------------

    def import_roster_excel(
        self,
        excel_path: str | Path,
        *,
        sheet_name: Optional[str] = None,
        mode: str = "replace",  # "replace" or "upsert"
        strict_ids: bool = True,
    ) -> None:
        """
        Import roster Excel into SQLite.

        mode:
          - replace: wipe players/roster and re-import
          - upsert: update existing, insert new, do not delete missing rows
        strict_ids:
          - enforce PlayerID format (recommended P000001). If False, allows any non-empty string.
        """
        import pandas as pd  # local import so repo can be used without pandas in non-import contexts

        excel_path = str(excel_path)
        df = pd.read_excel(excel_path, sheet_name=(sheet_name if sheet_name is not None else 0))
        df_columns = list(df.columns)

        _require_columns(df_columns, [ROSTER_COL_TEAM_ID, ROSTER_COL_PLAYER_ID])

        # Basic cleaning: strip whitespace in key columns
        df[ROSTER_COL_TEAM_ID] = df[ROSTER_COL_TEAM_ID].astype(str).str.strip()
        df[ROSTER_COL_PLAYER_ID] = df[ROSTER_COL_PLAYER_ID].astype(str).str.strip()

        # Validate uniqueness of player_id inside this file
        assert_unique_ids(df[ROSTER_COL_PLAYER_ID].tolist(), what="player_id (in Excel)")

        players: List[PlayerRow] = []
        roster: List[RosterRow] = []
        contracts_by_id: Dict[str, Dict[str, Any]] = {}

        # Columns we treat as "core" (not attributes)
        core_cols = {
            ROSTER_COL_TEAM_ID, ROSTER_COL_PLAYER_ID,
            "Name", "name",
            "POS", "pos",
            "Age", "age",
            "HT", "height", "height_in",
            "WT", "weight", "weight_lb",
            "Salary", "salary", "salary_amount",
            "OVR", "ovr",
        }
        salary_year_cols = [
            col for col in df_columns if _SALARY_YEAR_COL_RE.match(str(col).strip())
        ]
        core_cols.update(salary_year_cols)

        for _, row in df.iterrows():
            raw_pid = row.get(ROSTER_COL_PLAYER_ID)
            raw_tid = row.get(ROSTER_COL_TEAM_ID)

            pid = normalize_player_id(raw_pid, strict=strict_ids, allow_legacy_numeric=not strict_ids)
            tid = normalize_team_id(raw_tid, strict=True)

            # pick best name/pos column
            name = row.get("name", None)
            if name is None:
                name = row.get("Name", None)
            pos = row.get("pos", None)
            if pos is None:
                pos = row.get("POS", None)

            # age
            age = row.get("age", None)
            if age is None:
                age = row.get("Age", None)
            try:
                age_i = int(age) if age is not None and str(age).strip() != "" else None
            except (TypeError, ValueError):
                _warn_limited("IMPORT_AGE_COERCE_FAILED", f"player_id={raw_pid!r} age={age!r}", limit=3)
                age_i = None

            # height / weight
            ht = row.get("height_in", None)
            if ht is None:
                ht = row.get("HT", None)
            height_in = parse_height_in(ht) if not isinstance(ht, (int, float)) else int(ht)

            wt = row.get("weight_lb", None)
            if wt is None:
                wt = row.get("WT", None)
            weight_lb = parse_weight_lb(wt) if not isinstance(wt, (int, float)) else int(wt)

            # salary
            sal = row.get("salary_amount", None)
            if sal is None:
                sal = row.get("Salary", None)
            sal_raw = "" if sal is None else str(sal).strip()
            is_two_way_boot = sal_raw == "--"
            salary_amount = 0 if is_two_way_boot else parse_salary_int(sal)

            salary_by_year, contract_options = _extract_salary_and_options_excel(row, df_columns)
            if salary_by_year or is_two_way_boot:
                if salary_by_year:
                    start_year = min(int(k) for k in salary_by_year.keys())
                    years = max(len(salary_by_year), 1)
                else:
                    # Salary-free two-way bootstrap: keep one-season placeholder at current season.
                    start_year = int(_state_season_year_ssot())
                    years = 1
                    salary_by_year = {str(start_year): 0.0}
                contract_id = f"BOOT_{season_id_from_year(start_year)}_{pid}"
                status = "ACTIVE" if str(tid).upper() != "FA" else "INACTIVE"
                contract_type = "TWO_WAY" if is_two_way_boot else "STANDARD"
                c = {
                    "contract_id": contract_id,
                    "player_id": str(pid),
                    "team_id": str(tid).upper(),
                    "signed_date": "1900-01-01",
                    "start_season_year": int(start_year),
                    "years": int(years),
                    "salary_by_year": salary_by_year,
                    "options": [] if is_two_way_boot else [dict(x) for x in (contract_options or [])],
                    "status": status,
                    "contract_type": contract_type,
                }
                if is_two_way_boot:
                    c["two_way"] = True
                    c["two_way_game_limit"] = 50
                    c["postseason_eligible"] = False
                    c["salary_free"] = True
                contracts_by_id[str(contract_id)] = c

            # ovr
            ovr = row.get("ovr", None)
            if ovr is None:
                ovr = row.get("OVR", None)
            try:
                ovr_i = int(ovr) if ovr is not None and str(ovr).strip() != "" else None
            except (TypeError, ValueError):
                _warn_limited("IMPORT_OVR_COERCE_FAILED", f"player_id={raw_pid!r} ovr={ovr!r}", limit=3)
                ovr_i = None

            # attributes: any columns not in core
            attrs: Dict[str, Any] = {}
            for col in df_columns:
                if col in core_cols:
                    continue
                v = row.get(col)
                # keep NaN out of JSON
                if v is None:
                    continue
                # pandas NaN check without importing numpy directly
                if isinstance(v, float) and v != v:
                    continue
                attrs[col] = v

            players.append(
                PlayerRow(
                    player_id=str(pid),
                    name=str(name) if name is not None else None,
                    pos=str(pos) if pos is not None else None,
                    age=age_i,
                    height_in=height_in,
                    weight_lb=weight_lb,
                    ovr=ovr_i,
                    attrs_json=json.dumps(attrs, ensure_ascii=False, separators=(",", ":")),
                )
            )
            roster.append(RosterRow(player_id=str(pid), team_id=str(tid), salary_amount=salary_amount))

        now = _utc_now_iso()
        # Ensure schema exists before transactional import
        self.init_db()
        with self.transaction() as cur:

            if mode == "replace":
                cur.execute("DELETE FROM roster;")
                cur.execute("DELETE FROM contracts;")
                cur.execute("DELETE FROM players;")
            elif mode == "upsert":
                pass
            else:
                raise ValueError("mode must be 'replace' or 'upsert'")

            # Upsert players
            cur.executemany(
                """
                INSERT INTO players(player_id, name, pos, age, height_in, weight_lb, ovr, attrs_json, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                [(p.player_id, p.name, p.pos, p.age, p.height_in, p.weight_lb, p.ovr, p.attrs_json, now, now) for p in players],
            )

            # Upsert roster
            cur.executemany(
                """
                INSERT INTO roster(player_id, team_id, salary_amount, status, updated_at)
                VALUES(?, ?, ?, 'active', ?)
                ON CONFLICT(player_id) DO UPDATE SET
                    team_id=excluded.team_id,
                    salary_amount=excluded.salary_amount,
                    status='active',
                    updated_at=excluded.updated_at;
                """,
                [(r.player_id, r.team_id, r.salary_amount, now) for r in roster],
            )

        if contracts_by_id:
            self.upsert_contract_records(contracts_by_id)
            self.rebuild_contract_indices()

        # Validate after import
        self.validate_integrity(strict_ids=strict_ids)

    def export_roster_excel(self, excel_path: str | Path) -> None:
        """Export canonical roster table back to Excel."""
        import pandas as pd

        rows = self._conn.execute(
            """
            SELECT r.team_id, p.player_id, p.name, p.pos, p.age, p.height_in, p.weight_lb, r.salary_amount, p.ovr, p.attrs_json
            FROM roster r
            JOIN players p ON p.player_id = r.player_id
            WHERE r.status='active'
            ORDER BY r.team_id, p.player_id;
            """
        ).fetchall()

        out: List[Dict[str, Any]] = []
        for r in rows:
            attrs = json.loads(r["attrs_json"]) if r["attrs_json"] else {}
            base = {
                "team_id": r["team_id"],
                "player_id": r["player_id"],
                "name": r["name"],
                "pos": r["pos"],
                "age": r["age"],
                "height_in": r["height_in"],
                "weight_lb": r["weight_lb"],
                "salary_amount": r["salary_amount"],
                "ovr": r["ovr"],
            }
            base.update(attrs)
            out.append(base)

        df = pd.DataFrame(out)
        df.to_excel(str(excel_path), index=False)

    # ------------------------
    # Reads
    # ------------------------

    def get_player(self, player_id: str) -> Dict[str, Any]:
        pid = normalize_player_id(player_id, strict=False)
        row = self._conn.execute("SELECT * FROM players WHERE player_id=?", (str(pid),)).fetchone()
        if not row:
            raise KeyError(f"player not found: {player_id}")
        d = dict(row)
        d["player_id"] = str(d.get("player_id"))
        d["attrs"] = json.loads(d["attrs_json"]) if d.get("attrs_json") else {}
        return d

    def get_team_roster(self, team_id: str) -> List[Dict[str, Any]]:
        tid = normalize_team_id(team_id, strict=True)
        rows = self._conn.execute(
            """
            SELECT p.player_id, p.name, p.pos, p.age, p.height_in, p.weight_lb, p.ovr, r.salary_amount, p.attrs_json
            FROM roster r
            JOIN players p ON p.player_id = r.player_id
            WHERE r.team_id=? AND r.status='active'
            ORDER BY p.ovr DESC, p.player_id ASC;
            """,
            (str(tid),),
        ).fetchall()
        out: List[Dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            d["player_id"] = str(d.get("player_id"))
            d["attrs"] = json.loads(d["attrs_json"]) if d.get("attrs_json") else {}
            out.append(d)
        return out

    def get_active_roster_salary_rows(self) -> List[Dict[str, Any]]:
        """Return active roster rows for fast trade-rule validation.

        This is a narrow SSOT read from the roster table only:
          - team_id
          - player_id
          - salary_amount

        Intended use:
          - build tick-level indexes (player->team, player->salary, team payroll, team roster counts)
          - avoid per-deal SQL fan-out during large search (hundreds of thousands of deal validations).
        """
        rows = self._conn.execute(
            "SELECT team_id, player_id, salary_amount FROM roster WHERE status='active';"
        ).fetchall()

        out: List[Dict[str, Any]] = []
        for r in rows:
            # Normalize defensively (SSOT should already be canonical).
            tid = str(normalize_team_id(r["team_id"], strict=True)).upper()
            pid = str(normalize_player_id(r["player_id"], strict=False, allow_legacy_numeric=True))
            sal = r["salary_amount"]
            out.append(
                {
                    "team_id": tid,
                    "player_id": pid,
                    "salary_amount": int(sal) if sal is not None else None,
                }
            )
        return out


    def get_team_id_by_player(self, player_id: str) -> str:
        pid = normalize_player_id(player_id, strict=False, allow_legacy_numeric=True)
        row = self._conn.execute(
            "SELECT team_id FROM roster WHERE player_id=? AND status='active';",
            (str(pid),),
        ).fetchone()
        if not row:
            raise KeyError(f"active roster entry not found for player_id={player_id}")
        return str(row["team_id"])

    def get_team_ids_by_players(self, player_ids: Iterable[str]) -> Dict[str, str]:
        """Bulk lookup: player_id -> active roster team_id.

        Notes:
        - Uses SQLite SSOT (roster table).
        - Returns only players found on an active roster.
        - Keys are normalized canonical player_id strings.
        """
        # Normalize and de-dup while preserving deterministic order.
        normalized: List[str] = []
        seen: set[str] = set()
        for pid in player_ids:
            npid = str(normalize_player_id(pid, strict=False, allow_legacy_numeric=True))
            if npid in seen:
                continue
            seen.add(npid)
            normalized.append(npid)

        if not normalized:
            return {}

        out: Dict[str, str] = {}
        # SQLite default variable limit is often 999; chunk defensively.
        CHUNK = 900
        for i in range(0, len(normalized), CHUNK):
            chunk = normalized[i : i + CHUNK]
            placeholders = ",".join(["?"] * len(chunk))
            rows = self._conn.execute(
                f"SELECT player_id, team_id FROM roster WHERE status='active' AND player_id IN ({placeholders});",
                chunk,
            ).fetchall()
            for r in rows:
                out[str(r["player_id"])] = str(r["team_id"]).upper()
        return out

    def get_active_signed_dates_by_players(self, player_ids: Iterable[str]) -> Dict[str, Optional[str]]:
        """Bulk lookup: player_id -> signed_date for the active contract.

        Notes:
        - Uses SQLite SSOT (contracts table).
        - For players without an active contract, value will be None.
        - Keys are normalized canonical player_id strings.
        """
        normalized: List[str] = []
        seen: set[str] = set()
        for pid in player_ids:
            npid = str(normalize_player_id(pid, strict=False, allow_legacy_numeric=True))
            if npid in seen:
                continue
            seen.add(npid)
            normalized.append(npid)

        if not normalized:
            return {}

        # Pre-fill with None so callers can fail-fast if needed.
        out: Dict[str, Optional[str]] = {pid: None for pid in normalized}

        CHUNK = 900
        for i in range(0, len(normalized), CHUNK):
            chunk = normalized[i : i + CHUNK]
            placeholders = ",".join(["?"] * len(chunk))
            # Prefer contracts.is_active (robust even if derived tables aren't rebuilt).
            rows = self._conn.execute(
                f"""
                SELECT player_id, signed_date, updated_at
                FROM contracts
                WHERE is_active=1 AND player_id IN ({placeholders})
                ORDER BY updated_at DESC;
                """,
                chunk,
            ).fetchall()

            # If duplicates exist (shouldn't), keep the most recently updated.
            for r in rows:
                pid = str(r["player_id"])
                if out.get(pid) is None:
                    out[pid] = r["signed_date"]

        return out

    def get_salary_amount(self, player_id: str) -> Optional[int]:
        pid = normalize_player_id(player_id, strict=False, allow_legacy_numeric=True)
        row = self._conn.execute(
            "SELECT salary_amount FROM roster WHERE player_id=? AND status='active';",
            (str(pid),),
        ).fetchone()
        if not row:
            return None
        salary = row["salary_amount"]
        return int(salary) if salary is not None else None

    def get_roster_player_ids(self, team_id: str) -> set[str]:
        tid = normalize_team_id(team_id, strict=True)
        rows = self._conn.execute(
            "SELECT player_id FROM roster WHERE team_id=? AND status='active';",
            (str(tid),),
        ).fetchall()
        return {str(r["player_id"]) for r in rows}

    def get_all_player_ids(self) -> set[str]:
        rows = self._conn.execute("SELECT player_id FROM players;").fetchall()
        return {str(r["player_id"]) for r in rows}

    def list_teams(self) -> List[str]:
        rows = self._conn.execute("SELECT DISTINCT team_id FROM roster WHERE status='active' ORDER BY team_id;").fetchall()
        return [r["team_id"] for r in rows]

    # ------------------------
    # Writes (Roster operations)
    # ------------------------

    def trade_player(self, player_id: str, to_team_id: str) -> None:
        """Move player to another team."""
        pid = normalize_player_id(player_id, strict=False)
        to_tid = normalize_team_id(to_team_id, strict=True)
        now = _utc_now_iso()

        with self.transaction() as cur:
            # Must exist in roster
            exists = cur.execute("SELECT team_id FROM roster WHERE player_id=? AND status='active';", (str(pid),)).fetchone()
            if not exists:
                raise KeyError(f"active roster entry not found for player_id={player_id}")

            cur.execute(
                "UPDATE roster SET team_id=?, updated_at=? WHERE player_id=?;",
                (str(to_tid), now, str(pid)),
            )
            # If there's an active contract, update team_id too (optional, but helps consistency)
            cur.execute(
                "UPDATE contracts SET team_id=?, updated_at=? WHERE player_id=? AND is_active=1;",
                (str(to_tid), now, str(pid)),
            )

    def release_to_free_agency(self, player_id: str) -> None:
        """Set team_id to FA."""
        self.trade_player(player_id, "FA")

    def set_salary(self, player_id: str, salary_amount: int) -> None:
        pid = normalize_player_id(player_id, strict=False)
        now = _utc_now_iso()
        with self.transaction() as cur:
            cur.execute(
                "UPDATE roster SET salary_amount=?, updated_at=? WHERE player_id=?;",
                (int(salary_amount), now, str(pid)),
            )

    # ------------------------
    # Integrity
    # ------------------------

    def validate_integrity(self, *, strict_ids: bool = True) -> None:
        """
        Fail fast on ID split / missing rows / invalid team codes.
        Run this after imports and after any batch roster changes.
        """
        # schema version check
        row = self._conn.execute("SELECT value FROM meta WHERE key='schema_version';").fetchone()
        if not row:
            raise ValueError("DB meta.schema_version missing (run init_db)")
        if row["value"] != SCHEMA_VERSION:
            raise ValueError(f"DB schema_version {row['value']} != expected {SCHEMA_VERSION}")

        # player_id uniqueness is enforced by PK; also validate format if strict
        if strict_ids:
            rows = self._conn.execute("SELECT player_id FROM players;").fetchall()
            for r in rows:
                normalize_player_id(r["player_id"], strict=True)

        # roster must reference existing players (FK enforces, but keep explicit check)
        bad = self._conn.execute(
            """
            SELECT r.player_id
            FROM roster r
            LEFT JOIN players p ON p.player_id = r.player_id
            WHERE p.player_id IS NULL;
            """
        ).fetchall()
        if bad:
            raise ValueError(f"roster has player_ids missing in players: {[x['player_id'] for x in bad]}")

        # team_id normalization check
        rows = self._conn.execute("SELECT DISTINCT team_id FROM roster WHERE status='active';").fetchall()
        for r in rows:
            normalize_team_id(r["team_id"], strict=True)

        # Guardrail (dev-time): contract_json must be extras-only.
        # After Option A migration, ALL SSOT fields must live in first-class columns.
        # If SSOT keys appear inside contract_json, it indicates a regression that can
        # resurrect stale values and reintroduce SSOT split bugs.
        rows = self._conn.execute(
            """
            SELECT contract_id, contract_json
            FROM contracts
            WHERE contract_json IS NOT NULL
              AND TRIM(contract_json) != '';
            """
        ).fetchall()
        for r in rows:
            cid = str(r["contract_id"])
            raw = r["contract_json"]
            if raw is None:
                continue
            if isinstance(raw, str) and raw.strip().lower() == "null":
                continue
            try:
                obj = json.loads(raw) if not isinstance(raw, (dict, list)) else raw
            except Exception as exc:
                raise ValueError(f"contracts.contract_json invalid JSON (contract_id={cid}): {exc}") from exc
            if obj is None:
                continue
            if not isinstance(obj, dict):
                raise ValueError(
                    "contracts.contract_json must be a JSON object (extras-only). "
                    f"Found {type(obj).__name__} (contract_id={cid})"
                )
            bad_keys = sorted(set(obj.keys()).intersection(CONTRACT_SSOT_FIELDS))
            if bad_keys:
                raise ValueError(
                    "contracts.contract_json contains SSOT keys (extras-only violation): "
                    f"contract_id={cid} keys={bad_keys}"
                )

        # draft_picks.protection_json must be canonical (SSOT)
        rows = self._conn.execute(
            """
            SELECT pick_id, protection_json
            FROM draft_picks
            WHERE protection_json IS NOT NULL
              AND TRIM(protection_json) != '';
            """
        ).fetchall()
        if rows:
            try:
                from trades.protection import normalize_protection
                from trades.errors import TradeError as _TradeError
            except Exception as exc:  # pragma: no cover
                raise ImportError("trades.protection is required") from exc
            for r in rows:
                pid = str(r["pick_id"])
                raw = r["protection_json"]
                if raw is None:
                    continue
                if isinstance(raw, str) and raw.strip().lower() == "null":
                    continue
                try:
                    obj = json.loads(raw) if not isinstance(raw, (dict, list)) else raw
                except Exception as exc:
                    raise ValueError(f"draft_picks.protection_json invalid JSON (pick_id={pid}): {exc}") from exc
                if obj is None:
                    continue
                if not isinstance(obj, dict):
                    raise ValueError(
                        "draft_picks.protection_json must be a JSON object. "
                        f"Found {type(obj).__name__} (pick_id={pid})"
                    )
                try:
                    norm = normalize_protection(obj, pick_id=pid)
                except _TradeError as exc:
                    raise ValueError(f"draft_picks.protection_json invalid schema (pick_id={pid}): {exc}") from exc
                if obj != norm:
                    raise ValueError(
                        "draft_picks.protection_json not canonical (SSOT violation): "
                        f"pick_id={pid} got={obj} expected={norm}"
                    )

        # No duplicate active roster entries (PK ensures), but check status sanity
        rows = self._conn.execute("SELECT COUNT(*) AS c FROM roster WHERE status='active';").fetchone()
        if rows and rows["c"] <= 0:
            raise ValueError("no active roster entries found")

    def _smoke_check(self) -> None:
        """
        Lightweight self-check for repo wiring.
        Runs init_db(), and only validates if there is roster data present.
        """
        self.init_db()
        has_roster = self._conn.execute("SELECT 1 FROM roster LIMIT 1;").fetchone()
        if has_roster:
            self.validate_integrity()

    # ------------------------
    # Convenience
    # ------------------------

    def __enter__(self) -> "LeagueRepo":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


# ----------------------------
# CLI
# ----------------------------

def _cmd_init(args) -> None:
    with LeagueRepo(args.db) as repo:
        repo.init_db()
    print(f"OK: initialized {args.db}")

def _cmd_import_roster(args) -> None:
    with LeagueRepo(args.db) as repo:
        repo.import_roster_excel(args.excel, sheet_name=args.sheet, mode=args.mode, strict_ids=not args.allow_legacy_ids)
    print(f"OK: imported roster from {args.excel} into {args.db}")

def _cmd_export_roster(args) -> None:
    with LeagueRepo(args.db) as repo:
        repo.export_roster_excel(args.excel)
    print(f"OK: exported roster to {args.excel}")

def _cmd_validate(args) -> None:
    with LeagueRepo(args.db) as repo:
        repo.validate_integrity(strict_ids=not args.allow_legacy_ids)
    print(f"OK: validation passed for {args.db}")

def main(argv: Optional[Sequence[str]] = None) -> None:
    p = argparse.ArgumentParser(description="LeagueRepo (SQLite single source of truth)")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="initialize DB schema")
    p_init.add_argument("--db", required=True, help="path to sqlite db file")
    p_init.set_defaults(func=_cmd_init)

    p_imp = sub.add_parser("import_roster", help="import roster excel into DB")
    p_imp.add_argument("--db", required=True, help="path to sqlite db file")
    p_imp.add_argument("--excel", required=True, help="path to roster excel file")
    p_imp.add_argument("--sheet", default=None, help="sheet name (optional)")
    p_imp.add_argument("--mode", choices=["replace", "upsert"], default="replace")
    p_imp.add_argument("--allow-legacy-ids", action="store_true", help="allow non-P000001 style player_id")
    p_imp.set_defaults(func=_cmd_import_roster)

    p_exp = sub.add_parser("export_roster", help="export roster from DB to excel")
    p_exp.add_argument("--db", required=True, help="path to sqlite db file")
    p_exp.add_argument("--excel", required=True, help="output excel path")
    p_exp.set_defaults(func=_cmd_export_roster)

    p_val = sub.add_parser("validate", help="validate DB integrity")
    p_val.add_argument("--db", required=True, help="path to sqlite db file")
    p_val.add_argument("--allow-legacy-ids", action="store_true", help="allow non-P000001 style player_id")
    p_val.set_defaults(func=_cmd_validate)

    args = p.parse_args(argv)
    args.func(args)

if __name__ == "__main__":
    main()
