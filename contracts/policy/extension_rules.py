from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Mapping

from config import SEASON_LENGTH_DAYS, SEASON_START_DAY, SEASON_START_MONTH
from contracts.negotiation.errors import (
    MSG_NEGOTIATION_EXTENSION_NOT_ELIGIBLE,
    MSG_NEGOTIATION_EXTENSION_OPTION_WINDOW_VIOLATION,
    MSG_NEGOTIATION_EXTENSION_TOTAL_SEASONS_EXCEEDED,
    MSG_NEGOTIATION_EXTENSION_TYPE_INVALID,
    MSG_NEGOTIATION_EXTENSION_WINDOW_CLOSED,
    NEGOTIATION_BAD_PAYLOAD,
    NEGOTIATION_EXTENSION_NOT_ELIGIBLE,
    NEGOTIATION_EXTENSION_OPTION_WINDOW_VIOLATION,
    NEGOTIATION_EXTENSION_TOTAL_SEASONS_EXCEEDED,
    NEGOTIATION_EXTENSION_TYPE_INVALID,
    NEGOTIATION_EXTENSION_WINDOW_CLOSED,
)
from contracts.negotiation.errors import ContractNegotiationError
from contracts.negotiation.utils import safe_float, safe_int
from contracts.policy.salary_limits import (
    dve_first_year_band,
    rookie_extension_first_year_ceiling,
    veteran_extension_first_year_ceiling,
)
from contracts.policy.raise_limits import validate_salary_curve_with_anchor


_EXTENSION_TYPES = {"ROOKIE", "VETERAN", "DVE"}
_ROOKIE_OPTION_3RD = {"ROOKIE_3RD_YEAR_TEAM_OPTION", "ROOKIE_TEAM_OPTION_3RD", "TEAM_OPTION_YEAR3"}
_ROOKIE_OPTION_4TH = {"ROOKIE_4TH_YEAR_TEAM_OPTION", "ROOKIE_TEAM_OPTION_4TH", "TEAM_OPTION_YEAR4"}


def infer_extension_type(
    player_ctx: Mapping[str, Any],
    contract_ctx: Mapping[str, Any],
    request_type: str | None,
) -> str:
    req = str(request_type or "").strip().upper()
    if req:
        if req not in _EXTENSION_TYPES:
            raise ContractNegotiationError(
                NEGOTIATION_EXTENSION_TYPE_INVALID,
                MSG_NEGOTIATION_EXTENSION_TYPE_INVALID,
                {"request_type": req},
            )
        return req

    if _is_rookie_scale_first_round(contract_ctx):
        return "ROOKIE"

    exp = int(safe_int(player_ctx.get("exp"), 0))
    if 7 <= exp <= 8 and _remaining_seasons(contract_ctx, player_ctx.get("sign_year")) in {1, 2}:
        if _resolve_higher_max_criteria(player_ctx=player_ctx, contract_ctx=contract_ctx, league_ctx={}) is True:
            return "DVE"
    return "VETERAN"


def validate_extension_eligibility(
    *,
    extension_type: str,
    player_ctx: Mapping[str, Any],
    contract_ctx: Mapping[str, Any],
    league_ctx: Mapping[str, Any],
    now_date_iso: str,
) -> None:
    ext = str(extension_type or "").strip().upper()
    if ext not in _EXTENSION_TYPES:
        raise ContractNegotiationError(
            NEGOTIATION_EXTENSION_TYPE_INVALID,
            MSG_NEGOTIATION_EXTENSION_TYPE_INVALID,
            {"extension_type": ext},
        )

    sign_year = int(safe_int(league_ctx.get("sign_year"), 0))
    if sign_year <= 0:
        raise ContractNegotiationError(
            NEGOTIATION_BAD_PAYLOAD,
            "league_ctx.sign_year is required",
            {"missing": "sign_year"},
        )

    if ext == "ROOKIE":
        _validate_rookie_eligibility(
            player_ctx=player_ctx,
            contract_ctx=contract_ctx,
            sign_year=sign_year,
            now_date_iso=now_date_iso,
        )
        return

    if ext == "VETERAN":
        _validate_veteran_eligibility(contract_ctx=contract_ctx, sign_year=sign_year, now_date_iso=now_date_iso)
        return

    _validate_dve_eligibility(
        player_ctx=player_ctx,
        contract_ctx=contract_ctx,
        league_ctx=league_ctx,
        sign_year=sign_year,
    )


def max_total_seasons_at_sign(extension_type: str) -> int:
    ext = str(extension_type or "").strip().upper()
    if ext in {"ROOKIE", "DVE"}:
        return 6
    return 5


def calc_extension_first_year_limit(
    *,
    extension_type: str,
    salary_cap: float,
    prev_salary: float,
    eaps: float,
    exp: int,
    dve_min_pct: float = 0.30,
    dve_max_pct: float = 0.35,
) -> dict[str, float]:
    ext = str(extension_type or "").strip().upper()
    cap = float(safe_float(salary_cap, 0.0))
    prev = float(safe_float(prev_salary, 0.0))
    eaps_f = float(safe_float(eaps, 0.0))
    exp_i = int(safe_int(exp, 0))

    if ext == "ROOKIE":
        max_first = rookie_extension_first_year_ceiling(prev_salary=prev, salary_cap=cap)
        return {
            "min_first_year": 0.0,
            "max_first_year": float(max_first),
            "rule_meta": {
                "extension_type": "ROOKIE",
                "cap_25pct": float(cap * 0.25),
                "prev_105pct": float(prev * 1.05),
            },
        }

    if ext == "VETERAN":
        max_first = veteran_extension_first_year_ceiling(
            prev_salary=prev,
            eaps=eaps_f,
            exp=exp_i,
            salary_cap=cap,
        )
        return {
            "min_first_year": 0.0,
            "max_first_year": float(max_first),
            "rule_meta": {
                "extension_type": "VETERAN",
                "prev_140pct": float(prev * 1.40),
                "eaps_140pct": float(eaps_f * 1.40),
            },
        }

    if ext == "DVE":
        band = dve_first_year_band(cap, min_pct=dve_min_pct, max_pct=dve_max_pct)
        return {
            "min_first_year": float(band["min_first_year"]),
            "max_first_year": float(band["max_first_year"]),
            "rule_meta": {
                "extension_type": "DVE",
                "min_pct": float(safe_float(dve_min_pct, 0.30)),
                "max_pct": float(safe_float(dve_max_pct, 0.35)),
            },
        }

    raise ContractNegotiationError(
        NEGOTIATION_EXTENSION_TYPE_INVALID,
        MSG_NEGOTIATION_EXTENSION_TYPE_INVALID,
        {"extension_type": ext},
    )


def validate_fixed_raise_curve(
    *,
    salary_by_year: Mapping[int | str, float],
    first_year_salary: float,
    max_delta_pct: float = 0.08,
) -> list[dict[str, Any]]:
    chk = validate_salary_curve_with_anchor(
        salary_by_year,
        anchor_salary=first_year_salary,
        max_delta_pct=max_delta_pct,
        allow_descend=True,
    )
    violations: list[dict[str, Any]] = []
    for v in (chk.violations or []):
        row = dict(v)
        if "anchor_salary" in row and "first_year_salary" not in row:
            row["first_year_salary"] = float(row.pop("anchor_salary"))
        row.pop("direction", None)
        violations.append(row)
    return violations


def validate_extension_total_seasons(
    *,
    current_start_year: int,
    current_years: int,
    added_years: int,
    sign_year: int,
    extension_type: str,
) -> None:
    start = int(safe_int(current_start_year, 0))
    years = int(safe_int(current_years, 0))
    add = int(safe_int(added_years, 0))
    sign = int(safe_int(sign_year, 0))
    if start <= 0 or years <= 0 or add <= 0 or sign <= 0:
        raise ContractNegotiationError(
            NEGOTIATION_BAD_PAYLOAD,
            "invalid years for extension total-seasons check",
            {
                "current_start_year": start,
                "current_years": years,
                "added_years": add,
                "sign_year": sign,
            },
        )

    current_end = start + years - 1
    remaining = max(0, current_end - sign + 1)
    total_at_sign = int(remaining + add)
    max_total = int(max_total_seasons_at_sign(extension_type))
    if total_at_sign > max_total:
        raise ContractNegotiationError(
            NEGOTIATION_EXTENSION_TOTAL_SEASONS_EXCEEDED,
            MSG_NEGOTIATION_EXTENSION_TOTAL_SEASONS_EXCEEDED,
            {"total_at_sign": total_at_sign, "max_total": max_total},
        )


def validate_option_decision_window(
    *,
    option_type: str,
    season_year: int,
    decision_date_iso: str,
    contract_meta: Mapping[str, Any],
) -> None:
    opt = str(option_type or "").strip().upper()
    season_y = int(safe_int(season_year, 0))
    if season_y <= 0:
        raise ContractNegotiationError(
            NEGOTIATION_BAD_PAYLOAD,
            "season_year is required",
            {"season_year": season_year},
        )

    decision_date = _parse_iso_date(decision_date_iso)
    contract_start = int(safe_int(contract_meta.get("start_season_year"), 0))
    if contract_start <= 0:
        if opt in _ROOKIE_OPTION_3RD:
            contract_start = season_y - 2
        elif opt in _ROOKIE_OPTION_4TH:
            contract_start = season_y - 3
    if contract_start <= 0:
        raise ContractNegotiationError(
            NEGOTIATION_BAD_PAYLOAD,
            "contract_meta.start_season_year is required",
            {"missing": "start_season_year"},
        )

    if opt in _ROOKIE_OPTION_3RD:
        expected = contract_start + 2
        if season_y != expected:
            raise ContractNegotiationError(
                NEGOTIATION_BAD_PAYLOAD,
                "rookie 3rd-year option season mismatch",
                {"expected_season_year": expected, "season_year": season_y},
            )
        window_start = _season_end(contract_start) + timedelta(days=1)
        window_end = _season_start(contract_start + 1)
    elif opt in _ROOKIE_OPTION_4TH:
        expected = contract_start + 3
        if season_y != expected:
            raise ContractNegotiationError(
                NEGOTIATION_BAD_PAYLOAD,
                "rookie 4th-year option season mismatch",
                {"expected_season_year": expected, "season_year": season_y},
            )
        window_start = _season_end(contract_start + 1) + timedelta(days=1)
        window_end = _season_start(contract_start + 2)
    else:
        return

    if not (window_start <= decision_date < window_end):
        raise ContractNegotiationError(
            NEGOTIATION_EXTENSION_OPTION_WINDOW_VIOLATION,
            MSG_NEGOTIATION_EXTENSION_OPTION_WINDOW_VIOLATION,
            {
                "option_type": opt,
                "decision_date": decision_date.isoformat(),
                "window_start": window_start.isoformat(),
                "window_end_exclusive": window_end.isoformat(),
            },
        )


def _validate_rookie_eligibility(
    *,
    player_ctx: Mapping[str, Any],
    contract_ctx: Mapping[str, Any],
    sign_year: int,
    now_date_iso: str,
) -> None:
    if not _is_rookie_scale_first_round(contract_ctx):
        raise ContractNegotiationError(
            NEGOTIATION_EXTENSION_NOT_ELIGIBLE,
            MSG_NEGOTIATION_EXTENSION_NOT_ELIGIBLE,
            {"reason": "not_rookie_scale_first_round"},
        )

    start = int(safe_int(contract_ctx.get("start_season_year"), 0))
    if start <= 0:
        raise ContractNegotiationError(
            NEGOTIATION_BAD_PAYLOAD,
            "contract_ctx.start_season_year is required",
            {"missing": "start_season_year"},
        )
    if sign_year != start + 3:
        raise ContractNegotiationError(
            NEGOTIATION_EXTENSION_WINDOW_CLOSED,
            MSG_NEGOTIATION_EXTENSION_WINDOW_CLOSED,
            {"reason": "rookie_extension_sign_year_mismatch", "expected_sign_year": start + 3, "sign_year": sign_year},
        )

    decision_date = _parse_iso_date(now_date_iso)
    window_start = _season_end(start + 2) + timedelta(days=1)
    window_end = _season_start(start + 3)
    if not (window_start <= decision_date < window_end):
        raise ContractNegotiationError(
            NEGOTIATION_EXTENSION_WINDOW_CLOSED,
            MSG_NEGOTIATION_EXTENSION_WINDOW_CLOSED,
            {
                "reason": "rookie_extension_window_closed",
                "window_start": window_start.isoformat(),
                "window_end_exclusive": window_end.isoformat(),
                "decision_date": decision_date.isoformat(),
            },
        )

    if not _is_fourth_year_team_option_exercised(contract_ctx=contract_ctx, start_year=start):
        raise ContractNegotiationError(
            NEGOTIATION_EXTENSION_NOT_ELIGIBLE,
            MSG_NEGOTIATION_EXTENSION_NOT_ELIGIBLE,
            {"reason": "rookie_fourth_year_team_option_not_exercised"},
        )


def _validate_veteran_eligibility(*, contract_ctx: Mapping[str, Any], sign_year: int, now_date_iso: str) -> None:
    _ = now_date_iso
    years = int(safe_int(contract_ctx.get("years"), 0))
    start = int(safe_int(contract_ctx.get("start_season_year"), 0))
    if years <= 0 or start <= 0:
        raise ContractNegotiationError(
            NEGOTIATION_BAD_PAYLOAD,
            "contract_ctx.start_season_year and years are required",
            {"start_season_year": start, "years": years},
        )
    if years <= 2:
        raise ContractNegotiationError(
            NEGOTIATION_EXTENSION_NOT_ELIGIBLE,
            MSG_NEGOTIATION_EXTENSION_NOT_ELIGIBLE,
            {"reason": "veteran_extension_disallows_1_2_year_contract"},
        )

    signed_elapsed = sign_year - start
    if 3 <= years <= 4 and signed_elapsed < 2:
        raise ContractNegotiationError(
            NEGOTIATION_EXTENSION_WINDOW_CLOSED,
            MSG_NEGOTIATION_EXTENSION_WINDOW_CLOSED,
            {"reason": "veteran_extension_requires_2nd_anniversary", "signed_elapsed": signed_elapsed},
        )
    if 5 <= years <= 6 and signed_elapsed < 3:
        raise ContractNegotiationError(
            NEGOTIATION_EXTENSION_WINDOW_CLOSED,
            MSG_NEGOTIATION_EXTENSION_WINDOW_CLOSED,
            {"reason": "veteran_extension_requires_3rd_anniversary", "signed_elapsed": signed_elapsed},
        )


def _validate_dve_eligibility(
    *,
    player_ctx: Mapping[str, Any],
    contract_ctx: Mapping[str, Any],
    league_ctx: Mapping[str, Any],
    sign_year: int,
) -> None:
    exp = int(safe_int(player_ctx.get("exp"), 0))
    if exp < 7 or exp > 8:
        raise ContractNegotiationError(
            NEGOTIATION_EXTENSION_NOT_ELIGIBLE,
            MSG_NEGOTIATION_EXTENSION_NOT_ELIGIBLE,
            {"reason": "dve_exp_must_be_7_8", "exp": exp},
        )

    remaining = _remaining_seasons(contract_ctx, sign_year)
    if remaining not in {1, 2}:
        raise ContractNegotiationError(
            NEGOTIATION_EXTENSION_NOT_ELIGIBLE,
            MSG_NEGOTIATION_EXTENSION_NOT_ELIGIBLE,
            {"reason": "dve_requires_1_2_years_remaining", "remaining_seasons": remaining},
        )

    if not _validate_dve_origin_or_trade_exception(player_ctx=player_ctx, contract_ctx=contract_ctx, league_ctx=league_ctx):
        raise ContractNegotiationError(
            NEGOTIATION_EXTENSION_NOT_ELIGIBLE,
            MSG_NEGOTIATION_EXTENSION_NOT_ELIGIBLE,
            {"reason": "dve_origin_or_trade_exception_failed"},
        )

    higher_max_ok = _resolve_higher_max_criteria(player_ctx=player_ctx, contract_ctx=contract_ctx, league_ctx=league_ctx)
    if higher_max_ok is None:
        raise ContractNegotiationError(
            NEGOTIATION_BAD_PAYLOAD,
            "missing Higher Max Criteria source",
            {"missing": "higher_max_criteria_by_player_or_resolver"},
        )
    if not higher_max_ok:
        raise ContractNegotiationError(
            NEGOTIATION_EXTENSION_NOT_ELIGIBLE,
            MSG_NEGOTIATION_EXTENSION_NOT_ELIGIBLE,
            {"reason": "dve_higher_max_criteria_not_met"},
        )


def _is_rookie_scale_first_round(contract_ctx: Mapping[str, Any]) -> bool:
    ctype = str(contract_ctx.get("contract_type") or "").strip().upper()
    cj = contract_ctx.get("contract_json")
    cjson = cj if isinstance(cj, Mapping) else {}
    rookie_meta = cjson.get("rookie_scale") if isinstance(cjson.get("rookie_scale"), Mapping) else {}
    round_no = int(safe_int(rookie_meta.get("draft_round"), 0))
    return bool(ctype == "ROOKIE_SCALE" and round_no == 1)


def _is_fourth_year_team_option_exercised(*, contract_ctx: Mapping[str, Any], start_year: int) -> bool:
    target_year = int(start_year) + 3
    options = contract_ctx.get("options")
    if not isinstance(options, list):
        return False
    for opt in options:
        if not isinstance(opt, Mapping):
            continue
        oy = int(safe_int(opt.get("season_year"), 0))
        otype = str(opt.get("type") or opt.get("option_type") or "").strip().upper()
        status = str(opt.get("status") or "").strip().upper()
        if oy == target_year and otype == "TEAM" and status == "EXERCISED":
            return True
    return False


def _remaining_seasons(contract_ctx: Mapping[str, Any], sign_year: Any) -> int:
    start = int(safe_int(contract_ctx.get("start_season_year"), 0))
    years = int(safe_int(contract_ctx.get("years"), 0))
    sign = int(safe_int(sign_year, 0))
    if start <= 0 or sign <= 0:
        return 0
    if years <= 0:
        sy = contract_ctx.get("salary_by_year")
        if isinstance(sy, Mapping):
            years = len([1 for k in sy.keys() if str(k).strip()])
    if years <= 0:
        return 0
    end = start + years - 1
    return max(0, end - sign + 1)


def _validate_dve_origin_or_trade_exception(
    *,
    player_ctx: Mapping[str, Any],
    contract_ctx: Mapping[str, Any],
    league_ctx: Mapping[str, Any],
) -> bool:
    current_team = str(
        contract_ctx.get("team_id")
        or player_ctx.get("team_id")
        or league_ctx.get("team_id")
        or ""
    ).strip().upper()
    cj = contract_ctx.get("contract_json")
    cjson = cj if isinstance(cj, Mapping) else {}
    rookie_meta = cjson.get("rookie_scale") if isinstance(cjson.get("rookie_scale"), Mapping) else {}
    origin_team = str(
        rookie_meta.get("draft_team_id")
        or rookie_meta.get("original_team_id")
        or contract_ctx.get("original_team_id")
        or ""
    ).strip().upper()
    draft_year = int(safe_int(rookie_meta.get("draft_year"), 0))

    if origin_team and current_team and origin_team == current_team:
        return True

    tx_hist = league_ctx.get("player_tx_history")
    if not isinstance(tx_hist, Mapping):
        raise ContractNegotiationError(
            NEGOTIATION_BAD_PAYLOAD,
            "missing_origin_history",
            {"missing": "player_tx_history"},
        )
    pid = str(player_ctx.get("player_id") or contract_ctx.get("player_id") or "").strip()
    if not pid:
        raise ContractNegotiationError(
            NEGOTIATION_BAD_PAYLOAD,
            "missing_origin_history",
            {"missing": "player_id"},
        )
    moves = tx_hist.get(pid)
    if not isinstance(moves, list) or not moves:
        raise ContractNegotiationError(
            NEGOTIATION_BAD_PAYLOAD,
            "missing_origin_history",
            {"player_id": pid},
        )

    norm_moves: list[dict[str, Any]] = []
    for m in moves:
        if not isinstance(m, Mapping):
            continue
        sy = int(safe_int(m.get("season_year"), 0))
        if sy <= 0:
            raise ContractNegotiationError(
                NEGOTIATION_BAD_PAYLOAD,
                "missing_origin_history",
                {"reason": "transaction_missing_season_year"},
            )
        tx_type = str(m.get("tx_type") or m.get("type") or "").strip().upper()
        if not tx_type:
            raise ContractNegotiationError(
                NEGOTIATION_BAD_PAYLOAD,
                "missing_origin_history",
                {"reason": "transaction_missing_type"},
            )
        norm_moves.append(
            {
                "season_year": sy,
                "from_team": str(m.get("from_team") or "").strip().upper(),
                "to_team": str(m.get("to_team") or "").strip().upper(),
                "tx_type": tx_type,
            }
        )
    norm_moves.sort(key=lambda x: int(x["season_year"]))

    if draft_year > 0:
        cutoff = draft_year + 4
        early_moves = [m for m in norm_moves if int(m["season_year"]) < cutoff]
        for m in early_moves:
            if m["from_team"] != m["to_team"] and m["tx_type"] != "TRADE":
                return False
    if current_team:
        for m in norm_moves:
            if m["to_team"] == current_team and m["tx_type"] == "TRADE":
                return True
    return False


def _resolve_higher_max_criteria(
    *,
    player_ctx: Mapping[str, Any],
    contract_ctx: Mapping[str, Any],
    league_ctx: Mapping[str, Any],
) -> bool | None:
    pid = str(player_ctx.get("player_id") or contract_ctx.get("player_id") or "").strip()
    by_player = league_ctx.get("higher_max_criteria_by_player")
    if isinstance(by_player, Mapping) and pid:
        if pid in by_player:
            value = by_player.get(pid)
            if isinstance(value, Mapping):
                return bool(value.get("eligible"))
            return bool(value)

    resolver = league_ctx.get("higher_max_criteria_resolver")
    if callable(resolver):
        return bool(resolver(pid))
    return None


def _parse_iso_date(value: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except Exception as exc:  # pragma: no cover - strict parse branch
        raise ContractNegotiationError(
            NEGOTIATION_BAD_PAYLOAD,
            "invalid ISO date",
            {"value": value},
        ) from exc


def _season_start(season_year: int) -> date:
    return date(int(season_year), int(SEASON_START_MONTH), int(SEASON_START_DAY))


def _season_end(season_year: int) -> date:
    return _season_start(int(season_year)) + timedelta(days=int(SEASON_LENGTH_DAYS))
