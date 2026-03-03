from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Sequence, Tuple, Optional, Set

from . import config
from .names import generate_unique_full_name
from .types import CollegePlayer, CollegeTeam, json_dumps
from ratings_2k import generate_2k_ratings

# ----------------------------
# Name generation
# ----------------------------

def _pick_weighted(rng: random.Random, items: Sequence[Tuple[str, float]]) -> str:
    total = sum(w for _, w in items)
    r = rng.random() * total
    acc = 0.0
    for v, w in items:
        acc += w
        if r <= acc:
            return v
    return items[-1][0]


def _clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def _round_int(x: float) -> int:
    return int(round(x))


@dataclass(frozen=True, slots=True)
class PlayerProfile:
    name: str
    pos: str
    age: int
    height_in: int
    weight_lb: int
    ovr: int
    attrs: Dict[str, Any]


def sample_class_strength(rng: random.Random) -> float:
    """Sample a class_strength ~ N(0, std) clipped to clamp."""
    lo, hi = config.CLASS_STRENGTH_CLAMP
    x = rng.gauss(0.0, config.CLASS_STRENGTH_STD)
    return float(_clamp(x, lo, hi))


def generate_player_profile(
    rng: random.Random,
    *,
    class_strength: float,
    class_year: int,
    used_name_keys: Optional[Set[str]] = None,
) -> PlayerProfile:
    """
    Generate a single player profile.

    Philosophy:
    - attrs_json SSOT는 ratings_2k.generate_2k_ratings()가 만든 2K base ratings dict.
    - OVR은 generator가 target_ovr로 함께 반환한 값을 그대로 사용한다.
    """
    # Name
    if used_name_keys is None:
        used_name_keys = set()
    name = generate_unique_full_name(rng, used_name_keys)

    # Position
    pos = _pick_weighted(rng, list(config.POS_WEIGHTS.items()))

    # Size (rough by position)
    # Keep this light; detailed body types can be layered later.
    if pos in ("PG", "SG"):
        h_mu = 74.0 if pos == "PG" else 76.0
        w_mu = 185.0 if pos == "PG" else 200.0
    elif pos == "SF":
        h_mu, w_mu = 79.0, 215.0
    elif pos == "PF":
        h_mu, w_mu = 82.0, 235.0
    else:  # C
        h_mu, w_mu = 84.0, 255.0

    height_in = _round_int(_clamp(rng.gauss(h_mu, 1.8), config.HEIGHT_IN_RANGE[0], config.HEIGHT_IN_RANGE[1]))
    weight_lb = _round_int(_clamp(rng.gauss(w_mu, 18.0), config.WEIGHT_LB_RANGE[0], config.WEIGHT_LB_RANGE[1]))

    # Age: base by class year + small jitter
    base_age = config.BASE_AGE_BY_CLASS_YEAR.get(class_year, 18 + (class_year - 1))
    age = int(_clamp(base_age + rng.choice([0, 0, 0, 1]), 18, 24))

    gen = generate_2k_ratings(
        rng,
        pos=str(pos),
        height_in=int(height_in),
        weight_lb=int(weight_lb),
        age=int(age),
        class_year=int(class_year),
        class_strength=float(class_strength),
        target_ovr=None,
        archetype_id=None,
        ovr_range=tuple(config.COLLEGE_OVR_RANGE),
    )

    ovr = int(gen.target_ovr)
    attrs = dict(gen.attrs)

    return PlayerProfile(
        name=name,
        pos=pos,
        age=age,
        height_in=height_in,
        weight_lb=weight_lb,
        ovr=ovr,
        attrs=attrs,
    )


def build_college_teams() -> List[CollegeTeam]:
    """Build the full list of college teams.

    Uses configured seeds for the first N teams, and deterministically auto-generates
    additional teams if COLLEGE_TEAM_COUNT > len(COLLEGE_TEAMS).
    """
    teams: List[CollegeTeam] = []

    seeds_by_id = {s.college_team_id: s for s in config.COLLEGE_TEAMS}
    confs = list(getattr(config, "COLLEGE_CONFERENCES", None) or ["North", "South", "East", "West"])

    for i in range(1, int(config.COLLEGE_TEAM_COUNT) + 1):
        tid = f"COL_{i:03d}"
        seed = seeds_by_id.get(tid)
        if seed is not None:
            name = seed.name
            conf = seed.conference
        else:
            conf = confs[(i - 1) % len(confs)]
            name = _auto_team_name(i, conf)

        teams.append(
            CollegeTeam(
                college_team_id=tid,
                name=str(name),
                conference=str(conf),
                meta={},
            )
        )

    return teams


def _auto_team_name(i: int, conference: str) -> str:
    """Deterministically generate additional fictional team names.

    Avoid randomness at import time; output must be stable across runs.
    """
    prefixes = [
        "Great", "Iron", "Pine", "Cedar", "Metro", "Summit", "Blue", "Stone", "Coastal", "Bayou",
        "Sun", "Desert", "Gulf", "Magnolia", "River", "Capitol", "Atlantic", "Crown", "Palisade", "Pacific",
        "Redstone", "Sierra", "Canyon", "Golden", "Frontier", "Highland", "Sequoia", "Prairie", "Lakeside", "Harbor",
    ]
    suffixes = ["State", "University", "College", "Tech", "A&M", "Institute", "Poly", "Academy"]

    p = prefixes[(i - 1) % len(prefixes)]
    s = suffixes[((i - 1) // len(prefixes)) % len(suffixes)]
    # Add numeric tail to guarantee uniqueness at high team counts.
    return f"{p} {conference} {s} {i:03d}"


def generate_initial_world_players(
    rng: random.Random,
    *,
    season_year: int,
    teams: Sequence[CollegeTeam],
    class_strength_for_entry_season: Callable[[int], float],
    used_name_keys: Optional[Set[str]] = None,
) -> List[CollegePlayer]:
    """
    Generate a full college world at game start:
    - Players for class_year 1..4 across all teams
    - entry_season_year is aligned such that class_year=1 corresponds to entry_season_year==season_year
    """
    players: List[CollegePlayer] = []
    if used_name_keys is None:
        used_name_keys = set()
    # deterministic team ordering
    team_ids = [t.college_team_id for t in teams]

    # We don't allocate player_id here; service does it in DB-aware way.
    # We'll use temporary placeholders and let service rewrite IDs.
    tmp_id_counter = 0

    for college_team_id in team_ids:
        for class_year, n in config.BOOTSTRAP_CLASS_YEAR_COUNTS_PER_TEAM.items():
            entry_season = int(season_year - (class_year - 1))
            cs = float(class_strength_for_entry_season(entry_season))

            for _ in range(int(n)):
                prof = generate_player_profile(rng, class_strength=cs, class_year=class_year, used_name_keys=used_name_keys)
                tmp_id_counter += 1
                tmp_pid = f"TMP{tmp_id_counter:06d}"

                players.append(
                    CollegePlayer(
                        player_id=tmp_pid,
                        name=prof.name,
                        pos=prof.pos,
                        age=prof.age,
                        height_in=prof.height_in,
                        weight_lb=prof.weight_lb,
                        ovr=prof.ovr,
                        college_team_id=college_team_id,
                        class_year=int(class_year),
                        entry_season_year=entry_season,
                        status="ACTIVE",
                        attrs=prof.attrs,
                    )
                )

    return players


def generate_players_for_team_class(
    rng: random.Random,
    *,
    college_team_id: str,
    class_year: int,
    entry_season_year: int,
    class_strength: float,
    count: int,
    used_name_keys: Optional[Set[str]] = None,
) -> List[CollegePlayer]:
    """Generate players for a single team and a single class year.

    This is primarily used by the deficit-fill offseason logic. The service layer
    rewrites player_id using DB-allocated ids.
    """
    out: List[CollegePlayer] = []
    n = int(count)
    if n <= 0:
        return out

    if used_name_keys is None:
        used_name_keys = set()

    cy = int(class_year)
    esy = int(entry_season_year)

    # Temporary ids only need to be unique within this batch.
    for k in range(n):
        prof = generate_player_profile(rng, class_strength=float(class_strength), class_year=cy, used_name_keys=used_name_keys)
        tmp_pid = f"TMPF{esy}{cy}{k + 1:05d}"
        out.append(
            CollegePlayer(
                player_id=tmp_pid,
                name=prof.name,
                pos=prof.pos,
                age=prof.age,
                height_in=prof.height_in,
                weight_lb=prof.weight_lb,
                ovr=prof.ovr,
                college_team_id=str(college_team_id),
                class_year=cy,
                entry_season_year=esy,
                status="ACTIVE",
                attrs=prof.attrs,
            )
        )

    return out


def generate_freshmen_for_season(
    rng: random.Random,
    *,
    entry_season_year: int,
    teams: Sequence[CollegeTeam],
    class_strength: float,
    used_name_keys: Optional[Set[str]] = None,
) -> List[CollegePlayer]:
    """Generate only freshmen for a new season and distribute across teams."""
    players: List[CollegePlayer] = []
    if used_name_keys is None:
        used_name_keys = set()
    team_ids = [t.college_team_id for t in teams]
    tmp_id_counter = 0

    for college_team_id in team_ids:
        for _ in range(int(config.FRESHMEN_PER_TEAM_PER_YEAR)):
            prof = generate_player_profile(rng, class_strength=float(class_strength), class_year=1, used_name_keys=used_name_keys)
            tmp_id_counter += 1
            tmp_pid = f"TMPF{entry_season_year}{tmp_id_counter:05d}"

            players.append(
                CollegePlayer(
                    player_id=tmp_pid,
                    name=prof.name,
                    pos=prof.pos,
                    age=prof.age,
                    height_in=prof.height_in,
                    weight_lb=prof.weight_lb,
                    ovr=prof.ovr,
                    college_team_id=college_team_id,
                    class_year=1,
                    entry_season_year=int(entry_season_year),
                    status="ACTIVE",
                    attrs=prof.attrs,
                )
            )
    return players
