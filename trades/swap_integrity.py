from __future__ import annotations

"""Shared swap-right validation helpers.

Why this module exists
----------------------
Historically, swap validation logic lived in two places:
  - trades.rules.builtin.ownership_rule (validator stage)
  - league_service.LeagueService.execute_trade (execution stage)

This duplication risks "validator passes, execute fails" mismatches as the
code evolves. This module centralizes the core swap integrity checks so both
stages stay consistent.

Scope
-----
We validate that:
  - Both referenced picks exist.
  - The picks match on (year, round).
  - swap_id is canonical for the pick pair.
  - If a swap_right record exists for swap_id, it matches the pick pair (and
    optionally its stored year/round matches the picks).
  - Ownership rules are enforced consistently:
      * existing right: from_team must own the right
      * new right: from_team must own at least one of the picks (creation gate)

Notes
-----
We intentionally do NOT enforce swap_right.active here to preserve current
behavior (inactive rights are handled at settlement time). If you want to
disallow trading inactive rights later, add a separate rule and call it from
both validator+execute.
"""

from typing import Any, Dict, Mapping, Optional

from .errors import SWAP_INVALID, SWAP_NOT_OWNED, TradeError
from .models import SwapAsset, compute_swap_id


def _team_u(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().upper()


def _mget(obj: Any, key: str, default: Any = None) -> Any:
    """Mapping-safe getter supporting dict and sqlite3.Row."""

    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    try:
        return obj[key]
    except Exception:
        return default


def _coerce_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def _validate_swap_core(
    *,
    swap_id: str,
    pick_id_a: str,
    pick_id_b: str,
    from_team: str,
    pick_a: Any,
    pick_b: Any,
    swap_record: Any,
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Core swap integrity validation.

    Returns:
        dict with keys: year, round, swap_exists
    """

    details: Dict[str, Any] = dict(context or {})
    details.update(
        {
            "swap_id": str(swap_id),
            "pick_id_a": str(pick_id_a),
            "pick_id_b": str(pick_id_b),
            "from_team": _team_u(from_team),
        }
    )

    expected = compute_swap_id(str(pick_id_a), str(pick_id_b))
    if str(swap_id) != expected:
        raise TradeError(
            SWAP_INVALID,
            "swap_id must be canonical for the pick pair",
            {**details, "expected": expected},
        )

    if not pick_a or not pick_b:
        raise TradeError(
            SWAP_INVALID,
            "Swap picks must exist",
            details,
        )

    year_a = _coerce_int(_mget(pick_a, "year"))
    round_a = _coerce_int(_mget(pick_a, "round"))
    year_b = _coerce_int(_mget(pick_b, "year"))
    round_b = _coerce_int(_mget(pick_b, "round"))

    if year_a is None or round_a is None or year_b is None or round_b is None:
        raise TradeError(
            SWAP_INVALID,
            "Swap picks missing year/round",
            {
                **details,
                "pick_a": {"year": _mget(pick_a, "year"), "round": _mget(pick_a, "round")},
                "pick_b": {"year": _mget(pick_b, "year"), "round": _mget(pick_b, "round")},
            },
        )

    if year_a != year_b or round_a != round_b:
        raise TradeError(
            SWAP_INVALID,
            "Swap picks must match year and round",
            {
                **details,
                "pick_a": {"year": year_a, "round": round_a},
                "pick_b": {"year": year_b, "round": round_b},
            },
        )

    from_team_u = _team_u(from_team)

    if swap_record:
        rec_pick_a = _mget(swap_record, "pick_id_a")
        rec_pick_b = _mget(swap_record, "pick_id_b")
        if not rec_pick_a or not rec_pick_b:
            raise TradeError(
                SWAP_INVALID,
                "Swap right record missing pick ids",
                {**details, "swap_record": {"pick_id_a": rec_pick_a, "pick_id_b": rec_pick_b}},
            )

        # Ensure the swap_id refers to the same pick pair as the DB record.
        if frozenset([str(rec_pick_a), str(rec_pick_b)]) != frozenset([str(pick_id_a), str(pick_id_b)]):
            raise TradeError(
                SWAP_INVALID,
                "Swap right record does not match pick pair",
                {
                    **details,
                    "swap_record": {"pick_id_a": str(rec_pick_a), "pick_id_b": str(rec_pick_b)},
                },
            )

        # Defensive: if swap_rights stores year/round, ensure it matches picks.
        rec_year = _coerce_int(_mget(swap_record, "year"))
        rec_round = _coerce_int(_mget(swap_record, "round"))
        if rec_year is not None and rec_year != year_a:
            raise TradeError(
                SWAP_INVALID,
                "Swap right record year does not match picks",
                {**details, "record_year": rec_year, "pick_year": year_a},
            )
        if rec_round is not None and rec_round != round_a:
            raise TradeError(
                SWAP_INVALID,
                "Swap right record round does not match picks",
                {**details, "record_round": rec_round, "pick_round": round_a},
            )

        owner = _team_u(_mget(swap_record, "owner_team"))
        if owner != from_team_u:
            raise TradeError(
                SWAP_NOT_OWNED,
                "Swap right not owned by team",
                {**details, "owner_team": owner},
            )

        originator = _team_u(_mget(swap_record, "originator_team"))
        transfer_count = _coerce_int(_mget(swap_record, "transfer_count")) or 0
        if not originator:
            originator = owner
        if owner != originator:
            raise TradeError(
                SWAP_INVALID,
                "Swap resale is not allowed",
                {**details, "owner_team": owner, "originator_team": originator, "transfer_count": transfer_count},
            )
        if transfer_count >= 1:
            raise TradeError(
                SWAP_INVALID,
                "Swap resale is not allowed",
                {**details, "owner_team": owner, "originator_team": originator, "transfer_count": transfer_count},
            )

        return {"year": year_a, "round": round_a, "swap_exists": True}

    # No existing record: creation gate must be satisfied.
    owner_a = _team_u(_mget(pick_a, "owner_team"))
    owner_b = _team_u(_mget(pick_b, "owner_team"))
    if owner_a != from_team_u and owner_b != from_team_u:
        raise TradeError(
            SWAP_INVALID,
            "Swap right cannot be created by team",
            {
                **details,
                "pick_owner_a": owner_a,
                "pick_owner_b": owner_b,
            },
        )

    return {"year": year_a, "round": round_a, "swap_exists": False}


def validate_swap_asset_snapshot(
    asset: SwapAsset,
    *,
    from_team: str,
    draft_picks: Mapping[str, Mapping[str, Any]],
    swap_rights: Mapping[str, Mapping[str, Any]],
) -> Dict[str, Any]:
    """Validate a SwapAsset using an in-memory snapshot (validator path)."""

    pick_a = draft_picks.get(str(asset.pick_id_a))
    pick_b = draft_picks.get(str(asset.pick_id_b))
    swap = swap_rights.get(str(asset.swap_id))
    return _validate_swap_core(
        swap_id=str(asset.swap_id),
        pick_id_a=str(asset.pick_id_a),
        pick_id_b=str(asset.pick_id_b),
        from_team=str(from_team),
        pick_a=pick_a,
        pick_b=pick_b,
        swap_record=swap,
    )


def validate_swap_asset_in_cur(
    *,
    cur: Any,
    swap_id: str,
    pick_id_a: str,
    pick_id_b: str,
    from_team: str,
) -> Dict[str, Any]:
    """Validate a swap using the live DB cursor (execution path)."""

    a = cur.execute(
        "SELECT pick_id, year, round, owner_team FROM draft_picks WHERE pick_id=?;",
        (str(pick_id_a),),
    ).fetchone()
    b = cur.execute(
        "SELECT pick_id, year, round, owner_team FROM draft_picks WHERE pick_id=?;",
        (str(pick_id_b),),
    ).fetchone()
    swap_row = cur.execute(
        "SELECT swap_id, pick_id_a, pick_id_b, year, round, owner_team, originator_team, transfer_count, active FROM swap_rights WHERE swap_id=?;",
        (str(swap_id),),
    ).fetchone()

    return _validate_swap_core(
        swap_id=str(swap_id),
        pick_id_a=str(pick_id_a),
        pick_id_b=str(pick_id_b),
        from_team=str(from_team),
        pick_a=a,
        pick_b=b,
        swap_record=swap_row,
    )
