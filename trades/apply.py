from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from league_repo import LeagueRepo
from schema import normalize_player_id, normalize_team_id

from .errors import APPLY_FAILED, TradeError
from .models import Deal, PlayerAsset, resolve_asset_receiver

from .orchestration.locks import trade_exec_serial_lock


@dataclass(frozen=True)
class _PlayerMove:
    player_id: str
    from_team: str
    to_team: str


def _normalize_player_id_str(value: Any) -> str:
    return str(normalize_player_id(value, strict=True))


def _normalize_team_id_str(value: Any) -> str:
    return str(normalize_team_id(value, strict=True))


def _collect_player_moves(deal: Deal) -> list[_PlayerMove]:
    moves: list[_PlayerMove] = []
    seen: set[str] = set()
    for from_team, assets in deal.legs.items():
        normalized_from_team = _normalize_team_id_str(from_team)
        for asset in assets:
            if not isinstance(asset, PlayerAsset):
                continue
            player_id = _normalize_player_id_str(asset.player_id)
            if player_id in seen:
                raise ValueError(f"duplicate player in trade assets: {player_id}")
            seen.add(player_id)
            try:
                to_team = resolve_asset_receiver(deal, normalized_from_team, asset)
            except TradeError as exc:
                # Preserve apply-layer error semantics.
                raise TradeError(
                    APPLY_FAILED,
                    "Failed to resolve asset receiver",
                    {
                        "sender_team": normalized_from_team,
                        "player_id": player_id,
                        "asset_to_team": getattr(asset, "to_team", None),
                        "cause_code": getattr(exc, "code", None),
                    },
                ) from exc
            normalized_to_team = _normalize_team_id_str(to_team)
            moves.append(
                _PlayerMove(
                    player_id=player_id,
                    from_team=normalized_from_team,
                    to_team=normalized_to_team,
                )
            )
    return moves


def _validate_player_moves(repo: LeagueRepo, moves: list[_PlayerMove]) -> None:
    for move in moves:
        try:
            current_team = repo.get_team_id_by_player(move.player_id)
        except KeyError as exc:
            raise ValueError(f"player_id not found in DB: {move.player_id}") from exc
        if current_team != move.from_team:
            raise ValueError(
                "player_id "
                f"{move.player_id} expected team {move.from_team} "
                f"but DB shows {current_team}"
            )


def _open_service(db_path: str):
    """
    Canonical entrypoint: LeagueService.open(db_path).
    Handles both context-manager and plain-object returns.
    """
    import contextlib
    from league_service import LeagueService  # local import to avoid cycles

    svc_or_cm = LeagueService.open(db_path)
    if hasattr(svc_or_cm, "__enter__"):
        return svc_or_cm  # context manager

    @contextlib.contextmanager
    def _cm():
        svc = svc_or_cm
        try:
            yield svc
        finally:
            close = getattr(svc, "close", None)
            if callable(close):
                close()

    return _cm()


def apply_deal_to_db(
    db_path: str,
    deal: Deal,
    source: str,
    deal_id: str | None,
    trade_date,
    dry_run: bool,
) -> Dict[str, Any]:
    if not db_path:
        raise ValueError("db_path is required to apply trades")

    player_moves = _collect_player_moves(deal)

    try:
        if dry_run:
            with LeagueRepo(db_path) as repo:
                repo.init_db()
                _validate_player_moves(repo, player_moves)
            return {
                "dry_run": True,
                "player_moves": [m.__dict__ for m in player_moves],
            }

        # Serialize against the trade-orchestration tick to avoid lost updates in
        # trade_market/trade_memory (derived state) and to keep trade constraints
        # consistent while a tick is running (single-process).
        with trade_exec_serial_lock(reason=f"APPLY_TRADE:{source}:{deal_id or ''}"):
            with _open_service(db_path) as svc:
                tx = svc.execute_trade(deal, source=source, trade_date=trade_date, deal_id=deal_id)

            # Project the committed trade (DB SSOT) into trade_market/trade_memory.
            # IMPORTANT:
            # - DB commit is the SSOT; market/memory are derived state.
            # - Projection failures must NOT fail the trade (otherwise the user sees an error
            #   while the DB has already changed).
            try:
                from .orchestration.market_state import apply_trade_executed_effects_to_state

                today_arg = None
                try:
                    from datetime import date as _date

                    if isinstance(trade_date, _date):
                        today_arg = trade_date
                except Exception:
                    today_arg = None

                sync_report = apply_trade_executed_effects_to_state(
                    transaction=tx,
                    today=today_arg,
                )
                if isinstance(tx, dict):
                    tx["market_sync"] = sync_report
            except Exception as exc:
                if isinstance(tx, dict):
                    tx["market_sync"] = {
                        "applied": False,
                        "exec_deal_id": tx.get("deal_id"),
                        "error": str(exc),
                        "exc": type(exc).__name__,
                    }

            return tx
    except TradeError:
        raise
    except Exception as exc:
        raise TradeError(APPLY_FAILED, "Failed to apply trade", {"error": str(exc)}) from exc
