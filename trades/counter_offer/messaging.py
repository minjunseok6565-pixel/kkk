from __future__ import annotations

"""trades/counter_offer/messaging.py

Human-facing messaging for counter offers.

This module deliberately avoids any DB reads.
It consumes:
- tick-scoped ValuationDataProvider (optional) for player/pick naming
- DealDiff from trades.counter_offer.diff
- base decision/evaluation hints (why we countered)

The output is intended for the negotiation UI (e.g., negotiation_store.append_message).
"""

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from ..models import Asset, PlayerAsset, PickAsset, SwapAsset, FixedAsset
from ..valuation.types import DealDecision, TeamDealEvaluation, DealVerdict

from .diff import DealDiff, AssetRef, ProtectionChange


# -----------------------------------------------------------------------------
# Naming helpers
# -----------------------------------------------------------------------------

def _safe_upper(x: Any) -> str:
    return str(x or "").strip().upper()


def _pick_protection_short(p: Optional[Mapping[str, Any]]) -> str:
    if not p:
        return ""
    try:
        t = str(p.get("type") or p.get("rule") or "").strip().upper()
    except Exception:
        t = ""

    if t == "TOP_N":
        try:
            n = int(p.get("n"))
            return f"Top-{n} 보호"
        except Exception:
            return "보호"
    return "보호"


def _provider_get(provider: Any, fn_name: str, *args):
    if provider is None:
        return None
    fn = getattr(provider, fn_name, None)
    if fn is None:
        return None
    try:
        return fn(*args)
    except Exception:
        return None


def _fmt_player(provider: Any, player_id: str) -> str:
    snap = _provider_get(provider, "get_player_snapshot", str(player_id))
    name = None
    pos = None
    ovr = None
    try:
        name = getattr(snap, "name", None)
        pos = getattr(snap, "pos", None)
        ovr = getattr(snap, "ovr", None)
    except Exception:
        pass

    pid = str(player_id)
    if isinstance(name, str) and name.strip():
        if pos and ovr is not None:
            try:
                return f"{name} ({pos}, OVR {float(ovr):.0f})"
            except Exception:
                return f"{name} ({pos})"
        if pos:
            return f"{name} ({pos})"
        return str(name)
    return f"PLAYER#{pid}"


def _fmt_pick(provider: Any, pick_id: str, protection: Optional[Mapping[str, Any]] = None) -> str:
    snap = _provider_get(provider, "get_pick_snapshot", str(pick_id))
    year = None
    rnd = None
    orig = None
    try:
        year = getattr(snap, "year", None)
        rnd = getattr(snap, "round", None)
        orig = getattr(snap, "original_team", None)
    except Exception:
        pass

    base = None
    if year and rnd:
        try:
            base = f"{int(year)} {int(rnd)}라"
        except Exception:
            base = f"{year} {rnd}라"
    else:
        base = f"PICK#{pick_id}"

    if orig:
        base += f" ({_safe_upper(orig)})"

    # Prefer explicit asset protection if given; else snapshot
    prot = protection
    if prot is None:
        try:
            prot = getattr(snap, "protection", None)
        except Exception:
            prot = None

    ps = _pick_protection_short(prot if isinstance(prot, Mapping) else None)
    if ps:
        base += f" [{ps}]"
    return base


def _fmt_swap(provider: Any, swap_id: str) -> str:
    snap = _provider_get(provider, "get_swap_snapshot", str(swap_id))
    year = None
    rnd = None
    try:
        year = getattr(snap, "year", None)
        rnd = getattr(snap, "round", None)
    except Exception:
        pass

    if year and rnd:
        return f"{int(year)} {int(rnd)}라 스왑권"
    return f"SWAP#{swap_id}"


def _fmt_fixed(provider: Any, asset_id: str) -> str:
    snap = _provider_get(provider, "get_fixed_asset_snapshot", str(asset_id))
    label = None
    try:
        label = getattr(snap, "label", None)
    except Exception:
        pass
    if label:
        return str(label)
    return f"ASSET#{asset_id}"


def format_asset_ref(a: AssetRef, *, provider: Any = None) -> str:
    if a.kind == "player":
        return _fmt_player(provider, a.ref_id)
    if a.kind == "pick":
        return _fmt_pick(provider, a.ref_id, None)
    if a.kind == "swap":
        return _fmt_swap(provider, a.ref_id)
    return _fmt_fixed(provider, a.ref_id)


def format_asset(asset: Asset, *, provider: Any = None) -> str:
    if isinstance(asset, PlayerAsset):
        return _fmt_player(provider, str(asset.player_id))
    if isinstance(asset, PickAsset):
        return _fmt_pick(provider, str(asset.pick_id), getattr(asset, "protection", None))
    if isinstance(asset, SwapAsset):
        return _fmt_swap(provider, str(asset.swap_id))
    if isinstance(asset, FixedAsset):
        return _fmt_fixed(provider, str(asset.asset_id))
    return str(asset)


# -----------------------------------------------------------------------------
# Message building
# -----------------------------------------------------------------------------


def _has_reason(dec: Any, code: str) -> bool:
    reasons = getattr(dec, "reasons", None) or tuple()
    for r in reasons:
        try:
            if str(getattr(r, "code", "") or "") == code:
                return True
        except Exception:
            continue
    return False


def _margin(eval_: TeamDealEvaluation, dec: DealDecision) -> float:
    try:
        return float(eval_.net_surplus) - float(dec.required_surplus)
    except Exception:
        return 0.0


def _list_join(items: Sequence[str], *, limit: int = 3) -> str:
    xs = [x for x in items if isinstance(x, str) and x.strip()]
    if not xs:
        return ""
    if len(xs) <= limit:
        return ", ".join(xs)
    return ", ".join(xs[:limit]) + f" 외 {len(xs) - limit}개"


def summarize_diff(diff: DealDiff, *, user_team_id: str, other_team_id: str, provider: Any = None) -> Dict[str, Any]:
    """Summarize DealDiff into small buckets for messaging."""

    user = _safe_upper(user_team_id)
    other = _safe_upper(other_team_id)

    # Note: deal.legs[team] are assets SENT by that team.
    added_user = [format_asset_ref(a, provider=provider) for a in (diff.added_by_team.get(user, tuple()) or tuple())]
    removed_user = [format_asset_ref(a, provider=provider) for a in (diff.removed_by_team.get(user, tuple()) or tuple())]

    added_other = [format_asset_ref(a, provider=provider) for a in (diff.added_by_team.get(other, tuple()) or tuple())]
    removed_other = [format_asset_ref(a, provider=provider) for a in (diff.removed_by_team.get(other, tuple()) or tuple())]

    prot_changes_user: List[str] = []
    for pc in (diff.protection_changes_by_team.get(user, tuple()) or tuple()):
        before = _pick_protection_short(pc.before)
        after = _pick_protection_short(pc.after)
        prot_changes_user.append(f"{_fmt_pick(provider, pc.pick_id)}: {before or '무보호'} → {after or '무보호'}")

    prot_changes_other: List[str] = []
    for pc in (diff.protection_changes_by_team.get(other, tuple()) or tuple()):
        before = _pick_protection_short(pc.before)
        after = _pick_protection_short(pc.after)
        prot_changes_other.append(f"{_fmt_pick(provider, pc.pick_id)}: {before or '무보호'} → {after or '무보호'}")

    return {
        "user_added": tuple(added_user),
        "user_removed": tuple(removed_user),
        "other_added": tuple(added_other),
        "other_removed": tuple(removed_other),
        "user_protection_changes": tuple(prot_changes_user),
        "other_protection_changes": tuple(prot_changes_other),
        "edit_distance": int(diff.edit_distance),
    }


def build_counter_message(
    *,
    user_team_id: str,
    other_team_id: str,
    base_decision: DealDecision,
    base_evaluation: TeamDealEvaluation,
    diff: DealDiff,
    provider: Any = None,
    relationship: Optional[Mapping[str, Any]] = None,
    strategy: Optional[str] = None,
) -> str:
    """Create a negotiation UI message (Korean) describing the counter offer."""

    user = _safe_upper(user_team_id)
    other = _safe_upper(other_team_id)

    rel = dict(relationship or {})
    fatigue = 0
    try:
        fatigue = int(rel.get("fatigue") or 0)
    except Exception:
        fatigue = 0

    # --- opener (why counter)
    m = _margin(base_evaluation, base_decision)
    if _has_reason(base_decision, "FIT_FAILS"):
        opener = "현재 제안은 우리 로스터/시스템 핏이 애매해."
    else:
        if m >= -0.75:
            opener = "현재 제안은 가치가 아주 조금 부족해."
        else:
            opener = "현재 제안은 가치가 부족해."

    # --- changes
    s = summarize_diff(diff, user_team_id=user, other_team_id=other, provider=provider)

    asks: List[str] = []

    # If we removed assets from OTHER leg => other gives less to user.
    if s["other_removed"]:
        asks.append(f"우리는 { _list_join(list(s['other_removed'])) }는 제외하고 싶어.")

    # If we added assets to USER leg => user gives more.
    if s["user_added"]:
        asks.append(f"대신 { _list_join(list(s['user_added'])) }를 추가해줘.")

    # Protection tweaks (usually user pick protection reduced)
    if s["user_protection_changes"]:
        asks.append(f"그리고 픽 보호를 조정해줘: { _list_join(list(s['user_protection_changes']), limit=2) }.")

    # If we swapped something on USER leg (remove+add one player) hint it.
    # Keep this subtle; detailed diff is already available.
    if not s["user_added"] and s["user_removed"]:
        asks.append(f"{ _list_join(list(s['user_removed'])) }는 빼고 다른 자산으로 맞춰보자.")

    # If nothing detected (should be rare)
    if not asks:
        asks.append("조건을 조금만 조정하면 진행할 수 있어.")

    # --- closer
    closer = "이 조건이면 진행할 수 있어."
    if fatigue >= 6:
        closer = "이게 우리가 낼 수 있는 최선(사실상 최종안)이야."

    # Add a short tag for UI logs
    tag = ""
    if strategy:
        tag = f"[{strategy}] "

    msg = tag + opener + " " + " ".join(asks) + " " + closer
    return " ".join(msg.split())
