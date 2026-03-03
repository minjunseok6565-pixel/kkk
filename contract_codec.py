"""
contract_codec.py

Contracts SSOT serialization/deserialization helpers.

Background
----------
This project stores the authoritative contract ledger in the SQLite `contracts` table.
Historically, the table also included a `contract_json` column that duplicated many
canonical fields (team_id, salary_by_year, options, etc.). That duplication creates
a class of SSOT split bugs: a write path updates columns but forgets to update JSON,
and later a read path prefers JSON, resurrecting stale values.

Design goal (Option A)
----------------------
1) Treat first-class SQL columns as the *only* SSOT for contract state.
2) Store only "extras" (non-SSOT fields) in `contracts.contract_json`.
3) On read, build the canonical contract dict from columns, then merge extras
   after stripping any SSOT keys (so legacy/buggy JSON can never override SSOT).
4) On write, compute and persist SSOT fields into columns and persist only extras
   into `contract_json` (SSOT keys removed). This makes SSOT split bugs impossible
   to reintroduce accidentally.

This module is intentionally dependency-light:
- It MUST NOT import league_repo / league_service (avoid circular imports).
- It MAY import schema normalization helpers.

Public API
----------
- CONTRACT_SSOT_FIELDS: keys that define canonical contract state in the in-memory dict.
- CONTRACT_JSON_DISALLOWED_KEYS: keys stripped from extras when reading/writing contract_json.
- CONTRACTS_UPSERT_COLUMNS: column order for the contracts INSERT/UPSERT tuple.
- contract_from_row(row): decode DB row -> canonical contract dict (+ extras).
- contract_to_upsert_row(contract, now_iso, contract_id_fallback=None): encode contract dict -> tuple
  matching CONTRACTS_UPSERT_COLUMNS.
- extract_contract_extras(contract): compute extras-only dict suitable for contract_json storage.
"""

from __future__ import annotations

import json
import math
from typing import Any, Dict, FrozenSet, Mapping, MutableMapping, Optional, Sequence, Tuple

from schema import normalize_player_id, normalize_team_id, season_id_from_year


# ---------------------------------------------------------------------------
# SSOT key sets
# ---------------------------------------------------------------------------

# Canonical contract fields that MUST be represented by first-class SQL columns.
#
# These keys are used throughout the current codebase (valuation, offseason processing,
# options, trade rules) and correspond directly to columns in the `contracts` table:
#   - contract_id            -> contracts.contract_id
#   - player_id              -> contracts.player_id
#   - team_id                -> contracts.team_id
#   - signed_date            -> contracts.signed_date
#   - start_season_year      -> contracts.start_season_year
#   - years                  -> contracts.years
#   - salary_by_year         -> contracts.salary_by_season_json
#   - options                -> contracts.options_json
#   - status                 -> contracts.status
#   - is_active              -> contracts.is_active  (derived from status on write)
#   - contract_type          -> contracts.contract_type
CONTRACT_SSOT_FIELDS: FrozenSet[str] = frozenset(
    {
        "contract_id",
        "player_id",
        "team_id",
        "signed_date",
        "start_season_year",
        "years",
        "salary_by_year",
        "options",
        "status",
        "is_active",
        "contract_type",
    }
)

# Additional keys that should never live inside contract_json.
#
# This is a strict superset of CONTRACT_SSOT_FIELDS and includes:
# - DB-only column names that should not be duplicated in JSON
# - legacy aliases that occasionally appear in downstream code paths
#   (e.g. valuation tolerates salary_by_season)
CONTRACT_JSON_DISALLOWED_KEYS: FrozenSet[str] = frozenset(
    set(CONTRACT_SSOT_FIELDS)
    | {
        # derived/DB columns (not part of the canonical dict, but still SSOT at DB level)
        "start_season_id",
        "end_season_id",
        "salary_by_season_json",
        "options_json",
        "created_at",
        "updated_at",
        "contract_json",
        # legacy aliases / tolerated fallbacks
        "salary_by_season",
        "salary_by_season_map",
        "option_years",
        "option_years_sorted",
    }
)

# Column order used by LeagueRepo/LeagueService contracts UPSERT.
# Keep this list in sync with the INSERT statement in both modules.
CONTRACTS_UPSERT_COLUMNS: Tuple[str, ...] = (
    "contract_id",
    "player_id",
    "team_id",
    "start_season_id",
    "end_season_id",
    "salary_by_season_json",
    "contract_type",
    "is_active",
    "created_at",
    "updated_at",
    "signed_date",
    "start_season_year",
    "years",
    "options_json",
    "status",
    "contract_json",
)


# ---------------------------------------------------------------------------
# JSON helpers (match existing project conventions)
# ---------------------------------------------------------------------------

def _json_dumps(obj: Any) -> str:
    return json.dumps(
        obj,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    )


def _json_loads(value: Any, default: Any) -> Any:
    """Safe JSON loader.

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
        return default


# ---------------------------------------------------------------------------
# Small coercion helpers
# ---------------------------------------------------------------------------

def _safe_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    try:
        if value is None:
            return default
        # bool is int subclass; reject bool explicitly.
        if isinstance(value, bool):
            return default
        return int(value)
    except Exception:
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return float(default)
        # bool is int subclass; reject bool explicitly.
        if isinstance(value, bool):
            return float(default)
        x = float(value)
        if not math.isfinite(x):
            return float(default)
        return x
    except Exception:
        return float(default)


def _as_str(value: Any) -> str:
    try:
        return str(value)
    except Exception:
        return ""


def _row_has_key(row: Any, key: str) -> bool:
    try:
        keys = row.keys()  # sqlite3.Row supports keys()
        return key in keys
    except Exception:
        return isinstance(row, Mapping) and key in row


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    """Get a value from sqlite3.Row / dict-like mapping safely."""
    try:
        if _row_has_key(row, key):
            return row[key]
    except Exception:
        pass
    if isinstance(row, Mapping):
        return row.get(key, default)
    return default


def _normalize_salary_by_year_map(obj: Any) -> Dict[str, float]:
    """Normalize salary map into storage shape: { '2025': 15161800.0, ... }."""
    if not isinstance(obj, Mapping):
        return {}
    out: Dict[str, float] = {}
    for k, v in obj.items():
        try:
            year_i = int(k)
        except Exception:
            # ignore non-season keys (data cleanliness)
            continue
        val_f = _safe_float(v, 0.0)
        # Keep zero values; some flows may intentionally encode 0 for dead years.
        out[str(year_i)] = float(val_f)
    return out


def _normalize_options_list(obj: Any) -> list:
    """Normalize contract["options"] into a JSON-friendly list.

    Safety rules:
    - Only list/tuple are accepted (options order matters).
    - Any other type (including dict) becomes [].
    """
    if obj is None:
        return []
    if isinstance(obj, list):
        return list(obj)
    if isinstance(obj, tuple):
        return list(obj)
    return []


def _status_to_is_active_int(status: Any) -> int:
    return 1 if str(status or "").strip().upper() == "ACTIVE" else 0


def _normalize_contract_type(value: Any) -> str:
    ct = str(value or "").strip().upper()
    return ct if ct else "STANDARD"


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def extract_contract_extras(contract: Mapping[str, Any]) -> Dict[str, Any]:
    """Return a dict containing only extras (non-SSOT fields).

    The returned dict is safe to store in contracts.contract_json.

    Notes
    -----
    - SSOT/DB keys are removed according to CONTRACT_JSON_DISALLOWED_KEYS.
    - The function performs a shallow copy only (nested values are reused).
    """
    if not isinstance(contract, Mapping):
        return {}
    extras: Dict[str, Any] = dict(contract)
    for k in CONTRACT_JSON_DISALLOWED_KEYS:
        extras.pop(k, None)
    return extras


def contract_from_row(row: Any) -> Dict[str, Any]:
    """Decode a `contracts` table row into a canonical contract dict (+ extras).

    Rules
    -----
    - SSOT fields always come from columns.
    - contract_json is treated as extras only; SSOT keys in JSON are ignored/stripped.
    """
    # Canonical from columns
    contract_id = _as_str(_row_get(row, "contract_id", ""))
    player_id = _as_str(_row_get(row, "player_id", ""))
    team_id = _as_str(_row_get(row, "team_id", "")).upper()

    signed_date = _row_get(row, "signed_date", None)
    signed_date_s = _as_str(signed_date) if signed_date is not None else None

    start_year_i = _safe_int(_row_get(row, "start_season_year", None), None)
    years_i = _safe_int(_row_get(row, "years", None), None)

    salary_raw = _json_loads(_row_get(row, "salary_by_season_json", None), {})
    salary_by_year = _normalize_salary_by_year_map(salary_raw)

    options_raw = _json_loads(_row_get(row, "options_json", None), [])
    options = _normalize_options_list(options_raw)

    is_active_col_raw = _row_get(row, "is_active", 0)
    try:
        is_active = bool(int(is_active_col_raw) if is_active_col_raw is not None else 0)
    except Exception:
        is_active = False

    status_col = _row_get(row, "status", None)
    status = str(status_col or "").strip().upper()
    # Match existing Service loader behavior: if status is blank but is_active is true, treat as ACTIVE.
    if not status and is_active:
        status = "ACTIVE"

    contract_type_col = _row_get(row, "contract_type", None)
    contract_type = _normalize_contract_type(contract_type_col)

    out: Dict[str, Any] = {
        "contract_id": contract_id,
        "player_id": player_id,
        "team_id": team_id,
        "signed_date": signed_date_s,
        "start_season_year": start_year_i,
        "years": years_i,
        "salary_by_year": salary_by_year,
        "options": options,
        "status": status,
        "is_active": bool(is_active),
        "contract_type": contract_type,
    }

    # Merge extras from contract_json (if any), after stripping SSOT keys.
    raw_json = _row_get(row, "contract_json", None)
    extras_obj = _json_loads(raw_json, None)
    if isinstance(extras_obj, Mapping):
        extras: Dict[str, Any] = dict(extras_obj)
        for k in CONTRACT_JSON_DISALLOWED_KEYS:
            extras.pop(k, None)
        # Merge extras last (cannot override SSOT since we removed those keys).
        out.update(extras)

    return out


def derive_contract_end_season_id(contract: Mapping[str, Any]) -> Optional[str]:
    """Best-effort derive contract end season_id (e.g. '2025-26').

    The canonical contract dict intentionally avoids duplicating DB-only derived
    columns (like ``end_season_id``) into the persisted ``contract_json`` field.
    Consumers that need an end season id should derive it from canonical SSOT
    fields, not from JSON.

    Precedence:
      1) If contract already contains a non-empty ``end_season_id`` key, use it.
         (Some callers may attach DB columns to the dict for convenience.)
      2) Derive from (start_season_year, years) using :func:`schema.season_id_from_year`.
      3) Fallback: infer from the max year key in salary_by_year.

    Returns:
        season_id string like '2025-26', or None if unavailable.
    """
    if not isinstance(contract, Mapping):
        return None

    # 1) Prefer explicit DB-derived field if present.
    end_sid = contract.get("end_season_id")
    if end_sid:
        s = str(end_sid).strip()
        if s:
            return s

    # 2) Derive from canonical SSOT columns.
    start_year_i = _safe_int(contract.get("start_season_year"), None)
    years_i = _safe_int(contract.get("years"), None)
    if start_year_i:
        y = years_i if (years_i and years_i > 0) else 1
        end_year = int(start_year_i) + int(y) - 1
        try:
            return str(season_id_from_year(int(end_year)))
        except Exception:
            return None

    # 3) Fallback: infer from salary_by_year keys.
    sal = contract.get("salary_by_year")
    if isinstance(sal, Mapping) and sal:
        ys: list[int] = []
        for k in sal.keys():
            try:
                ys.append(int(k))
            except Exception:
                continue
        if ys:
            try:
                return str(season_id_from_year(int(max(ys))))
            except Exception:
                return None

    return None


def contract_to_upsert_row(
    contract: Mapping[str, Any],
    *,
    now_iso: str,
    contract_id_fallback: Optional[str] = None,
) -> Tuple[Any, ...]:
    """Encode a contract dict into a tuple matching CONTRACTS_UPSERT_COLUMNS.

    This function implements the same core semantics as the existing
    LeagueRepo.upsert_contract_records / LeagueService._upsert_contract_records_in_cur:

    - player_id is normalized with allow_legacy_numeric=True (strict=False).
    - team_id is normalized with strict=False when non-empty; empty team_id becomes "".
    - is_active column is derived from status == "ACTIVE" (not from contract["is_active"]).
    - start/end season ids are derived from start_season_year and years.
    - salary_by_year stored into salary_by_season_json (canonical).
    - options stored into options_json (canonical).
    - contract_json stores only extras (SSOT keys stripped); if no extras, stores NULL.

    Raises
    ------
    ValueError:
        If contract_id or player_id is missing/invalid enough that normalization fails.
        (Fail-fast is desirable in development to prevent silent SSOT corruption.)
    """
    if not isinstance(contract, Mapping):
        raise TypeError("contract_to_upsert_row expects a mapping/dict-like contract")

    # Identify contract_id (primary key)
    contract_id = str(contract.get("contract_id") or contract_id_fallback or "").strip()
    if not contract_id:
        raise ValueError("contract_id is required (missing from contract dict and fallback)")

    # Normalize player_id (required; fail-fast if missing)
    player_id_raw = contract.get("player_id")
    player_id_norm = str(normalize_player_id(player_id_raw, strict=False, allow_legacy_numeric=True))

    # Normalize team_id (nullable-ish in current code; empty string is allowed by schema)
    team_raw = contract.get("team_id")
    team_id_norm = ""
    if team_raw is not None:
        team_s = str(team_raw).strip()
        if team_s:
            # Do not use strict=True here; current repo/service use strict=False for contract upserts.
            team_id_norm = str(normalize_team_id(team_s, strict=False)).upper()

    signed_date = contract.get("signed_date")
    signed_date_s = str(signed_date) if signed_date is not None else None

    start_year_i = _safe_int(contract.get("start_season_year"), None)
    years_i = _safe_int(contract.get("years"), None)

    # Derived season ids (stored in DB for convenience / indexing)
    start_season_id = str(season_id_from_year(start_year_i)) if start_year_i else None
    if start_year_i and years_i:
        end_year = start_year_i + max((years_i or 1) - 1, 0)
        end_season_id = str(season_id_from_year(end_year))
    else:
        end_season_id = start_season_id

    # Canonical salary/options
    salary_src = contract.get("salary_by_year")
    if salary_src is None:
        # tolerate legacy key
        salary_src = contract.get("salary_by_season")
    salary_by_year = _normalize_salary_by_year_map(salary_src or {})
    salary_json = _json_dumps(salary_by_year)

    options = _normalize_options_list(contract.get("options") or [])
    options_json = _json_dumps(options)

    status = str(contract.get("status") or "").strip().upper()
    is_active_int = _status_to_is_active_int(status)

    contract_type = _normalize_contract_type(contract.get("contract_type"))

    # Extras-only JSON storage (SSOT keys removed)
    extras = extract_contract_extras(contract)
    contract_json = _json_dumps(extras) if extras else None

    created_at = str(now_iso)
    updated_at = str(now_iso)

    row: Tuple[Any, ...] = (
        contract_id,
        player_id_norm,
        team_id_norm,
        start_season_id,
        end_season_id,
        salary_json,
        contract_type,
        int(is_active_int),
        created_at,
        updated_at,
        signed_date_s,
        start_year_i,
        years_i,
        options_json,
        status,
        contract_json,
    )

    # Defensive: ensure the tuple length matches the expected schema order.
    if len(row) != len(CONTRACTS_UPSERT_COLUMNS):
        raise AssertionError(
            f"contract_to_upsert_row produced {len(row)} values, expected {len(CONTRACTS_UPSERT_COLUMNS)}"
        )

    return row
