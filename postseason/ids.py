from __future__ import annotations

"""Deterministic IDs for postseason entities.

Why deterministic?
- News de-dup & caching becomes reliable
- Debugging / replay support becomes much easier
- Avoids random UUIDs that change across runs

ID conventions
--------------
- Play-In game IDs:
    PI{season_year}_{CONF}_{KEY}
    ex) PI2026_E_7V8, PI2026_W_FINAL
- Playoffs series IDs:
    PO{season_year}_{CONF}_{ROUND}_{LABEL}
    ex) PO2026_E_R1_1V8, PO2026_W_R2_SF1, PO2026_F_F_FIN
- Playoffs game IDs:
    {series_id}_G{n}
    ex) PO2026_E_R1_1V8_G3
"""

import hashlib
from typing import Literal, Optional

import state


Conf = Literal["E", "W", "F"]


def stable_int_seed(text: str) -> int:
    """Return a stable 32-bit int seed from arbitrary text."""
    h = hashlib.sha1(str(text).encode("utf-8")).hexdigest()
    return int(h[:8], 16)


def _parse_season_year_from_season_id(season_id: str) -> Optional[int]:
    """Parse start year from season_id like '2025-26'."""
    if not season_id:
        return None
    s = str(season_id).strip()
    if len(s) < 4:
        return None
    head = s.split("-", 1)[0].strip()
    try:
        year = int(head)
    except Exception:
        return None
    return year if year > 0 else None


def current_season_year() -> int:
    """Best-effort season year from SSOT state.

    Prefers league.season_year, falls back to parsing active_season_id.
    """
    ctx = state.get_league_context_snapshot() or {}
    sy = ctx.get("season_year")
    try:
        if sy is not None:
            sy_i = int(sy)
            if sy_i > 0:
                return sy_i
    except Exception:
        pass

    active = ctx.get("active_season_id")
    year = _parse_season_year_from_season_id(str(active or ""))
    if year:
        return year

    raise RuntimeError("current_season_year unavailable: league.season_year/active_season_id missing or invalid")


def _conf_code(conf: str) -> Conf:
    c = str(conf or "").strip().lower()
    if c.startswith("e"):
        return "E"
    if c.startswith("w"):
        return "W"
    return "F"


def make_play_in_game_id(season_year: int, conf: str, key: str) -> str:
    """Create deterministic Play-In game_id."""
    conf_code = _conf_code(conf)
    k = str(key).strip().upper().replace(" ", "")
    if not k:
        raise ValueError("play-in key is empty")
    return f"PI{int(season_year)}_{conf_code}_{k}"


def make_series_id(season_year: int, conf: str, round_code: str, label: str) -> str:
    conf_code = _conf_code(conf)
    rc = str(round_code).strip().upper().replace(" ", "")
    lb = str(label).strip().upper().replace(" ", "")
    if not rc:
        raise ValueError("round_code is empty")
    if not lb:
        raise ValueError("label is empty")
    return f"PO{int(season_year)}_{conf_code}_{rc}_{lb}"


def make_series_game_id(series_id: str, game_number: int) -> str:
    sid = str(series_id).strip()
    if not sid:
        raise ValueError("series_id is empty")
    n = int(game_number)
    if n <= 0:
        raise ValueError("game_number must be >= 1")
    return f"{sid}_G{n}"
