from __future__ import annotations

"""Utility helpers for the agency subsystem.

Important: This project uses an in-game clock (game_time/state) and generally avoids
OS time. Helpers here are written so they can be used both in-game and in unit tests.
"""

import datetime as _dt
import hashlib
import json
import math
from typing import Any, Dict, Mapping, Optional


# ---------------------------------------------------------------------------
# JSON helpers (stable, compact)
# ---------------------------------------------------------------------------


def json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str)


def json_loads(value: Any, *, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


# ---------------------------------------------------------------------------
# Math helpers
# ---------------------------------------------------------------------------


def safe_float(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return float(default)
        return float(x)
    except Exception:
        return float(default)


def safe_float_opt(x: Any) -> Optional[float]:
    """Return float(x) if possible, else None.

    Unlike safe_float(), this helper preserves *missing* values (None/empty).
    It is intended for parsing optional targets in payloads/JSON.
    """
    try:
        if x is None:
            return None
        if isinstance(x, str) and not x.strip():
            return None
        return float(x)
    except Exception:
        return None



def safe_int(x: Any, default: int = 0) -> int:
    try:
        if x is None:
            return int(default)
        return int(x)
    except Exception:
        return int(default)


def clamp(x: Any, lo: float, hi: float) -> float:
    xf = safe_float(x, lo)
    if xf < lo:
        return float(lo)
    if xf > hi:
        return float(hi)
    return float(xf)


def clamp01(x: Any) -> float:
    return clamp(x, 0.0, 1.0)


def sigmoid(x: float) -> float:
    # stable sigmoid
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


# ---------------------------------------------------------------------------
# Date helpers (ISO strings)
# ---------------------------------------------------------------------------


def norm_date_iso(value: Any) -> Optional[str]:
    """Normalize date-like value to YYYY-MM-DD, else None."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    s = s[:10]
    try:
        _dt.date.fromisoformat(s)
    except Exception:
        return None
    return s


def norm_month_key(value: Any) -> Optional[str]:
    """Normalize YYYY-MM string, else None."""
    if value is None:
        return None
    s = str(value).strip()
    if len(s) < 7:
        return None
    s = s[:7]
    try:
        y, m = s.split("-")
        _dt.date(int(y), int(m), 1)
    except Exception:
        return None
    return f"{int(y):04d}-{int(m):02d}"


def date_add_days(date_iso: str, days: int) -> str:
    """Return YYYY-MM-DD + days (days can be negative)."""
    d = _dt.date.fromisoformat(str(date_iso)[:10])
    out = d + _dt.timedelta(days=int(days))
    return out.isoformat()


# ---------------------------------------------------------------------------
# Deterministic randomness
# ---------------------------------------------------------------------------


def stable_u01(*parts: Any) -> float:
    """Deterministic pseudo-random in [0, 1) from arbitrary parts.

    This is used to make outcomes reproducible across runs given the same inputs.
    """
    h = hashlib.sha256()
    for p in parts:
        h.update(str(p).encode("utf-8"))
        h.update(b"|")
    # Use 64 bits for a stable float.
    n = int.from_bytes(h.digest()[:8], "big")
    return (n % (2**53)) / float(2**53)


def make_event_id(prefix: str, *parts: Any) -> str:
    """Build a deterministic event_id.

    Deterministic IDs are important for idempotency in append-only logs.
    """
    h = hashlib.sha256()
    h.update(str(prefix).encode("utf-8"))
    h.update(b"|")
    for p in parts:
        h.update(str(p).encode("utf-8"))
        h.update(b"|")
    return f"{prefix}_{h.hexdigest()[:16]}"


# ---------------------------------------------------------------------------
# Mental attribute helpers
# ---------------------------------------------------------------------------


def extract_mental_from_attrs(attrs_json: Any, *, keys: Mapping[str, str]) -> Dict[str, int]:
    """Extract mental traits from a player's attrs_json.

    Args:
        attrs_json: players.attrs_json (dict or JSON string)
        keys: mapping logical_name -> attrs_json_key

    Returns:
        dict with logical names mapped to 0..100 ints (missing => 50).

    Note:
    - We default to 50 (neutral) when missing or invalid.
    """
    attrs = json_loads(attrs_json, default={})
    if not isinstance(attrs, Mapping):
        attrs = {}

    out: Dict[str, int] = {}
    for logical, raw_key in keys.items():
        try:
            v = attrs.get(raw_key)
            iv = safe_int(v, 50)
        except Exception:
            iv = 50
        # Clamp to plausible 0..100
        if iv < 0:
            iv = 0
        if iv > 100:
            iv = 100
        out[str(logical)] = int(iv)
    return out


def mental_norm(mental: Mapping[str, Any], key: str, default: float = 0.5) -> float:
    """Return normalized mental value in [0..1] from mapping, tolerant."""
    try:
        v = mental.get(key)
    except Exception:
        v = None
    if v is None:
        return float(default)
    # Accept either 0..1 floats or 0..100 ints
    vf = safe_float(v, default * 100.0)
    if vf <= 1.0:
        return clamp01(vf)
    return clamp01(vf / 100.0)


