from __future__ import annotations

"""Award candidate scoring (optional).

This module provides *deterministic* award candidate rankings using only the
workflow state's `player_stats` (and optionally `team_stats`).

Important:
- This is NOT meant to replicate official NBA award voting.
- It is designed to power game UI/News/Season reports with plausible,
  explainable candidates.

The output includes both:
- a `score` used for ranking
- a few key stats to display as supporting evidence

Because `player_stats` alone doesn't encode things like rookie status,
starting role, or team record, the APIs allow callers to pass optional
hints (rookie ids, starter flags, etc.).
"""

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .metrics import build_metric_registry, compute_metric_value
from .qualifiers import build_qualifier_rules, player_qualifies
from .types import coerce_float, normalized_player_lines, safe_div


@dataclass(frozen=True)
class AwardModel:
    """A weighted z-score model."""

    award: str
    features: Tuple[Tuple[str, float], ...]
    require_qualifier: bool = True


AWARD_MODELS: Dict[str, AwardModel] = {
    "MVP": AwardModel(
        "MVP",
        features=(
            ("PTS", 1.00),
            ("AST", 0.85),
            ("REB", 0.55),
            ("STL", 0.25),
            ("BLK", 0.20),
            ("TS_PCT", 0.90),
            ("TOV", -0.35),
        ),
    ),
    "DPOY": AwardModel(
        "DPOY",
        features=(
            ("STL", 1.00),
            ("BLK", 1.10),
            ("REB", 0.35),
            ("TOV", -0.10),
        ),
    ),
    "6MOY": AwardModel(
        "6MOY",
        features=(
            ("PTS", 1.10),
            ("TS_PCT", 0.65),
            ("AST", 0.25),
            ("REB", 0.20),
            ("TOV", -0.25),
        ),
    ),
    "MIP": AwardModel(
        "MIP",
        features=(
            ("PTS", 0.85),
            ("AST", 0.55),
            ("REB", 0.45),
            ("TS_PCT", 0.45),
        ),
        require_qualifier=True,
    ),
    "ROY": AwardModel(
        "ROY",
        features=(
            ("PTS", 1.00),
            ("AST", 0.70),
            ("REB", 0.55),
            ("TS_PCT", 0.40),
            ("TOV", -0.20),
        ),
        require_qualifier=True,
    ),
}


def _mean_std(values: Sequence[float]) -> Tuple[float, float]:
    if not values:
        return 0.0, 0.0
    m = sum(values) / float(len(values))
    var = sum((v - m) ** 2 for v in values) / float(len(values))
    return m, var ** 0.5


def _z(v: float, mean: float, std: float) -> float:
    if std <= 1e-12:
        return 0.0
    return (v - mean) / std


def compute_award_candidates(
    player_stats: Mapping[str, Any],
    team_stats: Mapping[str, Any] | None = None,
    *,
    award: str = "MVP",
    phase: str = "regular",
    top_n: int = 10,
    qualifier_profile: str = "auto",
    rookie_player_ids: Optional[Iterable[str]] = None,
    exclude_starters: Optional[bool] = None,
    starter_flags: Optional[Mapping[str, bool]] = None,
) -> List[Dict[str, Any]]:
    """Rank award candidates."""

    model = AWARD_MODELS.get(award.upper())
    if model is None:
        raise ValueError(f"Unsupported award: {award!r}")

    registry = build_metric_registry()
    rules = build_qualifier_rules(team_stats, player_stats, profile=qualifier_profile, phase=phase)

    rookie_set = {str(x) for x in rookie_player_ids} if rookie_player_ids is not None else None

    rows: List[Dict[str, Any]] = []
    feature_values: Dict[str, List[float]] = {k: [] for k, _w in model.features}

    pts_metric = registry.get("PTS")

    for pl in normalized_player_lines(player_stats):
        gp = max(0, pl.gp)
        if gp <= 0:
            continue

        if rookie_set is not None and pl.player_id not in rookie_set:
            continue

        if exclude_starters and starter_flags is not None and starter_flags.get(pl.player_id):
            continue

        if model.require_qualifier and pts_metric and not player_qualifies(pts_metric, pl.totals, gp, rules):
            continue

        feats: Dict[str, float] = {}
        for key, _w in model.features:
            metric = registry.get(key)
            if metric is None:
                feats[key] = 0.0
                continue

            if metric.mode == "per_game":
                val = compute_metric_value(metric, pl.totals, gp)
            elif key in {"TS_PCT", "EFG_PCT", "FG_PCT", "3P_PCT", "FT_PCT", "AST_TOV"}:
                val = compute_metric_value(metric, pl.totals, gp)
            else:
                val = safe_div(coerce_float(pl.totals.get(key), 0.0), float(gp), 0.0)

            feats[key] = float(val)

        for k in feature_values.keys():
            feature_values[k].append(feats.get(k, 0.0))

        rows.append(
            {
                "player_id": pl.player_id,
                "name": pl.name,
                "team_id": pl.team_id,
                "games": gp,
                "MIN_total": coerce_float(pl.totals.get("MIN"), 0.0),
                "features": feats,
            }
        )

    if not rows:
        return []

    # Compute mean/std for each feature and minutes
    stats: Dict[str, Tuple[float, float]] = {k: _mean_std(vals) for k, vals in feature_values.items()}
    min_totals = [float(x.get("MIN_total") or 0.0) for x in rows]
    min_mean, min_std = _mean_std(min_totals)

    scored: List[Dict[str, Any]] = []
    for r in rows:
        feats = r["features"]
        score = 0.0
        for k, w in model.features:
            mean, std = stats.get(k, (0.0, 0.0))
            score += float(w) * _z(float(feats.get(k, 0.0)), mean, std)

        score += 0.05 * _z(float(r.get("MIN_total") or 0.0), min_mean, min_std)

        out = {
            "player_id": r["player_id"],
            "name": r["name"],
            "team_id": r["team_id"],
            "games": r["games"],
            "score": round(score, 4),
        }

        contributions: List[Tuple[str, float]] = []
        for k, w in model.features:
            mean, std = stats.get(k, (0.0, 0.0))
            contrib = float(w) * _z(float(feats.get(k, 0.0)), mean, std)
            contributions.append((k, contrib))
        contributions.sort(key=lambda x: abs(x[1]), reverse=True)
        out["why"] = [
            {"metric": k, "contribution": round(c, 4), "value": round(float(feats.get(k, 0.0)), 4)}
            for k, c in contributions[:4]
        ]
        scored.append(out)

    scored.sort(key=lambda x: (-float(x.get("score") or 0.0), -int(x.get("games") or 0), str(x.get("player_id") or "")))
    return scored[: max(0, int(top_n or 0))]
