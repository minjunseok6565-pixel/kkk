from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

from .errors import TradeError, DEAL_INVALIDATED, INVALID_TEAM, MISSING_TO_TEAM
from .protection import normalize_protection, normalize_protection_optional
from schema import normalize_player_id, normalize_team_id


@dataclass(frozen=True)
class PlayerAsset:
    kind: str
    player_id: str
    to_team: Optional[str] = None


@dataclass(frozen=True)
class PickAsset:
    kind: str
    pick_id: str
    to_team: Optional[str] = None
    protection: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class SwapAsset:
    kind: str
    swap_id: str
    pick_id_a: str
    pick_id_b: str
    to_team: Optional[str] = None


@dataclass(frozen=True)
class FixedAsset:
    kind: str
    asset_id: str
    to_team: Optional[str] = None


Asset = Union[PlayerAsset, PickAsset, SwapAsset, FixedAsset]


@dataclass
class Deal:
    teams: List[str]
    legs: Dict[str, List[Asset]]
    meta: Optional[Dict[str, Any]] = field(default_factory=dict)


def compute_swap_id(pick_id_a: str, pick_id_b: str) -> str:
    sorted_ids = sorted([str(pick_id_a), str(pick_id_b)])
    return f"SWAP_{sorted_ids[0]}__{sorted_ids[1]}"


def asset_key(asset: Asset) -> str:
    if isinstance(asset, PlayerAsset):
        return f"player:{asset.player_id}"
    if isinstance(asset, PickAsset):
        return f"pick:{asset.pick_id}"
    if isinstance(asset, SwapAsset):
        return f"swap:{asset.swap_id}"
    return f"fixed_asset:{asset.asset_id}"


def resolve_asset_receiver(deal: Deal, sender_team: str, asset: Asset) -> str:
    """Resolve which team receives an asset in a Deal.

    Rules:
      - If asset.to_team is set, use it.
      - If the deal has exactly 2 teams, the receiver is the other team.
      - Otherwise (multi-team deal), to_team must be present.

    Notes:
      - This is intentionally kept in trades.models as a shared utility so that
        agreements/apply/valuation can agree on a single resolution rule.
      - Hard validation for missing to_team in multi-team deals is already enforced
        by parse_deal(), but this function is defensive for callers that may operate
        on older/constructed Deal objects.
    """
    to_team = getattr(asset, "to_team", None)
    if to_team:
        return str(to_team)
    if len(deal.teams) == 2:
        other_team = [team for team in deal.teams if team != sender_team]
        if other_team:
            return str(other_team[0])
    raise TradeError(
        MISSING_TO_TEAM,
        "Missing to_team for multi-team deal asset",
        {"sender_team": sender_team, "asset": asset_key(asset)},
    )


def _normalize_protection(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Legacy wrapper kept for internal callers.

    SSOT lives in trades.protection.normalize_protection.
    """

    return normalize_protection(raw)



def normalize_pick_protection(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize/validate a pick protection payload.

    This is a public wrapper over the internal _normalize_protection() helper.

    Rationale
    ---------
    Multiple subsystems (deal parsing/canonicalization, AI generation decorators,
    valuation overrides) need to create protection dicts that are guaranteed to
    satisfy SSOT rules (PickProtectionSchemaRule) and settlement logic.

    Keeping this wrapper in trades.models makes the normalization rules a single
    source of truth.
    """
    return _normalize_protection(raw)


def _normalize_team_id(value: Any, *, context: str) -> str:
    try:
        return str(normalize_team_id(value, strict=True))
    except Exception as exc:
        # Never leak ValueError to API layers; treat invalid IDs as a trade payload error.
        raise TradeError(
            INVALID_TEAM,
            "Invalid team_id in deal payload",
            {"context": str(context), "value": value},
        ) from exc


def _normalize_player_id(value: Any, *, context: str) -> str:
    try:
        return str(normalize_player_id(value, strict=True))
    except Exception as exc:
        is_numeric = isinstance(value, str) and value.strip().isdigit()
        is_legacy_int = isinstance(value, int) and not isinstance(value, bool) and value >= 0
        if is_numeric or is_legacy_int:
            try:
                return str(
                    normalize_player_id(
                        str(value),
                        strict=False,
                        allow_legacy_numeric=True,
                    )
                )
            except Exception as legacy_exc:
                raise TradeError(
                    DEAL_INVALIDATED,
                    "Invalid player_id in deal payload",
                    {"context": str(context), "value": value},
                ) from legacy_exc
        raise TradeError(
            DEAL_INVALIDATED,
            "Invalid player_id in deal payload",
            {"context": str(context), "value": value},
        ) from exc


def _parse_asset(raw: Dict[str, Any]) -> Asset:
    kind = str(raw.get("kind", "")).lower()
    to_team = raw.get("to_team")
    to_team = _normalize_team_id(to_team, context="asset.to_team") if to_team else None
    if kind == "player":
        player_id = raw.get("player_id")
        if player_id is None:
            raise TradeError(DEAL_INVALIDATED, "Missing player_id in asset", raw)
        pid = _normalize_player_id(player_id, context="asset.player_id")
        return PlayerAsset(kind="player", player_id=pid, to_team=to_team)
    if kind == "pick":
        pick_id = raw.get("pick_id")
        if not pick_id:
            raise TradeError(DEAL_INVALIDATED, "Missing pick_id in asset", raw)
        protection = None
        if "protection" in raw:
            # Allow explicit null to behave like "no protection".
            protection_raw = raw.get("protection")
            protection = normalize_protection_optional(protection_raw, pick_id=str(pick_id))
        return PickAsset(
            kind="pick",
            pick_id=str(pick_id),
            to_team=to_team,
            protection=protection,
        )
    if kind == "swap":
        pick_id_a = raw.get("pick_id_a")
        pick_id_b = raw.get("pick_id_b")
        if not isinstance(pick_id_a, str) or not pick_id_a.strip():
            raise TradeError(DEAL_INVALIDATED, "Missing pick_id_a in asset", raw)
        if not isinstance(pick_id_b, str) or not pick_id_b.strip():
            raise TradeError(DEAL_INVALIDATED, "Missing pick_id_b in asset", raw)
        swap_id = raw.get("swap_id")
        if not isinstance(swap_id, str) or not swap_id.strip():
            swap_id = compute_swap_id(pick_id_a, pick_id_b)
        return SwapAsset(
            kind="swap",
            swap_id=str(swap_id),
            pick_id_a=str(pick_id_a),
            pick_id_b=str(pick_id_b),
            to_team=to_team,
        )
    if kind == "fixed_asset":
        asset_id = raw.get("asset_id")
        if not isinstance(asset_id, str) or not asset_id.strip():
            raise TradeError(DEAL_INVALIDATED, "Missing asset_id in asset", raw)
        return FixedAsset(kind="fixed_asset", asset_id=str(asset_id), to_team=to_team)
    raise TradeError(DEAL_INVALIDATED, "Unknown asset kind", raw)


def parse_deal(payload: Dict[str, Any]) -> Deal:
    teams_raw = payload.get("teams")
    legs_raw = payload.get("legs")
    if not isinstance(teams_raw, list) or not isinstance(legs_raw, dict):
        raise TradeError(DEAL_INVALIDATED, "Invalid deal payload", payload)

    teams = [_normalize_team_id(t, context="deal.teams") for t in teams_raw]
    if not teams:
        raise TradeError(DEAL_INVALIDATED, "Deal must include teams", payload)

    normalized_legs_raw = {
        _normalize_team_id(k, context="deal.legs key"): v for k, v in legs_raw.items()
    }

    extra_leg_teams = sorted(set(normalized_legs_raw.keys()) - set(teams))
    if extra_leg_teams:
        raise TradeError(
            DEAL_INVALIDATED,
            "Deal legs contain teams not listed in deal.teams",
            {
                "extra_leg_teams": extra_leg_teams,
                "teams": teams,
                "legs_keys": sorted(normalized_legs_raw.keys()),
            },
        )

    legs: Dict[str, List[Asset]] = {}
    for team_id in teams:
        if team_id not in normalized_legs_raw:
            raise TradeError(DEAL_INVALIDATED, f"Missing legs for team {team_id}", payload)
        raw_assets = normalized_legs_raw.get(team_id) or []
        if not isinstance(raw_assets, list):
            raise TradeError(DEAL_INVALIDATED, f"Invalid legs for team {team_id}", payload)
        legs[team_id] = [_parse_asset(asset) for asset in raw_assets]

    if len(teams) >= 3:
        for team_id, assets in legs.items():
            for asset in assets:
                if not asset.to_team:
                    raise TradeError(
                        MISSING_TO_TEAM,
                        "Missing to_team for multi-team deal asset",
                        {"team_id": team_id, "asset": asset},
                    )

    meta = payload.get("meta")
    if meta is not None and not isinstance(meta, dict):
        raise TradeError(DEAL_INVALIDATED, "meta must be an object", payload)

    return Deal(teams=teams, legs=legs, meta=meta or {})


def canonicalize_deal(deal: Deal) -> Deal:
    teams = sorted(_normalize_team_id(team_id, context="deal.teams") for team_id in deal.teams)
    legs: Dict[str, List[Asset]] = {}
    for team_id in sorted(deal.legs.keys()):
        normalized_team_id = _normalize_team_id(team_id, context="deal.legs key")
        assets = list(deal.legs.get(team_id, []))
        normalized_assets: List[Asset] = []
        for asset in assets:
            if isinstance(asset, PlayerAsset):
                to_team = (
                    _normalize_team_id(asset.to_team, context="deal.asset.to_team")
                    if asset.to_team
                    else None
                )
                normalized_assets.append(
                    PlayerAsset(kind=asset.kind, player_id=asset.player_id, to_team=to_team)
                )
            elif isinstance(asset, PickAsset):
                to_team = (
                    _normalize_team_id(asset.to_team, context="deal.asset.to_team")
                    if asset.to_team
                    else None
                )
                protection = None
                if asset.protection is not None:
                    protection = normalize_protection(asset.protection, pick_id=asset.pick_id)
                normalized_assets.append(
                    PickAsset(
                        kind=asset.kind,
                        pick_id=asset.pick_id,
                        to_team=to_team,
                        protection=protection,
                    )
                )
            elif isinstance(asset, SwapAsset):
                to_team = (
                    _normalize_team_id(asset.to_team, context="deal.asset.to_team")
                    if asset.to_team
                    else None
                )
                normalized_assets.append(
                    SwapAsset(
                        kind=asset.kind,
                        swap_id=asset.swap_id,
                        pick_id_a=asset.pick_id_a,
                        pick_id_b=asset.pick_id_b,
                        to_team=to_team,
                    )
                )
            elif isinstance(asset, FixedAsset):
                to_team = (
                    _normalize_team_id(asset.to_team, context="deal.asset.to_team")
                    if asset.to_team
                    else None
                )
                normalized_assets.append(
                    FixedAsset(kind=asset.kind, asset_id=asset.asset_id, to_team=to_team)
                )
        normalized_assets.sort(key=_asset_sort_key)
        legs[normalized_team_id] = normalized_assets
    meta = dict(deal.meta) if deal.meta else {}
    return Deal(teams=teams, legs=legs, meta=meta)


def _asset_sort_key(asset: Asset) -> tuple:
    if isinstance(asset, PlayerAsset):
        return (0, asset.to_team or "", asset.player_id)
    if isinstance(asset, PickAsset):
        return (1, asset.to_team or "", asset.pick_id)
    if isinstance(asset, SwapAsset):
        return (2, asset.to_team or "", asset.swap_id)
    return (3, asset.to_team or "", asset.asset_id)


def serialize_deal(deal: Deal) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "teams": list(deal.teams),
        "legs": {},
    }
    for team_id in deal.legs:
        assets_payload = []
        for asset in deal.legs[team_id]:
            if isinstance(asset, PlayerAsset):
                payload_asset = {"kind": "player", "player_id": asset.player_id}
                if asset.to_team:
                    payload_asset["to_team"] = asset.to_team
                assets_payload.append(payload_asset)
            elif isinstance(asset, PickAsset):
                payload_asset = {"kind": "pick", "pick_id": asset.pick_id}
                if asset.to_team:
                    payload_asset["to_team"] = asset.to_team
                if asset.protection is not None:
                    payload_asset["protection"] = dict(asset.protection)
                assets_payload.append(payload_asset)
            elif isinstance(asset, SwapAsset):
                payload_asset = {
                    "kind": "swap",
                    "swap_id": asset.swap_id,
                    "pick_id_a": asset.pick_id_a,
                    "pick_id_b": asset.pick_id_b,
                }
                if asset.to_team:
                    payload_asset["to_team"] = asset.to_team
                assets_payload.append(payload_asset)
            elif isinstance(asset, FixedAsset):
                payload_asset = {"kind": "fixed_asset", "asset_id": asset.asset_id}
                if asset.to_team:
                    payload_asset["to_team"] = asset.to_team
                assets_payload.append(payload_asset)
        payload["legs"][team_id] = assets_payload
    if deal.meta:
        payload["meta"] = dict(deal.meta)
    return payload
