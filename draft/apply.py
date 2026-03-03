from __future__ import annotations

"""Apply a drafted college player to DB (players/roster/contracts)."""

import json
import hashlib
import game_time
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Protocol

from config import CAP_BASE_SALARY_CAP, CAP_ROUND_UNIT

# SSOT: season-based salary cap computation (avoid duplicated cap math in draft).
try:
    from cap_model import CapModel
    _CAP_MODEL = CapModel.defaults()
except Exception:  # pragma: no cover
    CapModel = None  # type: ignore
    _CAP_MODEL = None  # type: ignore
from league_repo import LeagueRepo
from contracts.models import new_contract_id, make_contract_record
from ratings_2k import REQUIRED_KEYS, validate_attrs

from .pool import Prospect
from .types import DraftTurn, TeamId, norm_team_id


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str)


_PLAYER_ID_RE = re.compile(r"^P\d{6}$")


def _looks_like_player_id(x: Any) -> bool:
    s = str(x or "")
    return bool(_PLAYER_ID_RE.match(s))


def _is_college_player_id(repo: LeagueRepo, player_id: str) -> bool:
    row = repo._conn.execute(
        "SELECT 1 FROM college_players WHERE player_id=? LIMIT 1;",
        (str(player_id),),
    ).fetchone()
    return bool(row)

def _require_contiguous_salary_years(
    salary_by_year: Mapping[int, int],
    *,
    start_year: int,
    years: int,
    context: str,
) -> None:
    """Fail-loud guard to prevent silent contract shape corruption.

    Some downstream logic (options processing, salary derivations) assumes that
    salary_by_year contains contiguous keys starting from start_year.
    """
    missing = [start_year + i for i in range(int(years)) if (start_year + i) not in salary_by_year]
    if missing:
        raise ValueError(
            f"{context}: salary_by_year missing required seasons: missing={missing} "
            f"start_year={start_year} years={years} got_years={sorted(int(k) for k in salary_by_year.keys())}"
        )


def _build_rookie_scale_team_options(*, draft_year: int) -> List[Dict[str, Any]]:
    """NBA-like 1st-round rookie scale team options.

    In the NBA, first-round rookies have two guaranteed seasons, then two team
    options (years 3 and 4).

    We represent this using existing option processing:
      - salary_by_year includes 4 seasons upfront
      - options mark seasons draft_year+2 and draft_year+3 as TEAM/PENDING
      - when a TEAM option is declined, salary for that season is removed
        (contracts.options.apply_option_decision)
    """
    dy = int(draft_year)
    return [
        {"season_year": dy + 2, "type": "TEAM", "status": "PENDING", "decision_date": None},
        {"season_year": dy + 3, "type": "TEAM", "status": "PENDING", "decision_date": None},
    ]


# -----------------------------------------------------------------------------
# Second-round pick exception (SRPE) helpers (NBA-like)
# -----------------------------------------------------------------------------

# Minimal, cap-relative approximation for "0 YOS minimum" used as SRPE base.
# (Keeps numbers scaling with league cap and avoids hardcoding per-year tables.)
SRPE_MIN0_CAP_RATIO = 0.0075
SRPE_MAX_MULT = 1.85
SRPE_MIN_MULT = 1.05
SRPE_ANNUAL_RAISE = 0.05


def _round_to_unit(value: float, unit: int) -> int:
    u = max(1, int(unit))
    return int(round(float(value) / float(u))) * u


def _cap_for_season_year(season_year: int, *, cap_model: Optional["CapModel"] = None) -> int:
    """Return salary cap for season_year using CapModel SSOT.

    NOTE:
    - Prefer an injected cap_model (constructed from SSOT: league.trade_rules).
    - Fall back to CapModel.defaults() only when cap_model is not provided (e.g.,
      offline/unit-test usage).
    """
    y = int(season_year)
    if cap_model is not None:
        try:
            return int(cap_model.salary_cap_for_season(y))
        except Exception:
            pass
    if _CAP_MODEL is not None:
        try:
            return int(_CAP_MODEL.salary_cap_for_season(y))
        except Exception:
            pass
    # Conservative fallback: base cap (keeps SRPE non-zero), no formula duplication.
    return _round_to_unit(float(CAP_BASE_SALARY_CAP), int(CAP_ROUND_UNIT))


def _min0_salary_for_season_year(season_year: int, *, cap_model: Optional["CapModel"] = None) -> int:
    cap = _cap_for_season_year(int(season_year), cap_model=cap_model)
    return _round_to_unit(float(cap) * float(SRPE_MIN0_CAP_RATIO), int(CAP_ROUND_UNIT))


def _stable_u32(text: str) -> int:
    # NOTE: built-in hash() is randomized per-process; use a stable digest.
    h = hashlib.blake2b(str(text).encode("utf-8"), digest_size=4).digest()
    return int.from_bytes(h, "big")


def _srpe_template_for_pick(*, draft_year: int, overall_no: int, player_id: str) -> str:
    u = _stable_u32(f"{int(draft_year)}:{int(overall_no)}:{str(player_id)}") % 100
    ov = int(overall_no)
    if 31 <= ov <= 40:
        return "3+1" if u < 40 else "2+1"
    if 41 <= ov <= 45:
        return "3+1" if u < 20 else "2+1"
    if 46 <= ov <= 60:
        return "3+1" if u < 10 else "2+1"
    return "2+1"


def _srpe_first_year_salary(*, draft_year: int, overall_no: int, cap_model: Optional["CapModel"] = None) -> int:
    min0 = _min0_salary_for_season_year(int(draft_year), cap_model=cap_model)
    # linear premium: 31 -> SRPE_MAX_MULT, 60 -> SRPE_MIN_MULT
    t = (int(overall_no) - 31) / 29.0
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    mult = float(SRPE_MAX_MULT) + (float(SRPE_MIN_MULT) - float(SRPE_MAX_MULT)) * t
    return _round_to_unit(float(min0) * mult, int(CAP_ROUND_UNIT))


def _build_second_round_exception_terms(
    *,
    draft_year: int,
    overall_no: int,
    player_id: str,
    cap_model: Optional["CapModel"] = None,
) -> tuple[int, Dict[int, int], List[Dict[str, Any]], Dict[str, Any]]:
    template = _srpe_template_for_pick(draft_year=int(draft_year), overall_no=int(overall_no), player_id=str(player_id))
    years = 4 if template == "3+1" else 3
    y1 = _srpe_first_year_salary(draft_year=int(draft_year), overall_no=int(overall_no), cap_model=cap_model)
    salary_by_year: Dict[int, int] = {}
    for i in range(int(years)):
        salary_by_year[int(draft_year) + i] = _round_to_unit(float(y1) * (1.0 + float(SRPE_ANNUAL_RAISE) * i), int(CAP_ROUND_UNIT))
    opt_year = int(draft_year) + (int(years) - 1)
    options = [{"season_year": int(opt_year), "type": "TEAM", "status": "PENDING", "decision_date": None}]
    meta = {"template": template, "draft_year": int(draft_year), "overall_no": int(overall_no)}
    return int(years), salary_by_year, options, meta


class RookieContractPolicy(Protocol):
    def build_salary_by_year(self, *, draft_year: int, overall_no: int, years: int = 4) -> Dict[int, int]:
        ...


class SimpleRookieScalePolicy:
    """Very simple rookie salary curve (placeholder).

    Produces annual salaries in dollars; intentionally rough.
    """

    def __init__(self, *, base_top1: int = 10_000_000, floor_late1: int = 1_000_000, floor_2nd: int = 800_000):
        self.base_top1 = int(base_top1)
        self.floor_late1 = int(floor_late1)
        self.floor_2nd = int(floor_2nd)

    def build_salary_by_year(self, *, draft_year: int, overall_no: int, years: int = 4) -> Dict[int, int]:
        dy = int(draft_year)
        ov = int(overall_no)
        yrs = max(1, int(years))

        if ov <= 30:
            # linear drop from #1 to #30
            t = (ov - 1) / 29.0 if ov > 1 else 0.0
            s0 = int(round(self.base_top1 * (1.0 - 0.85 * t)))
            s0 = max(s0, self.floor_late1)
        else:
            # second round low guarantees
            s0 = self.floor_2nd

        # small raises year-to-year (approx)
        out: Dict[int, int] = {}
        for i in range(yrs):
            out[dy + i] = int(round(s0 * (1.0 + 0.05 * i)))
        return out


@dataclass(frozen=True, slots=True)
class ApplyPickResult:
    player_id: str
    contract_id: str
    team_id: TeamId
    tx_entry: Dict[str, Any]
    promoted_from_college: bool


def apply_pick_to_db(
    *,
    db_path: str,
    turn: DraftTurn,
    prospect: Prospect,
    draft_year: int,
    cap_model: Optional["CapModel"] = None,
    contract_policy: Optional[RookieContractPolicy] = None,
    contract_years: int = 4,
    tx_date_iso: Optional[str] = None,
    source: str = "draft",
) -> ApplyPickResult:
    """Persist a drafted rookie to DB."""
    dbp = str(db_path)
    team_id = norm_team_id(turn.drafting_team)
    dy = int(draft_year)

    round_no = int(turn.round)
    is_first_round = round_no == 1
    is_second_round = round_no == 2

    options: List[Dict[str, Any]] = []
    srpe_meta: Dict[str, Any] = {}

    # Contract shape rules:
    # - 1st round: NBA-like rookie scale = 2 years guaranteed + two TEAM options (yrs 3/4)
    # - 2nd round: SRPE (Second-Round Pick Exception) templates:
    #   * 2+1 (TEAM option year 3) or 3+1 (TEAM option year 4)
    if is_first_round:
        years_i = 4
        policy = contract_policy or SimpleRookieScalePolicy()
        salary_by_year = policy.build_salary_by_year(draft_year=dy, overall_no=int(turn.overall_no), years=int(years_i))
    elif is_second_round:
        # Ignore contract_years; use SRPE template.
        years_i, salary_by_year, options, srpe_meta = _build_second_round_exception_terms(
            draft_year=dy,
            overall_no=int(turn.overall_no),
            player_id=str(prospect.temp_id),
            cap_model=cap_model,
        )
    else:
        years_i = max(1, int(contract_years))
        policy = contract_policy or SimpleRookieScalePolicy()
        salary_by_year = policy.build_salary_by_year(draft_year=dy, overall_no=int(turn.overall_no), years=int(years_i))

    if is_first_round:
        # Fail-loud: ensure rookie scale always contains 4 contiguous seasons.
        _require_contiguous_salary_years(
            salary_by_year,
            start_year=dy,
            years=4,
            context="1st-round rookie scale",
        )
        options = _build_rookie_scale_team_options(draft_year=dy)
        # Additional fail-loud: ensure the option seasons exist in salary_by_year.
        opt_years = [int(o.get("season_year")) for o in options]
        for oy in opt_years:
            if oy not in salary_by_year:
                raise ValueError(
                    "1st-round rookie scale: option season missing in salary_by_year: "
                    f"season_year={oy} salary_years={sorted(int(k) for k in salary_by_year.keys())}"
                )

    if is_second_round:
        # Fail-loud: ensure contiguous salary years for the chosen SRPE template.
        _require_contiguous_salary_years(
            salary_by_year,
            start_year=dy,
            years=int(years_i),
            context="2nd-round SRPE",
        )
        # Ensure option season exists in salary_by_year.
        if not options:
            raise ValueError("2nd-round SRPE: missing TEAM option record")
        oy = int(options[0].get("season_year"))
        if oy not in salary_by_year:
            raise ValueError(
                "2nd-round SRPE: option season missing in salary_by_year: "
                f"season_year={oy} salary_years={sorted(int(k) for k in salary_by_year.keys())}"
            )

    signed_date_iso = game_time.require_date_iso(tx_date_iso, field="tx_date_iso")

    with LeagueRepo(dbp) as repo:
        repo.init_db()
        now = game_time.utc_like_from_date_iso(signed_date_iso, field="tx_date_iso")
        pick_id = str(turn.pick_id)

        # Idempotency guard: if this pick_id was already applied, return the recorded result.
        # This prevents "restart from scratch" from getting stuck after partial progress.
        row_applied = repo._conn.execute(
            """
            SELECT pick_id, drafting_team, prospect_temp_id, player_id, contract_id, meta_json
            FROM draft_results
            WHERE pick_id=? LIMIT 1;
            """,
            (pick_id,),
        ).fetchone()
        if row_applied:
            if str(row_applied["drafting_team"]) != str(team_id) or str(row_applied["prospect_temp_id"]) != str(prospect.temp_id):
                raise RuntimeError(
                    "draft_results already contains this pick_id but does not match current inputs: "
                    f"pick_id={pick_id} drafting_team(db={row_applied['drafting_team']!r}, cur={team_id!r}) "
                    f"prospect_temp_id(db={row_applied['prospect_temp_id']!r}, cur={prospect.temp_id!r})"
                )
            meta0: Dict[str, Any] = {}
            try:
                mj = row_applied["meta_json"]
                if mj:
                    meta0 = json.loads(str(mj))
            except Exception:
                meta0 = {}

            # Keep tx_entry shape stable; mark as already applied.
            tx_entry = {
                "type": "draft_pick_applied",
                "source": str(source),
                "date": signed_date_iso,
                "season_year": dy - 1,
                "teams": [team_id],
                "draft_year": dy,
                "prospect_temp_id": str(prospect.temp_id),
                "prospect_source": str(meta0.get("prospect_source") or "unknown"),
                "college_promoted": bool(meta0.get("college_promoted") or False),
                "already_applied": True,
                "pick": {
                    "overall_no": int(turn.overall_no),
                    "round": int(turn.round),
                    "slot": int(turn.slot),
                    "pick_id": pick_id,
                    "original_team": str(turn.original_team),
                    "drafting_team": str(team_id),
                },
                "player": {
                    "player_id": str(row_applied["player_id"]),
                    "name": str(prospect.name),
                    "pos": str(prospect.pos),
                    "age": int(prospect.age),
                    "ovr": int(prospect.ovr),
                },
                "contract": {
                    "contract_id": str(row_applied["contract_id"]),
                },
            }
            return ApplyPickResult(
                player_id=str(row_applied["player_id"]),
                contract_id=str(row_applied["contract_id"]),
                team_id=team_id,
                tx_entry=tx_entry,
                promoted_from_college=bool(meta0.get("college_promoted") or False),
            )

        contract_id = new_contract_id()

        # College-only: prospect.temp_id must be a real college player_id.
        temp_id = str(prospect.temp_id)
        if not _looks_like_player_id(temp_id):
            raise ValueError(f"prospect.temp_id must be a real player_id like P000001: {temp_id}")
        if not _is_college_player_id(repo, temp_id):
            raise ValueError(f"no college_players row found for prospect.temp_id: {temp_id}")
        row = repo._conn.execute(
            "SELECT 1 FROM players WHERE player_id=? LIMIT 1;",
            (temp_id,),
        ).fetchone()
        if row:
            raise ValueError(f"cannot promote: player_id already exists in players: {temp_id}")
        player_id = temp_id
        promoted_from_college = True

        # Upsert players/roster
        attrs = dict(prospect.attrs) if isinstance(prospect.attrs, dict) else {}
        validate_attrs(attrs, strict=True)
        attrs = {k: attrs[k] for k in REQUIRED_KEYS}
        # IMPORTANT: attrs_json SSOT는 2K base ratings dict만 저장한다.
        # 드래프트 메타는 draft_results.meta_json + transactions_log로만 남긴다.

        # Pick = one atomic DB transaction (players/roster/contracts/indices/tx/college cleanup + draft_results)
        with repo.transaction() as cur:
            cur.execute(
                """
                INSERT INTO players(player_id, name, pos, age, height_in, weight_lb, ovr, attrs_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(player_id) DO UPDATE SET
                    name=excluded.name,
                    pos=excluded.pos,
                    age=excluded.age,
                    height_in=excluded.height_in,
                    weight_lb=excluded.weight_lb,
                    ovr=excluded.ovr,
                    attrs_json=excluded.attrs_json,
                    updated_at=excluded.updated_at;
                """,
                (
                    player_id,
                    str(prospect.name),
                    str(prospect.pos),
                    int(prospect.age),
                    int(prospect.height_in),
                    int(prospect.weight_lb),
                    int(prospect.ovr),
                    _json_dumps(attrs),
                    now,
                    now,
                ),
            )
            cur.execute(
                """
                INSERT INTO roster(player_id, team_id, salary_amount, status, updated_at)
                VALUES (?, ?, ?, 'active', ?)
                ON CONFLICT(player_id) DO UPDATE SET
                    team_id=excluded.team_id,
                    salary_amount=excluded.salary_amount,
                    status=excluded.status,
                    updated_at=excluded.updated_at;
                """,
                (
                    player_id,
                    team_id,
                    int(
                        salary_by_year.get(dy)
                        if salary_by_year.get(dy) is not None
                        else (next(iter(salary_by_year.values())) if salary_by_year else 0)
                    ),
                    now,
                ),
            )

            # Upsert contract (nested SAVEPOINT inside outer pick transaction)
            contract = make_contract_record(
                contract_id=contract_id,
                player_id=player_id,
                team_id=team_id,
                signed_date_iso=signed_date_iso,
                start_season_year=dy,
                years=int(years_i),
                salary_by_year={int(k): int(v) for k, v in salary_by_year.items()},
                options=options,
                status="ACTIVE",
            )
            if is_first_round:
                # Stored in SSOT `contracts.contract_type` column; contract_json stores extras only.
                contract["contract_type"] = "ROOKIE_SCALE"
                contract["guaranteed_years"] = 2
                contract["rookie_scale"] = {
                    "draft_year": dy,
                    "overall_no": int(turn.overall_no),
                    "round": round_no,
                    "slot": int(turn.slot),
                }
            if is_second_round:
                contract["contract_type"] = "SECOND_ROUND_EXCEPTION"
                contract["guaranteed_years"] = max(0, int(years_i) - 1)
                contract["srpe"] = dict(srpe_meta) if isinstance(srpe_meta, dict) else {}
            repo.upsert_contract_records({contract_id: contract})
            repo.rebuild_contract_indices()

            cur.execute("DELETE FROM college_player_season_stats WHERE player_id=?;", (player_id,))
            cur.execute("DELETE FROM college_draft_entries WHERE player_id=?;", (player_id,))
            cur.execute("DELETE FROM college_players WHERE player_id=?;", (player_id,))

            meta_json = _json_dumps(
                {
                    "source": str(source),
                    "prospect_source": "college",
                    "college_promoted": True,
                }
            )

            tx_entry = {
                "type": "draft_pick_applied",
                "source": str(source),
                "date": signed_date_iso,
                "season_year": dy - 1,  # drafted after season dy-1
                "teams": [team_id],
                "draft_year": dy,
                "prospect_temp_id": str(prospect.temp_id),
                "prospect_source": "college",
                "college_promoted": True,
                "pick": {
                    "overall_no": int(turn.overall_no),
                    "round": int(turn.round),
                    "slot": int(turn.slot),
                    "pick_id": pick_id,
                    "original_team": str(turn.original_team),
                    "drafting_team": str(team_id),
                },
                "player": {
                    "player_id": player_id,
                    "name": str(prospect.name),
                    "pos": str(prospect.pos),
                    "age": int(prospect.age),
                    "ovr": int(prospect.ovr),
                },
                "contract": {
                    "contract_id": contract_id,
                    "start_season_year": dy,
                    "years": int(years_i),
                    "salary_by_year": {str(k): int(v) for k, v in salary_by_year.items()},
                    **(
                        (
                            {"contract_type": "ROOKIE_SCALE", "options": options}
                            if is_first_round
                            else ({"contract_type": "SECOND_ROUND_EXCEPTION", "options": options} if is_second_round else {})
                        )
                    ),
                },
            }

            # Transactions log (nested SAVEPOINT inside outer pick transaction)
            repo.insert_transactions([tx_entry])

            # Draft SSOT record (must commit with the same pick transaction)
            cur.execute(
                """
                INSERT INTO draft_results(
                    pick_id, draft_year, overall_no, "round", slot,
                    original_team, drafting_team,
                    prospect_temp_id, player_id, contract_id,
                    applied_at, source, meta_json,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(pick_id) DO UPDATE SET
                    draft_year=excluded.draft_year,
                    overall_no=excluded.overall_no,
                    "round"=excluded."round",
                    slot=excluded.slot,
                    original_team=excluded.original_team,
                    drafting_team=excluded.drafting_team,
                    prospect_temp_id=excluded.prospect_temp_id,
                    player_id=excluded.player_id,
                    contract_id=excluded.contract_id,
                    applied_at=excluded.applied_at,
                    source=excluded.source,
                    meta_json=excluded.meta_json,
                    updated_at=excluded.updated_at;
                """,
                (
                    pick_id,
                    dy,
                    int(turn.overall_no),
                    int(turn.round),
                    int(turn.slot),
                    str(turn.original_team),
                    str(team_id),
                    str(prospect.temp_id),
                    str(player_id),
                    str(contract_id),
                    now,
                    str(source),
                    meta_json,
                    now,
                    now,
                ),
            )

    return ApplyPickResult(
        player_id=player_id,
        contract_id=contract_id,
        team_id=team_id,
        tx_entry=tx_entry,
        promoted_from_college=True,
    )
