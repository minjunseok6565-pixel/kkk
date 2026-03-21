from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from matchengine_v3 import quality


_OVERRIDE_CTX = {
    "DEF_QUALITY_LABEL_OVERRIDES_V1": {
        "version": 1,
        "actions": {
            "Cut": {
                "PASS_KICKOUT": "weak",
                "SHOT_RIM_LAYUP": "tight",
            }
        },
    }
}


def test_preset_defense_uses_context_override_label() -> None:
    base = quality.get_base_quality_label("Preset_Defense", "Cut", "PASS_KICKOUT")
    assert base == "neutral"

    overridden = quality.get_base_quality_label(
        "Preset_Defense",
        "Cut",
        "PASS_KICKOUT",
        context=_OVERRIDE_CTX,
    )
    assert overridden == "weak"


def test_non_preset_scheme_ignores_override_payload() -> None:
    without_ctx = quality.get_base_quality_label("Drop", "Cut", "PASS_KICKOUT")
    with_ctx = quality.get_base_quality_label(
        "Drop",
        "Cut",
        "PASS_KICKOUT",
        context=_OVERRIDE_CTX,
    )
    assert with_ctx == without_ctx


def test_compute_quality_detail_exposes_override_source() -> None:
    detail = quality.compute_quality_score(
        "Preset_Defense",
        "Cut",
        "PASS_KICKOUT",
        role_players={},
        context=_OVERRIDE_CTX,
        return_detail=True,
    )
    assert isinstance(detail, quality.QualityDetail)
    assert detail.base_label == "weak"
    assert detail.label_source == "override"
    assert detail.override_hit is True
