"""Defense scheme meta tuning tables.

Separated from era.py so tuning can happen without touching runtime config logic.
"""
from __future__ import annotations

DEFENSE_META_PARAMS = {
    "defense_meta_strength": 0.45,
    "defense_meta_clamp_lo": 0.80,
    "defense_meta_clamp_hi": 1.20,
    "defense_meta_temperature": 1.10,
    "defense_meta_floor": 0.03,
    "defense_meta_action_mult_tables": {
        "Drop": {
            "PnR": 0.92,
            "Drive": 0.95,
            "PostUp": 1.05,
            "HornsSet": 1.02,
            "Cut": 1.03,
            "Kickout": 1.02,
            "ExtraPass": 1.02,
        },
        "Switch_Everything": {
            "PnR": 0.85,
            "DHO": 0.92,
            "Drive": 0.95,
            "PostUp": 1.10,
            "Cut": 1.08,
            "SpotUp": 1.02,
            "HornsSet": 1.05,
            "ExtraPass": 1.02,
        },
        "Switch_1_4": {
            # 1-4 switch: still discourages simple PnR/DHO triggers, but less extreme than all-switch.
            # Allows more drive attempts vs a backline anchor, and slightly increases post probing.
            "PnR": 0.88,
            "DHO": 0.95,
            "Drive": 0.97,
            "PostUp": 1.07,
            "Cut": 1.05,
            "SpotUp": 1.02,
            "HornsSet": 1.04,
            "ExtraPass": 1.02,
        },
        "Hedge_ShowRecover": {
            "PnR": 0.90,
            "Drive": 0.92,
            "Kickout": 1.05,
            "ExtraPass": 1.05,
            "SpotUp": 1.04,
            "DHO": 0.95,
        },
        "AtTheLevel": {
            "PnR": 0.89,
            "DHO": 0.92,
            "Drive": 0.92,
            "Kickout": 1.02,
            "ExtraPass": 1.02,
            "SpotUp": 1.03,
            "Cut": 1.02,
            "HornsSet": 1.02,
        },
        "Blitz_TrapPnR": {
            "PnR": 0.82,
            "Drive": 0.90,
            "ExtraPass": 1.08,
            "Kickout": 1.08,
            "SpotUp": 1.06,
            "Cut": 1.03,
            "HornsSet": 1.02,
        },
        "Zone": {
            "Drive": 0.88,
            "PostUp": 0.90,
            "SpotUp": 1.02,
            "ExtraPass": 1.03,
            "Kickout": 1.02,
            "DHO": 0.95,
            "Cut": 0.94,
            "HornsSet": 1.02,
        },
        "Preset_Defense": {
            # Neutral-safe scaffold for preset overlays.
            "PnR": 1.00,
            "DHO": 1.00,
            "Drive": 1.00,
            "PostUp": 1.00,
            "Cut": 1.00,
            "SpotUp": 1.00,
            "HornsSet": 1.00,
            "Kickout": 1.00,
            "ExtraPass": 1.00,
        },
    },
    "defense_meta_priors_rules": {
        "Drop": [
            {"key": "SHOT_MID_PU", "mult": 1.08},
            {"key": "SHOT_3_OD", "mult": 1.03},
            {"key": "SHOT_RIM_LAYUP", "mult": 0.96},
            {"key": "SHOT_RIM_DUNK", "mult": 0.96},
            {"key": "SHOT_RIM_CONTACT", "mult": 0.96},
        ],
        "Hedge_ShowRecover": [
            {"key": "PASS_KICKOUT", "mult": 1.06},
            {"key": "PASS_EXTRA", "mult": 1.05},
        ],
        "AtTheLevel": [
            {"key": "PASS_SHORTROLL", "min": 0.12, "require_base_action": "PnR"},
            {"key": "PASS_KICKOUT", "mult": 1.06, "require_base_action": "PnR"},
            {"key": "SHOT_3_OD", "mult": 0.94, "require_base_action": "PnR"},
            {"key": "SHOT_MID_PU", "mult": 0.94, "require_base_action": "PnR"},
            {"key": "TO_HANDLE_LOSS", "mult": 1.06, "require_base_action": "PnR"},
        ],
        "Blitz_TrapPnR": [
            {"key": "PASS_SHORTROLL", "min": 0.10, "require_base_action": "PnR"},
        ],
        "Zone": [
            {"key": "SHOT_3_CS", "mult": 1.02},
            {"key": "PASS_EXTRA", "mult": 1.02},
        ],
        "Switch_Everything": [
            {"key": "SHOT_POST", "mult": 1.08},
            {"key": "TO_HANDLE_LOSS", "mult": 1.04},
        ],
        "Switch_1_4": [
            # 1-4 switch tends to invite some post probing (wings) and creates mild handle pressure.
            {"key": "SHOT_POST", "mult": 1.05},
            {"key": "TO_HANDLE_LOSS", "mult": 1.02},
        ],
    },
}
