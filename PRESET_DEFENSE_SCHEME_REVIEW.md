# 수비 프리셋 스킴 확장 계획 정합성 검토 보고서

## 1) 결론 요약

- **현재 프로젝트 구조와 정합하게 구현 가능한 계획**입니다.
- 특히 현재 코드베이스에는 이미 프리셋 수비를 위한 핵심 기반이 존재합니다.
  - 스킴 키(`Preset_Defense`)가 엔진/품질 테이블에 연결되어 있음
  - `context.DEF_QUALITY_LABEL_OVERRIDES_V1` 오버라이드 경로가 구현되어 있음
  - `action_weight_mult`, `outcome_by_action_mult`, `outcome_global_mult` 저장/전달 경로가 이미 열려 있음
- 따라서 이번 계획은 **“기존 경로에 규칙 엔진(컴파일러) + UI 입력기(모달/패널) + 직렬화(serde)”를 추가하는 형태**가 가장 안정적입니다.

---

## 2) 현재 코드베이스와의 정합성 점검

### A. 데이터 전달/저장 파이프라인

현재 전술 저장 payload에 아래 필드들이 이미 포함됩니다.

- `action_weight_mult`
- `outcome_by_action_mult`
- `outcome_global_mult`
- `context`

즉, 이번 계획의 핵심 출력물(액션 버짓, outcome shaping, 전역 압박, 품질 라벨 오버라이드)을 저장할 **API 스키마/저장 경로는 이미 충분**합니다.

### B. 프리셋 수비 전용 품질 오버라이드 경로

현재 엔진은 `Preset_Defense`일 때만 `context.DEF_QUALITY_LABEL_OVERRIDES_V1`를 읽어 `weak/tight/neutral` 라벨을 오버라이드합니다.

- 이는 사용자가 선택한 “억제/허용 강도”에 따라 특정 outcome의 성공률 성향을 바꾸려는 요구와 정확히 맞습니다.
- 스킴 기본값이 neutral이므로, "기본" 상태 처리도 자연스럽습니다.

### C. UI/프런트 구조 정합성

공격 프리셋은 이미 `draft + compiler + modal + serde` 패턴으로 동작합니다.

- 수비 프리셋도 동일한 패턴으로 추가하면 코드 일관성을 유지할 수 있습니다.
- 전술 화면(`tacticsScreen`)은 이미 `Preset_Defense` 조건 분기가 존재하므로 진입 포인트가 명확합니다.

### D. 검증(Validation) 정합성

엔진 validation 계층에서 `DEF_QUALITY_LABEL_OVERRIDES_V1`는 `Preset_Defense`일 때만 허용/정리됩니다.

- 즉, 의도하지 않은 다른 수비 스킴 오염을 막는 안전장치가 이미 존재합니다.
- 이번 확장 시에도 동일 키를 사용하면 검증 계층과 충돌하지 않습니다.

---

## 3) 제안 계획의 핵심 규칙 정리 (현재 요구사항 반영)

## 3-1. 액션 버짓

대상 액션:

- `Cut, DHO, Drive, ISO, PnP, PnR, PostUp, SpotUp, TransitionEarly`

권장 컴파일 결과:

- 각 액션 버짓을 정규화하여 `action_weight_mult[action]` 산출
- 버짓 미선택 액션은 기본값(1.0000) 유지
- 미대상 액션(`Kickout`, `ExtraPass`)은 기본값(1.0000) 유지

## 3-2. 액션별 2묶음-3선택 구조

사용자 선택:

1. 묶음 A 억제 + 묶음 B 허용
2. 중립
3. 묶음 A 허용 + 묶음 B 억제

비기본 선택 시 강도 선택:

- 억제 / 강하게 억제 (허용 측 자동 역매핑: 허용 / 강하게 허용)

컴파일 규칙:

- 선택된 묶음의 outcome shaping에 표 정의값 적용
- 반대 묶음은 자동 대응값 적용
- 묶음 밖 outcome은 1.0000 유지
- 기본 선택이면 해당 액션 전 outcome shaping = 1.0000

## 3-3. 성공률(quality label) 조정

요구사항에 맞춰 아래처럼 정리:

- 기본: 모든 outcome `neutral`
- 허용/강하게 허용 시: 표에 정의된 weak/tight 세트만 오버라이드
- 억제/강하게 억제는 허용의 반대 해석으로 자동 생성
  - 권장 변환: 허용의 `weak` ↔ 억제의 `tight`, 허용의 `tight` ↔ 억제의 `weak`

> 주의: 현재 엔진이 지원하는 라벨은 `weak/tight/neutral`입니다. (`tough/wide_open` 같은 확장은 본 범위 밖)

## 3-4. 전역 압박 슬라이더

요구 정의와 정합하게 `outcome_global_mult`에 반영:

- TO: `TO_HANDLE_LOSS`, `TO_CHARGE` (0.85~1.15)
- FOUL: `FOUL_DRAW_RIM`, `FOUL_DRAW_JUMPER`, `FOUL_DRAW_POST`, `FOUL_REACH_TRAP` (0.90~1.10)
- 단계형(-2~+2) 또는 연속 슬라이더 모두 가능하나, 단계값은 제공된 샘플 매핑을 그대로 우선 적용 권장

---

## 4) 미정의 액션/미정의 outcome 처리안

## 4-1. 미정의 액션: `Kickout`, `ExtraPass`

권장 정책(안정 우선):

- **Preset_Defense v1에서는 2묶음 규칙 미적용**
- `action_weight_mult`에서 별도 조정하지 않음(=1.0000)
- `outcome_by_action_mult.Kickout/ExtraPass` 미생성
- `DEF_QUALITY_LABEL_OVERRIDES_V1.actions`에도 해당 액션 키 미생성

효과:

- 현재 의도와 일치(“정의되지 않았으므로 중립/미지정”) 
- 추후 v1.1에서 해당 액션만 안전하게 확장 가능

## 4-2. 액션 내 미정의 outcome

정책:

- 표에 언급되지 않은 outcome은 기본 중립 유지
  - shaping: 1.0000
  - success label: `neutral`
- 구현상으로는 "명시적 지정하지 않음"이 가장 안전
  - 엔진 기본 neutral fallback + 기본 multiplier(1.0) 경로 활용

이 방식은 실수로 다른 outcome을 과도 보정하는 리스크를 줄입니다.

---

## 5) 구현 시 주의 포인트 (사전 리스크)

1. **역매핑 규칙 일관성**
   - 허용 기준 weak/tight를 억제에서 반전할 때 액션별 예외를 만들지 말고 공통 함수로 처리

2. **중복 오버라이드 충돌**
   - 동일 outcome이 여러 규칙에서 덮어써질 수 있으므로 병합 우선순위 명확화 필요
   - 권장: `기본(중립) → 액션별 오버라이드 → 전역 오버라이드(있다면)`

3. **UI 입력과 엔진 값의 단위 분리**
   - UI는 선택지 enum(중립/허용/억제/강하게허용/강하게억제)로 관리
   - 실제 수치 변환은 compiler에서만 수행

4. **검증/테스트 포인트**
   - 기본 선택 시 완전 neutral
   - 억제 선택 시 반대 묶음 자동 허용
   - 강하게 억제 선택 시 반대 묶음 자동 강하게 허용
   - 미정의 액션/미정의 outcome 비개입 보장

---

## 6) 권장 산출 구조 (v1)

- `presetDefenseDraft` (UI 상태)
  - action budget
  - 액션별 묶음 선택 + 강도
  - global pressure slider
- `compilePresetDefenseDraft(draft)` 결과
  - `action_weight_mult`
  - `outcome_by_action_mult`
  - `outcome_global_mult`
  - `context.DEF_QUALITY_LABEL_OVERRIDES_V1`
  - (선택) `context.USER_PRESET_DEFENSE_DRAFT_V1` 스냅샷

---

## 7) 최종 평가

- 요청하신 최종 계획은 현재 코드베이스와 **높은 정합성**을 가집니다.
- 특히 이미 존재하는 `Preset_Defense` 전용 오버라이드 경로를 활용하면, 엔진 구조를 크게 건드리지 않고도 구현 가능합니다.
- 미정의 액션/미정의 outcome은 v1에서 **비개입(중립/미지정)** 원칙으로 처리하는 것이 가장 안정적이며, 향후 데이터 축적 후 확장하는 전략이 적절합니다.

