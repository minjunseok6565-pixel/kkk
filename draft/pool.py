from __future__ import annotations

"""Draft prospect pool (DB-backed).

Source of truth:
  - college_draft_entries (declared prospects)
  - draft_watch_runs / draft_watch_probs (pre-declaration watch snapshots)
  - college_players (bio/ratings)
  - college_player_season_stats (season performance)
  - college_teams (display metadata)
"""

import json
import math
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Set, Tuple

from ratings_2k import validate_attrs, potential_grade_to_scalar


@dataclass(frozen=True, slots=True)
class Prospect:
    temp_id: str
    name: str
    pos: str
    age: int
    height_in: int
    weight_lb: int
    ovr: int
    attrs: Dict[str, Any] = field(default_factory=dict)
    meta: Dict[str, Any] = field(default_factory=dict)
    # Internal-only: raw workout payloads keyed by team_id.
    # IMPORTANT: do NOT expose this via to_dict(); use to_public_dict(viewer_team_id=...) instead.
    workouts_by_team: Dict[str, Any] = field(default_factory=dict)
    # Internal-only: raw interview payloads keyed by team_id.
    # IMPORTANT: do NOT expose this via to_dict(); use to_public_dict(viewer_team_id=...) instead.
    interviews_by_team: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "temp_id": str(self.temp_id),
            "name": str(self.name),
            "pos": str(self.pos),
            "age": int(self.age),
            "height_in": int(self.height_in),
            "weight_lb": int(self.weight_lb),
            "ovr": int(self.ovr),
            "attrs": dict(self.attrs) if isinstance(self.attrs, dict) else {},
            "meta": dict(self.meta) if isinstance(self.meta, dict) else {},
        }

    def to_public_dict(self, *, viewer_team_id: Optional[str] = None) -> Dict[str, Any]:
        """Public (user-facing) JSON shape.

        Fog-of-war rule: NEVER expose attrs/ovr/potential_* or full decision_trace.
        Combine is public; workouts/interviews are team-private and only the viewer team's result is exposed.
        """
        college = self.meta.get("college")
        if not isinstance(college, dict):
            college = {}

        season_stats = self.meta.get("season_stats")
        if season_stats is not None and not isinstance(season_stats, dict):
            season_stats = None

        projected_pick: Optional[int] = None
        try:
            v = self.meta.get("consensus_projected_pick")
            projected_pick = int(v) if v is not None else None
        except Exception:
            projected_pick = None

        combine = self.meta.get("combine")
        if isinstance(combine, dict):
            combine_out: Optional[Dict[str, Any]] = _deep_drop_keys(combine, _SENSITIVE_KEYS_EVENT_PAYLOAD)
        else:
            combine_out = None

        workout_out: Optional[Dict[str, Any]] = None
        vt = _norm_team_id_loose(viewer_team_id)
        if vt:
            payload = self.workouts_by_team.get(vt)
            if isinstance(payload, dict):
                payload = _deep_drop_keys(payload, _SENSITIVE_KEYS_EVENT_PAYLOAD)
                workout_out = {"team_id": vt, "result": payload}

        interview_out: Optional[Dict[str, Any]] = None
        if vt:
            payload = self.interviews_by_team.get(vt)
            if isinstance(payload, dict):
                payload = _deep_drop_keys(payload, _SENSITIVE_KEYS_EVENT_PAYLOAD)
                interview_out = {"team_id": vt, "result": payload}

        return {
            "temp_id": str(self.temp_id),
            "name": str(self.name),
            "pos": str(self.pos),
            "age": int(self.age),
            "height_in": int(self.height_in),
            "weight_lb": int(self.weight_lb),
            "college": {
                "college_team_id": str(college.get("college_team_id") or ""),
                "college_team_name": str(college.get("college_team_name") or ""),
                "conference": str(college.get("conference") or ""),
                "class_year": _safe_int(college.get("class_year"), default=0),
                "entry_season_year": _safe_int(college.get("entry_season_year"), default=0),
                "status": str(college.get("status") or ""),
                "declared_at": str(college.get("declared_at") or ""),
            },
            "season_stats": season_stats,
            "consensus": {"projected_pick": projected_pick},
            "combine": combine_out,
            "workout": workout_out,
            "interview": interview_out,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "Prospect":
        a = dict(d.get("attrs") or {}) if isinstance(d, Mapping) else {}
        m = dict(d.get("meta") or {}) if isinstance(d, Mapping) else {}
        return cls(
            temp_id=str(d.get("temp_id") or ""),
            name=str(d.get("name") or "Unknown"),
            pos=str(d.get("pos") or "G"),
            age=int(d.get("age") or 19),
            height_in=int(d.get("height_in") or 78),
            weight_lb=int(d.get("weight_lb") or 210),
            ovr=int(d.get("ovr") or 60),
            attrs=a if isinstance(a, dict) else {},
            meta=m if isinstance(m, dict) else {},
        )


@dataclass(slots=True)
class DraftPool:
    """A mutable container for prospects during a draft."""

    draft_year: int
    prospects_by_temp_id: Dict[str, Prospect]
    available_temp_ids: Set[str] = field(default_factory=set)
    ranked_temp_ids: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.draft_year = int(self.draft_year)
        if not isinstance(self.prospects_by_temp_id, dict):
            self.prospects_by_temp_id = {}
        if not self.available_temp_ids:
            self.available_temp_ids = set(self.prospects_by_temp_id.keys())
        if not self.ranked_temp_ids:
            # Preserve insertion order as default ranking.
            self.ranked_temp_ids = list(self.prospects_by_temp_id.keys())

    def list_available(self) -> List[Prospect]:
        # Prefer ranked order (stable, user-friendly) while filtering for availability.
        out: List[Prospect] = []
        if self.ranked_temp_ids:
            for tid in self.ranked_temp_ids:
                if tid in self.available_temp_ids and tid in self.prospects_by_temp_id:
                    out.append(self.prospects_by_temp_id[tid])
            if out:
                return out
        # Fallback: deterministic ordering.
        ids = sorted(self.available_temp_ids)
        return [self.prospects_by_temp_id[i] for i in ids if i in self.prospects_by_temp_id]

    def get(self, temp_id: str) -> Prospect:
        tid = str(temp_id)
        if tid not in self.prospects_by_temp_id:
            raise KeyError(f"prospect not found: temp_id={temp_id}")
        return self.prospects_by_temp_id[tid]

    def is_available(self, temp_id: str) -> bool:
        return str(temp_id) in self.available_temp_ids

    def mark_picked(self, temp_id: str) -> None:
        tid = str(temp_id)
        if tid not in self.available_temp_ids:
            raise ValueError(f"prospect already picked or unavailable: temp_id={temp_id}")
        self.available_temp_ids.remove(tid)

    def unmark_picked(self, temp_id: str) -> None:
        tid = str(temp_id)
        if tid in self.prospects_by_temp_id:
            self.available_temp_ids.add(tid)

    def to_dict(self) -> Dict[str, Any]:
        # Serialize prospects in ranked order if possible (for stable UI + replay).
        if self.ranked_temp_ids:
            prospects_list = [
                self.prospects_by_temp_id[tid].to_dict()
                for tid in self.ranked_temp_ids
                if tid in self.prospects_by_temp_id
            ]
        else:
            prospects_list = [p.to_dict() for p in self.prospects_by_temp_id.values()]
        return {
            "draft_year": int(self.draft_year),
            "prospects": prospects_list,
            "available_temp_ids": sorted(self.available_temp_ids),
            "ranked_temp_ids": list(self.ranked_temp_ids),
        }

    def to_public_dict(self, *, viewer_team_id: Optional[str] = None) -> Dict[str, Any]:
        """Public (user-facing) JSON shape.

        - prospects are ordered by a public-only ranking (no hidden attrs/ovr/potential signals)
        - ranked_temp_ids are also public-only ordering
        """
        tids = list(self.prospects_by_temp_id.keys())
        tids.sort(key=lambda tid: _public_sort_key(self.prospects_by_temp_id.get(tid)))
        prospects_list = [
            self.prospects_by_temp_id[tid].to_public_dict(viewer_team_id=viewer_team_id)
            for tid in tids
            if tid in self.prospects_by_temp_id
        ]
        return {
            "draft_year": int(self.draft_year),
            "prospects": prospects_list,
            "available_temp_ids": sorted(self.available_temp_ids),
            # IMPORTANT: this is NOT the internal big-board used by AI.
            "ranked_temp_ids": list(tids),
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "DraftPool":
        dy = int(d.get("draft_year") or 0)
        prospects = {}
        for row in (d.get("prospects") or []):
            if not isinstance(row, Mapping):
                continue
            p = Prospect.from_dict(row)
            if p.temp_id:
                prospects[p.temp_id] = p
        avail = set(d.get("available_temp_ids") or prospects.keys())
        ranked = list(d.get("ranked_temp_ids") or [])
        if not ranked:
            ranked = list(prospects.keys())
        return cls(draft_year=dy, prospects_by_temp_id=prospects, available_temp_ids=avail, ranked_temp_ids=ranked)


def _json_loads(value: Any, default: Any) -> Any:
    """Best-effort json.loads helper (accepts str/bytes/dict/list)."""
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        s = value.decode("utf-8") if isinstance(value, (bytes, bytearray)) else str(value)
        if not s:
            return default
        return json.loads(s)
    except Exception:
        return default



# -----------------------------------------------------------------------------
# Public redaction helpers
# -----------------------------------------------------------------------------

_SENSITIVE_KEYS_EVENT_PAYLOAD: Set[str] = {
    # Fog-of-war (never expose these to user)
    "ovr",
    "attrs",
    "Potential",
    "potential",
    "potential_points",
    "potential_grade",
    # Mental / injury (never expose raw values if they accidentally appear in payloads)
    "M_WorkEthic",
    "M_Coachability",
    "M_Ambition",
    "M_Loyalty",
    "M_Ego",
    "M_Adaptability",
    "I_InjuryFreq",
}


def _deep_drop_keys(obj: Any, keys: Set[str]) -> Any:
    """Recursively remove sensitive keys from nested dict/list structures."""
    if isinstance(obj, dict):
        out: Dict[str, Any] = {}
        for k, v in obj.items():
            if str(k) in keys:
                continue
            out[str(k)] = _deep_drop_keys(v, keys)
        return out
    if isinstance(obj, list):
        return [_deep_drop_keys(v, keys) for v in obj]
    return obj


def _norm_team_id_loose(team_id: Any) -> str:
    """Best-effort normalization (avoid importing draft.types to keep this module light)."""
    return str(team_id or "").strip().upper()


def _safe_int(x: Any, *, default: int = 0) -> int:
    try:
        if x is None:
            return int(default)
        return int(x)
    except Exception:
        return int(default)


def _f(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return float(default)
        return float(x)
    except Exception:
        return float(default)


def _median_int_half_up(values: List[int]) -> int:
    """Deterministic median for even-length lists (round half up)."""
    vs = [int(v) for v in values if v is not None]
    if not vs:
        return 9999
    vs.sort()
    n = len(vs)
    mid = n // 2
    if n % 2 == 1:
        return int(vs[mid])
    a = int(vs[mid - 1])
    b = int(vs[mid])
    return int(math.floor((a + b) / 2.0 + 0.5))


def _public_prod_score(season_stats: Any) -> float:
    """Public-only productivity proxy (college per-game stats).

    Intentionally coarse; avoids using hidden ratings.
    """
    if not isinstance(season_stats, dict):
        return 0.0
    pts = _f(season_stats.get("pts"), 0.0)
    ast = _f(season_stats.get("ast"), 0.0)
    reb = _f(season_stats.get("reb"), 0.0)
    stl = _f(season_stats.get("stl"), 0.0)
    blk = _f(season_stats.get("blk"), 0.0)
    ts = _f(season_stats.get("ts_pct"), 0.0)
    base = pts * 1.0 + ast * 0.7 + reb * 0.5 + stl * 0.6 + blk * 0.6
    if ts > 0.0:
        base *= (0.75 + ts)
    return float(base)


def _public_projected_pick(meta: Any) -> int:
    if not isinstance(meta, dict):
        return 9999
    try:
        v = meta.get("consensus_projected_pick")
        return int(v) if v is not None else 9999
    except Exception:
        return 9999


def _public_sort_key(p: Any) -> Tuple[int, float, int, str]:
    if not isinstance(p, Prospect):
        return (9999, 0.0, 9999, "")
    proj_i = _public_projected_pick(p.meta)
    prod = _public_prod_score(p.meta.get("season_stats"))
    age = _safe_int(getattr(p, "age", None), default=9999)
    return (int(proj_i), -float(prod), int(age), str(getattr(p, "temp_id", "")))


def _inject_consensus_projected_pick_by_experts(
    *,
    prospects_by_temp_id: Dict[str, Prospect],
    ranked_temp_ids: List[str],
    draft_year: int,
) -> None:
    """Populate meta.consensus_projected_pick using median expert ranks.

    - Uses already-loaded Prospect objects (no DB reload).
    - Silent no-op on any error (consensus is optional display data).
    """
    try:
        from draft.expert_bigboard import (
            DEFAULT_EXPERT_IDS,
            PHASE_AUTO,
            compute_expert_ranks_from_prospects,
        )
    except Exception:
        return

    ordered: List[Prospect] = []
    if ranked_temp_ids:
        for tid in ranked_temp_ids:
            p = prospects_by_temp_id.get(str(tid))
            if isinstance(p, Prospect):
                ordered.append(p)
    else:
        for tid in sorted(prospects_by_temp_id.keys()):
            p = prospects_by_temp_id.get(str(tid))
            if isinstance(p, Prospect):
                ordered.append(p)

    if not ordered:
        return

    rank_maps: List[Dict[str, int]] = []
    for eid in DEFAULT_EXPERT_IDS:
        try:
            r = compute_expert_ranks_from_prospects(
                prospects=ordered,
                draft_year=int(draft_year),
                expert_id=str(eid),
                phase=PHASE_AUTO,
                limit=None,
            )
            rm = r.get("ranks")
            if isinstance(rm, dict) and rm:
                rank_maps.append({str(k): int(v) for k, v in rm.items() if v is not None})
        except Exception:
            continue

    if not rank_maps:
        return

    for tid, p in list(prospects_by_temp_id.items()):
        vals: List[int] = []
        for rm in rank_maps:
            v = rm.get(str(tid))
            vals.append(int(v) if v is not None else 9999)
        consensus = _median_int_half_up(vals)
        meta2 = dict(p.meta) if isinstance(p.meta, dict) else {}
        meta2["consensus_projected_pick"] = int(consensus)
        prospects_by_temp_id[str(tid)] = Prospect(
            temp_id=p.temp_id,
            name=p.name,
            pos=p.pos,
            age=p.age,
            height_in=p.height_in,
            weight_lb=p.weight_lb,
            ovr=p.ovr,
            attrs=p.attrs,
            meta=meta2,
            workouts_by_team=p.workouts_by_team,
            interviews_by_team=p.interviews_by_team,
        )


def load_pool_from_db(
    *,
    db_path: str,
    draft_year: int,
    season_year: Optional[int] = None,
    limit: Optional[int] = None,
) -> DraftPool:
    """Load declared prospects from DB and build a DraftPool.

    - Uses temp_id == player_id (college player ids are the stable identifier).
    - Pulls season stats from (season_year) which defaults to (draft_year - 1).
    """
    dy = int(draft_year)
    if dy <= 0:
        raise ValueError(f"invalid draft_year: {draft_year}")
    sy = int(season_year) if season_year is not None else (dy - 1)
    if sy <= 0:
        raise ValueError(f"invalid season_year: {season_year}")
    lim = int(limit) if limit is not None else None
    if lim is not None and lim <= 0:
        raise ValueError(f"invalid limit: {limit}")

    # Local import to avoid heavy deps / cycles at import time.
    from league_repo import LeagueRepo

    sql = """
SELECT
  e.player_id              AS player_id,
  e.declared_at            AS declared_at,
  e.decision_json          AS decision_json,

  p.college_team_id        AS college_team_id,
  p.class_year             AS class_year,
  p.entry_season_year      AS entry_season_year,
  p.status                 AS status,

  p.name                   AS name,
  p.pos                    AS pos,
  p.age                    AS age,
  p.height_in              AS height_in,
  p.weight_lb              AS weight_lb,
  p.ovr                    AS ovr,
  p.attrs_json             AS attrs_json,

  ps.stats_json            AS stats_json,

  t.name                   AS college_team_name,
  t.conference             AS conference
FROM college_draft_entries e
JOIN college_players p
  ON p.player_id = e.player_id
LEFT JOIN college_player_season_stats ps
  ON ps.player_id = e.player_id
 AND ps.season_year = ?
LEFT JOIN college_teams t
  ON t.college_team_id = p.college_team_id
WHERE e.draft_year = ?;
""".strip()

    combine_map: Dict[str, Dict[str, Any]] = {}
    workout_rows_by_prospect: Dict[str, List[Dict[str, Any]]] = {}
    interview_rows_by_prospect: Dict[str, List[Dict[str, Any]]] = {}

    with LeagueRepo(str(db_path)) as repo:
        repo.init_db()
        rows = repo._conn.execute(sql, (sy, dy)).fetchall()

        # Optional: attach Combine / Workouts / Interviews meta when tables exist.
        try:
            crows = repo._conn.execute(
                "SELECT prospect_temp_id, result_json FROM draft_combine_results WHERE draft_year = ?;",
                (dy,),
            ).fetchall()
        except sqlite3.OperationalError:
            crows = []
        for cr in crows:
            pid = str(cr["prospect_temp_id"] or "").strip()
            if not pid:
                continue
            payload = _json_loads(cr["result_json"], default={})
            if isinstance(payload, dict):
                combine_map[pid] = payload

        try:
            wrows = repo._conn.execute(
                "SELECT team_id, prospect_temp_id, result_json FROM draft_workout_results WHERE draft_year = ?;",
                (dy,),
            ).fetchall()
        except sqlite3.OperationalError:
            wrows = []
        for wr in wrows:
            pid = str(wr["prospect_temp_id"] or "").strip()
            if not pid:
                continue
            team_id = str(wr["team_id"] or "").strip()
            if not team_id:
                continue
            payload = _json_loads(wr["result_json"], default={})
            if not isinstance(payload, dict):
                payload = {}
            workout_rows_by_prospect.setdefault(pid, []).append({"team_id": team_id, "payload": payload})

        try:
            irows = repo._conn.execute(
                "SELECT team_id, prospect_temp_id, result_json FROM draft_interview_results WHERE draft_year = ?;",
                (dy,),
            ).fetchall()
        except sqlite3.OperationalError:
            irows = []
        for ir in irows:
            pid = str(ir["prospect_temp_id"] or "").strip()
            if not pid:
                continue
            team_id = str(ir["team_id"] or "").strip()
            if not team_id:
                continue
            payload = _json_loads(ir["result_json"], default={})
            if not isinstance(payload, dict):
                payload = {}
            interview_rows_by_prospect.setdefault(pid, []).append({"team_id": team_id, "payload": payload})


    if not rows:
        raise ValueError(
            f"No declared prospects found for draft_year={dy}. "
            f"Run college.finalize_season_and_generate_entries(season_year={sy}, draft_year={dy}) first."
        )

    scored: List[tuple] = []
    for r in rows:
        player_id = str(r["player_id"] or "").strip()
        if not player_id:
            continue

        name = str(r["name"] or "Unknown")
        pos = str(r["pos"] or "G")
        age = int(r["age"] or 19)
        height_in = int(r["height_in"] or 78)
        weight_lb = int(r["weight_lb"] or 210)
        ovr = int(r["ovr"] or 60)

        ratings = _json_loads(r["attrs_json"], default={})
        if not isinstance(ratings, dict):
            ratings = {}
        # SSOT 강제: derived_formulas.COL 기반 2K 키가 누락되면 여기서 즉시 실패
        validate_attrs(ratings, strict=True)

        decision_trace = _json_loads(r["decision_json"], default={})
        if not isinstance(decision_trace, dict):
            decision_trace = {}

        season_stats = _json_loads(r["stats_json"], default=None)
        if season_stats is not None and not isinstance(season_stats, dict):
            season_stats = None

        # Potential (grade -> points) for sorting & UI
        pot_grade = str(ratings.get("Potential") or "C-")
        pot_scalar = float(potential_grade_to_scalar(pot_grade))
        pot_points = 60.0 + (pot_scalar - 0.40) * (37.0 / 0.60)
        if pot_points < 60.0:
            pot_points = 60.0
        if pot_points > 97.0:
            pot_points = 97.0
        potential_points = int(round(pot_points))

        college_team_id = str(r["college_team_id"] or "")
        college_team_name = str(r["college_team_name"] or "")
        conference = str(r["conference"] or "")

        try:
            class_year = int(r["class_year"] or 1)
        except Exception:
            class_year = 1
        try:
            entry_season_year = int(r["entry_season_year"] or 0)
        except Exception:
            entry_season_year = 0

        meta: Dict[str, Any] = {
            "potential_grade": pot_grade,
            "potential_points": int(potential_points),
            "college": {
                "college_team_id": college_team_id,
                "college_team_name": college_team_name,
                "conference": conference,
                "class_year": int(class_year),
                "entry_season_year": int(entry_season_year),
                "status": str(r["status"] or ""),
                "declared_at": str(r["declared_at"] or ""),
            },
            "decision_trace": dict(decision_trace),
            "season_stats": season_stats,
        }

        # Attach Combine / Workouts meta (if present).
        combine = combine_map.get(player_id)
        if combine and isinstance(combine, dict):
            # Defensive: old DB payloads may include sensitive keys (e.g., "ovr").
            meta["combine"] = _deep_drop_keys(combine, _SENSITIVE_KEYS_EVENT_PAYLOAD)

        workouts = workout_rows_by_prospect.get(player_id)
        workouts_by_team: Dict[str, Any] = {}
        if workouts:
            by_team_overall: Dict[str, float] = {}
            notes_sample: List[str] = []
            overall_sum = 0.0
            overall_cnt = 0
            best_overall: Optional[float] = None
            best_team_id: Optional[str] = None

            for wr in workouts:
                team_id = str(wr.get("team_id") or "").strip()
                payload = wr.get("payload") or {}
                if not team_id or not isinstance(payload, dict):
                    continue

                # Defensive: old DB payloads may include sensitive keys (e.g., "ovr").
                payload = _deep_drop_keys(payload, _SENSITIVE_KEYS_EVENT_PAYLOAD)
                workouts_by_team[team_id] = payload

                scores = payload.get("scores")
                if not isinstance(scores, dict):
                    scores = {}
                try:
                    ov = float(scores.get("overall"))
                except Exception:
                    continue

                by_team_overall[team_id] = float(ov)
                overall_sum += float(ov)
                overall_cnt += 1

                if best_overall is None or float(ov) > float(best_overall):
                    best_overall = float(ov)
                    best_team_id = team_id

                ns = payload.get("notes")
                if isinstance(ns, list):
                    for n in ns:
                        if isinstance(n, str) and n and n not in notes_sample:
                            notes_sample.append(n)
                            if len(notes_sample) >= 3:
                                break
                if len(notes_sample) >= 3:
                    # keep it compact
                    pass

            if overall_cnt > 0:
                meta["workouts"] = {
                    "count": int(overall_cnt),
                    "overall_avg": round(float(overall_sum / float(overall_cnt)), 2),
                    "overall_best": round(float(best_overall), 2) if best_overall is not None else None,
                    "best_team_id": best_team_id,
                    "by_team_overall": by_team_overall,
                    "notes_sample": notes_sample,
                }

        interviews = interview_rows_by_prospect.get(player_id)
        interviews_by_team: Dict[str, Any] = {}
        if interviews:
            for ir in interviews:
                team_id = str(ir.get("team_id") or "").strip()
                if not team_id:
                    continue
                payload = ir.get("payload")
                if not isinstance(payload, dict):
                    payload = {}
                payload = _deep_drop_keys(payload, _SENSITIVE_KEYS_EVENT_PAYLOAD)
                interviews_by_team[team_id] = payload


        p = Prospect(
            temp_id=player_id,
            name=name,
            pos=pos,
            age=age,
            height_in=height_in,
            weight_lb=weight_lb,
            ovr=ovr,
            attrs=ratings,   # <- NBA players.attrs_json과 동일한 2K SSOT dict
            meta=meta,       # <- 스카우팅/표시용
            workouts_by_team=workouts_by_team if workouts else {},
            interviews_by_team=interviews_by_team if interviews else {},
        )

        proj = decision_trace.get("projected_pick")
        try:
            proj_i = int(proj) if proj is not None else 9999
        except Exception:
            proj_i = 9999

        # Sort: projected_pick asc, ovr desc, potential_points desc, age asc, temp_id asc
        sort_key = (proj_i, -int(ovr), -int(potential_points), int(age), player_id)
        scored.append((sort_key, p))

    if not scored:
        raise ValueError(
            f"No usable declared prospects found for draft_year={dy}. "
            f"Run college.finalize_season_and_generate_entries(season_year={sy}, draft_year={dy}) first."
        )

    scored.sort(key=lambda x: x[0])
    if lim is not None:
        scored = scored[:lim]

    prospects_by_temp_id: Dict[str, Prospect] = {}
    ranked_temp_ids: List[str] = []
    for _, p in scored:
        if not p.temp_id or p.temp_id in prospects_by_temp_id:
            continue
        prospects_by_temp_id[p.temp_id] = p
        ranked_temp_ids.append(p.temp_id)

    if not prospects_by_temp_id:
        raise ValueError(
            f"No usable declared prospects found for draft_year={dy}. "
            f"Run college.finalize_season_and_generate_entries(season_year={sy}, draft_year={dy}) first."
        )

    _inject_consensus_projected_pick_by_experts(
        prospects_by_temp_id=prospects_by_temp_id,
        ranked_temp_ids=ranked_temp_ids,
        draft_year=dy,
    )

    return DraftPool(
        draft_year=dy,
        prospects_by_temp_id=prospects_by_temp_id,
        available_temp_ids=set(prospects_by_temp_id.keys()),
        ranked_temp_ids=ranked_temp_ids,
    )


def load_watch_pool_from_db(
    *,
    db_path: str,
    draft_year: int,
    run_id: Optional[str] = None,
    season_year: Optional[int] = None,
    min_prob: Optional[float] = None,
    limit: Optional[int] = None,
) -> DraftPool:
    """Load a pre-declaration "watch" snapshot from DB and build a DraftPool.

    - Sources: draft_watch_runs + draft_watch_probs (+ college_players, season stats, teams)
    - Uses temp_id == player_id (college player ids are the stable identifier).
    - season_year defaults to draft_watch_runs.season_year (or draft_year - 1).
    - Inclusion is filtered by declare_prob >= min_prob (default: run.min_inclusion_prob).
      IMPORTANT: declare_prob is NOT used for ranking (only inclusion).
    """
    dy = int(draft_year)
    if dy <= 0:
        raise ValueError(f"invalid draft_year: {draft_year}")
    lim = int(limit) if limit is not None else None
    if lim is not None and lim <= 0:
        raise ValueError(f"invalid limit: {limit}")

    # Local import to avoid heavy deps / cycles at import time.
    from league_repo import LeagueRepo

    combine_map: Dict[str, Dict[str, Any]] = {}
    workout_rows_by_prospect: Dict[str, List[Dict[str, Any]]] = {}
    interview_rows_by_prospect: Dict[str, List[Dict[str, Any]]] = {}

    with LeagueRepo(str(db_path)) as repo:
        repo.init_db()

        # Resolve run
        try:
            if run_id:
                run_row = repo._conn.execute(
                    """
                    SELECT run_id, period_key, as_of_date, season_year, min_inclusion_prob
                    FROM draft_watch_runs
                    WHERE run_id = ?
                    LIMIT 1;
                    """,
                    (str(run_id),),
                ).fetchone()
            else:
                run_row = repo._conn.execute(
                    """
                    SELECT run_id, period_key, as_of_date, season_year, min_inclusion_prob
                    FROM draft_watch_runs
                    WHERE draft_year = ?
                    ORDER BY period_key DESC, created_at DESC
                    LIMIT 1;
                    """,
                    (dy,),
                ).fetchone()
        except sqlite3.OperationalError as e:
            raise ValueError(
                "draft_watch tables not found. Ensure db_schema/draft.py has been applied/migrated."
            ) from e

        if not run_row:
            raise ValueError(
                f"No draft_watch_runs found for draft_year={dy}. "
                f"Run college.service.recompute_draft_watch_run(draft_year={dy}, ...) first."
            )

        run_id_used = str(run_row["run_id"] or "").strip()
        if not run_id_used:
            raise ValueError(f"Invalid draft_watch_runs row (empty run_id) for draft_year={dy}.")

        run_period_key = str(run_row["period_key"] or "").strip()
        run_as_of_date = str(run_row["as_of_date"] or "").strip()
        run_sy = int(run_row["season_year"] or (dy - 1))
        sy = int(season_year) if season_year is not None else int(run_sy)
        if sy <= 0:
            raise ValueError(f"invalid season_year: {season_year}")

        prob_floor = float(min_prob) if min_prob is not None else float(run_row["min_inclusion_prob"] or 0.35)
        if prob_floor < 0.0:
            prob_floor = 0.0
        if prob_floor > 1.0:
            prob_floor = 1.0

        # Optional: attach Combine / Workouts meta when tables exist.
        try:
            crows = repo._conn.execute(
                "SELECT prospect_temp_id, result_json FROM draft_combine_results WHERE draft_year = ?;",
                (dy,),
            ).fetchall()
        except sqlite3.OperationalError:
            crows = []
        for cr in crows:
            pid = str(cr["prospect_temp_id"] or "").strip()
            if not pid:
                continue
            payload = _json_loads(cr["result_json"], default={})
            if isinstance(payload, dict):
                combine_map[pid] = payload

        try:
            wrows = repo._conn.execute(
                "SELECT team_id, prospect_temp_id, result_json FROM draft_workout_results WHERE draft_year = ?;",
                (dy,),
            ).fetchall()
        except sqlite3.OperationalError:
            wrows = []
        for wr in wrows:
            pid = str(wr["prospect_temp_id"] or "").strip()
            if not pid:
                continue
            team_id = str(wr["team_id"] or "").strip()
            if not team_id:
                continue
            payload = _json_loads(wr["result_json"], default={})
            if not isinstance(payload, dict):
                payload = {}
            workout_rows_by_prospect.setdefault(pid, []).append({"team_id": team_id, "payload": payload})


        try:
            irows = repo._conn.execute(
                "SELECT team_id, prospect_temp_id, result_json FROM draft_interview_results WHERE draft_year = ?;",
                (dy,),
            ).fetchall()
        except sqlite3.OperationalError:
            irows = []
        for ir in irows:
            pid = str(ir["prospect_temp_id"] or "").strip()
            if not pid:
                continue
            team_id = str(ir["team_id"] or "").strip()
            if not team_id:
                continue
            payload = _json_loads(ir["result_json"], default={})
            if not isinstance(payload, dict):
                payload = {}
            interview_rows_by_prospect.setdefault(pid, []).append({"team_id": team_id, "payload": payload})

        sql = """
SELECT
  wp.player_id             AS player_id,
  wp.declare_prob          AS declare_prob,
  wp.projected_pick        AS projected_pick,
  wp.decision_json         AS decision_json,

  p.college_team_id        AS college_team_id,
  p.class_year             AS class_year,
  p.entry_season_year      AS entry_season_year,
  p.status                 AS status,

  p.name                   AS name,
  p.pos                    AS pos,
  p.age                    AS age,
  p.height_in              AS height_in,
  p.weight_lb              AS weight_lb,
  p.ovr                    AS ovr,
  p.attrs_json             AS attrs_json,

  ps.stats_json            AS stats_json,

  t.name                   AS college_team_name,
  t.conference             AS conference
FROM draft_watch_probs wp
JOIN college_players p
  ON p.player_id = wp.player_id
LEFT JOIN college_player_season_stats ps
  ON ps.player_id = wp.player_id
 AND ps.season_year = ?
LEFT JOIN college_teams t
  ON t.college_team_id = p.college_team_id
WHERE wp.run_id = ?
  AND wp.declare_prob >= ?;
"""
        rows = repo._conn.execute(sql, (sy, run_id_used, prob_floor)).fetchall()

    if not rows:
        raise ValueError(
            f"No watch prospects found for draft_year={dy} (run_id={run_id_used}, min_prob={prob_floor}). "
            f"Try lowering min_prob or recomputing the watch run."
        )

    scored: List[tuple] = []
    for r in rows:
        player_id = str(r["player_id"] or "").strip()
        if not player_id:
            continue

        name = str(r["name"] or "Unknown")
        pos = str(r["pos"] or "G")
        age = int(r["age"] or 19)
        height_in = int(r["height_in"] or 78)
        weight_lb = int(r["weight_lb"] or 210)
        ovr = int(r["ovr"] or 60)

        ratings = _json_loads(r["attrs_json"], default={})
        if not isinstance(ratings, dict):
            ratings = {}
        # SSOT 강제: derived_formulas.COL 기반 2K 키가 누락되면 여기서 즉시 실패
        validate_attrs(ratings, strict=True)

        decision_trace = _json_loads(r["decision_json"], default={})
        if not isinstance(decision_trace, dict):
            decision_trace = {}

        # Ensure projected_pick is present in decision_trace if available in the watch row
        try:
            wpick = r["projected_pick"]
            if wpick is not None and "projected_pick" not in decision_trace:
                decision_trace["projected_pick"] = int(wpick)
        except Exception:
            pass

        season_stats = _json_loads(r["stats_json"], default=None)
        if season_stats is not None and not isinstance(season_stats, dict):
            season_stats = None

        # Potential (grade -> points) for sorting & UI
        pot_grade = str(ratings.get("Potential") or "C-")
        pot_scalar = float(potential_grade_to_scalar(pot_grade))
        pot_points = 60.0 + (pot_scalar - 0.40) * (37.0 / 0.60)
        if pot_points < 60.0:
            pot_points = 60.0
        if pot_points > 97.0:
            pot_points = 97.0
        potential_points = int(round(pot_points))

        college_team_id = str(r["college_team_id"] or "")
        college_team_name = str(r["college_team_name"] or "")
        conference = str(r["conference"] or "")

        try:
            class_year = int(r["class_year"] or 1)
        except Exception:
            class_year = 1
        try:
            entry_season_year = int(r["entry_season_year"] or 0)
        except Exception:
            entry_season_year = 0

        meta: Dict[str, Any] = {
            "potential_grade": pot_grade,
            "potential_points": int(potential_points),
            "college": {
                "college_team_id": college_team_id,
                "college_team_name": college_team_name,
                "conference": conference,
                "class_year": int(class_year),
                "entry_season_year": int(entry_season_year),
                "status": str(r["status"] or ""),
                # Watch pool is pre-declaration; leave declared_at empty.
                "declared_at": "",
            },
            "decision_trace": dict(decision_trace),
            "season_stats": season_stats,
            "watch": {
                "run_id": run_id_used,
                "period_key": run_period_key,
                "as_of_date": run_as_of_date,
                "min_inclusion_prob": float(prob_floor),
                "declare_prob": float(r["declare_prob"] or 0.0),
            },
        }

        # Attach Combine / Workouts meta (if present).
        combine = combine_map.get(player_id)
        if combine and isinstance(combine, dict):
            meta["combine"] = _deep_drop_keys(combine, _SENSITIVE_KEYS_EVENT_PAYLOAD)

        workouts = workout_rows_by_prospect.get(player_id)
        workouts_by_team: Dict[str, Any] = {}
        if workouts:
            workouts_by_team = {}
            by_team_overall: Dict[str, float] = {}
            notes_sample: List[str] = []
            overall_sum = 0.0
            overall_cnt = 0
            best_overall: Optional[float] = None
            best_team_id: Optional[str] = None

            for wr in workouts:
                team_id = str(wr.get("team_id") or "").strip()
                payload = wr.get("payload") or {}
                if not team_id or not isinstance(payload, dict):
                    continue

                payload = _deep_drop_keys(payload, _SENSITIVE_KEYS_EVENT_PAYLOAD)
                workouts_by_team[team_id] = payload

                scores = payload.get("scores")
                if not isinstance(scores, dict):
                    scores = {}
                try:
                    ov = float(scores.get("overall"))
                except Exception:
                    continue

                by_team_overall[team_id] = float(ov)
                overall_sum += float(ov)
                overall_cnt += 1

                if best_overall is None or float(ov) > float(best_overall):
                    best_overall = float(ov)
                    best_team_id = team_id

                ns = payload.get("notes")
                if isinstance(ns, list):
                    for n in ns:
                        if isinstance(n, str) and n and n not in notes_sample:
                            notes_sample.append(n)
                            if len(notes_sample) >= 3:
                                break

            if overall_cnt > 0:
                meta["workouts"] = {
                    "count": int(overall_cnt),
                    "overall_avg": round(float(overall_sum / float(overall_cnt)), 2),
                    "overall_best": round(float(best_overall), 2) if best_overall is not None else None,
                    "best_team_id": best_team_id,
                    "by_team_overall": by_team_overall,
                    "notes_sample": notes_sample,
                }


        interviews = interview_rows_by_prospect.get(player_id)
        interviews_by_team: Dict[str, Any] = {}
        if interviews:
            for ir in interviews:
                team_id = str(ir.get("team_id") or "").strip()
                if not team_id:
                    continue
                payload = ir.get("payload")
                if not isinstance(payload, dict):
                    payload = {}
                payload = _deep_drop_keys(payload, _SENSITIVE_KEYS_EVENT_PAYLOAD)
                interviews_by_team[team_id] = payload

        p = Prospect(
            temp_id=player_id,
            name=name,
            pos=pos,
            age=age,
            height_in=height_in,
            weight_lb=weight_lb,
            ovr=ovr,
            attrs=ratings,   # <- NBA players.attrs_json과 동일한 2K SSOT dict
            meta=meta,       # <- 스카우팅/표시용
            workouts_by_team=workouts_by_team if workouts else {},
            interviews_by_team=interviews_by_team if interviews else {},
        )

        proj = decision_trace.get("projected_pick")
        try:
            proj_i = int(proj) if proj is not None else 9999
        except Exception:
            proj_i = 9999

        # Sort (declare_prob is NOT used): projected_pick asc, ovr desc, potential_points desc, age asc, temp_id asc
        sort_key = (proj_i, -int(ovr), -int(potential_points), int(age), player_id)
        scored.append((sort_key, p))

    if not scored:
        raise ValueError(
            f"No usable watch prospects found for draft_year={dy} (run_id={run_id_used}). "
            f"Try lowering min_prob or recomputing the watch run."
        )

    scored.sort(key=lambda x: x[0])
    if lim is not None:
        scored = scored[:lim]

    prospects_by_temp_id: Dict[str, Prospect] = {}
    ranked_temp_ids: List[str] = []
    for _, p in scored:
        if not p.temp_id or p.temp_id in prospects_by_temp_id:
            continue
        prospects_by_temp_id[p.temp_id] = p
        ranked_temp_ids.append(p.temp_id)

    if not prospects_by_temp_id:
        raise ValueError(
            f"No usable watch prospects found for draft_year={dy} (run_id={run_id_used}). "
            f"Try lowering min_prob or recomputing the watch run."
        )

    _inject_consensus_projected_pick_by_experts(
        prospects_by_temp_id=prospects_by_temp_id,
        ranked_temp_ids=ranked_temp_ids,
        draft_year=dy,
    )

    return DraftPool(
        draft_year=dy,
        prospects_by_temp_id=prospects_by_temp_id,
        available_temp_ids=set(prospects_by_temp_id.keys()),
        ranked_temp_ids=ranked_temp_ids,
    )
