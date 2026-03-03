from __future__ import annotations

"""Leaderboard generation.

This module turns season totals (player_stats/team_stats) into robust leaderboards.

Key features:
- Supports per-game, totals, per-36, and ratio/advanced metrics
- Applies qualifiers (min GP, min attempts, ...)
- Deterministic ordering and tie handling
- Produces a rich bundle suitable for caching
"""

from typing import Any, Dict, List, Mapping, Optional, Tuple

from .metrics import build_metric_registry, compute_metric_value, list_metrics
from .qualifiers import build_qualifier_rules, player_qualifies
from .types import (
    LeaderboardConfig,
    LeaderboardRow,
    LeaderboardsBundle,
    Metric,
    QualifierRules,
    coerce_float,
    normalized_player_lines,
)



def _default_config() -> LeaderboardConfig:
    return {
        "top_n": 5,
        "include_ties": True,
        "qualifier_profile": "auto",
        "modes": ["per_game", "totals", "per_36", "advanced"],
        "categories": ["traditional", "shooting", "advanced", "rate"],
    }


def _merge_config(user: Optional[LeaderboardConfig]) -> LeaderboardConfig:
    cfg = _default_config()
    if not user:
        return cfg
    cfg.update({k: v for k, v in user.items() if v is not None})
    return cfg


def _rounded(value: float, decimals: int) -> float:
    try:
        return round(float(value), int(decimals))
    except Exception:
        return 0.0


def _build_min_required(rules: QualifierRules) -> Dict[str, Any]:
    return {
        "min_gp": rules["min_gp"],
        "min_min_total": rules["min_min_total"],
        "min_fga": rules["min_fga"],
        "min_3pa": rules["min_3pa"],
        "min_fta": rules["min_fta"],
    }


def _stable_sort_key(row: LeaderboardRow, tiebreak: Tuple[float, ...], *, desc: bool) -> Tuple:
    value = coerce_float(row.get("value"), 0.0)
    primary = (-value,) if desc else (value,)
    tb = tuple(-float(x) for x in tiebreak)
    pid = str(row.get("player_id") or "")
    return primary + tb + (pid,)


def _assign_ranks(sorted_rows: List[LeaderboardRow], *, tol: float = 1e-12) -> None:
    last_value: Optional[float] = None
    last_rank = 0
    for i, r in enumerate(sorted_rows, start=1):
        v = coerce_float(r.get("value"), 0.0)
        if last_value is not None and abs(v - last_value) <= tol:
            r["rank"] = last_rank
        else:
            r["rank"] = i
            last_rank = i
            last_value = v


def _trim_with_ties(rows: List[LeaderboardRow], *, top_n: int, include_ties: bool, tol: float = 1e-12) -> List[LeaderboardRow]:
    if top_n <= 0:
        return []
    if len(rows) <= top_n:
        return rows
    if not include_ties:
        return rows[:top_n]

    cutoff = coerce_float(rows[top_n - 1].get("value"), 0.0)
    out: List[LeaderboardRow] = []
    for r in rows:
        out.append(r)
        if len(out) >= top_n and abs(coerce_float(r.get("value"), 0.0) - cutoff) > tol:
            break
    return out


def build_row(
    *,
    player_id: str,
    name: str,
    team_id: str,
    gp: int,
    totals: Mapping[str, float],
    metric: Metric,
    value: float,
    rules: QualifierRules,
) -> LeaderboardRow:
    row: LeaderboardRow = {
        "player_id": player_id,
        "name": name,
        "team_id": team_id,
        "games": gp,
        "MIN": coerce_float(totals.get("MIN"), 0.0),
        "value": value,
        "qualifies": player_qualifies(metric, totals, gp, rules),
        "min_required": _build_min_required(rules),
    }

    if metric.mode == "per_game":
        row["per_game"] = value

    row[metric.key] = value
    return row


def compute_metric_leaderboard(
    player_stats: Mapping[str, Any],
    team_stats: Mapping[str, Any] | None,
    *,
    metric: Metric,
    phase: str,
    rules: QualifierRules,
    top_n: int,
    include_ties: bool,
) -> List[LeaderboardRow]:
    rows: List[LeaderboardRow] = []

    for pl in normalized_player_lines(player_stats):
        gp = max(0, pl.gp)
        if gp <= 0:
            continue

        value_raw = compute_metric_value(metric, pl.totals, gp)
        value = _rounded(value_raw, metric.decimals)

        row = build_row(
            player_id=pl.player_id,
            name=pl.name,
            team_id=pl.team_id,
            gp=gp,
            totals=pl.totals,
            metric=metric,
            value=value,
            rules=rules,
        )
        if not row.get("qualifies"):
            continue
        rows.append(row)

    def tiebreak_tuple(r: LeaderboardRow) -> Tuple[float, ...]:
        totals_for_tb: List[float] = []
        pid = str(r.get("player_id") or "")
        entry = player_stats.get(pid) if isinstance(player_stats, Mapping) else None
        totals = entry.get("totals") if isinstance(entry, Mapping) else None
        totals = totals if isinstance(totals, Mapping) else {}
        for k in metric.tiebreak_totals:
            totals_for_tb.append(coerce_float(totals.get(k), 0.0))
        totals_for_tb.append(coerce_float(totals.get("MIN"), 0.0))
        return tuple(totals_for_tb)

    rows.sort(key=lambda r: _stable_sort_key(r, tiebreak_tuple(r), desc=metric.sort_desc))
    _assign_ranks(rows)
    rows = _trim_with_ties(rows, top_n=top_n, include_ties=include_ties)
    return rows


def compute_leaderboards(
    player_stats: Mapping[str, Any],
    team_stats: Mapping[str, Any] | None,
    *,
    phase: str = "regular",
    config: LeaderboardConfig | None = None,
) -> LeaderboardsBundle:
    """Compute a rich leaderboards bundle suitable for caching."""

    cfg = _merge_config(config)
    top_n = int(cfg.get("top_n", 5) or 5)
    include_ties = bool(cfg.get("include_ties", True))
    profile = cfg.get("qualifier_profile", "auto")

    rules = build_qualifier_rules(team_stats, player_stats, profile=profile, phase=phase)

    registry = build_metric_registry()
    keys = cfg.get("metric_keys")
    metrics = list_metrics(registry=registry, modes=cfg.get("modes"), categories=cfg.get("categories"), keys=keys)

    out_per_game: Dict[str, List[LeaderboardRow]] = {}
    out_totals: Dict[str, List[LeaderboardRow]] = {}
    out_per_36: Dict[str, List[LeaderboardRow]] = {}
    out_adv: Dict[str, List[LeaderboardRow]] = {}

    for m in metrics:
        board = compute_metric_leaderboard(
            player_stats,
            team_stats,
            metric=m,
            phase=phase,
            rules=rules,
            top_n=top_n,
            include_ties=include_ties,
        )

        if m.mode == "per_game":
            out_per_game[m.key] = board
        elif m.mode == "totals":
            out_totals[m.key] = board
        elif m.mode == "per_36":
            out_per_36[m.key] = board
        else:
            out_adv[m.key] = board

    meta = {
        "phase": phase,
        "qualifier_rules": dict(rules),
        "config": dict(cfg),
        "metrics": [
            {
                "key": m.key,
                "label": m.label,
                "category": m.category,
                "mode": m.mode,
                "decimals": m.decimals,
                "sort_desc": m.sort_desc,
                "qualifier": m.qualifier,
            }
            for m in metrics
        ],
    }

    return {"meta": meta, "per_game": out_per_game, "totals": out_totals, "per_36": out_per_36, "advanced": out_adv}

