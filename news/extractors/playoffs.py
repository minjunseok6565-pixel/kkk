from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from ..ids import make_event_id
from ..models import NewsEvent


def _parse_date_iso(value: Any) -> Optional[date]:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    if len(s) >= 10:
        s = s[:10]
    try:
        return date.fromisoformat(s)
    except Exception:
        return None


def _playoff_round_label(round_name: Optional[str]) -> str:
    mapping = {
        "Conference Quarterfinals": "플레이오프 1라운드",
        "Conference Semifinals": "플레이오프 2라운드",
        "Conference Finals": "컨퍼런스 파이널",
        "NBA Finals": "NBA 파이널",
    }
    if not round_name:
        return "플레이오프"
    return mapping.get(round_name, str(round_name))


def iter_series(playoffs: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Iterate series dicts in the standard bracket shape produced by `postseason.bracket`."""
    bracket = playoffs["bracket"]
    east = bracket["east"]
    west = bracket["west"]

    out: List[Dict[str, Any]] = []
    out.extend(east["quarterfinals"])
    out.extend(west["quarterfinals"])
    out.extend(east["semifinals"])
    out.extend(west["semifinals"])
    if east.get("finals"):
        out.append(east["finals"])
    if west.get("finals"):
        out.append(west["finals"])
    if bracket.get("finals"):
        out.append(bracket["finals"])
    return out

def series_key(series: Dict[str, Any]) -> str:
    return f"{series.get('round')}::{series.get('home_court')}::{series.get('road')}"


def wins_through(series: Dict[str, Any], game_index: int) -> Dict[str, int]:
    wins: Dict[str, int] = {}
    for g in (series.get("games") or [])[: game_index + 1]:
        w = g.get("winner")
        if not w:
            continue
        wins[str(w)] = wins.get(str(w), 0) + 1
    return wins


def _series_score(home_id: str, road_id: str, wins: Dict[str, int]) -> str:
    return f"{wins.get(home_id, 0)}-{wins.get(road_id, 0)}"


def _is_match_point(best_of: int, wins_home: int, wins_road: int) -> bool:
    need = best_of // 2 + 1
    return max(wins_home, wins_road) == need - 1 and (wins_home != wins_road)


def extract_playoff_events(
    playoffs: Dict[str, Any],
    *,
    processed_game_ids: set[str],
    boxscore_lookup: Dict[Tuple[str, str, str, int, int], Dict[str, Any]] | None = None,
) -> List[NewsEvent]:
    """Extract playoff events for games that are not yet processed.

    This extractor is *strictly* deduped by processed_game_ids (game_id), and does not use
    any legacy counters or series-length snapshots.

    Args:
        playoffs: postseason['playoffs'] snapshot
        processed_game_ids: set of game_id that already generated news
        boxscore_lookup: optional index mapping
            (date, home_id, away_id, home_score, away_score) -> game_result_v2

    Returns:
        A list of NewsEvent objects for newly finished playoff games.
    """
    if not isinstance(processed_game_ids, set):
        processed_game_ids = set(str(x) for x in processed_game_ids)  # type: ignore[arg-type]

    events: List[NewsEvent] = []

    for s in iter_series(playoffs):
        games = s.get("games") or []
        if not isinstance(games, list):
            continue

        home_id = str(s.get("home_court") or "")
        road_id = str(s.get("road") or "")
        best_of = int(s.get("best_of") or 7)
        round_label = _playoff_round_label(s.get("round"))
        key = series_key(s)

        for idx, g in enumerate(games):
            if not isinstance(g, dict):
                continue

            winner = str(g.get("winner") or "")
            if not winner:
                continue

            game_id = str(g.get("game_id") or "").strip()
            if not game_id:
                # Postseason games should always carry deterministic game_id.
                # Skip silently to avoid spamming duplicates if upstream forgot to set it.
                continue

            if game_id in processed_game_ids:
                continue

            d = _parse_date_iso(g.get("date"))
            d_iso = d.isoformat() if d else str(g.get("date") or "")[:10]

            try:
                hs = int(g.get("home_score"))
                as_ = int(g.get("away_score"))
            except Exception:
                hs = 0
                as_ = 0

            loser = road_id if winner == home_id else home_id
            margin = abs(hs - as_)

            wins = wins_through(s, idx)
            h_wins = wins.get(home_id, 0)
            r_wins = wins.get(road_id, 0)
            s_score = _series_score(home_id, road_id, wins)
            game_number = idx + 1

            # Attempt to attach top performer strings
            top_perf: List[str] = []
            if boxscore_lookup is not None:
                gr = boxscore_lookup.get((d_iso, home_id, road_id, hs, as_))
                if isinstance(gr, dict):
                    top_perf = _extract_top_performers_simple(gr, winner, limit=2)

            base_facts = {
                "game_id": game_id,
                "series_key": key,
                "series_id": s.get("series_id"),
                "round_label": round_label,
                "round": s.get("round"),
                "game_number": game_number,
                "home_team_id": home_id,
                "away_team_id": road_id,
                "home_score": hs,
                "away_score": as_,
                "winner": winner,
                "loser": loser,
                "margin": margin,
                "is_overtime": bool(g.get("is_overtime")),
                "series_score": s_score,
                "top_performers": top_perf,
            }

            # Game recap is always generated
            events.append(
                {
                    "event_id": make_event_id("PLAYOFF", game_id, "RECAP"),
                    "date": d_iso,
                    "type": "PLAYOFF_GAME_RECAP",
                    "importance": 0.0,
                    "facts": base_facts,
                    "related_team_ids": [home_id, road_id],
                    "related_player_ids": [],
                    "related_player_names": [],
                    "tags": ["playoffs"],
                }
            )

            # Series swing / match point / elimination
            need = best_of // 2 + 1
            if max(h_wins, r_wins) >= need:
                events.append(
                    {
                        "event_id": make_event_id("PLAYOFF", game_id, "ELIM"),
                        "date": d_iso,
                        "type": "PLAYOFF_ELIMINATION",
                        "importance": 0.0,
                        "facts": base_facts,
                        "related_team_ids": [home_id, road_id],
                        "related_player_ids": [],
                        "related_player_names": [],
                        "tags": ["playoffs"],
                    }
                )
            elif _is_match_point(best_of, h_wins, r_wins):
                events.append(
                    {
                        "event_id": make_event_id("PLAYOFF", game_id, "MP"),
                        "date": d_iso,
                        "type": "PLAYOFF_MATCH_POINT",
                        "importance": 0.0,
                        "facts": base_facts,
                        "related_team_ids": [home_id, road_id],
                        "related_player_ids": [],
                        "related_player_names": [],
                        "tags": ["playoffs"],
                    }
                )
            else:
                # swing when series becomes tied or lead changes
                if idx > 0:
                    prev_wins = wins_through(s, idx - 1)
                    prev_h = prev_wins.get(home_id, 0)
                    prev_r = prev_wins.get(road_id, 0)
                    prev_leader = home_id if prev_h > prev_r else road_id if prev_r > prev_h else None
                    cur_leader = home_id if h_wins > r_wins else road_id if r_wins > h_wins else None
                    if (prev_leader != cur_leader) or (cur_leader is None):
                        events.append(
                            {
                                "event_id": make_event_id("PLAYOFF", game_id, "SWING"),
                                "date": d_iso,
                                "type": "PLAYOFF_SERIES_SWING",
                                "importance": 0.0,
                                "facts": base_facts,
                                "related_team_ids": [home_id, road_id],
                                "related_player_ids": [],
                                "related_player_names": [],
                                "tags": ["playoffs"],
                            }
                        )

    return events


def _extract_top_performers_simple(game_result_v2: Dict[str, Any], winner_team_id: str, *, limit: int = 2) -> List[str]:
    """Pull a compact top-performer list from a v2 game_result.

    Output entries look like "Name 34득점".
    """
    try:
        teams = game_result_v2.get("teams") or {}
        team = teams.get(winner_team_id) or {}
        rows = team.get("players") or []
    except Exception:
        return []

    if not isinstance(rows, list):
        return []

    scored = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        name = str(r.get("Name") or "")
        try:
            pts = int(float(r.get("PTS") or 0))
        except Exception:
            pts = 0
        try:
            ast = int(float(r.get("AST") or 0))
        except Exception:
            ast = 0
        try:
            reb = int(float(r.get("REB") or 0))
        except Exception:
            reb = 0
        gs = pts + 0.7 * reb + 0.7 * ast
        if name:
            scored.append((gs, f"{name} {pts}득점"))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [s for _, s in scored[:limit]]
