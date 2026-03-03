from __future__ import annotations

"""Salary matching bracket derivation (SSOT).

The NBA salary matching rule (below the first apron) is commonly expressed as
piecewise functions over *outgoing salary* with a "buffer" and a "mid add"
constant. To avoid boundary drift, the two outgoing-salary thresholds are
*derived* from (mid_add, buffer) rather than being stored independently.

This module provides the single SSOT function for deriving those thresholds.

Definitions (dollar integers)
-----------------------------
Let:
  - mid_add_d: the constant added in the middle bracket
  - buffer_d:  the constant buffer added in the small / large brackets

Then the outgoing salary thresholds are derived as:
  - small_out_max_d = max(0, mid_add_d - buffer_d)
  - mid_out_max_d   = small_out_max_d * 4

These choices ensure continuity:
  - 2*out + buffer == out + mid_add at out = small_out_max
  - out + mid_add  == 1.25*out + buffer at out = mid_out_max
"""

from typing import Tuple


def derive_salary_matching_brackets(
    *,
    match_mid_add_d: int,
    match_buffer_d: int,
) -> Tuple[int, int]:
    """Derive (match_small_out_max_d, match_mid_out_max_d).

    Args:
        match_mid_add_d: middle bracket constant (dollar integer).
        match_buffer_d: buffer constant (dollar integer).

    Returns:
        (match_small_out_max_d, match_mid_out_max_d)
    """

    mid_add = int(match_mid_add_d)
    buffer = int(match_buffer_d)
    if buffer < 0:
        buffer = 0

    small_out_max = max(0, int(mid_add) - int(buffer))
    mid_out_max = int(int(small_out_max) * 4)
    return int(small_out_max), int(mid_out_max)
