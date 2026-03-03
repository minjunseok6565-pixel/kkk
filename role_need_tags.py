from __future__ import annotations

from typing import Dict, Final, Tuple

NeedTag = str
RoleName = str
NeedLabelKo = str

"""
SSOT for mapping *offensive role keys* -> (need_tag, Korean label).

- team_situation.py: needs 산출 시 role gap -> need_tag로 변환할 때 사용
- team_utility.py: player supply(역할 기반) 태그 생성 시 사용

IMPORTANT:
- 이 모듈은 "매핑 표준"만 제공한다.
- 팀 상황(니즈 생성), 가치 평가(유틸리티/가격화) 로직은 여기로 들어오면 안 된다.

Role keys are canonical C13 names (matchengine_v3.offense_roles).
"""


ROLE_TO_NEED_TAG_AND_LABEL: Final[Dict[RoleName, Tuple[NeedTag, NeedLabelKo]]] = {
    "Engine_Primary": ("PRIMARY_INITIATOR", "프라이머리 엔진"),
    "Engine_Secondary": ("SECONDARY_CREATOR", "세컨더리 엔진"),
    "Transition_Engine": ("TRANSITION_ENGINE", "트랜지션 엔진"),
    "Shot_Creator": ("SHOT_CREATION", "샷 크리에이터"),
    "Rim_Pressure": ("RIM_PRESSURE", "림 프레셔"),
    "SpotUp_Spacer": ("SPACING", "스팟업 스페이서"),
    "Movement_Shooter": ("MOVEMENT_SHOOTING", "무브먼트 슈터"),
    "Cutter_Finisher": ("RIM_PRESSURE", "커터/피니셔"),
    "Connector": ("CONNECTOR_PLAY", "커넥터"),
    "Roll_Man": ("ROLL_THREAT", "롤맨/림런"),
    "ShortRoll_Hub": ("SHORT_ROLL_PLAY", "숏롤 허브"),
    "Pop_Threat": ("POP_BIG", "팝/스트레치 빅"),
    "Post_Anchor": ("POST_HUB", "포스트 앵커"),
}

# Convenience: role -> tag only
ROLE_TO_NEED_TAG: Final[Dict[RoleName, NeedTag]] = {
    role: tag_label[0] for role, tag_label in ROLE_TO_NEED_TAG_AND_LABEL.items()
}


def role_to_need_tag(role: str) -> Tuple[NeedTag, NeedLabelKo]:
    """Map a role key to a stable need tag + Korean label."""
    role = str(role or "")
    if role in ROLE_TO_NEED_TAG_AND_LABEL:
        return ROLE_TO_NEED_TAG_AND_LABEL[role]

    # fallback heuristic (safety)
    rl = role.lower()
    if "def" in rl:
        return ("DEFENSE", "수비 자원")
    if "shooter" in rl or "spacer" in rl:
        return ("SPACING", "슈팅")
    if "rim" in rl or "roll" in rl or "cut" in rl:
        return ("RIM_PRESSURE", "림 근처 위협")
    if "post" in rl:
        return ("POST_HUB", "포스트")
    if "engine" in rl or "creator" in rl:
        return ("SHOT_CREATION", "크리에이션")

    return ("ROLE_GAP", "역할")


def role_to_need_tag_only(role: str) -> NeedTag:
    """Convenience wrapper when you only need the tag."""
    return role_to_need_tag(role)[0]
