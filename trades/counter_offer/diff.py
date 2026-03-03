from __future__ import annotations

"""trades/counter_offer/diff.py

Deal diff utilities for counter offers.

Goals
-----
- Provide a stable, UI-friendly representation of what changed between two deals.
- Be robust to canonicalization differences (ordering, None fields).
- Detect *meaningful* modifications such as pick protection changes.

This module is pure: it does not read DB/state.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ..models import (
    Deal,
    Asset,
    PlayerAsset,
    PickAsset,
    SwapAsset,
    FixedAsset,
    canonicalize_deal,
    asset_key,
)


def _team_id(x: Any) -> str:
    return str(x or "").strip().upper()


def _asset_kind(asset: Asset) -> str:
    if isinstance(asset, PlayerAsset):
        return "player"
    if isinstance(asset, PickAsset):
        return "pick"
    if isinstance(asset, SwapAsset):
        return "swap"
    return "fixed"


def _asset_ref_id(asset: Asset) -> str:
    if isinstance(asset, PlayerAsset):
        return str(asset.player_id)
    if isinstance(asset, PickAsset):
        return str(asset.pick_id)
    if isinstance(asset, SwapAsset):
        return str(asset.swap_id)
    return str(asset.asset_id)


def _canon_protection(p: Any) -> Optional[Dict[str, Any]]:
    if p is None:
        return None
    if not isinstance(p, Mapping):
        # Not canonical, but keep best-effort for diff.
        return {"_invalid": True, "value": str(p)}

    # Keep only stable keys.
    out: Dict[str, Any] = {}
    t = p.get("type", p.get("rule"))
    if isinstance(t, str):
        out["type"] = t.strip().upper()

    # TOP_N only in this project
    if "n" in p:
        try:
            out["n"] = int(p.get("n"))
        except Exception:
            out["n"] = p.get("n")

    comp = p.get("compensation")
    if isinstance(comp, Mapping):
        # Preserve label/value if present.
        out["compensation"] = {
            "label": str(comp.get("label") or ""),
            "value": comp.get("value"),
        }

    # If empty, treat as None.
    return out or None


@dataclass(frozen=True, slots=True)
class AssetRef:
    """A compact reference used in diff payloads."""

    team_id: str
    kind: str
    asset_key: str
    ref_id: str
    to_team: Optional[str] = None


@dataclass(frozen=True, slots=True)
class ProtectionChange:
    team_id: str
    pick_id: str
    asset_key: str
    before: Optional[Dict[str, Any]]
    after: Optional[Dict[str, Any]]

    @property
    def before_n(self) -> Optional[int]:
        try:
            if self.before and self.before.get("type") == "TOP_N":
                return int(self.before.get("n"))
        except Exception:
            return None
        return None

    @property
    def after_n(self) -> Optional[int]:
        try:
            if self.after and self.after.get("type") == "TOP_N":
                return int(self.after.get("n"))
        except Exception:
            return None
        return None


@dataclass(frozen=True, slots=True)
class AttributeChange:
    """Generic modification for non-pick assets (currently: to_team changes)."""

    team_id: str
    asset_key: str
    kind: str
    ref_id: str
    field: str
    before: Any
    after: Any


@dataclass(frozen=True, slots=True)
class DealDiff:
    """Diff between two deals, grouped by sender team (legs key)."""

    base_deal_id: Optional[str] = None
    counter_deal_id: Optional[str] = None

    added_by_team: Dict[str, Tuple[AssetRef, ...]] = field(default_factory=dict)
    removed_by_team: Dict[str, Tuple[AssetRef, ...]] = field(default_factory=dict)
    protection_changes_by_team: Dict[str, Tuple[ProtectionChange, ...]] = field(default_factory=dict)
    attribute_changes_by_team: Dict[str, Tuple[AttributeChange, ...]] = field(default_factory=dict)

    edit_distance: int = 0

    def to_payload(self) -> Dict[str, Any]:
        def _ar(a: AssetRef) -> Dict[str, Any]:
            return {
                "team_id": a.team_id,
                "kind": a.kind,
                "asset_key": a.asset_key,
                "ref_id": a.ref_id,
                "to_team": a.to_team,
            }

        def _pc(c: ProtectionChange) -> Dict[str, Any]:
            return {
                "team_id": c.team_id,
                "pick_id": c.pick_id,
                "asset_key": c.asset_key,
                "before": c.before,
                "after": c.after,
            }

        def _ac(c: AttributeChange) -> Dict[str, Any]:
            return {
                "team_id": c.team_id,
                "asset_key": c.asset_key,
                "kind": c.kind,
                "ref_id": c.ref_id,
                "field": c.field,
                "before": c.before,
                "after": c.after,
            }

        return {
            "base_deal_id": self.base_deal_id,
            "counter_deal_id": self.counter_deal_id,
            "added_by_team": {k: [_ar(x) for x in v] for k, v in (self.added_by_team or {}).items()},
            "removed_by_team": {k: [_ar(x) for x in v] for k, v in (self.removed_by_team or {}).items()},
            "protection_changes_by_team": {k: [_pc(x) for x in v] for k, v in (self.protection_changes_by_team or {}).items()},
            "attribute_changes_by_team": {k: [_ac(x) for x in v] for k, v in (self.attribute_changes_by_team or {}).items()},
            "edit_distance": int(self.edit_distance),
        }


def compute_deal_diff(base: Deal, counter: Deal) -> DealDiff:
    """Compute a diff between two deals.

    Both deals are canonicalized first (stable ordering) to avoid false diffs.
    """

    base_c = canonicalize_deal(base)
    counter_c = canonicalize_deal(counter)

    teams: List[str] = []
    for t in list(base_c.teams) + list(counter_c.teams):
        tu = _team_id(t)
        if tu and tu not in teams:
            teams.append(tu)

    added_by_team: Dict[str, List[AssetRef]] = {tid: [] for tid in teams}
    removed_by_team: Dict[str, List[AssetRef]] = {tid: [] for tid in teams}
    protection_changes_by_team: Dict[str, List[ProtectionChange]] = {tid: [] for tid in teams}
    attribute_changes_by_team: Dict[str, List[AttributeChange]] = {tid: [] for tid in teams}

    edit = 0

    for tid in teams:
        base_leg = list(base_c.legs.get(tid, []) or [])
        counter_leg = list(counter_c.legs.get(tid, []) or [])

        base_map: Dict[str, Asset] = {}
        counter_map: Dict[str, Asset] = {}

        for a in base_leg:
            try:
                base_map[asset_key(a)] = a
            except Exception:
                # fallback: use repr
                base_map[repr(a)] = a

        for a in counter_leg:
            try:
                counter_map[asset_key(a)] = a
            except Exception:
                counter_map[repr(a)] = a

        base_keys = set(base_map.keys())
        counter_keys = set(counter_map.keys())

        # Added / removed
        for k in sorted(counter_keys - base_keys):
            a = counter_map.get(k)
            if a is None:
                continue
            added_by_team[tid].append(
                AssetRef(
                    team_id=tid,
                    kind=_asset_kind(a),
                    asset_key=str(k),
                    ref_id=_asset_ref_id(a),
                    to_team=(str(getattr(a, "to_team", None)).upper() if getattr(a, "to_team", None) else None),
                )
            )
            edit += 1

        for k in sorted(base_keys - counter_keys):
            a = base_map.get(k)
            if a is None:
                continue
            removed_by_team[tid].append(
                AssetRef(
                    team_id=tid,
                    kind=_asset_kind(a),
                    asset_key=str(k),
                    ref_id=_asset_ref_id(a),
                    to_team=(str(getattr(a, "to_team", None)).upper() if getattr(a, "to_team", None) else None),
                )
            )
            edit += 1

        # Modifications for keys in common
        for k in sorted(base_keys & counter_keys):
            a0 = base_map.get(k)
            a1 = counter_map.get(k)
            if a0 is None or a1 is None:
                continue

            # Pick protection change
            if isinstance(a0, PickAsset) and isinstance(a1, PickAsset):
                p0 = _canon_protection(getattr(a0, "protection", None))
                p1 = _canon_protection(getattr(a1, "protection", None))
                if p0 != p1:
                    protection_changes_by_team[tid].append(
                        ProtectionChange(
                            team_id=tid,
                            pick_id=str(a0.pick_id),
                            asset_key=str(k),
                            before=p0,
                            after=p1,
                        )
                    )
                    edit += 1

            # to_team change (covers multi-team future)
            t0 = getattr(a0, "to_team", None)
            t1 = getattr(a1, "to_team", None)
            if (t0 or None) != (t1 or None):
                attribute_changes_by_team[tid].append(
                    AttributeChange(
                        team_id=tid,
                        asset_key=str(k),
                        kind=_asset_kind(a0),
                        ref_id=_asset_ref_id(a0),
                        field="to_team",
                        before=t0,
                        after=t1,
                    )
                )
                edit += 1

    # prune empty lists
    def _prune(d: Dict[str, List[Any]]) -> Dict[str, Tuple[Any, ...]]:
        out: Dict[str, Tuple[Any, ...]] = {}
        for k, v in d.items():
            if v:
                out[k] = tuple(v)
        return out

    return DealDiff(
        base_deal_id=getattr(base, "meta", None).get("deal_id") if isinstance(getattr(base, "meta", None), dict) else None,
        counter_deal_id=getattr(counter, "meta", None).get("deal_id") if isinstance(getattr(counter, "meta", None), dict) else None,
        added_by_team=_prune(added_by_team),
        removed_by_team=_prune(removed_by_team),
        protection_changes_by_team=_prune(protection_changes_by_team),
        attribute_changes_by_team=_prune(attribute_changes_by_team),
        edit_distance=int(edit),
    )
