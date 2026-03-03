# =============================================================================
# [DATA FILE ONLY]  (자동 분리됨)
# 이 파일은 로직이 아니라 '튜닝 테이블/상수'만 담는 **데이터 모듈**입니다.
# LLM 컨텍스트에는 기본적으로 포함하지 말고, 테이블을 수정/튜닝할 때만 열어보세요.
#
# 포함 데이터(요약):
#   - ROLE_FIT_WEIGHTS: {role: {stat_key: weight}}
#   - ROLE_FIT_CUTS: {role: (s_min, a_min, b_min, c_min)}
#   - ROLE_PRIOR_MULT_RAW: {grade: {'GOOD': mult, 'BAD': mult}}
#   - ROLE_LOGIT_DELTA_RAW: {grade: delta}
#   - 로직 파일: role_fit.py
# =============================================================================

"""Data tables for role_fit.py.

This module is intentionally data-heavy.
Avoid including it in LLM context unless you are tuning role weights / cuts.

C13 role system (Modern NBA Offensive Role System v1):
  Engine_Primary, Engine_Secondary, Transition_Engine,
  Shot_Creator, Rim_Pressure,
  SpotUp_Spacer, Movement_Shooter, Cutter_Finisher,
  Connector,
  Roll_Man, ShortRoll_Hub, Pop_Threat, Post_Anchor

Note:
- These tables are keyed by *canonical* C13 names.
"""

from __future__ import annotations

from typing import Dict, Tuple

# -----------------------------
# Role prior / logit tuning
# -----------------------------
ROLE_PRIOR_MULT_RAW = {
    "S": {"GOOD": 1.06, "BAD": 0.94},
    "A": {"GOOD": 1.03, "BAD": 0.97},
    "B": {"GOOD": 1.00, "BAD": 1.00},
    "C": {"GOOD": 0.93, "BAD": 1.10},
    "D": {"GOOD": 0.85, "BAD": 1.25},
}
ROLE_LOGIT_DELTA_RAW = {"S": 0.18, "A": 0.10, "B": 0.00, "C": -0.18, "D": -0.35}


# -----------------------------
# Role fit weights (C13 roles)
# Sum of weights per role == 1.0
# -----------------------------
ROLE_FIT_WEIGHTS: Dict[str, Dict[str, float]] = {
    # Main on-ball engine (PnR/drive-kick hub)
    "Engine_Primary": {
        "PNR_READ": 0.22,
        "DRIVE_CREATE": 0.18,
        "HANDLE_SAFE": 0.16,
        "PASS_CREATE": 0.16,
        "PASS_SAFE": 0.10,
        "SHOT_3_OD": 0.08,
        "SHOT_MID_PU": 0.05,
        "FIRST_STEP": 0.05,
    },
    # Secondary handler / secondary creator
    "Engine_Secondary": {
        "SHOT_3_CS": 0.20,
        "PASS_SAFE": 0.16,
        "PASS_CREATE": 0.14,
        "PNR_READ": 0.14,
        "HANDLE_SAFE": 0.12,
        "DRIVE_CREATE": 0.10,
        "SHOT_3_OD": 0.06,
        "FIRST_STEP": 0.04,
        "SHOT_MID_PU": 0.04,
    },
    # Transition push + early offense decisions
    "Transition_Engine": {
        "FIRST_STEP": 0.18,
        "DRIVE_CREATE": 0.14,
        "HANDLE_SAFE": 0.14,
        "PASS_CREATE": 0.14,
        "PASS_SAFE": 0.12,
        "ENDURANCE": 0.12,
        "FIN_RIM": 0.10,
        "SHOT_3_CS": 0.06,
    },
    # ISO / pull-up creator
    "Shot_Creator": {
        "SHOT_3_OD": 0.24,
        "SHOT_MID_PU": 0.22,
        "HANDLE_SAFE": 0.14,
        "DRIVE_CREATE": 0.14,
        "FIRST_STEP": 0.08,
        "SHOT_FT": 0.07,
        "PNR_READ": 0.06,
        "PASS_CREATE": 0.05,
    },
    # Rim pressure / slasher
    "Rim_Pressure": {
        "DRIVE_CREATE": 0.20,
        "FIRST_STEP": 0.16,
        "FIN_RIM": 0.14,
        "FIN_CONTACT": 0.12,
        "SHOT_FT": 0.10,
        "HANDLE_SAFE": 0.10,
        "PASS_CREATE": 0.08,
        "PASS_SAFE": 0.06,
        "SHOT_TOUCH": 0.04,
    },
    # Spot-up spacer
    "SpotUp_Spacer": {
        "SHOT_3_CS": 0.45,
        "SHOT_MID_CS": 0.15,
        "PASS_SAFE": 0.10,
        "ENDURANCE": 0.10,
        "HANDLE_SAFE": 0.08,
        "FIRST_STEP": 0.05,
        "FIN_RIM": 0.04,
        "SHOT_FT": 0.03,
    },
    # Off-screen / movement shooter
    "Movement_Shooter": {
        "SHOT_3_CS": 0.34,  # fixed to make sum exactly 1.0
        "ENDURANCE": 0.18,
        "SHOT_MID_CS": 0.10,
        "FIRST_STEP": 0.08,
        "HANDLE_SAFE": 0.08,
        "PASS_SAFE": 0.08,
        "SHOT_3_OD": 0.05,
        "DRIVE_CREATE": 0.04,
        "SHOT_FT": 0.03,
        "SHOT_TOUCH": 0.02,
    },
    # Cut / dunker-spot / backdoor finisher
    "Cutter_Finisher": {
        "FIN_RIM": 0.26,
        "FIN_DUNK": 0.22,
        "FIRST_STEP": 0.16,
        "FIN_CONTACT": 0.14,
        "SHOT_TOUCH": 0.08,
        "ENDURANCE": 0.06,
        "HANDLE_SAFE": 0.04,
        "PASS_SAFE": 0.04,
    },
    # 0.5 / connector playmaker
    "Connector": {
        "PASS_SAFE": 0.28,
        "PASS_CREATE": 0.22,
        "HANDLE_SAFE": 0.14,
        "PNR_READ": 0.10,
        "SHOT_3_CS": 0.10,
        "DRIVE_CREATE": 0.06,
        "SHORTROLL_PLAY": 0.05,
        "ENDURANCE": 0.05,
    },
    # Roll man / rim-runner
    "Roll_Man": {
        "FIN_RIM": 0.20,
        "FIN_DUNK": 0.18,
        "FIN_CONTACT": 0.14,
        "PHYSICAL": 0.14,
        "REB_OR": 0.12,
        "ENDURANCE": 0.08,
        "SEAL_POWER": 0.08,
        "SHORTROLL_PLAY": 0.06,
    },
    # Short roll hub
    "ShortRoll_Hub": {
        "SHORTROLL_PLAY": 0.28,
        "PASS_SAFE": 0.18,
        "PASS_CREATE": 0.16,
        "HANDLE_SAFE": 0.10,
        "FIN_RIM": 0.10,
        "PNR_READ": 0.06,
        "SHOT_MID_CS": 0.06,
        "PHYSICAL": 0.06,
    },
    # Pop / stretch screener
    "Pop_Threat": {
        "SHOT_3_CS": 0.32,
        "SHOT_MID_CS": 0.14,
        "PASS_SAFE": 0.14,
        "SHORTROLL_PLAY": 0.10,
        "PHYSICAL": 0.10,
        "HANDLE_SAFE": 0.06,
        "PNR_READ": 0.06,
        "REB_DR": 0.05,
        "ENDURANCE": 0.03,
    },
    # Post option / inside-out anchor
    "Post_Anchor": {
        "POST_CONTROL": 0.22,
        "POST_SCORE": 0.20,
        "PASS_SAFE": 0.16,
        "PASS_CREATE": 0.14,
        "SHOT_TOUCH": 0.08,
        "SEAL_POWER": 0.07,
        "FIN_CONTACT": 0.06,
        "SHOT_MID_CS": 0.04,
        "FIN_RIM": 0.03,
    },
}


# -----------------------------
# Role fit grade cuts (S/A/B/C thresholds)
# (s_min, a_min, b_min, c_min)
# -----------------------------
ROLE_FIT_CUTS: Dict[str, Tuple[int, int, int, int]] = {
    "Engine_Primary": (80, 72, 64, 56),
    "Engine_Secondary": (78, 70, 62, 54),
    "Transition_Engine": (75, 67, 59, 51),
    "Shot_Creator": (79, 71, 63, 55),
    "Rim_Pressure": (76, 68, 60, 52),
    "SpotUp_Spacer": (80, 72, 64, 56),
    "Movement_Shooter": (80, 72, 64, 56),
    "Cutter_Finisher": (76, 68, 60, 52),
    "Connector": (78, 70, 62, 54),
    "Roll_Man": (75, 67, 59, 51),
    "ShortRoll_Hub": (78, 70, 62, 54),
    "Pop_Threat": (80, 72, 64, 56),
    "Post_Anchor": (78, 70, 62, 54),
}
