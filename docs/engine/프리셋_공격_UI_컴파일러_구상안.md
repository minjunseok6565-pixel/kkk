# 프리셋 공격 스킴 UI/컴파일러 구상안 (요청 반영 버전)

## 1) 목표
- `Preset_Offense`를 유저가 세부 조정 가능한 **전술 편집 UI**로 노출한다.
- 프론트 입력값을 엔진 친화 포맷으로 **컴파일/정규화**해서 저장한다.
- 입력이 과도하게 튀지 않도록 각 항목의 **상하한(clamp)** 과 **합 정규화(normalize)** 를 적용한다.
- 요청한 프론트 제약(동시 많이/적게 금지, 조합 제한)을 UI 단계에서 먼저 보장하고, 컴파일러에서 2차 안전장치를 둔다.

---

## 2) 프론트 UI 구성

### 2-1. 상위 액션 볼륨(빈도) 제어

#### A. 슬라이더 5종
- Pick&Roll
- Drive
- TransitionEarly(속공)
- ISO
- PostUp

#### B. 3단 토글 2종
- Pass 빈도: `많이/보통/적게`
- Off-ball action 빈도: `많이/보통/적게`

#### C. 하위 볼륨 슬라이더
- Pick&Roll 내부: `PnR ↔ PnP` 양방향 슬라이더 (2축 합 100)
- Off-ball 내부: `Cut`, `SpotUp`, `DHO` 3개 슬라이더 (합 100)

---

### 2-2. Action별 Outcome 성향 제어

#### PnR
- 1차(3단):
  - 핸들러 직접 공격 (Handler Direct)
  - 롤맨 패스 (PASS_SHORTROLL)
- 제약:
  - 둘 다 `많이` 금지
  - 둘 다 `적게` 금지
- 2차(슬라이더):
  - 핸들러 림 진입 내부: `SHOT_RIM_*` vs `SHOT_TOUCH_FLOATER`
  - 핸들러 풀업 내부: `SHOT_3_OD` vs `SHOT_MID_PU`

#### PnP
- PnR과 동일 구조
- 롤맨 패스 축만 `팝아웃(SHOT_3_CS)`로 치환

#### TransitionEarly
- 1차(3단):
  - 속공 핸들러 직접 공격
  - 오픈 찬스 찾기 (`SHOT_3_CS`)
- 제약:
  - 둘 다 `많이` 금지
  - 둘 다 `적게` 금지
- 2차(슬라이더, 핸들러 직접 공격 내부):
  - `SHOT_3_OD`
  - `SHOT_RIM_*`
  - `SHOT_TOUCH_FLOATER`

#### Drive
- 항목(3단):
  - 핸들러 직접 림어택 (`SHOT_RIM_*`)
  - 킥아웃 (`PASS_KICKOUT`)
  - 미드레인지 풀업 (`SHOT_MID_PU`)
- 제약:
  - `많이`가 2개 이상이면, 남은 1개는 반드시 `적게`

#### ISO
- 항목(3단):
  - 림어택 (`SHOT_RIM_*`)
  - 플로터 (`SHOT_TOUCH_FLOATER`)
  - 풀업
  - 킥아웃 (`PASS_KICKOUT`)
- 풀업 내부(슬라이더):
  - `SHOT_3_OD` vs `SHOT_MID_PU`
- 제약:
  - `많이` 2개 선택 시, 나머지 2개는 `보통 1개 + 적게 1개`만 허용

#### Cut
- 항목(3단):
  - 직접 마무리 (`SHOT_RIM_*`)
  - 패스 (`PASS_EXTRA + PASS_KICKOUT`)

#### PostUp
- 항목(3단):
  - 포스트 마무리 (`SHOT_POST`)
  - 포스트 페이더웨이 (`SHOT_MID_PU`)
  - 패스 (`PASS_*`)
- 제약:
  - Drive/ISO와 동일한 다중 `많이` 제한 규칙(조합 제약) 적용

---

### 2-3. 글로벌 슬라이더
- 파울 유도 성향
- 위험 감수 성향
- `tempo_mult`

---

## 3) 프론트 상태 모델 제안

```ts
interface PresetOffenseDraft {
  // 상위 액션 볼륨
  actionVolume: {
    pnrFamily: number;   // Pick&Roll 계열
    drive: number;
    transition: number;
    iso: number;
    postUp: number;
  };

  passFreq: 'low' | 'mid' | 'high';
  offballFreq: 'low' | 'mid' | 'high';

  // 하위 볼륨
  pnrSplit: { pnr: number; pnp: number };           // sum=100
  offballSplit: { cut: number; spotUp: number; dho: number }; // sum=100

  // 액션별 outcome 성향(3단 + 하위 슬라이더)
  outcomes: {
    pnr: {
      handlerDirect: 'low'|'mid'|'high';
      rollPass: 'low'|'mid'|'high';
      rimVsFloater: { rim: number; floater: number }; // sum=100
      pullupSplit: { pull3: number; pull2: number };  // sum=100
    };
    pnp: {
      handlerDirect: 'low'|'mid'|'high';
      popOut: 'low'|'mid'|'high';
      rimVsFloater: { rim: number; floater: number }; // sum=100
      pullupSplit: { pull3: number; pull2: number };  // sum=100
    };
    transitionEarly: {
      handlerDirect: 'low'|'mid'|'high';
      openChance3: 'low'|'mid'|'high';
      directSplit: { trans3: number; rim: number; floater: number }; // sum=100
    };
    drive: {
      rim: 'low'|'mid'|'high';
      kickout: 'low'|'mid'|'high';
      pull2: 'low'|'mid'|'high';
    };
    iso: {
      rim: 'low'|'mid'|'high';
      floater: 'low'|'mid'|'high';
      pullup: 'low'|'mid'|'high';
      kickout: 'low'|'mid'|'high';
      pullupSplit: { pull3: number; pull2: number }; // sum=100
    };
    cut: {
      finish: 'low'|'mid'|'high';
      pass: 'low'|'mid'|'high';
    };
    postUp: {
      postFinish: 'low'|'mid'|'high';
      postFadeway: 'low'|'mid'|'high';
      pass: 'low'|'mid'|'high';
    };
  };

  foulDraw: number;   // 0~100
  riskTaking: number; // 0~100
  tempo: number;      // 0~100 (UI), compile 시 tempo_mult로 변환
}
```

---

## 4) 프론트 제약 로직(필수)

## 4-1. 공통 3단값 매핑
- `low = -1`
- `mid = 0`
- `high = +1`

이 값은 이후 컴파일 시 multiplier로 변환한다.

## 4-2. 요청 기반 제약 규칙
- PnR/PnP/TransitionEarly 2항목 쌍:
  - `(high, high)` 금지
  - `(low, low)` 금지
- Drive 3항목:
  - high count >= 2 이면 나머지 1개는 low 강제
- ISO 4항목:
  - high count == 2 이면 나머지 2개는 `(mid, low)` 조합만 허용
- PostUp 3항목:
  - Drive/ISO와 동일 조합 제한 정책 적용

## 4-3. UX 처리 권장
- 금지 조합 시 선택 자체를 disable하거나
- 선택 시 가장 최근 변경값 유지, 기존 conflicting 값 auto-adjust
- 사용자 메시지: "해당 조합은 동시에 선택할 수 없습니다"

---

## 5) 컴파일러 설계

## 5-1. 입력/출력

### 입력
- `PresetOffenseDraft`

### 출력(엔진 저장 payload 확장)
- `offense_scheme: "Preset_Offense"`
- `action_weight_mult`
- `outcome_by_action_mult`
- `outcome_global_mult`
- `context.tempo_mult`

> 기존 전술 payload(`lineup`, `minutes`, `rotation_offense_role_by_pid`, `defense_role_overrides`)는 그대로 유지하고,
> `Preset_Offense` 관련 값만 merge한다.

---

## 5-2. 수치 상하한(clamp) 제안

### A. action_weight_mult
- 최소 `0.85`, 최대 `1.15`
- 이유: 액션 선택 분포 급변 방지

### B. outcome_by_action_mult
- 최소 `0.88`, 최대 `1.12`
- 이유: 결과 확률은 민감하므로 더 보수적으로

### C. outcome_global_mult
- 파울 유도 계열: `0.90 ~ 1.12`
- 턴오버/리스크 계열: `0.90 ~ 1.15`

### D. tempo_mult
- `0.90 ~ 1.12`

---

## 5-3. 정규화 로직

### Step 1) UI 값 전처리
- 모든 슬라이더 0~100 clamp
- 합 제약 슬라이더는 합 100으로 재정규화
  - 예: `x_i' = x_i / sum(x) * 100`, sum=0이면 균등 분배

### Step 2) 3단값 -> 계수 변환
- 기본 변환:
  - `low -> 0.92`
  - `mid -> 1.00`
  - `high -> 1.08`
- 항목별 민감도에 따라 `0.90/1.00/1.10`로 확장 가능

### Step 3) 상위 액션 볼륨 변환
- 5개 상위 슬라이더를 softmax-like 또는 합정규화로 비중화
- 기준 비중(동일분배 or Preset_Offense 기본값) 대비 비율로 `action_weight_mult` 산출
- `pnrFamily`는 `pnrSplit`으로 `PnR`/`PnP`에 분배
- `offballFreq * offballSplit`으로 `Cut/SpotUp/DHO` 분배

### Step 4) outcome_by_action_mult 구성
- 액션별 3단 토글값을 base multiplier로 반영
- 하위 슬라이더 비중을 세부 outcome으로 분해 반영

예시(PnR):
- `handlerDirect`가 high면
  - `SHOT_RIM_*`, `SHOT_TOUCH_FLOATER`, `SHOT_3_OD`, `SHOT_MID_PU` 묶음 강화
- `rollPass`가 high면
  - `PASS_SHORTROLL` 강화
- `rimVsFloater`, `pullupSplit`로 강화량을 내부 분배

### Step 5) 글로벌 성향 반영
- `foulDraw` -> `FOUL_DRAW_*` 계열 전역 보정
- `riskTaking` -> `TO_HANDLE_LOSS`, `TO_CHARGE` 및 고난도 샷(예: `SHOT_3_OD`) 보정
- `tempo` -> `context.tempo_mult`

### Step 6) 최종 clamp + identity fallback
- 모든 multiplier를 항목별 하한/상한으로 clamp
- NaN/inf/누락 키는 1.0으로 대체

---

## 6) Outcome 키 매핑 가이드(제안)

### 6-1. 액션별 권장 반영 키
- `PnR`
  - 직접공격: `SHOT_RIM_LAYUP`, `SHOT_RIM_DUNK`, `SHOT_RIM_CONTACT`, `SHOT_TOUCH_FLOATER`, `SHOT_3_OD`, `SHOT_MID_PU`
  - 롤패스: `PASS_SHORTROLL`
- `PnP`
  - 직접공격: PnR과 동일
  - 팝아웃: `SHOT_3_CS`
- `TransitionEarly`
  - 직접공격: `SHOT_3_OD`, `SHOT_RIM_*`, `SHOT_TOUCH_FLOATER`
  - 오픈찬스: `SHOT_3_CS`
- `Drive`
  - `SHOT_RIM_*`, `PASS_KICKOUT`, `SHOT_MID_PU`
- `ISO`
  - `SHOT_RIM_*`, `SHOT_TOUCH_FLOATER`, `SHOT_3_OD`, `SHOT_MID_PU`, `PASS_KICKOUT`
- `Cut`
  - 마무리: `SHOT_RIM_*`
  - 패스: `PASS_EXTRA`, `PASS_KICKOUT`
- `PostUp`
  - `SHOT_POST`, `SHOT_MID_PU`, `PASS_KICKOUT`, `PASS_EXTRA`, `PASS_SHORTROLL`

### 6-2. 와일드카드(`SHOT_RIM_*`, `PASS_*`) 처리
- 컴파일 시 내부 확장 룰로 구체 키 세트로 풀어쓴다.
- 예:
  - `SHOT_RIM_*` => `SHOT_RIM_LAYUP`, `SHOT_RIM_DUNK`, `SHOT_RIM_CONTACT`
  - `PASS_*` => `PASS_KICKOUT`, `PASS_EXTRA`, `PASS_SHORTROLL`, `PASS_SKIP`

---

## 7) 컴파일러 의사코드

```python
def compile_preset_offense(draft, base_payload):
    d = sanitize_front_constraints(draft)          # 제약 재검증 + auto-fix
    d = clamp_and_renorm_sliders(d)

    awm = build_action_weight_mult(d)              # action_weight_mult
    obam = build_outcome_by_action_mult(d)         # outcome_by_action_mult
    ogm = build_outcome_global_mult(d)             # outcome_global_mult
    tempo_mult = map_tempo(d.tempo)                # context.tempo_mult

    awm = clamp_dict(awm, lo=0.85, hi=1.15)
    obam = clamp_nested(obam, lo=0.88, hi=1.12)
    ogm = clamp_dict(ogm, lo=0.90, hi=1.15)

    out = deepcopy(base_payload)
    out["offense_scheme"] = "Preset_Offense"
    out["action_weight_mult"] = merge_mult(out.get("action_weight_mult", {}), awm)
    out["outcome_by_action_mult"] = merge_nested(out.get("outcome_by_action_mult", {}), obam)
    out["outcome_global_mult"] = merge_mult(out.get("outcome_global_mult", {}), ogm)

    ctx = dict(out.get("context") or {})
    ctx["tempo_mult"] = clamp(tempo_mult, 0.90, 1.12)
    out["context"] = ctx
    return out
```

---

## 8) 저장/호환 전략
- API 저장 포맷은 기존 `_to_engine_tactics`를 유지한다.
- Preset UI에서 생성된 값은 `tactics` payload 내부 필드(`action_weight_mult`, `outcome_by_action_mult`, `outcome_global_mult`, `context`)로 넣으면 된다.
- 구버전 데이터(신규 필드 누락)는 모두 default 1.0으로 해석되도록 컴파일러에서 보강한다.

---

## 9) 테스트 포인트
- 제약 테스트
  - 금지 조합이 프론트에서 선택 불가인지
  - 강제 조정 시 기대 결과로 수렴하는지
- 컴파일 테스트
  - 모든 산출 multiplier가 clamp 범위 내인지
  - 합 정규화 대상 슬라이더의 합이 100 유지되는지
  - `offense_scheme=Preset_Offense` 강제 설정되는지
- 시뮬 연동 테스트
  - 저장 후 단일 경기 실행 시 engine payload에 반영되는지

---

## 10) 단계별 구현 순서 제안
1. 프론트 Draft 스키마 + 제약 엔진 먼저 구현
2. 컴파일러(정규화/클램프) 구현
3. API 저장 payload merge 연결
4. 단위 테스트(제약/컴파일) 작성
5. 통합 테스트(저장→시뮬 반영) 점검


---

## 11) 실제 패치 단위 구현안 (프론트)

> 목표: 전술 탭에서 `Preset_Offense` 선택 시 **전용 모달**을 열고, 모달의 Draft를 저장 직전에 컴파일해 기존 `/api/tactics` 저장 흐름에 합친다.

### 11-1. 기존 파일 패치

### A. `static/NBA.html`
- 전술 화면 섹션 하단에 `Preset Offense` 전용 모달 마크업 추가
- 필수 요소(id 기준)
  - `preset-offense-modal`
  - `preset-offense-open-btn` (공격 스킴이 Preset_Offense일 때만 노출)
  - `preset-offense-close-btn`
  - `preset-offense-reset-btn`
  - `preset-offense-apply-btn`
  - `preset-offense-form` (슬라이더/토글 컨테이너)
  - `preset-offense-errors`
- 접근성
  - `role="dialog"`, `aria-modal="true"`, `aria-labelledby`
  - ESC 닫기, 오버레이 클릭 닫기, 포커스 트랩

### B. `static/js/app/dom.js`
- 모달 관련 DOM 레퍼런스 추가
  - `presetOffenseModal`, `presetOffenseOpenBtn`, `presetOffenseApplyBtn` 등

### C. `static/js/features/tactics/tacticsScreen.js`
- 공격 스킴 선택 후 Preset_Offense이면 `preset-offense-open-btn` 활성화
- 저장 payload 생성(`buildTacticsPayload`) 단계에 아래 병합 로직 추가
  1. 기본 전술 payload 생성
  2. offenseScheme이 `Preset_Offense`면 Draft를 컴파일
  3. 컴파일 결과(`action_weight_mult`, `outcome_by_action_mult`, `outcome_global_mult`, `context`)를 payload에 merge
- 전술 로드 시 서버 응답의 기존 컴파일 결과를 Draft로 역직렬화하는 호출 추가
  - 역직렬화가 불완전하면 안전한 default draft 사용

### D. `static/js/app/events.js`
- 모달 열기/닫기/적용 이벤트 바인딩 추가
- 전술 저장 전 dirty 상태 갱신 이벤트 연결

### E. `static/css/screens/tactics.css`
- 모달 레이아웃 및 컴포넌트 스타일 추가
  - 상위/하위 슬라이더 그리드
  - 3단 토글 버튼 그룹(많이/보통/적게)
  - 제약 위반 경고 배지/텍스트
  - 모바일 축소 레이아웃

---

### 11-2. 프론트 신규 파일 (추가 필요)

### 1) `static/js/features/tactics/presetOffenseDraft.js`
- 역할: Draft SSOT + 기본값/정규화
- 포함 함수
  - `createDefaultPresetOffenseDraft()`
  - `clonePresetOffenseDraft(draft)`
  - `sanitizePresetOffenseDraft(draft)` (0~100 clamp + 합100 정규화)
  - `normalizePairTo100(a,b)`, `normalizeTripleTo100(a,b,c)`
- 기본값은 `Preset_Offense`의 현재 엔진 기본 성향(거의 중립)과 맞춰 전부 `mid` + 균등분배로 시작

### 2) `static/js/features/tactics/presetOffenseConstraints.js`
- 역할: 요청 제약을 프론트에서 강제
- 포함 함수
  - `enforcePairNoSameExtreme(left, right)`
  - `enforceDriveRule(rim, kickout, pull2)`
  - `enforceIsoRule(rim, floater, pullup, kickout)`
  - `enforcePostUpRule(postFinish, postFadeway, pass)`
  - `validatePresetOffenseDraft(draft): { ok, errors, warnings }`
- 정책
  - 입력 즉시 auto-fix + 사용자 경고
  - apply 버튼 누를 때 final validate(실패 시 저장 불가)

### 3) `static/js/features/tactics/presetOffenseCompiler.js`
- 역할: Draft -> tactics payload compile
- 포함 함수
  - `compilePresetOffenseDraft(draft, baseTactics)`
  - `mergeCompiledPresetIntoTactics(base, compiled)`
  - `mapLevelToMult(level, curve='default')`
  - `buildActionWeightMult(draft)`
  - `buildOutcomeByActionMult(draft)`
  - `buildOutcomeGlobalMult(draft)`
  - `buildContextPatch(draft)` (`tempo_mult`)
- 산출 키
  - `action_weight_mult`
  - `outcome_by_action_mult`
  - `outcome_global_mult`
  - `context.tempo_mult`

### 4) `static/js/features/tactics/presetOffenseModal.js`
- 역할: 모달 렌더/상호작용 전담
- 포함 함수
  - `openPresetOffenseModal(draft, onApply)`
  - `closePresetOffenseModal()`
  - `renderPresetOffenseModal(draft, validation)`
  - `bindPresetOffenseModalEvents()`
- 전술 화면 모듈과의 경계
  - 모달은 `draft patch`만 emit
  - 실제 저장은 `tacticsScreen.saveTacticsDraft`가 수행

### 5) `static/js/features/tactics/presetOffenseSerde.js`
- 역할: 서버 전술 payload <-> Draft 역직렬화
- 포함 함수
  - `draftFromSavedTactics(tactics)`
  - `injectDraftSnapshotToContext(draft, tactics)`
- 권장 저장 방식
  - `context.USER_PRESET_OFFENSE_DRAFT_V1`에 raw draft snapshot 보존
  - 다음 로드 때 높은 충실도로 복원

---

### 11-3. 프론트 적용 시나리오
1. 유저가 공격 스킴에서 `Preset_Offense` 선택
2. 전술 화면에 "프리셋 상세 설정" 버튼 노출
3. 버튼 클릭 -> 모달 오픈
4. 모달 내부에서 제약 즉시 적용 + 경고
5. 적용 클릭 -> Draft sanitize/validate -> state 저장(아직 서버 저장 X)
6. 전술 저장 클릭 시 compile + merge 후 `/api/tactics/{team}` PUT

---

## 12) 실제 패치 단위 구현안 (백엔드)

> 목표: 프론트에서 전달한 Preset Draft/컴파일 결과를 안전하게 저장하고, GET 시 UI 복원이 가능하도록 직렬화 필드를 유지한다.

### 12-1. 기존 파일 패치

### A. `app/api/routes/tactics.py`
- `_to_engine_tactics` 확장
  - 프론트가 보낸 다음 필드를 허용/정규화
    - `action_weight_mult`
    - `outcome_by_action_mult`
    - `outcome_global_mult`
    - `context.tempo_mult`
    - `context.USER_PRESET_OFFENSE_DRAFT_V1`
  - 타입 강제
    - dict 아니면 빈 dict
    - 수치값이 아니면 drop
- `_to_ui_tactics` 확장
  - UI payload에 `presetOffenseDraft` 필드를 optional로 실어줌
    - `context.USER_PRESET_OFFENSE_DRAFT_V1` 존재 시 우선 사용
    - 없으면 컴파일된 멀티플라이어를 기준으로 best-effort 역추정(혹은 null)

### B. `app/schemas/tactics.py`
- `TeamTacticsUpsertRequest`는 현재 `Dict[str,Any]`라 유연하나,
  실수 방지를 위해 Optional typed model 추가 검토
  - 예: `PresetOffenseDraftModel`(중첩 model)
  - 단, 초기 단계에서는 route-level sanitizer로 충분

### C. `sim/roster_adapter.py`
- 변경 최소화 원칙
  - 이미 `raw_tactics`의 `action_weight_mult`, `outcome_*`, `context`를 받아 `TacticsConfig`로 전달하므로 대규모 수정 불필요
- 보강 포인트
  - `context.tempo_mult` 유효 범위 벗어나면 clamp(0.90~1.12)
  - 숫자 변환 실패 시 삭제/기본값

### D. `matchengine_v3/sim_possession.py` (또는 pace 참조 지점)
- `context.tempo_mult`가 실질적으로 possession 템포 계산에 반영되는지 점검
- 미반영 상태면 pace/clock 관련 로직에 multiplier 적용 패치 필요
  - 예: 액션 선택 이전 possession pace factor에 곱

---

### 12-2. 백엔드 신규 파일 (추가 필요)

### 1) `app/services/tactics_preset_offense.py`
- 역할: 백엔드 안전 정규화/클램프 유틸 (2차 방어선)
- 포함 함수
  - `sanitize_compiled_tactics_patch(payload)`
  - `sanitize_preset_draft_snapshot(draft)`
  - `clamp_mult_dict`, `clamp_nested_mult_dict`
- 목적
  - 프론트 누락/우회 요청에도 서버 저장 데이터의 품질 보장

### 2) `tests/test_tactics_preset_offense_compiler_api.py`
- 역할: `/api/tactics` 저장시 Preset 관련 필드 정규화 검증
- 테스트 케이스
  - 범위 밖 multiplier clamp
  - 잘못된 타입 drop
  - `context.USER_PRESET_OFFENSE_DRAFT_V1` 보존 확인
  - GET 응답의 `presetOffenseDraft` 반환 확인

---

### 12-3. 백엔드 저장 정책
- 최종 저장 SSOT
  - 엔진 사용값: `action_weight_mult`, `outcome_by_action_mult`, `outcome_global_mult`, `context.tempo_mult`
  - UI 복원값: `context.USER_PRESET_OFFENSE_DRAFT_V1`
- 이중 저장 이유
  - 엔진은 즉시 사용 가능한 컴파일 결과 필요
  - UI는 유저가 마지막으로 조정한 원본 구조 복원이 필요

---

## 13) 프론트/백엔드 인터페이스 계약 (즉시 구현용)

### PUT `/api/tactics/{team_id}` 요청 예시(핵심만)
```json
{
  "tactics": {
    "offenseScheme": "Preset_Offense",
    "defenseScheme": "Drop",
    "starters": [],
    "rotation": [],
    "action_weight_mult": {
      "PnR": 1.06,
      "PnP": 1.02,
      "Drive": 1.04
    },
    "outcome_by_action_mult": {
      "PnR": { "PASS_SHORTROLL": 1.05, "SHOT_3_OD": 1.03 },
      "Drive": { "PASS_KICKOUT": 1.07, "SHOT_MID_PU": 0.96 }
    },
    "outcome_global_mult": {
      "FOUL_DRAW_RIM": 1.08,
      "TO_HANDLE_LOSS": 1.03
    },
    "context": {
      "tempo_mult": 1.04,
      "USER_PRESET_OFFENSE_DRAFT_V1": { "...": "draft snapshot" }
    }
  }
}
```

### GET `/api/tactics/{team_id}` 응답 예시(핵심만)
```json
{
  "team_id": "BOS",
  "tactics": {
    "offenseScheme": "Preset_Offense",
    "defenseScheme": "Drop",
    "starters": [],
    "rotation": [],
    "presetOffenseDraft": { "...": "복원용 draft" }
  }
}
```

---

## 14) 우선순위/작업 분할 (실개발 티켓 단위)

### FE-1 (모달 골격)
- HTML 모달 마크업 + DOM 등록 + 열고닫기 이벤트

### FE-2 (Draft/제약 엔진)
- `presetOffenseDraft.js`, `presetOffenseConstraints.js`

### FE-3 (컴파일/저장 연동)
- `presetOffenseCompiler.js`, `tacticsScreen.js` 병합 로직

### FE-4 (복원/UX)
- `presetOffenseSerde.js`, 기존 저장값에서 모달 상태 복원

### BE-1 (sanitize 서비스)
- `app/services/tactics_preset_offense.py` 추가

### BE-2 (routes 연동)
- `_to_engine_tactics`, `_to_ui_tactics`에서 Preset 필드 처리

### BE-3 (엔진 반영 확인)
- `tempo_mult` 실반영 점검/패치

### QA-1 (통합)
- 저장 -> 재진입 -> 경기 시뮬에서 실제 stat profile 변화 확인

