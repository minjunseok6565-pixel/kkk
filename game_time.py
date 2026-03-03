from __future__ import annotations

import datetime as _dt
from typing import Any, Optional


def game_date() -> _dt.date:
    # local import: 순환참조 방지
    import state
    # state.get_current_date_as_date()는 OS fallback 없이 fail-loud 여야 함
    return state.get_current_date_as_date()


def game_date_iso() -> str:
    return game_date().isoformat()


def require_date_iso(value: Any, *, field: str = "date_iso") -> str:
    """
    Ensure value is a valid YYYY-MM-DD (ISO date) and return normalized date ISO.
    Fail-loud. Never fall back to OS clock.
    """
    if value is None:
        raise ValueError(f"{field} is required (in-game date ISO; OS clock disabled)")
    s = str(value)[:10]
    try:
        _dt.date.fromisoformat(s)
    except ValueError as exc:
        raise ValueError(f"Invalid {field}: {value!r}") from exc
    return s


def utc_like_from_date_iso(date_iso: Any, *, field: str = "date_iso") -> str:
    """
    Convert a YYYY-MM-DD into a UTC-like timestamp string at midnight.
    """
    s = require_date_iso(date_iso, field=field)
    return f"{s}T00:00:00Z"


# --- Backwards/alias helpers (네가 적은 이름 그대로도 지원) ---

def utc_like_iso_from_date_iso(date_iso: Any) -> str:
    # alias
    return utc_like_from_date_iso(date_iso, field="date_iso")


def now_utc_like_iso() -> str:
    # OS 시간이 아니라 “게임 날짜 기반 UTC-like”를 의미
    return utc_like_from_date_iso(game_date_iso(), field="game_date")
