# 프리셋 수비 스킴 달성도 검증 브리핑 (2026-03-23)

## 검증 범위
- 프런트엔드 Draft/Modal/Compiler/Serde
- 전술 저장 API 라우트
- 매치엔진 validation/quality 경로

---

## 1) 액션 버짓(9개 액션) 설계 반영 여부

### 판단: **달성(구현됨)**
- 대상 액션 9개(`Cut, DHO, Drive, ISO, PnP, PnR, PostUp, SpotUp, TransitionEarly`)가 고정 키로 정의되어 있고, Draft 생성 시 버짓이 100으로 정규화됩니다.
- 버짓은 컴파일 단계에서 액션 가중 배율로 변환되며(평균 대비 비율), 상/하한 clamp(0.85~1.15)가 적용됩니다.

근거 파일:
- `static/js/features/tactics/presetDefenseDraft.js`
- `static/js/features/tactics/presetDefenseCompiler.js`

---

## 2) 액션별 2묶음 + 3선택지 + 비기본 강도(억제/강억제) 규칙

### 판단: **달성(구현됨)**
- UI에서 각 액션마다
  - `묶음 A 억제 + 묶음 B 허용`
  - `중립`
  - `묶음 A 허용 + 묶음 B 억제`
  의 3선택지가 제공됩니다.
- 중립이 아니면 강도 선택(`억제`/`강하게 억제`)이 노출되고,
  내부적으로는 선택된 쪽을 suppress(또는 strongSuppress), 반대쪽을 allow(또는 strongAllow)로 자동 대응시킵니다.
- 즉, 요청하신 “억제 선택 시 반대 묶음 허용, 강억제 선택 시 반대 묶음 강허용” 규칙이 컴파일러 로직으로 구현되어 있습니다.

근거 파일:
- `static/js/features/tactics/presetDefenseModal.js`
- `static/js/features/tactics/presetDefenseCompiler.js`

---

## 3) Outcome shaping 수치(1.xx / 0.xx) 테이블 정합성

### 판단: **달성(구현됨, 표와 일치)**
- `PRESET_DEFENSE_ACTION_TABLE`에 각 액션별 groupA/groupB outcome 집합과
  `allow/strongAllow/suppress/strongSuppress` 수치가 정의되어 있습니다.
- 표에 제시하신 대표 패턴(예: 1.08/0.92, 1.12/0.88, strong 1.15/0.85 등)이 액션별로 반영되어 있습니다.

근거 파일:
- `static/js/features/tactics/presetDefenseCompiler.js`

---

## 4) Outcome 성공률 라벨(weak/tight/neutral) 규칙

### 판단: **달성(구현됨)**
- 액션별 허용/강허용 라벨 세트가 테이블로 정의되어 있고,
- 억제 측은 라벨 반전(`weak ↔ tight`) 로직을 통해 자동 생성됩니다.
- 오버라이드는 `context.DEF_QUALITY_LABEL_OVERRIDES_V1`로 저장되며,
  엔진에서 `Preset_Defense`일 때만 읽습니다.
- 기본(중립) 선택이면 해당 액션 오버라이드가 생성되지 않아 neutral fallback이 유지됩니다.

근거 파일:
- `static/js/features/tactics/presetDefenseCompiler.js`
- `matchengine_v3/quality.py`
- `matchengine_v3/validation.py`

---

## 5) 미정의 액션/미포함 outcome 처리

### 판단: **대체로 달성(구현 방식상 비개입)**
- 컴파일 대상 액션은 9개로 제한되어 있어 `Kickout`, `ExtraPass`는 별도 2묶음 규칙이 없습니다.
- 결과적으로 해당 액션은 프리셋 수비 컴파일에서 비개입(기본값 유지)됩니다.
- 또한 액션 내에서 지정되지 않은 outcome은 map에 쓰이지 않아 엔진 기본값(중립)이 유지됩니다.

근거 파일:
- `static/js/features/tactics/presetDefenseDraft.js`
- `static/js/features/tactics/presetDefenseCompiler.js`

---

## 6) Global Pressure 슬라이더

### 판단: **달성(구현됨, 5단계 매핑 일치)**
- `pressureLevel`이 `-2..+2` 정수로 관리됩니다.
- `TO_HANDLE_LOSS`, `TO_CHARGE`, `FOUL_DRAW_RIM/JUMPER/POST`, `FOUL_REACH_TRAP`에 대해
  요청하신 5단계 샘플값과 동일한 outcome_global_mult 매핑이 구현되어 있습니다.

근거 파일:
- `static/js/features/tactics/presetDefenseDraft.js`
- `static/js/features/tactics/presetDefenseCompiler.js`

---

## 7) 저장/복원(Serde) 경로

### 판단: **달성(구현됨)**
- Draft 스냅샷이 `context.USER_PRESET_DEFENSE_DRAFT_V1` 및 `presetDefenseDraft`로 저장됩니다.
- 저장 전술에서 Draft를 복원하는 serde 경로가 존재합니다.
- API 라우트도 `USER_PRESET_DEFENSE_DRAFT_V1` 전달/반환을 보존합니다.

근거 파일:
- `static/js/features/tactics/presetDefenseSerde.js`
- `static/js/features/tactics/tacticsScreen.js`
- `app/api/routes/tactics.py`

---

## 8) 종합 판정

### 전체 달성도: **높음 (핵심 요구 대부분 구현 완료)**
- UI 선택 모델, 수치 테이블, 라벨 오버라이드, 전역 압박, 저장/복원까지
  요청하신 흐름이 실제 코드에 거의 그대로 구현되어 있습니다.
- 현재 코드 기준으로는 “설계 → 컴파일 → 저장” 루프가 완성되어 있으며,
  `Preset_Defense` 전용 quality override까지 엔진에서 인식합니다.

## 9) 추가 확인 권고(운영 전)
- 권고 1: 실제 시뮬레이션 1~2게임에서 선택한 정책이 기대한 방향으로 통계(림 시도/점퍼 비율/턴오버/파울)에 반영되는지 계측 로그로 점검.
- 권고 2: 프런트의 액션 버짓 슬라이더 UX(한 항목 조정 시 전체 자동 재정규화 체감) 튜닝 여부 검토.
- 권고 3: `Kickout`, `ExtraPass` 2묶음 정의를 v1.1 확장 과제로 분리.
