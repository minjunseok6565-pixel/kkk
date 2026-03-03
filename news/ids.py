from __future__ import annotations

import hashlib
from typing import Any


def make_event_id(prefix: str, *parts: Any) -> str:
    """Create a stable event identifier.

    IDs are used for deduplication inside caches. Keep them deterministic.
    """
    safe_parts = [str(prefix).strip()]
    for p in parts:
        s = str(p).strip()
        if s:
            safe_parts.append(s)
    return ":".join(safe_parts)


def make_article_id(event_id: str) -> str:
    """Derive a stable article identifier from event_id."""
    h = hashlib.sha1(str(event_id).encode("utf-8")).hexdigest()
    return h[:12]

