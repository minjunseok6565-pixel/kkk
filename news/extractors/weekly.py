from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from ..ids import make_event_id
from ..models import NewsEvent


def week_start_monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def build_week_window(current: date) -> Tuple[date, date, str]:
    """Return (week_start, week_end, week_key_str)."""
    ws = week_start_monday(current)
    we = current
    return ws, we, ws.isoformat()


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


@dataclass(frozen=True)
class _Record:
    wins: int = 0
    losses: int = 0

    def games(self) -> int:
        return self.wins + self.losses

    def win_pct(self) -> float:
        g = self.games()
        return (self.wins / g) if g > 0 else 0.5


def _update_record(rec: _Record, did_win: bool) -> _Record:
    if did_win:
        return _Record(rec.wins + 1, rec.losses)
    return _Record(rec.wins, rec.losses + 1)


def _game_margin(g: Dict[str, Any]) -> Optional[int]:
    try:
        return abs(int(g.get("home_score")) - int(g.get("away_score")))
    except Exception:
        return None


def _winner_loser(g: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    home = g.get("home_team_id")
    away = g.get("away_team_id")
    try:
        hs = int(g.get("home_score"))
        as_ = int(g.get("away_score"))
    except Exception:
        return None, None
    if hs == as_:
        return None, None
    if hs > as_:
        return str(home), str(away)
    return str(away), str(home)


def _compute_game_score(row: Dict[str, Any]) -> float:
    # A lightweight "game score"-like metric for highlighting.
    def f(k: str) -> float:
        try:
            return float(row.get(k, 0) or 0)
        except Exception:
            return 0.0

    pts = f("PTS")
    reb = f("REB")
    ast = f("AST")
    stl = f("STL")
    blk = f("BLK")
    tov = f("TOV")
    fgm = f("FGM")
    fga = f("FGA")
    ftm = f("FTM")
    fta = f("FTA")

    # Similar spirit to NBA Game Score (not identical)
    return pts + 0.7 * reb + 0.7 * ast + 2.0 * stl + 2.0 * blk - 1.0 * tov - 0.7 * (fga - fgm) - 0.4 * (fta - ftm)


def _triple_double(row: Dict[str, Any]) -> bool:
    cats = 0
    for k in ("PTS", "REB", "AST", "STL", "BLK"):
        try:
            if float(row.get(k, 0) or 0) >= 10:
                cats += 1
        except Exception:
            continue
    return cats >= 3


def _get_box_players(snapshot: Dict[str, Any], game_id: str, team_id: str) -> List[Dict[str, Any]]:
    gr = (snapshot.get("game_results") or {}).get(game_id)
    if not isinstance(gr, dict):
        return []
    teams = gr.get("teams") or {}
    if not isinstance(teams, dict):
        return []
    t = teams.get(team_id)
    if not isinstance(t, dict):
        return []
    rows = t.get("players") or []
    return rows if isinstance(rows, list) else []


def _top_performers(snapshot: Dict[str, Any], game_id: str, team_id: str, *, limit: int = 2) -> List[Dict[str, Any]]:
    rows = _get_box_players(snapshot, game_id, team_id)
    scored = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        scored.append((_compute_game_score(r), r))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [r for _, r in scored[:limit]]


def extract_weekly_events(
    snapshot: Dict[str, Any],
    *,
    start_date: str,
    end_date: str,
) -> List[NewsEvent]:
    """Extract deterministic weekly news events from the workflow snapshot.

    - Uses only regular-season games.
    - Generates a reasonably rich event pool; an editorial layer can later select.
    """
    start = _parse_date_iso(start_date)
    end = _parse_date_iso(end_date)
    if not start or not end:
        return []

    games_all = [g for g in (snapshot.get("games") or []) if isinstance(g, dict) and str(g.get("phase")) == "regular"]

    # Sort for record simulation (date then ingest_turn)
    def _sort_key(g: Dict[str, Any]):
        d = _parse_date_iso(g.get("date"))
        try:
            t = int(g.get("ingest_turn") or 0)
        except Exception:
            t = 0
        return (d or date.min, t)

    games_all_sorted = sorted(games_all, key=_sort_key)

    # Simulate team records through the season to estimate pre-game win%.
    records: Dict[str, _Record] = {}
    pregame_win_pct: Dict[str, Tuple[float, float, int, int]] = {}
    # game_id -> (home_pct, away_pct, home_g, away_g)

    for g in games_all_sorted:
        d = _parse_date_iso(g.get("date"))
        if not d:
            continue
        if d > end:
            break

        gid = str(g.get("game_id") or "")
        home = str(g.get("home_team_id") or "")
        away = str(g.get("away_team_id") or "")
        if not gid or not home or not away:
            continue

        rh = records.get(home, _Record())
        ra = records.get(away, _Record())
        pregame_win_pct[gid] = (rh.win_pct(), ra.win_pct(), rh.games(), ra.games())

        winner, loser = _winner_loser(g)
        if winner and loser:
            records[winner] = _update_record(records.get(winner, _Record()), True)
            records[loser] = _update_record(records.get(loser, _Record()), False)

    # Filter games in window
    window_games = []
    for g in games_all_sorted:
        d = _parse_date_iso(g.get("date"))
        if not d:
            continue
        if start <= d <= end:
            window_games.append(g)

    events: List[NewsEvent] = []

    # Game-level events
    for g in window_games:
        gid = str(g.get("game_id") or "")
        d = _parse_date_iso(g.get("date"))
        if not gid or not d:
            continue
        home = str(g.get("home_team_id") or "")
        away = str(g.get("away_team_id") or "")
        winner, loser = _winner_loser(g)
        if not winner or not loser:
            continue

        margin = _game_margin(g)
        is_ot = bool(g.get("is_overtime"))

        # Pre-game win% gap based on simulated records
        h_pct, a_pct, h_g, a_g = pregame_win_pct.get(gid, (0.5, 0.5, 0, 0))
        fav = home if h_pct >= a_pct else away
        dog = away if fav == home else home
        fav_pct = max(h_pct, a_pct)
        dog_pct = min(h_pct, a_pct)
        gap = fav_pct - dog_pct

        # Upset (avoid very early season noise)
        if (h_g >= 5 and a_g >= 5) and winner == dog and gap >= 0.15:
            events.append(
                {
                    "event_id": make_event_id("WEEKLY", d.isoformat(), "UPSET", gid),
                    "date": d.isoformat(),
                    "type": "UPSET",
                    "importance": 0.0,
                    "facts": {
                        "game_id": gid,
                        "home_team_id": home,
                        "away_team_id": away,
                        "home_score": g.get("home_score"),
                        "away_score": g.get("away_score"),
                        "winner": winner,
                        "loser": loser,
                        "margin": margin,
                        "is_overtime": is_ot,
                        "favorite": fav,
                        "underdog": dog,
                        "pregame_win_pct_gap": gap,
                    },
                    "related_team_ids": [home, away],
                    "related_player_ids": [],
                    "related_player_names": [],
                    "tags": ["game"],
                }
            )

        # Clutch / OT
        if is_ot or (margin is not None and margin <= 3):
            events.append(
                {
                    "event_id": make_event_id("WEEKLY", d.isoformat(), "CLUTCH_OT", gid),
                    "date": d.isoformat(),
                    "type": "CLUTCH_OT",
                    "importance": 0.0,
                    "facts": {
                        "game_id": gid,
                        "home_team_id": home,
                        "away_team_id": away,
                        "home_score": g.get("home_score"),
                        "away_score": g.get("away_score"),
                        "winner": winner,
                        "loser": loser,
                        "margin": margin,
                        "is_overtime": is_ot,
                    },
                    "related_team_ids": [home, away],
                    "related_player_ids": [],
                    "related_player_names": [],
                    "tags": ["game"],
                }
            )

        # Blowout
        if margin is not None and margin >= 25:
            events.append(
                {
                    "event_id": make_event_id("WEEKLY", d.isoformat(), "BLOWOUT", gid),
                    "date": d.isoformat(),
                    "type": "BLOWOUT",
                    "importance": 0.0,
                    "facts": {
                        "game_id": gid,
                        "home_team_id": home,
                        "away_team_id": away,
                        "home_score": g.get("home_score"),
                        "away_score": g.get("away_score"),
                        "winner": winner,
                        "loser": loser,
                        "margin": margin,
                        "is_overtime": is_ot,
                    },
                    "related_team_ids": [home, away],
                    "related_player_ids": [],
                    "related_player_names": [],
                    "tags": ["game"],
                }
            )

        # Player highlight candidates (from game result box)
        for tid in (home, away):
            for row in _top_performers(snapshot, gid, tid, limit=3):
                if not isinstance(row, dict):
                    continue
                pname = row.get("Name") or ""
                pid = str(row.get("PlayerID") or "")
                pts = row.get("PTS")
                ast = row.get("AST")
                reb = row.get("REB")
                stl = row.get("STL")
                blk = row.get("BLK")
                th = row.get("3PM")

                # Specialized achievements
                try:
                    pts_i = int(float(pts or 0))
                except Exception:
                    pts_i = 0
                try:
                    ast_i = int(float(ast or 0))
                except Exception:
                    ast_i = 0
                try:
                    reb_i = int(float(reb or 0))
                except Exception:
                    reb_i = 0
                try:
                    stl_i = int(float(stl or 0))
                except Exception:
                    stl_i = 0
                try:
                    blk_i = int(float(blk or 0))
                except Exception:
                    blk_i = 0
                try:
                    th_i = int(float(th or 0))
                except Exception:
                    th_i = 0

                # Build a single "masterclass" baseline (useful even when no threshold)
                gs = _compute_game_score(row)
                if gs >= 38.0:
                    events.append(
                        {
                            "event_id": make_event_id("WEEKLY", d.isoformat(), "PLAYER_MASTERCLASS", gid, pid),
                            "date": d.isoformat(),
                            "type": "PLAYER_MASTERCLASS",
                            "importance": 0.0,
                            "facts": {
                                "game_id": gid,
                                "team_id": tid,
                                "player_id": pid,
                                "player_name": pname,
                                "pts": pts_i,
                                "reb": reb_i,
                                "ast": ast_i,
                                "stl": stl_i,
                                "blk": blk_i,
                                "3pm": th_i,
                                "game_score": gs,
                            },
                            "related_team_ids": [tid],
                            "related_player_ids": [pid] if pid else [],
                            "related_player_names": [str(pname)] if pname else [],
                            "tags": ["player"],
                        }
                    )

                if pts_i >= 40:
                    events.append(
                        {
                            "event_id": make_event_id("WEEKLY", d.isoformat(), "PLAYER_40PTS", gid, pid),
                            "date": d.isoformat(),
                            "type": "PLAYER_40PTS",
                            "importance": 0.0,
                            "facts": {"game_id": gid, "team_id": tid, "player_id": pid, "player_name": pname, "pts": pts_i, "reb": reb_i, "ast": ast_i, "stl": stl_i, "blk": blk_i, "3pm": th_i},
                            "related_team_ids": [tid],
                            "related_player_ids": [pid] if pid else [],
                            "related_player_names": [str(pname)] if pname else [],
                            "tags": ["player"],
                        }
                    )

                if _triple_double(row):
                    events.append(
                        {
                            "event_id": make_event_id("WEEKLY", d.isoformat(), "PLAYER_TRIPLE_DOUBLE", gid, pid),
                            "date": d.isoformat(),
                            "type": "PLAYER_TRIPLE_DOUBLE",
                            "importance": 0.0,
                            "facts": {"game_id": gid, "team_id": tid, "player_id": pid, "player_name": pname, "pts": pts_i, "reb": reb_i, "ast": ast_i, "stl": stl_i, "blk": blk_i, "3pm": th_i},
                            "related_team_ids": [tid],
                            "related_player_ids": [pid] if pid else [],
                            "related_player_names": [str(pname)] if pname else [],
                            "tags": ["player"],
                        }
                    )

                if ast_i >= 10:
                    events.append(
                        {
                            "event_id": make_event_id("WEEKLY", d.isoformat(), "PLAYER_10AST", gid, pid),
                            "date": d.isoformat(),
                            "type": "PLAYER_10AST",
                            "importance": 0.0,
                            "facts": {"game_id": gid, "team_id": tid, "player_id": pid, "player_name": pname, "pts": pts_i, "reb": reb_i, "ast": ast_i, "stl": stl_i, "blk": blk_i, "3pm": th_i},
                            "related_team_ids": [tid],
                            "related_player_ids": [pid] if pid else [],
                            "related_player_names": [str(pname)] if pname else [],
                            "tags": ["player"],
                        }
                    )

                if reb_i >= 20:
                    events.append(
                        {
                            "event_id": make_event_id("WEEKLY", d.isoformat(), "PLAYER_20REB", gid, pid),
                            "date": d.isoformat(),
                            "type": "PLAYER_20REB",
                            "importance": 0.0,
                            "facts": {"game_id": gid, "team_id": tid, "player_id": pid, "player_name": pname, "pts": pts_i, "reb": reb_i, "ast": ast_i, "stl": stl_i, "blk": blk_i, "3pm": th_i},
                            "related_team_ids": [tid],
                            "related_player_ids": [pid] if pid else [],
                            "related_player_names": [str(pname)] if pname else [],
                            "tags": ["player"],
                        }
                    )

                if stl_i >= 5:
                    events.append(
                        {
                            "event_id": make_event_id("WEEKLY", d.isoformat(), "PLAYER_5STL", gid, pid),
                            "date": d.isoformat(),
                            "type": "PLAYER_5STL",
                            "importance": 0.0,
                            "facts": {"game_id": gid, "team_id": tid, "player_id": pid, "player_name": pname, "pts": pts_i, "reb": reb_i, "ast": ast_i, "stl": stl_i, "blk": blk_i, "3pm": th_i},
                            "related_team_ids": [tid],
                            "related_player_ids": [pid] if pid else [],
                            "related_player_names": [str(pname)] if pname else [],
                            "tags": ["player"],
                        }
                    )

                if blk_i >= 5:
                    events.append(
                        {
                            "event_id": make_event_id("WEEKLY", d.isoformat(), "PLAYER_5BLK", gid, pid),
                            "date": d.isoformat(),
                            "type": "PLAYER_5BLK",
                            "importance": 0.0,
                            "facts": {"game_id": gid, "team_id": tid, "player_id": pid, "player_name": pname, "pts": pts_i, "reb": reb_i, "ast": ast_i, "stl": stl_i, "blk": blk_i, "3pm": th_i},
                            "related_team_ids": [tid],
                            "related_player_ids": [pid] if pid else [],
                            "related_player_names": [str(pname)] if pname else [],
                            "tags": ["player"],
                        }
                    )

                if th_i >= 7:
                    events.append(
                        {
                            "event_id": make_event_id("WEEKLY", d.isoformat(), "PLAYER_7_3PM", gid, pid),
                            "date": d.isoformat(),
                            "type": "PLAYER_7_3PM",
                            "importance": 0.0,
                            "facts": {"game_id": gid, "team_id": tid, "player_id": pid, "player_name": pname, "pts": pts_i, "reb": reb_i, "ast": ast_i, "stl": stl_i, "blk": blk_i, "3pm": th_i},
                            "related_team_ids": [tid],
                            "related_player_ids": [pid] if pid else [],
                            "related_player_names": [str(pname)] if pname else [],
                            "tags": ["player"],
                        }
                    )

    # Team streaks through end date
    streaks = _compute_current_streaks(games_all_sorted, end)
    # Take top 4 by absolute length
    top_streaks = sorted(streaks.items(), key=lambda kv: abs(kv[1]), reverse=True)[:4]
    for tid, st in top_streaks:
        if abs(st) < 5:
            continue
        events.append(
            {
                "event_id": make_event_id("WEEKLY", end.isoformat(), "STREAK_TEAM", tid, st),
                "date": end.isoformat(),
                "type": "STREAK_TEAM",
                "importance": 0.0,
                "facts": {"team_id": tid, "streak_len": st},
                "related_team_ids": [tid],
                "related_player_ids": [],
                "related_player_names": [],
                "tags": ["team"],
            }
        )

    return events


def _compute_current_streaks(games_sorted: List[Dict[str, Any]], end: date) -> Dict[str, int]:
    """Compute current W/L streak for each team as of end date.

    Positive => winning streak, negative => losing streak.
    """
    last_results: Dict[str, List[bool]] = {}
    for g in games_sorted:
        d = _parse_date_iso(g.get("date"))
        if not d or d > end:
            continue
        winner, loser = _winner_loser(g)
        if not winner or not loser:
            continue
        last_results.setdefault(winner, []).append(True)
        last_results.setdefault(loser, []).append(False)

    streaks: Dict[str, int] = {}
    for tid, results in last_results.items():
        # walk backwards until streak breaks
        st = 0
        for r in reversed(results):
            if st == 0:
                st = 1 if r else -1
            else:
                if (st > 0 and r) or (st < 0 and not r):
                    st += 1 if st > 0 else -1
                else:
                    break
        streaks[tid] = st
    return streaks
