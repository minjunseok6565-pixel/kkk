from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .protection import normalize_protection


@dataclass(frozen=True, slots=True)
class ProtectionResolution:
    pick_id: str
    slot: int
    protected: bool
    owner_team_before: str
    owner_team_after: str
    original_team: str
    compensation_required: bool
    compensation_label: str | None = None
    compensation_value: float | None = None


@dataclass(frozen=True, slots=True)
class SwapResolution:
    swap_id: str
    slot_a: int
    slot_b: int
    owner_team: str
    owner_a_before: str
    owner_b_before: str
    owner_a_after: str
    owner_b_after: str
    exercisable: bool
    swap_executed: bool
    chosen_pick_id: str | None
    other_owner_team: str | None


def normalize_team_id(team_id: object) -> str:
    return str(team_id or "").upper().strip()


def resolve_pick_protection(
    *,
    pick_id: str,
    slot: int,
    owner_team: object,
    original_team: object,
    protection: Mapping[str, Any] | None,
) -> ProtectionResolution:
    owner_before = normalize_team_id(owner_team)
    original = normalize_team_id(original_team)

    if protection is None:
        return ProtectionResolution(
            pick_id=str(pick_id),
            slot=int(slot),
            protected=False,
            owner_team_before=owner_before,
            owner_team_after=owner_before,
            original_team=original,
            compensation_required=False,
        )

    normalized = normalize_protection(dict(protection), pick_id=str(pick_id))
    n_val = int(normalized.get("n") or 0)
    is_protected = int(slot) <= n_val

    owner_after = owner_before
    compensation_required = False
    compensation_label: str | None = None
    compensation_value: float | None = None

    if is_protected and owner_before and original and owner_before != original:
        owner_after = original
        comp = normalized.get("compensation") or {}
        compensation_required = True
        compensation_label = str(comp.get("label") or "") or None
        try:
            compensation_value = float(comp.get("value"))
        except Exception:
            compensation_value = None

    return ProtectionResolution(
        pick_id=str(pick_id),
        slot=int(slot),
        protected=bool(is_protected),
        owner_team_before=owner_before,
        owner_team_after=owner_after,
        original_team=original,
        compensation_required=compensation_required,
        compensation_label=compensation_label,
        compensation_value=compensation_value,
    )


def resolve_swap_outcome(
    *,
    swap_id: str,
    pick_id_a: str,
    pick_id_b: str,
    slot_a: int,
    slot_b: int,
    owner_team: object,
    owner_a: object,
    owner_b: object,
) -> SwapResolution:
    owner = normalize_team_id(owner_team)
    owner_a_before = normalize_team_id(owner_a)
    owner_b_before = normalize_team_id(owner_b)

    exercisable = bool(owner) and owner in {owner_a_before, owner_b_before}
    if not exercisable:
        return SwapResolution(
            swap_id=str(swap_id),
            slot_a=int(slot_a),
            slot_b=int(slot_b),
            owner_team=owner,
            owner_a_before=owner_a_before,
            owner_b_before=owner_b_before,
            owner_a_after=owner_a_before,
            owner_b_after=owner_b_before,
            exercisable=False,
            swap_executed=False,
            chosen_pick_id=None,
            other_owner_team=None,
        )

    if int(slot_a) == int(slot_b):
        return SwapResolution(
            swap_id=str(swap_id),
            slot_a=int(slot_a),
            slot_b=int(slot_b),
            owner_team=owner,
            owner_a_before=owner_a_before,
            owner_b_before=owner_b_before,
            owner_a_after=owner_a_before,
            owner_b_after=owner_b_before,
            exercisable=True,
            swap_executed=False,
            chosen_pick_id=str(pick_id_a),
            other_owner_team=None,
        )

    if int(slot_a) < int(slot_b):
        chosen_pick_id = str(pick_id_a)
    else:
        chosen_pick_id = str(pick_id_b)

    if owner_a_before == owner_b_before:
        other_owner = owner_a_before
    else:
        other_owner = owner_a_before if owner_a_before != owner else owner_b_before

    owner_a_after = owner
    owner_b_after = owner
    if chosen_pick_id == str(pick_id_a):
        owner_a_after = owner
        owner_b_after = other_owner
    else:
        owner_b_after = owner
        owner_a_after = other_owner

    return SwapResolution(
        swap_id=str(swap_id),
        slot_a=int(slot_a),
        slot_b=int(slot_b),
        owner_team=owner,
        owner_a_before=owner_a_before,
        owner_b_before=owner_b_before,
        owner_a_after=owner_a_after,
        owner_b_after=owner_b_after,
        exercisable=True,
        swap_executed=True,
        chosen_pick_id=chosen_pick_id,
        other_owner_team=other_owner,
    )
