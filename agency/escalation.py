from __future__ import annotations

"""Escalation helpers for agency issues.

Purpose
-------
Tick v2 needs consistent escalation behavior across multiple "issue axes".
This module keeps the stage math small and reusable.

Stages
------
- 0: NONE (no active escalation)
- 1: PRIVATE (player/coach private concern)
- 2: AGENT (agent pressure)
- 3: PUBLIC (media / public pressure)

Trade requests are handled separately (existing trade_request_level).
"""

from typing import Any, Dict, Mapping

from .utils import clamp01, safe_int


STAGE_NONE = 0
STAGE_PRIVATE = 1
STAGE_AGENT = 2
STAGE_PUBLIC = 3


def stage_label(stage: Any) -> str:
    s = safe_int(stage, 0)
    if s <= 0:
        return "NONE"
    if s == 1:
        return "PRIVATE"
    if s == 2:
        return "AGENT"
    return "PUBLIC"


# ---------------------------------------------------------------------------
# Stage codec helpers
# ---------------------------------------------------------------------------


_STAGE_LABEL_TO_I = {
    "NONE": STAGE_NONE,
    "PRIVATE": STAGE_PRIVATE,
    "AGENT": STAGE_AGENT,
    "PUBLIC": STAGE_PUBLIC,
}


def stage_index(stage: Any, default: int = STAGE_PRIVATE) -> int:
    """Convert a stage representation into a canonical int in [0..3].

    This is a defensive helper that makes event payload parsing robust.

    Accepted inputs:
    - int/float-like: 0..3
    - numeric strings: "1", "2", "3" (also accepts "1.0")
    - label strings: "NONE"/"PRIVATE"/"AGENT"/"PUBLIC" (case-insensitive)

    Unknown or missing values fall back to `default`.
    """

    # Missing
    if stage is None:
        s = safe_int(default, STAGE_PRIVATE)
    # Strings can be labels or numeric.
    elif isinstance(stage, str):
        t = stage.strip().upper()
        if not t:
            s = safe_int(default, STAGE_PRIVATE)
        elif t in _STAGE_LABEL_TO_I:
            s = int(_STAGE_LABEL_TO_I[t])
        else:
            # numeric-ish string? ("2", "2.0")
            try:
                s = int(float(t))
            except Exception:
                s = safe_int(default, STAGE_PRIVATE)
    else:
        s = safe_int(stage, safe_int(default, STAGE_PRIVATE))

    # Clamp
    if s < STAGE_NONE:
        return STAGE_NONE
    if s > STAGE_PUBLIC:
        return STAGE_PUBLIC
    return int(s)


def stage_i_from_payload(payload: Any, default: int = STAGE_PRIVATE) -> int:
    """Read stage index from an event payload (backward-compatible).

    Priority:
      1) payload["stage_i"]  (canonical)
      2) payload["stage"]    (legacy: may be label or int)
      3) default

    This helper exists because older saves/events may have stored stage as a label
    ("PRIVATE") while other producers stored it as an int.
    """

    if not isinstance(payload, Mapping):
        return stage_index(None, default=default)
    if "stage_i" in payload:
        return stage_index(payload.get("stage_i"), default=default)
    return stage_index(payload.get("stage"), default=default)


def stage_fields(stage: Any) -> Dict[str, Any]:
    """Return a (stage_i, stage) field bundle for event payloads."""

    si = stage_index(stage, default=STAGE_NONE)
    return {"stage_i": int(si), "stage": stage_label(si)}


def desired_stage(*, frustration: float, threshold: float, delta_2: float, delta_3: float) -> int:
    """Map a frustration score into a desired stage (1..3)."""
    fr = float(clamp01(frustration))
    th = float(clamp01(threshold))
    d2 = max(0.0, float(delta_2))
    d3 = max(d2, float(delta_3))

    if fr < th:
        return STAGE_NONE
    if fr >= th + d3:
        return STAGE_PUBLIC
    if fr >= th + d2:
        return STAGE_AGENT
    return STAGE_PRIVATE


def advance_stage(prev_stage: Any, *, desired: int, max_stage: int = STAGE_PUBLIC) -> int:
    """Advance at most +1 step toward desired stage."""
    ps = safe_int(prev_stage, 0)
    ds = safe_int(desired, 0)
    ms = safe_int(max_stage, STAGE_PUBLIC)
    if ms <= 0:
        ms = STAGE_PUBLIC

    if ds <= STAGE_NONE:
        return STAGE_NONE

    if ps <= STAGE_NONE:
        return STAGE_PRIVATE

    if ds > ps:
        return int(min(ps + 1, ds, ms))

    return int(min(max(ps, STAGE_PRIVATE), ms))


def decay_stage(prev_stage: Any, *, frustration: float, threshold: float, decay_ratio: float = 0.55) -> int:
    """Decay stage slowly when frustration is no longer high.

    - If frustration < threshold*decay_ratio: stage steps down by 1.
    - If frustration >= threshold: keep.
    """
    ps = safe_int(prev_stage, 0)
    if ps <= 0:
        return 0

    fr = float(clamp01(frustration))
    th = float(clamp01(threshold))
    if th <= 0.0:
        return 0

    if fr >= th:
        return int(ps)

    if fr < (float(decay_ratio) * th):
        return int(max(ps - 1, 0))

    return int(ps)
