# 스켈레톤 점수 문법(SSOT) 구현 상세 설계안

이 문서는 **바로 패치 가능한 수준**으로, 8-tier 점수표 + contract tag 가감 + tier별 `PLAYER_HEAVY / PICK_HEAVY / MIXED` 스켈레톤 구조를 구현하기 위한 상세 작업안을 정의한다.

> 목표: 스켈레톤 단계에서 최소 거래 문법(점수표)을 강제한 초안을 생성하고, 후단 평가/협상 로직은 별도로 유지한다.

---

## 1) 구현 범위 / 비범위

## 구현 범위
- 점수표 SSOT 모듈 신설 (tier 점수, pick 점수, contract 가감).
- tier별 빌더 모듈 8개 신설 (`mvp/all_nba/all_star/high_starter/starter/high_rotation/rotation/garbage`).
- `DealGeneratorConfig`에 contract route 설정 필드 보강.
- `build_default_registry()`에 새 스펙 일괄 등록.
- 라우팅 ID / 스펙 ID 명명 일관화(소문자 권장).
- 문법 강제는 **builder 내부 점수 충족 로직**으로 우선 구현.

## 비범위(이번 단계)
- 최종 score gate(수리/수정 이후 재검증)는 **선택사항**으로 문서화만 하고, 이번 패치에서는 기본 비활성.

---

## 2) 점수표 SSOT 파일 설계

## 파일 경로
- `trades/generation/dealgen/skeleton_score_ssot.py`

## 핵심 상수

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Literal

Tier = Literal[
    "MVP", "ALL_NBA", "ALL_STAR", "HIGH_STARTER",
    "STARTER", "HIGH_ROTATION", "ROTATION", "GARBAGE",
]
ContractTag = Literal["OVERPAY", "FAIR", "VALUE"]

TIER_POINTS: Dict[str, float] = {
    "MVP": 26.0,
    "ALL_NBA": 18.0,
    "ALL_STAR": 12.0,
    "HIGH_STARTER": 8.0,
    "STARTER": 4.0,
    "HIGH_ROTATION": 2.0,
    "ROTATION": 1.0,
    "GARBAGE": 0.25,  # 최소 토큰(선택): 없으면 0.0으로 두어도 됨
}

PICK_POINTS: Dict[str, float] = {
    "FIRST": 4.0,
    "SECOND": 0.5,
}

CONTRACT_TAG_BONUS: Dict[str, float] = {
    "OVERPAY": -1.0,
    "FAIR": 0.0,
    "VALUE": +1.0,
}

SCORE_TOLERANCE: float = 0.5
```

> `GARBAGE`는 질문에서 점수가 직접 제시되지 않았으므로, 문법상 완전 0점으로 방치할지(0.0) 소량 토큰(0.25)으로 둘지 선택해야 한다. 운영상 “노이즈 자산 추가”를 막고 싶으면 0.0 권장.

## 유틸 함수(필수)
- `normalize_tier(tier: str) -> str`
- `normalize_contract_tag(tag: str) -> str`
- `target_required_score(tier: str, contract_tag: str) -> float`
  - `required = TIER_POINTS[tier] + CONTRACT_TAG_BONUS[tag]`
  - `required = max(0.0, required)`
- `asset_points_for_pick(round_no: int) -> float`
  - `1라운드=4.0`, `2라운드=0.5`, 그 외 0.
- `is_score_satisfied(offered: float, required: float, tolerance: float = SCORE_TOLERANCE) -> bool`

## 데이터 모델(권장)

```python
@dataclass(frozen=True, slots=True)
class ScoreTarget:
    tier: str
    contract_tag: str
    required_score: float
    tolerance: float = SCORE_TOLERANCE
```

---

## 3) contract route 설정 필드 보강

## 변경 파일
- `trades/generation/dealgen/types.py` (`DealGeneratorConfig`)

## 추가 필드

```python
skeleton_route_contract_overpay: Tuple[str, ...] = tuple()
skeleton_route_contract_fair: Tuple[str, ...] = tuple()
skeleton_route_contract_value: Tuple[str, ...] = tuple()
```

## 적용 방식
- `skeleton_registry.py`는 이미 위 3필드를 읽도록 구현되어 있으므로(현재 `getattr`), 타입 정의만 보강하면 된다.
- 기본값은 `tuple()`로 두어 기존 동작을 깨지 않는다.
- 운영 튜닝 시에는 예시처럼 설정 가능:
  - `overpay`: `("mvp.pick_heavy", "all_nba.pick_heavy")`
  - `value`: `("mvp.player_heavy", "all_nba.player_heavy", "all_star.mixed")`

---

## 4) tier별 skeleton_builders_* 파일 구조 설계

## 신규 파일(8개)
- `trades/generation/dealgen/skeleton_builders_mvp.py`
- `trades/generation/dealgen/skeleton_builders_all_nba.py`
- `trades/generation/dealgen/skeleton_builders_all_star.py`
- `trades/generation/dealgen/skeleton_builders_high_starter.py`
- `trades/generation/dealgen/skeleton_builders_starter.py`
- `trades/generation/dealgen/skeleton_builders_high_rotation.py`
- `trades/generation/dealgen/skeleton_builders_rotation.py`
- `trades/generation/dealgen/skeleton_builders_garbage.py`

## 각 파일 공통 함수
- `build_<tier>_player_heavy(ctx: BuildContext) -> List[DealCandidate]`
- `build_<tier>_pick_heavy(ctx: BuildContext) -> List[DealCandidate]`
- `build_<tier>_mixed(ctx: BuildContext) -> List[DealCandidate]`

> `garbage` tier는 정책상 1~2개만 둘 수 있다. 현재 route가 `garbage.garbage` 단일 ID이므로,
> - 옵션 A: `build_garbage_garbage` 단일 함수
> - 옵션 B: 3개 유지 + route를 3개로 확장
> 둘 중 하나를 고른다. **기존 호환성 우선이면 옵션 A 권장.**

## 각 builder 내부 알고리즘(공통 골격)
1. focal 자산 식별 (`ctx.target` 또는 `ctx.sale_asset`).
2. `required = target_required_score(tier, contract_tag)` 계산.
3. 후보 자산 풀 준비:
   - 선수 후보: 기존 `_split_young_candidates`, `_pick_return_player_salaryish_with_need` 재사용.
   - 픽 후보: `_add_pick_package` 또는 신규 score-aware pick selector 사용.
4. archetype별 채우기 전략
   - `player_heavy`: 선수 점수로 70~90% 먼저 채우고 픽으로 미세조정.
   - `pick_heavy`: 픽으로 70~90% 먼저 채우고 선수로 보정.
   - `mixed`: 선수/픽 비중 40~60 범위로 균형.
5. `offered_score >= required - tolerance` 만족 시 candidate 생성.
6. 실패 시 빈 리스트 반환(문법 미충족 초안 방지).

## 점수 계산 구현 상세
- 선수 점수는 빌더에서 해당 플레이어 tier를 재분류(`classify_target_profile`)하여 `TIER_POINTS` 매핑 사용.
- 픽 점수는 라운드 기준으로 `FIRST=4`, `SECOND=0.5`.
- contract_tag 가감은 **focal 타깃 required score** 계산에만 적용(반환 자산 개별 보정은 하지 않음).

## 성능 보호 장치
- 빌더당 최대 후보 생성 수: `1~2`개.
- 점수 충족 탐색 시 최대 반복/조합 수 상한 설정(예: 12회).
- 실패 즉시 종료(무한 조합 방지).

---

## 5) default registry 스펙 확장

## 변경 파일
- `trades/generation/dealgen/skeleton_registry.py`

## import 추가
- 위 8개 builder 파일에서 필요한 함수 import.

## 스펙 ID 규칙
- 소문자 통일 권장:
  - `mvp.player_heavy`, `mvp.pick_heavy`, `mvp.mixed`
  - `all_nba.player_heavy`, ...

> 현재 `DealGeneratorConfig`에 `"MVP.player_heavy"`처럼 대문자 ID가 일부 존재한다. 실제 registry ID와 일치해야 라우팅이 동작하므로, **config route 문자열도 동일 규칙으로 통일**한다.

## tier별 스펙 등록 예시

```python
SkeletonSpec(
    skeleton_id="mvp.player_heavy",
    domain="tier_score",
    compat_archetype="player_heavy",
    mode_allow=("BUY", "SELL"),
    target_tiers=("MVP",),
    priority=20,
    build_fn=build_mvp_player_heavy,
    contract_tags=("OVERPAY", "FAIR", "VALUE"),
)
```

- 동일 구조로 각 tier x archetype 등록.
- `timeline.*` 스펙은 하위 우선순위(예: 60+) 유지하여 fallback로 남긴다.

---

## 6) config tier route 문자열 정합성 수정

## 변경 파일
- `trades/generation/dealgen/types.py`

## 수정 포인트
- 현재 route 기본값의 ID 대소문자/실재 스펙 불일치 가능성 정리.
- 예시:
  - `"MVP.player_heavy"` → `"mvp.player_heavy"`
  - `"MVP.pick_heavy"` → `"mvp.pick_heavy"`
  - `"MVP.mixed"` → `"mvp.mixed"`

> 이 정합성 수정은 신규 registry 스펙 등록과 **같은 커밋**으로 묶어야 안전하다.

---

## 7) 테스트 설계 (바로 작성 가능한 수준)

## 파일 추가
- `trades/generation/dealgen/test_skeleton_score_ssot.py`
- `trades/generation/dealgen/test_skeleton_registry_contract_routes.py`
- `trades/generation/dealgen/test_skeleton_builders_tier_score_gate.py`

## 테스트 케이스
1. SSOT 점수 검증
- MVP=26, ALL_NBA=18 ... ROTATION=1
- FIRST=4, SECOND=0.5
- OVERPAY=-1, FAIR=0, VALUE=+1

2. required score 계산
- `MVP + FAIR = 26`
- `MVP + OVERPAY = 25`
- `MVP + VALUE = 27`

3. route 필터
- `skeleton_route_contract_value` 설정 시 value 태그에서 해당 스펙만 노출.

4. builder 문법 게이트
- 점수 미달 조합은 빈 결과.
- 점수 충족 조합은 후보 생성.

5. 회귀
- 기존 timeline fallback 스펙이 route 비어 있을 때 여전히 동작.

---

## 8) 단계별 패치 순서 (권장)

1. `skeleton_score_ssot.py` 생성 + 단위 테스트 작성.
2. `DealGeneratorConfig` contract route 필드 추가.
3. tier builder 8개 파일 생성(처음엔 최소 구현, 각 1개 candidate).
4. `skeleton_registry.py` import/spec 확장.
5. `types.py` route 기본 문자열 정합성 보정.
6. registry/빌더 테스트 추가.
7. `python -m unittest`로 관련 테스트 검증.

---

## 9) 선택사항: 최종 score gate(후속)

이번 작업에서는 기본 비활성로 두고, 후속에서 아래 중 하나로 활성화한다.

- 옵션 1: `repair_until_valid` 직후 score gate 재검증.
- 옵션 2: `core.py`의 최종 proposal append 직전 score gate 재검증.

권장 플래그:

```python
skeleton_score_final_gate_enabled: bool = False
```

---

## 10) 이 설계대로 수정 완료 시 최종 상태

이 문서대로 패치를 진행하면 최종적으로 다음 상태가 된다.

1. 스켈레톤 생성은 8-tier 점수표를 SSOT로 사용한다.
2. contract_tag(OVERPAY/FAIR/VALUE)는 타깃 required score에 -1/0/+1로 반영된다.
3. 각 tier는 `player_heavy / pick_heavy / mixed` 형태로 생성되며, 점수 미달 조합은 생성되지 않는다.
4. registry는 tier + contract route를 동시에 지원하며, 운영 설정으로 라우팅 전략을 세밀하게 바꿀 수 있다.
5. timeline 등 기존 스켈레톤은 fallback으로 유지되어 과도한 회귀 위험을 줄인다.
6. 최종 score gate는 후속 선택사항으로 남겨, 현재 목표(스켈레톤 단계 문법 강제)를 먼저 안정적으로 달성한다.

