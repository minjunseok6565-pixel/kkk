
"""contracts package public API.

We intentionally avoid importing `contracts.free_agents` at module import time.
`contracts.free_agents` imports LeagueRepo, which can be heavy and may create
circular-import risk for callers that only need contract helpers.

Free-agent helpers are exposed via lightweight lazy wrappers below.
"""

from contracts.models import (
    get_active_salary_for_season,
    make_contract_record,
    new_contract_id,
)
from contracts.options import (
    apply_option_decision,
    get_pending_options_for_season,
    normalize_option_record,
    recompute_contract_years_from_salary,
)
from contracts.options_policy import default_option_decision_policy

# Keep in sync with contracts.free_agents.FREE_AGENT_TEAM_ID
FREE_AGENT_TEAM_ID = "FA"


def list_free_agents(game_state: dict, *, player_ids_only: bool = True, repo=None):
    """Lazy wrapper around `contracts.free_agents.list_free_agents`."""
    from contracts.free_agents import list_free_agents as _list_free_agents

    return _list_free_agents(game_state, player_ids_only=player_ids_only, repo=repo)


def is_free_agent(game_state: dict, player_id: str, *, repo=None) -> bool:
    """Lazy wrapper around `contracts.free_agents.is_free_agent`."""
    from contracts.free_agents import is_free_agent as _is_free_agent

    return _is_free_agent(game_state, player_id, repo=repo)

__all__ = [
    "new_contract_id",
    "make_contract_record",
    "get_active_salary_for_season",
    "default_option_decision_policy",
    "normalize_option_record",
    "get_pending_options_for_season",
    "apply_option_decision",
    "recompute_contract_years_from_salary",
    "FREE_AGENT_TEAM_ID",
    "list_free_agents",
    "is_free_agent",
]
