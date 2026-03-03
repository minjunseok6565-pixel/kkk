from __future__ import annotations

"""trades/counter_offer/config.py

Counter-offer tuning knobs.

This module is intentionally *thin* and avoids duplicating SSOT logic.
It only provides:
- default budgets for counter-offer search (validation/evaluation caps)
- strategy toggles (fit-swap / sweetener / protection tweak)
- a helper to build DealGeneratorConfig/Budget used by existing dealgen utilities

Design notes
------------
- Counter offers should feel "NBA-like" while staying computationally bounded.
- We prefer minimal-edit counters (remove a sweetener / add a single pick) over
  full reworks.
- Anything that touches *hard legality* must still go through tick_ctx.validate_deal
  (SalaryMatching, Stepien, apron rules, locks, etc.)
"""

from dataclasses import dataclass, replace
from typing import Optional, Tuple


@dataclass(frozen=True, slots=True)
class CounterOfferConfig:
    """Configuration for counter-offer generation.

    The defaults are aimed for a commercial game:
    - stable/deterministic results
    - low CPU cost per negotiation update
    - "small, realistic" counter edits
    """

    # ------------------------------------------------------------------
    # Determinism
    # ------------------------------------------------------------------
    seed_salt: str = "counter_offer_v1"

    # ------------------------------------------------------------------
    # Global search budget
    # ------------------------------------------------------------------
    # Hard safety caps (per counter build call)
    max_validations: int = 140
    max_evaluations: int = 70

    # For repair loops (fit-swap uses repair_until_valid internally)
    max_repairs: int = 1

    # Candidate cap (avoid long tails)
    max_candidates: int = 24

    # ------------------------------------------------------------------
    # "Anchor" preservation (don't remove the primary asset the user is targeting)
    # ------------------------------------------------------------------
    anchor_keep_top_n: int = 1

    # ------------------------------------------------------------------
    # User experience guards
    # ------------------------------------------------------------------
    # If the user-side margin is worse than this, avoid proposing it.
    # (Margin = net_surplus - required_surplus)
    user_margin_floor: float = -14.0

    # Prefer counters with small edit distance (soft). This weight only affects ranking.
    prefer_min_edits: bool = True

    # ------------------------------------------------------------------
    # Strategy toggles
    # ------------------------------------------------------------------
    enable_fit_swap: bool = True
    enable_pick_sweeteners: bool = True
    enable_player_sweeteners: bool = True
    enable_remove_outgoing: bool = True
    enable_reduce_pick_protection: bool = True

    # ------------------------------------------------------------------
    # Extra ask policy (how hard the AI pushes above "just accept")
    # ------------------------------------------------------------------
    # Extra ask is expressed in the same value unit as valuation (TVU).
    # We scale it by decision_policy's "corridor" size (gray zone width).
    extra_ask_base_frac_of_corridor: float = 0.25
    extra_ask_max_frac_of_corridor: float = 0.65

    # Multipliers (applied to base extra ask)
    tough_mult_lo: float = 0.85
    tough_mult_hi: float = 1.55

    # Relationship tuning
    trust_relief: float = 0.22          # positive trust reduces ask
    distrust_pressure: float = 0.18     # negative trust increases ask
    fatigue_pressure: float = 0.10      # negotiation fatigue increases ask slightly
    promises_broken_pressure: float = 0.18
    deadline_relief: float = 0.12       # near deadline reduces ask a bit

    # ------------------------------------------------------------------
    # Outgoing removal (AI gives less)
    # ------------------------------------------------------------------
    remove_outgoing_max_assets: int = 2
    remove_outgoing_try_combinations: bool = True
    # Only remove low-impact players by default. Picks/swaps are preferred.
    remove_outgoing_player_market_cap: float = 10.0

    # ------------------------------------------------------------------
    # Reduce pick protection (make a protected pick more valuable)
    # ------------------------------------------------------------------
    reduce_pick_protection_step: int = 2
    reduce_pick_protection_max_steps: int = 3
    reduce_pick_protection_min_n: int = 1
    reduce_pick_protection_max_candidates: int = 4

    # ------------------------------------------------------------------
    # Sweetener budgets (existing dealgen.sweetener)
    # ------------------------------------------------------------------
    sweetener_max_additions: int = 2
    sweetener_candidate_width: int = 2

    # ------------------------------------------------------------------
    # Fit swap budgets (existing dealgen.fit_swap)
    # ------------------------------------------------------------------
    fit_swap_candidate_pool: int = 14
    fit_swap_try_top_n: int = 5
    fit_swap_max_repairs: int = 1

    # ------------------------------------------------------------------
    # Player sweetener fallback
    # ------------------------------------------------------------------
    player_sweetener_max_additions: int = 1
    player_sweetener_candidate_pool: int = 10
    # Only consider these buckets from asset_catalog for "throw-in" players.
    player_sweetener_buckets: Tuple[str, ...] = (
        "FILLER_CHEAP",
        "EXPIRING",
        "SURPLUS_LOW_FIT",
        "SURPLUS_REDUNDANT",
    )

    # Avoid proposing "major" players as sweeteners unless absolutely necessary.
    player_sweetener_market_cap: float = 18.0

    # ------------------------------------------------------------------
    # Dealgen config base tweaks
    # ------------------------------------------------------------------
    # Keep counter deals simple.
    max_assets_per_side: int = 6
    max_players_per_side: int = 2
    max_players_moved_total: int = 4
    max_picks_per_side: int = 3

    def to_dealgen_config(self):
        """Build a DealGeneratorConfig instance for using dealgen utilities.

        We intentionally reuse DealGeneratorConfig as SSOT for:
        - sweetener loop logic
        - fit-swap counter logic
        - scoring / discard heuristics

        The returned config is a *small override* over DealGeneratorConfig defaults.
        """

        from trades.generation.dealgen.types import DealGeneratorConfig

        base = DealGeneratorConfig()
        # Override only what we need.
        cfg = replace(
            base,
            max_assets_per_side=int(self.max_assets_per_side),
            max_players_per_side=int(self.max_players_per_side),
            max_players_moved_total=int(self.max_players_moved_total),
            max_picks_per_side=int(self.max_picks_per_side),

            sweetener_enabled=bool(self.enable_pick_sweeteners),
            sweetener_max_additions=int(self.sweetener_max_additions),
            sweetener_candidate_width=int(self.sweetener_candidate_width),

            fit_swap_enabled=bool(self.enable_fit_swap),
            fit_swap_candidate_pool=int(self.fit_swap_candidate_pool),
            fit_swap_try_top_n=int(self.fit_swap_try_top_n),
            fit_swap_max_repairs=int(self.fit_swap_max_repairs),
        )
        return cfg

    def to_dealgen_budget(
        self,
        *,
        max_validations: Optional[int] = None,
        max_evaluations: Optional[int] = None,
        max_repairs: Optional[int] = None,
    ):
        """Build a DealGeneratorBudget for utilities that require one.

        dealgen.sweetener / dealgen.fit_swap only use a subset of the budget fields,
        but we keep the struct complete for consistency.
        """

        from trades.generation.dealgen.types import DealGeneratorBudget

        return DealGeneratorBudget(
            max_targets=1,
            beam_width=1,
            max_attempts_per_target=1,
            max_validations=int(max_validations if max_validations is not None else self.max_validations),
            max_evaluations=int(max_evaluations if max_evaluations is not None else self.max_evaluations),
            max_repairs=int(max_repairs if max_repairs is not None else self.max_repairs),
        )
