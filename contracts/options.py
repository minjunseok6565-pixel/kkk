"""Contract option utilities."""

from __future__ import annotations

from contracts.options_policy import normalize_option_type


def normalize_option_record(option: dict) -> dict:
    if not isinstance(option, dict):
        raise ValueError("Option record must be a dict")
    if "season_year" not in option or "type" not in option or "status" not in option:
        raise ValueError("Option record missing required keys")

    season_year = int(option["season_year"])
    option_type = normalize_option_type(option["type"])
    status = str(option["status"]).strip().upper()
    if status not in {"PENDING", "EXERCISED", "DECLINED"}:
        raise ValueError(f"Invalid option status: {status}")

    decision_date = option.get("decision_date")
    if decision_date is not None:
        decision_date = str(decision_date)

    return {
        "season_year": season_year,
        "type": option_type,
        "status": status,
        "decision_date": decision_date,
    }


def get_pending_options_for_season(contract: dict, season_year: int) -> list[dict]:
    options = contract.get("options") or []
    return [
        option
        for option in options
        if option.get("season_year") == season_year
        and option.get("status") == "PENDING"
    ]


def apply_option_decision(
    contract: dict,
    option_index: int,
    decision: str,
    decision_date_iso: str,
) -> None:
    decision_value = str(decision).strip().upper()
    if decision_value not in {"EXERCISE", "DECLINE"}:
        raise ValueError(f"Invalid option decision: {decision}")

    option = contract["options"][option_index]
    if decision_value == "EXERCISE":
        option["status"] = "EXERCISED"
    else:
        option["status"] = "DECLINED"
    option["decision_date"] = decision_date_iso

    if decision_value == "DECLINE":
        # Declining an option terminates the deal from that season onward.
        #
        # Important: we *must* remove ALL future salary years to avoid leaving
        # "dangling" salary keys that can pollute downstream valuations or cap views.
        #
        # This also keeps multi-year option tails (e.g. 2+2) consistent:
        # - decline year 3 option => years 3+4 removed
        # - decline year 4 option => only year 4 removed
        cut_year = int(option["season_year"])
        salary_by_year = contract.get("salary_by_year") or {}
        # Remove salary for cut_year and any later seasons.
        for k in list(salary_by_year.keys()):
            try:
                y = int(k)
            except (TypeError, ValueError):
                continue
            if y >= cut_year:
                salary_by_year.pop(str(k), None)

        # Also mark any later PENDING options as declined (data cleanliness).
        try:
            for opt in (contract.get("options") or []):
                try:
                    oy = int(opt.get("season_year") or -1)
                except (TypeError, ValueError):
                    continue
                if oy >= cut_year and str(opt.get("status") or "").upper() == "PENDING":
                    opt["status"] = "DECLINED"
                    opt["decision_date"] = decision_date_iso
        except Exception:
            # Best-effort; don't fail the main decision on cleanup.
            pass


def recompute_contract_years_from_salary(contract: dict) -> None:
    start = int(contract.get("start_season_year") or 0)
    salary_by_year = contract.get("salary_by_year") or {}
    try:
        salary_years = sorted(int(year) for year in salary_by_year.keys())
    except ValueError:
        salary_years = []

    years = 0
    current = start
    while current in salary_years:
        years += 1
        current += 1

    contract["years"] = years
