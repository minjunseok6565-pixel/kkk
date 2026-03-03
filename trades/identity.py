from __future__ import annotations

"""Deal identity utilities.

This module is the SSOT (single source of truth) for:
- producing canonical deal payloads
- computing deal identity hashes (teams + legs; **meta excluded**)
- computing execution ids suitable for idempotency (identity + trade_date)

Why two ids?
- Identity hash: used for dedupe / "is this the same transaction?" comparisons.
  It must ignore deal.meta.
- Execution id: used for idempotency when committing trades. It includes the
  trade date so that the *same* transaction can happen again on a different
  date without being incorrectly treated as "already executed" forever.

Implementation notes:
- We rely on `trades.models.canonicalize_deal` to stabilize ordering.
- JSON dumps uses a stable configuration (sort_keys, separators) to make the
  hash deterministic.
"""

import hashlib
import json
from datetime import date, datetime
from typing import Any, Dict

from .models import Deal, canonicalize_deal, serialize_deal
from .trade_rules import parse_trade_deadline


# =============================================================================
# Stable JSON + hashing helpers
# =============================================================================

def stable_json_dumps(obj: Any) -> str:
    """Deterministic JSON dumps used for hashing.

    Important: callers should feed canonical payloads (sorted, normalized)
    because list ordering remains significant.
    """

    return json.dumps(
        obj,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    )


def _sha1_hex(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


# =============================================================================
# Deal payload + ids
# =============================================================================

def canonical_deal_payload(deal: Deal, *, include_meta: bool) -> Dict[str, Any]:
    """Return a canonical, serializable deal payload.

    Args:
        deal: Deal object.
        include_meta: Whether to include deal.meta.

    Returns:
        dict payload suitable for JSON serialization.
    """

    canon = canonicalize_deal(deal)
    payload = serialize_deal(canon)
    if not include_meta:
        payload.pop("meta", None)
    return payload


def deal_identity_hash(deal: Deal) -> str:
    """Hash of the transactional identity of a deal.

    This MUST ignore deal.meta.
    """

    payload = canonical_deal_payload(deal, include_meta=False)
    blob = stable_json_dumps(payload)
    return _sha1_hex(blob)


def _coerce_trade_date(value: Any) -> date:
    """Coerce a trade_date value into a date.

    Accepts date/datetime/ISO string. Raises ValueError if missing/invalid.
    """

    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    parsed = parse_trade_deadline(value)
    if parsed is None:
        raise ValueError(f"trade_date is required (got {value!r})")
    return parsed


def deal_execution_id(deal: Deal, *, trade_date: date | str | datetime) -> str:
    """Compute an execution id for idempotent trade commits.

    The id is derived from:
      sha1( deal_identity_hash(deal) + '|' + trade_date_iso )

    Args:
        deal: Deal object.
        trade_date: Date (or ISO date/datetime string) representing when the
            trade is executed.

    Returns:
        Hex sha1 string.

    Raises:
        ValueError: if trade_date cannot be parsed.
    """

    d = _coerce_trade_date(trade_date)
    base = deal_identity_hash(deal)
    return _sha1_hex(f"{base}|{d.isoformat()}")
