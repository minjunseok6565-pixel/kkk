# Preset Defense 권장안 A 설계안

`quality.py`에 `context` 기반 라벨 override 맵 주입 경로 추가

## 1) 목표

Preset_Defense 상세 설정에서 선택된 결과(묶음 선택 + 허용/억제 강도)를 단순 `outcome shaping(1.xx/0.xx)` 뿐 아니라, `outcome 성공률 라벨(weak/tight/neutral)`에도 반영할 수 있도록 엔진 경로를 설계한다.

핵심 요구사항:

- 프론트가 내린 라벨 override 데이터를 **안정적으로 수용**할 것
- 엔진 런타임에서 `quality.py` 계산 시 **결정론적/안전하게** 반영할 것
- 미정의 액션/미정의 outcome은 기존대로 neutral fallback을 유지할 것
- 기존 스킴 테이블(`SCHEME_BASE_OUTCOME_LABELS`) 기반 동작과 **하위 호환**될 것
- **`Preset_Defense` 스킴에서만** 라벨 override를 해석할 것

참고:

- 현재 `scheme_base_outcome_labels.py`에서 `"프리셋-수비"` 슬롯은 "모든 알려진 base_action/outcome에 neutral"로 자동 생성되어 있음
- 따라서 Preset_Defense는 기본적으로 neutral 출발점이며, override는 이 neutral baseline 위에서 필요한 항목만 덮어쓰는 구조가 자연스럽다

---

## 2) 현재 구조 기준 진입 가능 지점

현재 `quality.compute_quality_score()`는 `(scheme, base_action, outcome, role_players, ...)` 기반으로 정적 라벨 테이블을 읽어 `base_label`을 계산한다. 즉, 동적 라벨 주입을 하려면 `compute_quality_score()`에 런타임 컨텍스트를 전달하거나, 호출부에서 별도 라벨을 해석해 넘겨야 한다.

실제 적용 지점은 아래 2층 구조로 나누는 것이 안정적이다.

1. **입력 계층(저장/검증 시점)**: 프론트 입력을 sanitize해서 tactics context에 저장
2. **해석 계층(시뮬레이션 런타임)**: `quality.py`가 context override를 읽어 base label을 대체

이렇게 분리하면 프론트 변동(필드 추가/삭제)이 런타임에 직접 충격을 주지 않는다.

---

## 3) 데이터 계약(권장)

## 3-1. context 저장 키

- 권장 키: `DEF_QUALITY_LABEL_OVERRIDES_V1`
- 위치: `tactics.context.DEF_QUALITY_LABEL_OVERRIDES_V1`

## 3-2. payload shape (권장)

```json
{
  "version": 1,
  "actions": {
    "Cut": {
      "SHOT_RIM_LAYUP": "tight",
      "PASS_KICKOUT": "weak"
    },
    "PnR": {
      "SHOT_3_OD": "tight",
      "PASS_SHORTROLL": "weak"
    }
  },
  "meta": {
    "source": "preset_defense",
    "preset_version": "2026-01"
  }
}
```

설계 포인트:

- `actions`는 `base_action -> outcome -> label` 구조
- label 허용값은 `{ "weak", "tight", "neutral" }`로 1차 제한
- 미지정 action/outcome은 override 미적용(=기존 스킴 라벨 유지)

---

## 4) sanitize/validation 전략

## 4-1. sanitize 위치

`validation.py`의 tactics sanitize 단계에서 context 내 `DEF_QUALITY_LABEL_OVERRIDES_V1`를 별도 sanitize.

## 4-2. sanitize 규칙

1. object가 아니면 폐기
2. `version != 1`이면 폐기(또는 soft warn 후 ignore)
3. action key는 엔진 allowed action 집합과 교집합만 유지
4. outcome key는 엔진 allowed outcome 집합과 교집합만 유지
5. label은 소문자 normalize 후 `{weak, tight, neutral}`만 허용
6. 크기 제한:
   - action 최대 16
   - action당 outcome 최대 64
   - 전체 엔트리 최대 512
7. 초과/오염 데이터는 드롭하고 warning 축적

## 4-3. 저장 원칙

- sanitize 결과가 비어 있으면 key 자체를 context에서 제거
- 유효 엔트리만 남은 경우에만 저장

이 정책이면 프론트에서 예기치 않은 키를 보내도 런타임 안전성이 유지된다.

---

## 5) `quality.py` 확장 설계

## 5-1. 함수 시그니처 확장

현재:

- `compute_quality_score(scheme, base_action, outcome, role_players, ..., return_detail=False)`

권장:

- `compute_quality_score(..., context: Optional[Mapping[str, Any]] = None, return_detail=False)`

하위 호환을 위해 `context=None` 기본값 유지.

## 5-2. 라벨 해석 순서(우선순위)

`base_label` 결정 순서를 명확히 고정:

0. `scheme`이 Preset_Defense(정규화 후 `"프리셋-수비"`)가 아니면 override를 아예 보지 않음
1. (Preset_Defense인 경우에만) `context.DEF_QUALITY_LABEL_OVERRIDES_V1.actions[base_action][outcome]`
2. 없으면 기존 `SCHEME_BASE_OUTCOME_LABELS[scheme][base_action][outcome]`
3. 없으면 `neutral`

즉, override는 **Preset_Defense 한정 선택적 덮어쓰기**이고, 범위 밖은 기존 규칙 유지.

## 5-3. 안전 처리

- context 파싱 실패 시 예외 없이 무시
- 알 수 없는 label은 `neutral`로 강등
- reset 계열 outcome은 기존 규칙대로 quality 0 고정(override 불가)
- Preset_Defense가 아닌 스킴은 context override가 있어도 무시(정적 스킴 라벨만 사용)

## 5-4. 디버깅/관측성

`return_detail=True`일 때 아래 필드 추가 권장:

- `label_source`: `"override" | "scheme_table" | "fallback_neutral"`
- `override_hit`: `true/false`

이 값이 있어야 QA가 "왜 이 라벨이 적용됐는지"를 추적할 수 있다.

---

## 6) 호출부 연결 전략

`quality.compute_quality_score()`를 호출하는 resolve/priors 경로에서 가능한 한 동일한 context를 전달한다.

권장 원칙:

- possession 단위 `ctx`를 already-in-memory로 사용
- `ctx["tactics"]` 또는 `ctx` 자체에 sanitize된 override가 있으면 pass-through
- 호출부마다 context 전달 누락이 없도록 공통 헬퍼를 두는 방식이 안정적

예: `_quality_context(ctx)` 헬퍼를 만들고 모든 handler에서 동일하게 사용.

---

## 7) 프론트-백엔드 계약 안정화 포인트

1. 프론트는 **라벨 결과만** 전달 (`weak/tight/neutral`), 규칙 계산은 프론트 책임
2. 백엔드는 전달값을 그대로 신뢰하지 않고 sanitize 후 반영
3. 라벨 override 전달은 Preset_Defense일 때만 의미가 있음 (다른 스킴은 무시)
4. 프론트 계산 로직이 바뀌어도 백엔드는 contract shape만 맞으면 수용 가능
5. 서버가 저장한 sanitize 결과를 그대로 다시 내려주면(라운드트립) UI/엔진 불일치 최소화

---

## 8) 미정의 액션/미정의 outcome 처리 정책 (권장안 A 기준)

## 8-1. 미정의 액션

- sanitize 단계에서 drop
- 런타임 override 미적용
- 결과적으로 스킴 테이블 기반 기본 동작 유지

## 8-2. 미정의 outcome

- sanitize 단계에서 drop
- 런타임에서 neutral fallback이 아니라 "기존 스킴 라벨" 유지가 기본
  - 단, 스킴 테이블에도 없으면 neutral

즉, override는 **명시된 페어만 바꾼다**는 원칙을 유지한다.

추가 원칙:

- Preset_Defense가 아닌 경우에는 override payload 유무와 무관하게 `scheme_base_outcome_labels.py` 매핑을 그대로 사용한다.

---

## 9) 충돌/합성 규칙

quality 라벨 override와 shaping 멀티플라이어가 동시에 존재할 때는 둘 다 적용된다.

- prior 단계: `opp_outcome_*_mult`로 outcome 선택 확률 변경
- resolve 단계: quality label(`weak/tight`)로 성공 확률(로그잇) 변경

두 효과는 서로 다른 단계이므로 우선순위보다 "중첩 적용"으로 문서화해야 한다.

---

## 10) 단계별 도입 플랜

1. **Phase 1 (안전 도입)**
   - context 키/스키마 확정
   - validation sanitize 추가
   - `quality.py` context override read-only 적용

2. **Phase 2 (관측성 강화)**
   - `QualityDetail`에 source 필드 추가
   - 디버그 로그/리포트 경로에 override hit 노출

3. **Phase 3 (운영 안정화)**
   - 저장 데이터 샘플링/통계(override hit율, drop율)
   - 프론트 버전 호환 정책(버전 마이그레이션) 추가

---

## 10-1) 작업 대상 파일 및 추천 순서 (간단 가이드)

아래는 실제 패치 단계로 넘어갈 때의 최소 작업 단위다.

1. `matchengine_v3/validation.py`  
   - `context.DEF_QUALITY_LABEL_OVERRIDES_V1` sanitize 함수 추가/연결  
   - allowed action/outcome 교집합, label allowlist, size cap, warning 정책 반영

2. `matchengine_v3/quality.py`  
   - `compute_quality_score(..., context=None)` 시그니처 확장  
   - Preset_Defense 한정 override 해석(override → scheme_table → neutral) 로직 추가  
   - `return_detail=True` 시 `label_source`, `override_hit` 노출

3. `matchengine_v3/resolve_parts/*.py` 및 `matchengine_v3/possession/quality_bias.py`  
   - `compute_quality_score` 호출부에 context 전달 경로 정리(누락 없는지 점검)

4. (선택) `matchengine_v3/quality_data/scheme_base_outcome_labels.py`  
   - 코드 변경보다는 주석 보강 권장: Preset_Defense는 neutral baseline이며 동적 override는 runtime에서만 반영됨을 명시

5. 테스트 파일 추가 (신규)  
   - validation sanitize 단위 테스트  
   - quality override 적용/미적용(Preset vs non-Preset) 단위 테스트  
   - unknown action/outcome/label drop 및 fallback 검증

권장 실행 순서:

- **1 → 2 → 3 → 5 → (4 선택)**  
  (입력 안전성 확보 → 해석 로직 구현 → 호출부 연결 → 회귀 방지 테스트 → 문서성 주석 보강)

---

## 11) 최종 권고

권장안 A를 선택한다면, 핵심은 "`quality.py` 단독 변경"이 아니라 **입력 sanitize + 런타임 해석 + 관측성** 3요소를 함께 설계하는 것이다.

이 구조를 따르면:

- 프론트 값은 안전하게 수용되고
- 엔진에는 결정론적으로 반영되며
- 미정의 항목은 기존 동작을 깨지 않고
- 디버깅 가능한 형태로 운영할 수 있다.
