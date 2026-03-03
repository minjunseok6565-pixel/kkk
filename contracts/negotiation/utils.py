from __future__ import annotations

import datetime as _dt
import hashlib
import json
import math
from typing import Any, Mapping, Optional


def safe_int(x: Any, default: int = 0) -> int:
    try:
        if x is None:
            return int(default)
        return int(x)
    except Exception:
        return int(default)


def safe_float(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return float(default)
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return float(default)
        return float(v)
    except Exception:
        return float(default)


def clamp(x: Any, lo: float, hi: float) -> float:
    v = safe_float(x, lo)
    if v < lo:
        return float(lo)
    if v > hi:
        return float(hi)
    return float(v)


def clamp01(x: Any) -> float:
    return clamp(x, 0.0, 1.0)


def sigmoid(x: float) -> float:
    # Numerically stable sigmoid
    xf = float(x)
    if xf >= 0:
        z = math.exp(-xf)
        return 1.0 / (1.0 + z)
    z = math.exp(xf)
    return z / (1.0 + z)


def stable_u01(*parts: Any) -> float:
    """Deterministic pseudo-random in [0, 1) from arbitrary parts."""
    h = hashlib.sha256()
    for p in parts:
        h.update(str(p).encode("utf-8"))
        h.update(b"|")
    n = int.from_bytes(h.digest()[:8], "big")
    return (n % (2**53)) / float(2**53)


def json_dumps(obj: Any) -> str:
    """Stable JSON dump for logs/payload validation."""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def coerce_date_iso(value: Any, *, default: Optional[str] = None) -> Optional[str]:
    """Return YYYY-MM-DD or default/None."""
    if value is None:
        return default
    s = str(value).strip()
    if not s:
        return default
    s = s[:10]
    try:
        _dt.date.fromisoformat(s)
        return s
    except Exception:
        return default


def coerce_iso_like(value: Any, *, default: Optional[str] = None) -> Optional[str]:
    """Return an ISO-like string (date prefix validated)."""
    if value is None:
        return default
    s = str(value).strip()
    if not s:
        return default
    d = coerce_date_iso(s, default=None)
    if d is None:
        return default
    # Preserve the original time portion if present; otherwise return date.
    if len(s) >= 19 and "T" in s:
        return s
    return d


def date_add_days(date_iso: str, days: int) -> str:
    d = _dt.date.fromisoformat(str(date_iso)[:10])
    out = d + _dt.timedelta(days=int(days))
    return out.isoformat()


def mental_norm(mental: Mapping[str, Any] | None, key: str, default: float = 0.5) -> float:
    """Return normalized mental value in [0..1] from mapping, tolerant.

    Accepts:
      - 0..1 floats
      - 0..100 ints
    """
    if not isinstance(mental, Mapping):
        return float(default)
    try:
        v = mental.get(key)
    except Exception:
        v = None
    if v is None:
        return float(default)
    vf = safe_float(v, default * 100.0)
    if vf <= 1.0:
        return clamp01(vf)
    return clamp01(vf / 100.0)
