from __future__ import annotations

"""Pick protection normalization and validation.

This module is the SSOT (single source of truth) for the pick protection
schema used by:
- trade payload parsing / canonicalization
- validation rules
- trade execution / persistence
- draft settlement

Canonical protection schema (dict):

    {
        "type": "TOP_N",
        "n": int,  # 1..30
        "compensation": {
            "label": str,
            "value": int|float,
        },
    }

Notes:
- Input may provide "rule" instead of "type"; we accept both for robustness.
- The returned dict is always canonical and intentionally drops unknown keys.
"""

import math
from typing import Any, Dict, Optional

from .errors import TradeError, PROTECTION_INVALID


Protection = Dict[str, Any]


def normalize_protection(raw: Any, *, pick_id: Optional[str] = None) -> Protection:
    """Normalize and validate a protection object.

    Args:
        raw: Input protection object (must be a dict).
        pick_id: Optional pick id for better error details.

    Returns:
        Canonical protection dict.

    Raises:
        TradeError(PROTECTION_INVALID): if `raw` is invalid.
    """

    if not isinstance(raw, dict):
        raise TradeError(
            PROTECTION_INVALID,
            "Protection must be an object",
            {"pick_id": pick_id, "raw": raw},
        )

    protection_type = raw.get("type", raw.get("rule"))
    if not isinstance(protection_type, str) or not protection_type.strip():
        raise TradeError(
            PROTECTION_INVALID,
            "Protection type is required",
            {"pick_id": pick_id, "raw": raw},
        )
    protection_type_norm = protection_type.strip().upper()

    if protection_type_norm != "TOP_N":
        raise TradeError(
            PROTECTION_INVALID,
            "Unsupported protection type",
            {"pick_id": pick_id, "raw": raw},
        )

    raw_n = raw.get("n")

    # Reject bool explicitly (bool is a subclass of int).
    if isinstance(raw_n, bool):
        raise TradeError(
            PROTECTION_INVALID,
            "Protection n must be an integer",
            {"pick_id": pick_id, "raw": raw},
        )

    # Avoid silently truncating non-integer floats.
    if isinstance(raw_n, float) and not raw_n.is_integer():
        raise TradeError(
            PROTECTION_INVALID,
            "Protection n must be an integer",
            {"pick_id": pick_id, "raw": raw},
        )

    try:
        n_value = int(raw_n)
    except (TypeError, ValueError):
        raise TradeError(
            PROTECTION_INVALID,
            "Protection n must be an integer",
            {"pick_id": pick_id, "raw": raw},
        )

    if n_value < 1 or n_value > 30:
        raise TradeError(
            PROTECTION_INVALID,
            "Protection n out of range",
            {"pick_id": pick_id, "raw": raw},
        )

    compensation = raw.get("compensation")
    if not isinstance(compensation, dict):
        raise TradeError(
            PROTECTION_INVALID,
            "Protection compensation must be an object",
            {"pick_id": pick_id, "raw": raw},
        )

    compensation_value = compensation.get("value")
    if isinstance(compensation_value, bool) or not isinstance(compensation_value, (int, float)):
        raise TradeError(
            PROTECTION_INVALID,
            "Protection compensation value must be numeric",
            {"pick_id": pick_id, "raw": raw},
        )
    if isinstance(compensation_value, float) and not math.isfinite(compensation_value):
        raise TradeError(
            PROTECTION_INVALID,
            "Protection compensation value must be finite",
            {"pick_id": pick_id, "raw": raw},
        )

    compensation_label = compensation.get("label")
    if not isinstance(compensation_label, str) or not compensation_label.strip():
        compensation_label = "Protected pick compensation"

    # Canonical output intentionally drops unknown keys.
    return {
        "type": protection_type_norm,
        "n": n_value,
        "compensation": {
            "label": str(compensation_label),
            "value": compensation_value,
        },
    }


def normalize_protection_optional(
    raw: Any, *, pick_id: Optional[str] = None
) -> Optional[Protection]:
    """Optional variant of `normalize_protection`.

    Returns None when `raw` is None; otherwise normalizes/validates.
    """

    if raw is None:
        return None
    return normalize_protection(raw, pick_id=pick_id)
