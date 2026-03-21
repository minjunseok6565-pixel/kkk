# Preset Defense 실제 적용을 위한 파일 단위 패치 계획

## 목적

이 문서는 "수비 프리셋(Preset_Defense)"을 실제 제품에 반영하기 위해 필요한 **신규 파일 추가**와 **기존 파일 수정**을 파일 단위로 정리한 실행 계획서다.

- 범위: 프런트 전술 UI, 프리셋 수비 draft/compiler/serde, 서버 tactics schema/API, 엔진 validation/quality 연동, 테스트
- 비범위: 실제 밸런스 튜닝(수치 재조정), 운영 데이터 마이그레이션

---

## 1) 신규 추가 파일

## 1-1. `static/js/features/tactics/presetDefenseDraft.js`

### 역할
- 수비 프리셋 UI 상태(draft) 기본값/정규화/유효성 관리

### 핵심 패치
- `createDefaultPresetDefenseDraft()` 추가
  - `actionBudget` (Cut, DHO, Drive, ISO, PnP, PnR, PostUp, SpotUp, TransitionEarly)
  - `actionPolicies[action] = { side: "A"|"B"|"neutral", level: "normal"|"strong" }`
  - `pressureLevel` (-2..+2)
- `sanitizePresetDefenseDraft(draft)` 추가
  - 액션 키 누락 시 기본 채움
  - budget 합/범위 보정(음수 방지, 최대치 클램프)
  - 미정의 action(Kickout/ExtraPass) 입력 무시

---

## 1-2. `static/js/features/tactics/presetDefenseCompiler.js`

### 역할
- draft → 엔진 저장 payload(`action_weight_mult`, `outcome_by_action_mult`, `outcome_global_mult`, `context.DEF_QUALITY_LABEL_OVERRIDES_V1`) 변환

### 핵심 패치
- 액션 테이블 상수화
  - 각 액션의 묶음 A/B, outcome 목록, shaping 수치(허용/강허용/억제/강억제)
  - 성공률 라벨 조정 테이블(허용/강허용 weak/tight 세트)
- `buildDefenseActionWeightMult(draft)`
  - 9개 대상 액션 budget 정규화
  - 미선택/미정의 액션은 1.0
- `buildDefenseOutcomeByActionMult(draft)`
  - 선택 묶음은 억제/강억제, 반대 묶음은 자동 허용/강허용 적용
  - 중립 선택 시 해당 액션 미기입(=1.0 유지)
  - 묶음 외 outcome 미기입
- `buildDefenseQualityOverrides(draft)`
  - `context.DEF_QUALITY_LABEL_OVERRIDES_V1 = { version: 1, actions: {...} }`
  - 허용표를 기준으로 억제 선택 시 weak↔tight 반전
  - 중립은 override 미기입
- `buildDefenseOutcomeGlobalMult(draft)`
  - pressureLevel -2..+2 매핑
  - TO: 0.85~1.15, FOUL: 0.90~1.10
- `compilePresetDefenseDraft(draft, baseTactics)`
  - 기존 payload와 merge 가능한 형태로 반환

---

## 1-3. `static/js/features/tactics/presetDefenseModal.js`

### 역할
- 전술 화면에서 수비 프리셋 입력 UI(모달)

### 핵심 패치
- 액션 버짓 입력 UI
- 액션별 선택 UI
  - 3선택: `A 억제 + B 허용 / 중립 / A 허용 + B 억제`
  - 비중립 선택 시 강도: `억제` vs `강하게 억제`
- 전역 압박 슬라이더(-2..+2)
- 적용 콜백: `onApply(nextDraft, validation)`

---

## 1-4. `static/js/features/tactics/presetDefenseSerde.js`

### 역할
- 저장 payload ↔ UI draft 직렬화

### 핵심 패치
- `defenseDraftFromSavedTactics(tactics)`
  - `context.USER_PRESET_DEFENSE_DRAFT_V1` 우선 복원
  - 없으면 default draft
- `injectDefenseDraftSnapshotToContext(draft, tactics)`
  - `context.USER_PRESET_DEFENSE_DRAFT_V1` 저장

---

## 1-5. `tests/test_preset_defense_compiler.py`

### 역할
- 컴파일 규칙 단위 테스트

### 핵심 케이스
- 중립 선택 시 outcome mult/label override 비생성
- 억제 선택 시 반대 묶음 자동 허용
- 강하게 억제 시 반대 묶음 자동 강허용
- 미정의 액션(Kickout/ExtraPass) 비개입
- pressureLevel 단계별 글로벌 mult 정확성

---

## 1-6. `tests/test_preset_defense_serde.py`

### 역할
- draft snapshot 저장/복원 테스트

### 핵심 케이스
- context snapshot roundtrip
- 누락/오염 데이터 sanitize

---

## 2) 기존 수정 파일

## 2-1. `static/js/features/tactics/tacticsScreen.js`

### 수정 목적
- Preset_Defense 선택 시 수비 프리셋 모달 노출/적용/저장 통합

### 패치 상세
1. import 추가
- `presetDefenseDraft`, `presetDefenseCompiler`, `presetDefenseModal`, `presetDefenseSerde`

2. 상태 초기화 연동
- `applyTacticsDetail()`에서 `state.presetDefenseDraft = defenseDraftFromSavedTactics(...)`

3. 버튼 가시성/오픈 핸들러
- `updatePresetDefenseButtonVisibility()` 추가
- `openPresetDefenseModal()` 추가

4. 저장 payload 합성
- `buildTacticsPayload()`에서 `defenseScheme === "Preset_Defense"`일 때
  - `compilePresetDefenseDraft(...)`
  - `mergeCompiledPresetIntoTactics`와 유사한 merge 수행
  - draft snapshot context 주입

---

## 2-2. `static/js/app/state.js`

### 수정 목적
- 앱 상태에 수비 프리셋 draft 슬롯 추가

### 패치 상세
- `presetDefenseDraft: null` 필드 추가

---

## 2-3. `static/js/app/dom.js`

### 수정 목적
- 수비 프리셋 모달/버튼 DOM 레퍼런스 연결

### 패치 상세
- 버튼: `presetDefenseOpenBtn`
- 모달 루트/입력/적용/취소 버튼 element 추가

---

## 2-4. `static/js/core/constants/tactics.js`

### 수정 목적
- UI 레이블/옵션 상수 보강

### 패치 상세
- Preset_Defense 전용 표시 레이블 보강
- 액션 목록/정책 enum 상수 분리(컴파일러와 공유 가능하게 export)

---

## 2-5. `app/schemas/tactics.py`

### 수정 목적
- context typed key에 수비 draft snapshot 키 명시

### 패치 상세
- `TacticsContextModel`에 `USER_PRESET_DEFENSE_DRAFT_V1` 추가(옵셔널)
- extra allow 유지(하위호환)

---

## 2-6. `app/api/routes/tactics.py`

### 수정 목적
- 저장/조회 시 수비 draft snapshot 유지

### 패치 상세
- `_to_ui_tactics()` 반환값에 `presetDefenseDraft` 추가
  - `context.USER_PRESET_DEFENSE_DRAFT_V1` 파싱
- `_to_engine_tactics()`는 기존 유연 구조 유지
  - context sanitize 경로에 snapshot 키 pass-through 확인

---

## 2-7. `matchengine_v3/validation.py`

### 수정 목적
- 수비 오버라이드 payload 검증 강화

### 패치 상세
- 기존 `DEF_QUALITY_LABEL_OVERRIDES_V1` 검증은 유지
- 허용 action/outcome whitelist 최신화 확인
  - 미정의 action 키는 drop + warn
  - label은 weak/tight/neutral 외 거부

---

## 2-8. `tests/test_tactics_api.py`

### 수정 목적
- API roundtrip에서 수비 snapshot/override가 보존되는지 검증

### 패치 상세
- `Preset_Defense` payload put/get 시
  - `context.USER_PRESET_DEFENSE_DRAFT_V1`
  - `context.DEF_QUALITY_LABEL_OVERRIDES_V1`
  보존 assert 추가

---

## 2-9. `tests/test_quality_context_overrides.py`

### 수정 목적
- 프리셋 수비 오버라이드 규칙 회귀 방지

### 패치 상세
- 억제 반전(weak↔tight) 케이스 추가
- 중립 선택 시 override 미적용 케이스 추가

---

## 3) 미정의 액션/미정의 outcome 처리 정책(구현 명세)

- 미정의 액션: `Kickout`, `ExtraPass`
  - v1에서 컴파일 대상 제외
  - `action_weight_mult`/`outcome_by_action_mult`/`override.actions`에 미생성
- 미정의 outcome
  - 명시적 패치 미생성(기본 1.0 + neutral fallback 사용)

즉, "아무것도 쓰지 않으면 기본 동작" 원칙으로 처리한다.

---

## 4) 권장 적용 순서 (작업 순서)

1. Draft/Compiler/Serde(수비) 신규 파일 생성
2. tacticsScreen/state/dom 연동
3. API schema/routes 보강
4. validation/tests 보강
5. 수동 시나리오 점검
   - Preset_Defense 선택 → 모달 적용 → 저장 → 재진입 복원

---

## 5) 완료 기준(Definition of Done)

- Preset_Defense에서 수비 프리셋 모달로 액션 버짓/묶음 선택/압박 슬라이더 적용 가능
- 저장 후 재조회 시 동일 draft 복원
- 엔진 입력에 아래 4개가 의도대로 반영
  - `action_weight_mult`
  - `outcome_by_action_mult`
  - `outcome_global_mult`
  - `context.DEF_QUALITY_LABEL_OVERRIDES_V1`
- 미정의 액션/미정의 outcome은 중립(비개입) 유지
- 테스트 통과
