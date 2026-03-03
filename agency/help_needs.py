from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple


DEFAULT_ALLOWED_TAGS: Tuple[str, ...] = (
    "DEFENSE",
    "SPACING",
    "RIM_PRESSURE",
    "PRIMARY_INITIATOR",
    "SHOT_CREATION",
)


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return float(default)
        return float(x)
    except Exception:
        return float(default)


def _clamp01(x: Any) -> float:
    v = _safe_float(x, 0.0)
    if v < 0.0:
        return 0.0
    if v > 1.0:
        return 1.0
    return float(v)


def compute_team_need_tags(
    players: Sequence[Tuple[str, Mapping[str, Any], float]],
    *,
    max_tags: int = 3,
    allowed_tags: Sequence[str] = DEFAULT_ALLOWED_TAGS,
) -> List[str]:
    """Compute team-level need tags from a weighted set of player supplies.

    Args:
        players:
            Sequence of (player_id, attrs_dict, weight).
            attrs_dict is typically players.attrs_json (decoded).
            weight is typically minutes (or minutes share) for the evaluated window.
        max_tags:
            Maximum number of need tags to return.
        allowed_tags:
            Candidate tag names to consider.

    Returns:
        List of uppercase tags, ordered by descending need.
        Returns [] if fit engine is unavailable or inputs are empty.
    """

    try:
        from trades.valuation.fit_engine import FitEngine
        from trades.valuation.types import PlayerSnapshot
    except Exception:
        return []

    mt = int(max_tags)
    if mt <= 0:
        return []

    tags_u: List[str] = []
    seen: set[str] = set()
    for t in allowed_tags or []:
        s = str(t or "").strip().upper()
        if not s or s in seen:
            continue
        seen.add(s)
        tags_u.append(s)

    if not tags_u:
        return []

    fe = FitEngine()

    sum_w = 0.0
    weighted_cov: Dict[str, float] = {t: 0.0 for t in tags_u}

    for pid, attrs, w_raw in players or []:
        w = _safe_float(w_raw, 0.0)
        if w <= 0.0:
            continue

        attrs_dict: Dict[str, Any] = dict(attrs) if isinstance(attrs, Mapping) else {}

        try:
            snap = PlayerSnapshot(kind="player", player_id=str(pid), attrs=attrs_dict, meta={})
            sv = fe.compute_player_supply_vector(snap) or {}
            supply = {str(k).upper(): _clamp01(v) for k, v in dict(sv).items()}
        except Exception:
            supply = {}

        for t in tags_u:
            weighted_cov[t] += w * supply.get(t, 0.0)

        sum_w += w

    if sum_w <= 0.0:
        return []

    need_by_tag: Dict[str, float] = {}
    for t in tags_u:
        cov = weighted_cov.get(t, 0.0) / sum_w
        need_by_tag[t] = 1.0 - _clamp01(cov)

    out = sorted(tags_u, key=lambda t: (-need_by_tag.get(t, 0.0), t))[: min(mt, len(tags_u))]
    return [str(t).upper() for t in out if str(t).strip()]
