from __future__ import annotations

"""draft.interviews

Draft Team Interviews (DB-backed, user-selected questions).

목적(게임 체감):
- 워크아웃 이후 "인터뷰"를 팀 단위 프라이빗 이벤트로 추가하여,
  멘탈/성향 정보를 정성적으로(등급/답변) 파악하게 한다.

SSOT:
- draft_interview_results (draft_year, team_id, prospect_temp_id) -> result_json

Idempotency:
- 이미 결과가 존재하면 같은 key에 대해 다시 생성하지 않는다(ON CONFLICT DO NOTHING).
  (버튼을 여러 번 눌러도 결과가 바뀌지 않게)

Integration notes:
- prospect_temp_id == college player_id (draft.pool.load_pool_from_db와 동일)
- 이 모듈은 '선언자(= college_draft_entries)'가 존재해야 동작한다.
- 질문/점수/등급/대답 로직은 이 파일의 아래 "질문 레지스트리 / generate_interview_payload"
  를 교체/확장하여 주입한다.
"""

import json
import random
import sqlite3
import zlib
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import game_time

from .types import norm_team_id


# ----------------------------
# JSON helpers
# ----------------------------


def _json_loads(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str)


def _stable_u32(*parts: Any) -> int:
    """Stable 32-bit hash for deterministic RNG seeding (do not use built-in hash())."""
    s = "|".join(str(p) for p in parts)
    return int(zlib.crc32(s.encode("utf-8")) & 0xFFFFFFFF)


# ----------------------------
# Prospect loading (declared entries)
# ----------------------------


def _load_declared_prospects(db_path: str, draft_year: int) -> List[Dict[str, Any]]:
    """Load declared prospects for a given draft year.

    We intentionally mirror draft.events._load_declared_prospects logic to keep this module independent.
    """
    from league_repo import LeagueRepo

    dy = int(draft_year)
    sql = """
SELECT
  e.player_id       AS prospect_temp_id,
  e.declared_at     AS declared_at,
  e.decision_json   AS decision_json,
  p.name            AS name,
  p.pos             AS pos,
  p.age             AS age,
  p.height_in       AS height_in,
  p.weight_lb       AS weight_lb,
  p.ovr             AS ovr,
  p.attrs_json      AS attrs_json
FROM college_draft_entries e
JOIN college_players p
  ON p.player_id = e.player_id
WHERE e.draft_year = ?
ORDER BY p.ovr DESC, p.age ASC, e.player_id ASC;
""".strip()

    with LeagueRepo(str(db_path)) as repo:
        repo.init_db()
        rows = repo._conn.execute(sql, (dy,)).fetchall()

    out: List[Dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        d["prospect_temp_id"] = str(d.get("prospect_temp_id") or "").strip()
        if not d["prospect_temp_id"]:
            continue
        out.append(d)

    if not out:
        raise ValueError(
            f"No declared prospects found for draft_year={dy}. "
            f"Run college.finalize_season_and_generate_entries(season_year={dy-1}, draft_year={dy}) first."
        )
    return out


# ----------------------------
# Interview question registry (YOU will replace content here)
# ----------------------------


def list_interview_questions() -> List[Dict[str, Any]]:
    """Return the interview question catalog.

    Shape (recommended):
      {"id": "Q1", "question": "...", "meta": {"focus": ["M_WorkEthic", "M_Coachability"]}}

    NOTE:
    - UI가 아직 없더라도, 서버는 이 목록을 제공할 수 있어야 유저 선택(3개)을 구성할 수 있다.
    - 현재는 "자리"만 잡아둔 기본 질문 10개를 제공한다. (내용은 너가 교체)
    """
    # Placeholder 10 questions (replace these with your curated set).
    return [
        {"id": "Q1", "question": "Placeholder Q1 (replace)", "meta": {}},
        {"id": "Q2", "question": "Placeholder Q2 (replace)", "meta": {}},
        {"id": "Q3", "question": "Placeholder Q3 (replace)", "meta": {}},
        {"id": "Q4", "question": "Placeholder Q4 (replace)", "meta": {}},
        {"id": "Q5", "question": "Placeholder Q5 (replace)", "meta": {}},
        {"id": "Q6", "question": "Placeholder Q6 (replace)", "meta": {}},
        {"id": "Q7", "question": "Placeholder Q7 (replace)", "meta": {}},
        {"id": "Q8", "question": "Placeholder Q8 (replace)", "meta": {}},
        {"id": "Q9", "question": "Placeholder Q9 (replace)", "meta": {}},
        {"id": "Q10", "question": "Placeholder Q10 (replace)", "meta": {}},
    ]


def _question_index() -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for q in list_interview_questions():
        qid = str(q.get("id") or "").strip()
        if not qid:
            continue
        out[qid] = q
    return out


def _validate_selected_question_ids(selected_question_ids: Sequence[str]) -> List[str]:
    raw = [str(x or "").strip() for x in (selected_question_ids or []) if str(x or "").strip()]
    if len(raw) != 3:
        raise ValueError("selected_question_ids must have exactly 3 items.")
    if len(set(raw)) != len(raw):
        raise ValueError("selected_question_ids must be unique.")

    qidx = _question_index()
    unknown = [qid for qid in raw if qid not in qidx]
    if unknown:
        raise ValueError(f"unknown question id(s): {unknown}")
    return raw


# ----------------------------
# Payload generation hook (YOU will replace the internals)
# ----------------------------


def generate_interview_payload(
    *,
    prospect: Mapping[str, Any],
    selected_question_ids: Sequence[str],
    rng: random.Random,
) -> Dict[str, Any]:
    """Generate the interview payload for one prospect.

    This is the ONLY place you should need to edit for your custom logic.

    Inputs:
      - prospect: dict row loaded from DB (includes attrs_json, name, pos, etc.)
      - selected_question_ids: exactly 3 IDs (validated)
      - rng: deterministic RNG seeded per (draft_year, team_id, prospect)

    Output (recommended):
      {
        "version": "interview_v1",
        "asked": [
          {"id":"Q1","question":"...","grade":"B","answer":"..."},
          ...
        ]
      }

    IMPORTANT (Fog-of-war):
    - Do NOT include raw mental numbers or attrs_json in the output.
    - Prefer grades/buckets + qualitative answer text.
    """
    qidx = _question_index()
    asked: List[Dict[str, Any]] = []
    for qid in selected_question_ids:
        q = qidx.get(qid) or {}
        asked.append(
            {
                "id": qid,
                "question": str(q.get("question") or ""),
                # Placeholder: you will replace with your grade logic
                "grade": "N/A",
                "answer": "TODO: replace with your interview logic.",
            }
        )
    return {"version": "interview_v1", "asked": asked}


# ----------------------------
# Runner (DB write, idempotent)
# ----------------------------


def run_interviews(
    *,
    db_path: str,
    draft_year: int,
    team_id: str,
    interviews: Sequence[Mapping[str, Any]],
    rng_seed: Optional[int] = None,
) -> Dict[str, Any]:
    """Generate interview results for the specified team and specified prospects/questions.

    interviews item shape:
      {"prospect_temp_id": "...", "selected_question_ids": ["Q1","Q4","Q7"]}
    """
    from league_repo import LeagueRepo

    dy = int(draft_year)
    t = norm_team_id(team_id)
    if not t:
        raise ValueError(f"invalid team_id: {team_id}")

    prospects = _load_declared_prospects(str(db_path), dy)
    by_id: Dict[str, Dict[str, Any]] = {}
    declared_ids: set[str] = set()
    for p in prospects:
        pid = str(p.get("prospect_temp_id") or "").strip()
        if not pid:
            continue
        declared_ids.add(pid)
        by_id[pid] = p

    # Normalize + de-duplicate per prospect
    targets: List[Tuple[str, List[str]]] = []
    seen_pid: set[str] = set()
    missing_prospects: List[str] = []
    for it in (interviews or []):
        pid = str((it or {}).get("prospect_temp_id") or "").strip()
        if not pid or pid in seen_pid:
            continue
        seen_pid.add(pid)
        if pid not in declared_ids:
            missing_prospects.append(pid)
            continue
        qids = _validate_selected_question_ids((it or {}).get("selected_question_ids") or [])
        targets.append((pid, qids))

    if not targets:
        return {
            "ok": True,
            "draft_year": dy,
            "team_id": t,
            "skipped": True,
            "reason": "NO_VALID_TARGETS",
            "count": 0,
            "inserted": 0,
            "skipped_existing": 0,
            "missing_prospects": missing_prospects,
        }

    seed = int(rng_seed) if rng_seed is not None else (dy * 2033 + 97)
    now = game_time.now_utc_like_iso()
    inserted = 0
    skipped_existing = 0

    sql_ins = """
INSERT INTO draft_interview_results(draft_year, team_id, prospect_temp_id, result_json, created_at, updated_at)
VALUES (?, ?, ?, ?, ?, ?)
ON CONFLICT(draft_year, team_id, prospect_temp_id) DO NOTHING;
""".strip()

    with LeagueRepo(str(db_path)) as repo:
        repo.init_db()
        with repo.transaction() as cur:
            try:
                cur.execute("SELECT 1 FROM draft_interview_results LIMIT 1;")
            except sqlite3.OperationalError as e:
                raise RuntimeError("draft_interview_results table missing. Apply db_schema/draft.py patch first.") from e

            for pid, qids in targets:
                p = by_id.get(pid)
                if not p:
                    continue

                derived_seed = _stable_u32("interview_v1", seed, dy, t, pid)
                prng = random.Random(derived_seed)

                payload = generate_interview_payload(prospect=p, selected_question_ids=qids, rng=prng)
                if not isinstance(payload, dict):
                    raise ValueError("generate_interview_payload must return a dict")

                # Minimal metadata wrapper (safe; no raw attrs should be in payload)
                wrapper = {
                    "version": str(payload.get("version") or "interview_v1"),
                    "prospect_temp_id": pid,
                    "team_id": t,
                    "generated": {
                        "seed": int(seed),
                        "derived_seed": int(derived_seed),
                        "generated_at": now,
                        "model": "interview_v1",
                    },
                    "asked": payload.get("asked") if isinstance(payload.get("asked"), list) else [],
                }

                cur.execute(sql_ins, (dy, t, pid, _json_dumps(wrapper), now, now))
                if cur.rowcount and int(cur.rowcount) > 0:
                    inserted += 1
                else:
                    skipped_existing += 1

    return {
        "ok": True,
        "draft_year": dy,
        "team_id": t,
        "skipped": False,
        "reason": None,
        "count": int(len(targets)),
        "inserted": int(inserted),
        "skipped_existing": int(skipped_existing),
        "missing_prospects": missing_prospects,
    }
