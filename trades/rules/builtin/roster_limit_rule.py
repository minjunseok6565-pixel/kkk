from __future__ import annotations

from dataclasses import dataclass

from schema import normalize_team_id

from ...errors import ROSTER_LIMIT, TradeError
from ...models import PlayerAsset, resolve_asset_receiver
from ..base import TradeContext


@dataclass
class RosterLimitRule:
    rule_id: str = "roster_limit"
    priority: int = 60
    enabled: bool = True

    # NBA 현실감: 트레이드 후 로스터는 14~15명 범위로 유지.
    # NOTE: 표준 로스터(일반 계약)만 14~15 제한을 적용한다.
    #       active TWO_WAY 계약자는 카운트에서 제외한다.
    #       추가 규정: 트레이드 완료 시점에만 수취 팀의 TWO_WAY 보유는 최대 2명으로 제한한다.
    min_players: int = 14
    max_players: int = 15

    def validate(self, deal, ctx: TradeContext) -> None:
        players_out: dict[str, int] = {team_id: 0 for team_id in deal.teams}
        players_in: dict[str, int] = {team_id: 0 for team_id in deal.teams}

        for team_id, assets in deal.legs.items():
            for asset in assets:
                if not isinstance(asset, PlayerAsset):
                    continue
                players_out[team_id] += 1
                receiver = resolve_asset_receiver(deal, team_id, asset)
                players_in[receiver] += 1

        for team_id in deal.teams:
            tid = str(normalize_team_id(team_id, strict=True))
            current_ids = set(ctx.get_roster_player_ids(tid))

            # Standard roster count excludes active TWO_WAY contracts.
            standard_ids = set()
            if current_ids:
                marks = ",".join("?" for _ in current_ids)
                rows = ctx.repo._conn.execute(
                    f"""
                    SELECT player_id
                    FROM contracts
                    WHERE player_id IN ({marks})
                      AND UPPER(COALESCE(contract_type,''))='TWO_WAY'
                      AND UPPER(COALESCE(status,''))='ACTIVE'
                      AND COALESCE(is_active, 0)=1;
                    """,
                    tuple(current_ids),
                ).fetchall()
                two_way_ids = {str(r['player_id']) for r in rows}
                standard_ids = set(current_ids) - set(two_way_ids)
            else:
                two_way_ids = set()
                standard_ids = set()

            out_standard = 0
            out_two_way = 0
            for asset in deal.legs.get(team_id, []) or []:
                if not isinstance(asset, PlayerAsset):
                    continue
                pid = str(asset.player_id)
                if pid in standard_ids:
                    out_standard += 1
                if pid in two_way_ids:
                    out_two_way += 1

            in_standard = 0
            in_two_way = 0
            for from_team, assets in deal.legs.items():
                if str(from_team) == str(team_id):
                    continue
                for asset in assets or []:
                    if not isinstance(asset, PlayerAsset):
                        continue
                    recv = resolve_asset_receiver(deal, from_team, asset)
                    if str(recv) != str(team_id):
                        continue
                    # Incoming player counted unless currently active two-way.
                    pid = str(asset.player_id)
                    row = ctx.repo._conn.execute(
                        """
                        SELECT 1 FROM contracts
                        WHERE player_id=?
                          AND UPPER(COALESCE(contract_type,''))='TWO_WAY'
                          AND UPPER(COALESCE(status,''))='ACTIVE'
                          AND COALESCE(is_active, 0)=1
                        LIMIT 1;
                        """,
                        (pid,),
                    ).fetchone()
                    if not row:
                        in_standard += 1
                    else:
                        in_two_way += 1

            new_count = len(standard_ids) - int(out_standard) + int(in_standard)
            new_two_way_count = len(two_way_ids) - int(out_two_way) + int(in_two_way)
            if new_count > int(self.max_players):
                raise TradeError(
                    ROSTER_LIMIT,
                    "Roster limit exceeded",
                    {"team_id": team_id, "count": new_count, "max": int(self.max_players)},
                )

            # Trade-time only: when receiving TWO_WAY player(s), the receiving team may hold at most 2.
            if in_two_way > 0 and new_two_way_count > 2:
                raise TradeError(
                    ROSTER_LIMIT,
                    "Two-way trade-time limit exceeded",
                    {"team_id": team_id, "two_way_count": new_two_way_count, "trade_time_max_two_way": 2},
                )

            # Lower bound
            if new_count < int(self.min_players):
                raise TradeError(
                    ROSTER_LIMIT,
                    "Roster size below minimum",
                    {"team_id": team_id, "count": new_count, "min": int(self.min_players)},
                 )
