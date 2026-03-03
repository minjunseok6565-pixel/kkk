from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping

from ...errors import SWAP_INVALID, TradeError
from ...models import SwapAsset, resolve_asset_receiver
from ..base import TradeContext


@dataclass
class SwapYearRoundCapacityRule:
    """Enforce per-team swap capacity for (year, round=1).

    Rules:
      - A team can hold at most one active swap right for (year, round=1).
      - A team can carry at most one active swap obligation for (year, round=1).
        Obligation is counted for teams whose original pick is part of the swap
        and are not the current right holder.
    """

    rule_id: str = "swap_year_round_capacity"
    priority: int = 36
    enabled: bool = True

    def _assets_snapshot(self, ctx: TradeContext) -> Dict[str, Any]:
        cached = getattr(ctx, "extra", {}).get("assets_snapshot")
        if isinstance(cached, dict):
            return cached
        repo = getattr(ctx, "repo", None)
        if repo is not None and hasattr(repo, "get_trade_assets_snapshot"):
            snap = repo.get_trade_assets_snapshot()
            if isinstance(snap, dict):
                return snap
        return {"draft_picks": {}, "swap_rights": {}}

    @staticmethod
    def _is_active(record: Mapping[str, Any]) -> bool:
        v = record.get("active", True)
        if isinstance(v, bool):
            return v
        if isinstance(v, (int, float)):
            return bool(v)
        if isinstance(v, str):
            return v.strip().lower() not in ("0", "false", "no", "off", "")
        return True

    @staticmethod
    def _team_u(v: Any) -> str:
        return str(v or "").strip().upper()

    def validate(self, deal, ctx: TradeContext) -> None:
        snap = self._assets_snapshot(ctx)
        picks = snap.get("draft_picks") if isinstance(snap.get("draft_picks"), dict) else {}
        rights = snap.get("swap_rights") if isinstance(snap.get("swap_rights"), dict) else {}

        # Build projected active rights map after this deal.
        projected: Dict[str, Dict[str, Any]] = {}
        for sid, rec in rights.items():
            if not isinstance(rec, dict):
                continue
            if not self._is_active(rec):
                continue
            projected[str(sid)] = dict(rec)

        for from_team, assets in deal.legs.items():
            from_u = self._team_u(from_team)
            for asset in assets:
                if not isinstance(asset, SwapAsset):
                    continue
                to_u = self._team_u(resolve_asset_receiver(deal, from_team, asset))
                existing = projected.get(str(asset.swap_id), {})
                pick_a = picks.get(str(asset.pick_id_a))
                pick_b = picks.get(str(asset.pick_id_b))
                if not isinstance(pick_a, dict) or not isinstance(pick_b, dict):
                    # ownership/swap_integrity rules handle this with better errors.
                    continue
                year = int(pick_a.get("year") or 0)
                rnd = int(pick_a.get("round") or 0)
                originator = self._team_u(existing.get("originator_team") or from_u)
                transfer_count = int(existing.get("transfer_count") or 0)
                projected[str(asset.swap_id)] = {
                    "swap_id": str(asset.swap_id),
                    "pick_id_a": str(asset.pick_id_a),
                    "pick_id_b": str(asset.pick_id_b),
                    "year": year,
                    "round": rnd,
                    "owner_team": to_u,
                    "originator_team": originator,
                    "transfer_count": transfer_count if existing else 0,
                    "active": True,
                }

        holder_counts: Dict[tuple[str, int, int], int] = {}
        obligation_counts: Dict[tuple[str, int, int], int] = {}

        for rec in projected.values():
            if not self._is_active(rec):
                continue
            year = int(rec.get("year") or 0)
            rnd = int(rec.get("round") or 0)
            if year <= 0 or rnd != 1:
                continue

            holder = self._team_u(rec.get("owner_team"))
            if holder:
                hk = (holder, year, rnd)
                holder_counts[hk] = holder_counts.get(hk, 0) + 1
                if holder_counts[hk] > 1:
                    raise TradeError(
                        SWAP_INVALID,
                        "Team cannot hold more than one active first-round swap per year",
                        {"team_id": holder, "year": year, "round": rnd, "count": holder_counts[hk]},
                    )

            pick_a = picks.get(str(rec.get("pick_id_a") or ""))
            pick_b = picks.get(str(rec.get("pick_id_b") or ""))
            if not isinstance(pick_a, dict) or not isinstance(pick_b, dict):
                continue
            teams = {
                self._team_u(pick_a.get("original_team")),
                self._team_u(pick_b.get("original_team")),
            }
            for team in teams:
                if not team or team == holder:
                    continue
                ok = (team, year, rnd)
                obligation_counts[ok] = obligation_counts.get(ok, 0) + 1
                if obligation_counts[ok] > 1:
                    raise TradeError(
                        SWAP_INVALID,
                        "Team cannot carry more than one active first-round swap obligation per year",
                        {"team_id": team, "year": year, "round": rnd, "count": obligation_counts[ok]},
                    )
