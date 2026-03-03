from __future__ import annotations

"""Postseason field/seeding helpers.

This module is responsible for:
- Building postseason field from actual standings (SSOT)
- Optional random field generation (for sandbox / fun mode)
- Determining home-court advantage between two seed entries

No state mutation happens here (director handles state writes).
"""

import random
from typing import Any, Dict, List, Optional, Tuple

from config import TEAM_TO_CONF_DIV
from team_utils import get_conference_standings


def seed_entry_from_standings_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a standings row into the SeedEntry shape used by postseason."""
    return {
        "team_id": row.get("team_id"),
        "seed": row.get("rank"),
        "conference": row.get("conference"),
        "division": row.get("division"),
        "wins": row.get("wins"),
        "losses": row.get("losses"),
        "games_played": row.get("games_played"),
        "win_pct": row.get("win_pct"),
        "point_diff": row.get("point_diff"),
    }


def build_postseason_field_from_standings() -> Dict[str, Any]:
    """Return postseason field grouped by conference.

    Output (legacy contract; UI/server depends on it):
    {
      'east': {'auto_bids': [...], 'play_in': [...], 'eliminated': [...]},
      'west': {'auto_bids': [...], 'play_in': [...], 'eliminated': [...]},
    }
    """
    standings = get_conference_standings()
    field: Dict[str, Any] = {}

    for conf_key in ("east", "west"):
        conf_rows = standings.get(conf_key, [])
        seeds = [seed_entry_from_standings_row(r) for r in conf_rows]
        auto_bids = [s for s in seeds if isinstance(s.get("seed"), int) and int(s["seed"]) <= 6]
        play_in = [s for s in seeds if isinstance(s.get("seed"), int) and 7 <= int(s["seed"]) <= 10]
        eliminated = [s for s in seeds if isinstance(s.get("seed"), int) and int(s["seed"]) > 10]
        field[conf_key] = {
            "auto_bids": auto_bids,
            "play_in": play_in,
            "eliminated": eliminated,
        }

    return field


def pick_home_advantage(entry_a: Dict[str, Any], entry_b: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Return (home_adv, road) for a series matchup.

    Tie-break order:
    1) Lower seed number
    2) Higher win_pct
    3) Higher point_diff
    4) Lexicographic team_id (stable deterministic)
    """
    seed_a, seed_b = entry_a.get("seed"), entry_b.get("seed")
    if isinstance(seed_a, int) and isinstance(seed_b, int) and seed_a != seed_b:
        return (entry_a, entry_b) if seed_a < seed_b else (entry_b, entry_a)

    win_pct_a = float(entry_a.get("win_pct") or 0.0)
    win_pct_b = float(entry_b.get("win_pct") or 0.0)
    if win_pct_a != win_pct_b:
        return (entry_a, entry_b) if win_pct_a > win_pct_b else (entry_b, entry_a)

    pd_a = float(entry_a.get("point_diff") or 0.0)
    pd_b = float(entry_b.get("point_diff") or 0.0)
    if pd_a != pd_b:
        return (entry_a, entry_b) if pd_a > pd_b else (entry_b, entry_a)

    a_id = str(entry_a.get("team_id") or "")
    b_id = str(entry_b.get("team_id") or "")
    return (entry_a, entry_b) if a_id < b_id else (entry_b, entry_a)


# ---------------------------------------------------------------------------
# Random field generation (sandbox/fun mode)
# ---------------------------------------------------------------------------

def _random_seed_entry(team_id: str, seed: Optional[int], conf_key: str, rng: random.Random) -> Dict[str, Any]:
    info = TEAM_TO_CONF_DIV.get(team_id, {}) or {}
    division = info.get("division")

    # Higher seed => slightly higher expected win%
    base_win_pct = 0.78 - max((seed or 12) - 1, 0) * 0.035
    win_pct = max(0.35, min(0.80, base_win_pct + rng.uniform(-0.02, 0.03)))

    wins = int(round(win_pct * 82))
    wins = min(max(wins, 28), 64)
    losses = 82 - wins

    point_diff = int(rng.uniform(-6, 8) + (0.9 - (seed or 12) * 0.2))

    return {
        "team_id": team_id,
        "seed": seed,
        "conference": conf_key,
        "division": division,
        "wins": wins,
        "losses": losses,
        "games_played": 82,
        "win_pct": wins / 82 if 82 else 0.0,
        "point_diff": point_diff,
    }


def _build_random_conf_field(conf_key: str, my_team_id: Optional[str], rng: random.Random) -> Dict[str, Any]:
    conf_teams = [
        tid
        for tid, meta in TEAM_TO_CONF_DIV.items()
        if (meta.get("conference") or "").lower() == conf_key
    ]
    rng.shuffle(conf_teams)

    auto_slots = list(range(1, 7))
    play_in_slots = list(range(7, 11))

    auto_bids: List[Dict[str, Any]] = []
    play_in: List[Dict[str, Any]] = []
    eliminated: List[Dict[str, Any]] = []

    remaining = [tid for tid in conf_teams if tid != my_team_id]
    rng.shuffle(remaining)

    if my_team_id:
        my_seed = rng.choice(auto_slots + play_in_slots)
        if my_seed in auto_slots:
            auto_slots.remove(my_seed)
        else:
            play_in_slots.remove(my_seed)
        entry = _random_seed_entry(my_team_id, my_seed, conf_key, rng)
        (auto_bids if my_seed <= 6 else play_in).append(entry)

    for seed in auto_slots:
        if not remaining:
            break
        auto_bids.append(_random_seed_entry(remaining.pop(), seed, conf_key, rng))

    for seed in play_in_slots:
        if not remaining:
            break
        play_in.append(_random_seed_entry(remaining.pop(), seed, conf_key, rng))

    seed_counter = 11
    while remaining:
        eliminated.append(_random_seed_entry(remaining.pop(), seed_counter, conf_key, rng))
        seed_counter += 1

    auto_bids = sorted(auto_bids, key=lambda r: r.get("seed") or 99)
    play_in = sorted(play_in, key=lambda r: r.get("seed") or 99)

    return {"auto_bids": auto_bids, "play_in": play_in, "eliminated": eliminated}


def build_random_postseason_field(my_team_id: str) -> Dict[str, Any]:
    """Build a randomized postseason field. Useful for quick testing.

    Note: This does *not* mutate state; director does.
    """
    rng = random.Random()
    my_conf = (TEAM_TO_CONF_DIV.get(my_team_id, {}) or {}).get("conference") or "east"
    my_conf = str(my_conf).lower()

    field: Dict[str, Any] = {}
    for conf_key in ("east", "west"):
        attach_my_team = my_team_id if conf_key == my_conf else None
        field[conf_key] = _build_random_conf_field(conf_key, attach_my_team, rng)

    return field
