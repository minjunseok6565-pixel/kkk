from __future__ import annotations

from dataclasses import dataclass

from ...errors import DEAL_INVALIDATED, TradeError
from ...models import Deal, resolve_asset_receiver
from ..base import TradeContext


@dataclass
class DealShapeRule:
    rule_id: str = "deal_shape"
    priority: int = 15
    enabled: bool = True

    def validate(self, deal: Deal, ctx: TradeContext) -> None:
        teams = list(getattr(deal, "teams", []) or [])

        # A) A trade must involve at least two distinct teams.
        if len(teams) < 2:
            raise TradeError(
                DEAL_INVALIDATED,
                "Trade must include at least 2 teams",
                {"rule": self.rule_id, "teams": teams},
            )

        # B) The teams list must not contain duplicates.
        seen: set[str] = set()
        duplicates: list[str] = []
        for team_id in teams:
            if team_id in seen and team_id not in duplicates:
                duplicates.append(team_id)
            seen.add(team_id)
        if duplicates:
            raise TradeError(
                DEAL_INVALIDATED,
                "Deal teams must be unique",
                {"rule": self.rule_id, "teams": teams, "duplicates": duplicates},
            )

        legs = getattr(deal, "legs", None)
        if not isinstance(legs, dict):
            raise TradeError(
                DEAL_INVALIDATED,
                "Invalid deal legs",
                {"rule": self.rule_id, "teams": teams, "legs_type": type(legs).__name__},
            )

        # C) Prevent empty trades where no assets move.
        total_assets = 0
        for assets in legs.values():
            if assets:
                total_assets += len(assets)
        if total_assets <= 0:
            raise TradeError(
                DEAL_INVALIDATED,
                "Deal must include at least one moving asset",
                {"rule": self.rule_id, "teams": teams},
            )

        # D) Prevent dummy teams: every team listed must either send or receive at least one asset.
        outgoing_counts: dict[str, int] = {team_id: 0 for team_id in teams}
        incoming_counts: dict[str, int] = {team_id: 0 for team_id in teams}
        team_set = set(teams)

        for team_id in teams:
            team_assets = legs.get(team_id) or []
            outgoing_counts[team_id] = len(team_assets)

        for sender_team, assets in legs.items():
            if not assets:
                continue
            for asset in assets:
                receiver = resolve_asset_receiver(deal, sender_team, asset)
                if receiver not in team_set or receiver == sender_team:
                    # Receiver validation (membership / self-receiver) is handled by TeamLegsRule.
                    # Skip dummy-team evaluation here to avoid masking the underlying error.
                    return
                incoming_counts[receiver] += 1

        non_participants = [
            team_id
            for team_id in teams
            if outgoing_counts.get(team_id, 0) == 0 and incoming_counts.get(team_id, 0) == 0
        ]
        if non_participants:
            raise TradeError(
                DEAL_INVALIDATED,
                "Deal includes non-participating team(s)",
                {
                    "rule": self.rule_id,
                    "teams": teams,
                    "non_participants": non_participants,
                    "outgoing_counts": outgoing_counts,
                    "incoming_counts": incoming_counts,
                },
            )

