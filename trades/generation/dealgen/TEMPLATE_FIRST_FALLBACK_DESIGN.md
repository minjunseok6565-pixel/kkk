# Template-First + Fallback 자유 탐색 설계안

## 목표

현재 `tier_score` 스켈레톤(예: `mvp.player_heavy`) 중심의 자유 탐색 구조를 다음과 같이 확장한다.

1. **1차(Template-First)**: 3~4개의 사전 정의 템플릿을 먼저 시도한다.
2. 템플릿이 **구성 불가**(자산 부족/규칙 충돌)하거나, 이후 **평가 단계에서 탈락**하면
3. **2차(Fallback)**: 기존 `tier_score` 방식(점수표 + player/pick 비중 기반 자유 조합)으로 탐색한다.

핵심 요구사항:
- 템플릿은 나중에 기획자가 추가/수정할 수 있게 **플레이스홀더 구조**로 만든다.
- 템플릿만 채워 넣으면 자동으로 동작하도록 빌더/라우팅/오케스트레이션을 준비한다.

---

## 현재 구조 요약 (현행)

- 티어별 스켈레톤 ID는 대부분 공통 빌더 `build_tier_style_skeleton(...)`를 tier/style 파라미터만 달리 호출한다.
- skeleton 생성은 `build_offer_skeletons_buy/sell(...)`에서 registry route를 순회해 후보를 모은다.
- 생성된 후보는 `expand_variants -> repair_until_valid -> evaluate_and_score -> discard gate`를 거쳐 최종 proposal로 간다.

문제점(요구사항 관점):
- "템플릿 우선"이 명시적 단계로 분리되어 있지 않다.
- route에 포함된 skeleton들을 한 번에 수행하므로, 템플릿 실패 후 fallback이라는 정책 제어가 어렵다.

---

## 설계 원칙

1. **기존 호환성 유지**: 기본값은 지금과 유사하게 동작.
2. **템플릿 정의와 실행 로직 분리**:
   - 정의(무슨 구성인지)는 template catalog에서 관리.
   - 실행(실제로 deal 생성)은 template builder에서 수행.
3. **두 단계 라우팅 명시화**:
   - Stage A: template routes
   - Stage B: fallback routes
4. **실패 이유 관측 가능**:
   - 템플릿 실패 사유를 stats/tag로 남겨 디버깅 가능하게.

---

## 추가할 파일

### 1) `trades/generation/dealgen/template_specs.py` (신규)

템플릿 정의 전용 파일.

역할:
- 티어별 템플릿 리스트를 정의한다.
- 템플릿은 전부 **플레이스홀더**로 선언하고, 실제 구성은 이후 채워 넣는다.

예상 데이터 구조(개념):

```python
@dataclass(frozen=True, slots=True)
class TemplateSlot:
    slot_id: str                     # ex) "core_player", "first_pick_a"
    asset_type: Literal["PLAYER", "PICK", "SWAP"]
    constraints: Dict[str, Any]      # ex) {"min_tier": "ALL_NBA"}, {"round": 1}
    required: bool = True

@dataclass(frozen=True, slots=True)
class PackageTemplate:
    template_id: str                 # ex) "tpl_all_nba_1_plus_first_2"
    tier_scope: Tuple[str, ...]      # ex) ("MVP", "ALL_NBA")
    contract_tags: Tuple[str, ...]   # ex) ("OVERPAY", "FAIR", "VALUE")
    priority: int
    slots: Tuple[TemplateSlot, ...]
    min_score_ratio: float = 1.0     # required score 대비 최소 충족 비율
    max_assets_from_buyer: int = 6
```

플레이스홀더 예시:
- `tpl_<tier>_placeholder_1`
- `tpl_<tier>_placeholder_2`
- `tpl_<tier>_placeholder_3`
- `tpl_<tier>_placeholder_4`

제공 함수:
- `get_templates_for_tier(tier: str, contract_tag: str) -> List[PackageTemplate]`

> 이 파일은 기획자 작업 지점이다. 실제 템플릿의 slot/constraints만 채우면 된다.

---

### 2) `trades/generation/dealgen/skeleton_builders_template.py` (신규)

템플릿 실행 전용 빌더.

역할:
- `template_specs.py`에서 템플릿 정의를 가져온다.
- 템플릿 slot 순서대로 buyer 자산 풀에서 매칭한다.
- 실패 시 템플릿 단위로 종료하고 다음 템플릿으로 넘어간다.
- 성공하면 `DealCandidate`를 만든다.

핵심 함수:

```python
def build_template_first_skeletons(
    ctx: BuildContext,
    *,
    tier: str,
    skeleton_id_prefix: str,
    max_candidates: int,
) -> List[DealCandidate]:
    ...
```

내부 세부 함수(권장):
- `_match_player_slot(...)`
- `_match_pick_slot(...)`
- `_apply_template_slots(...)`
- `_validate_template_score_gate(...)`  # score_ssot required와 비교
- `_build_candidate_from_template(...)`

태그 규칙:
- `template:first`
- `template_id:<id>`
- `template_stage:primary`
- `template_result:built`

템플릿 실패 사유 태그(후보 미생성 사유 통계 용):
- `template_fail:no_player_match`
- `template_fail:no_pick_match`
- `template_fail:shape_gate`
- `template_fail:score_gate`

---

### 3) `trades/generation/dealgen/template_fallback_policy.py` (신규)

두 단계 실행 정책 캡슐화.

역할:
- stage A/B 실행 순서 관리.
- stage A 결과가 비었거나, stage A 후보가 후속 pipeline에서 전부 탈락한 경우 stage B 허용 여부 판단에 필요한 정책 함수 제공.

핵심 API(개념):

```python
@dataclass(frozen=True, slots=True)
class TemplateFallbackPlan:
    template_specs: Tuple[SkeletonSpec, ...]
    fallback_specs: Tuple[SkeletonSpec, ...]


def partition_specs_for_template_fallback(
    specs: List[SkeletonSpec],
) -> TemplateFallbackPlan:
    ...
```

판별 방법:
- `spec.domain == "template"` 를 template stage로 분류
- 나머지(`tier_score`, `timeline`)는 fallback stage

---

## 수정할 파일

### 4) `trades/generation/dealgen/types.py` (수정)

`DealGeneratorConfig`에 템플릿 우선 탐색 옵션 추가.

추가 필드:
- `template_first_enabled: bool = True`
- `template_first_max_templates_per_target: int = 4`
- `template_first_fallback_enabled: bool = True`
- `template_first_min_keep_after_eval: int = 1`
- `template_first_allow_timeline_in_fallback: bool = True`

route 분리 필드 추가(티어별):
- `skeleton_route_template_mvp: Tuple[str, ...] = (...)`
- `skeleton_route_template_all_nba: Tuple[str, ...] = (...)`
- ... (8-tier 모두)

fallback route 필드:
- `skeleton_route_fallback_mvp: Tuple[str, ...] = ("mvp.player_heavy", "mvp.pick_heavy", "mvp.mixed", ... )`
- ... (8-tier 모두)

기본값 전략:
- template route에는 placeholder template skeleton ID 3~4개.
- fallback route는 기존 route 내용을 그대로 복사.

---

### 5) `trades/generation/dealgen/skeleton_registry.py` (수정)

목표: template route와 fallback route를 선택적으로 조회 가능하게 확장.

수정 포인트:
1. `SkeletonSpec.domain`에 `"template"` 도메인 사용 가능.
2. `build_default_registry()`에 template skeleton spec 등록 추가.
   - 예: `SkeletonSpec("template.mvp.placeholder_1", "template", "template_first", ...)`
3. `get_specs_for_mode_and_tier(...)` 확장:
   - 인자 추가: `route_phase: str = "combined"`  # `template_only` | `fallback_only` | `combined`
   - `route_phase`에 따라 config의 template route / fallback route / 기존 union route를 선택.

호환성:
- `combined`를 기본값으로 두면 기존 호출부는 깨지지 않는다.

---

### 6) `trades/generation/dealgen/skeletons.py` (수정)

목표: `build_offer_skeletons_buy/sell`에서 stage A/B 후보 생성을 분리.

BUY/SSELL 공통 변경 방향:
1. target profile로 tier/contract_tag 계산.
2. `template_first_enabled`면
   - `route_phase="template_only"`로 specs 조회 후 실행.
   - 생성 후보를 `out_template`로 수집.
3. fallback 실행 조건:
   - `out_template`이 비었고 `template_first_fallback_enabled=True`면 fallback specs 실행.
   - 또는 정책상 stage A 이후 최소 후보 수 미달 시 fallback 실행.
4. 최종 반환 전에 기존과 동일하게 modifier/shape gate 적용.

추가 메타 태그:
- template 출신: `stage:template`
- fallback 출신: `stage:fallback`

주의:
- 이 함수는 "생성" 단계이므로 "평가 탈락" 정보는 아직 없음.
- 평가 탈락 후 fallback 재시도는 core에서 제어해야 한다.

---

### 7) `trades/generation/dealgen/core.py` (수정)

목표: 평가 단계에서 template-only 후보가 전멸하면 fallback 탐색을 1회 추가 수행.

권장 구현:

#### A. 타깃별 2-pass 시도
현재 타깃 루프에서 candidates를 1회 생성하는 부분을 아래처럼 변경.

- Pass 1: `build_offer_skeletons_buy(..., generation_phase="template")`
- Pass 1 후보들 평가/repair/score 수행
- 만약 pass1에서 proposal 0개면
  - Pass 2: `generation_phase="fallback"`로 재호출
  - pass2 후보들 동일 처리

SELL 모드도 동일한 패턴 적용.

#### B. 예산 보호
- pass2 진입 전 budget 잔량 체크 (`_budget_or_hard_cap_reached`)
- pass1에서 validations/evaluations를 과도하게 소비했으면 pass2 스킵.

#### C. 관측성(stats/telemetry)
- `stats.failures_by_kind["template_stage_empty"]`
- `stats.failures_by_kind["template_stage_all_discarded"]`
- `stats.failures_by_kind["fallback_stage_invoked"]`

---

### 8) `trades/generation/dealgen/skeleton_builders_mvp.py` 등 tier 파일들 (선택 수정)

선택지 2개:

1. **비수정 권장**: 기존 tier_score 빌더는 fallback으로 그대로 사용.
2. 필요 시만 수정: template 전용 래퍼 함수를 tier 파일에 추가.

권장: 최소 변경을 위해 tier 파일은 유지.

---

### 9) 테스트 파일 추가

#### `trades/generation/dealgen/test_template_first_fallback_routing.py` (신규)

테스트 시나리오:
1. template route spec가 있을 때 `template_only` 조회가 올바른지.
2. fallback_only 조회가 기존 tier_score spec를 반환하는지.
3. combined가 양쪽 union을 반환하는지.

#### `trades/generation/dealgen/test_template_builder_placeholders.py` (신규)

테스트 시나리오:
1. placeholder template 정의가 로딩되는지.
2. slot 매칭 실패 시 후보 0개 처리되는지.
3. slot 최소 충족 + score gate 충족 시 후보가 생성되는지.

#### `trades/generation/dealgen/test_template_eval_fallback_in_core.py` (신규)

테스트 시나리오:
1. pass1(template) 후보가 모두 discard되면 pass2(fallback)가 호출되는지.
2. pass1에서 proposal이 1개 이상 살아남으면 pass2를 생략하는지.
3. budget 소진 시 pass2를 생략하는지.

---

## 템플릿 플레이스홀더 계약(기획자용)

기획자가 채워야 하는 최소 항목:

1. `template_id`
2. `tier_scope`
3. `slots`
   - slot마다 `asset_type`과 `constraints`
4. (선택) `min_score_ratio`

### constraints 권장 키(초기 버전)

PLAYER slot:
- `min_tier`: `"MVP"|"ALL_NBA"|...`
- `max_tier`: optional
- `max_salary_m`: optional
- `min_control_years`: optional

PICK slot:
- `round`: `1|2`
- `protection_allowed`: bool
- `bucket_prefer`: `"FIRST_SAFE"|"FIRST_SENSITIVE"|"SECOND"`

SWAP slot:
- `year_window_min`
- `year_window_max`

> 초기 버전에서는 위 키만 지원하고, 이후 확장 시 매칭 함수에 키를 추가한다.

---

## 단계별 구현 순서 (권장)

1. `template_specs.py` + placeholder 3~4개 생성
2. `skeleton_builders_template.py` 구현 + 단위 테스트
3. `types.py` config route/옵션 추가
4. `skeleton_registry.py` route_phase 및 template domain 지원
5. `skeletons.py` 생성 단계 stage 분리
6. `core.py` 평가 탈락 후 fallback 2-pass 연결
7. 통합 테스트 + 회귀 테스트

---

## 롤아웃 전략

- 1단계: `template_first_enabled=False` 기본 롤아웃(코드 병합만)
- 2단계: 내부 시뮬레이션에서 `True` 켜고 품질/성능 확인
- 3단계: 전체 활성화

안전장치:
- template 실패 시 fallback 허용 기본값 `True`
- fallback route는 기존 route를 유지해 서비스 리스크 최소화

---

## 마이그레이션/호환성 체크리스트

- [ ] 기존 `skeleton_route_*`만 쓰는 환경에서도 `combined` 모드로 정상 동작
- [ ] template route가 비어 있을 때 즉시 fallback으로 진행
- [ ] stats/telemetry에서 stage별 실패율 추적 가능
- [ ] max_attempts/beam_width/validation/evaluation 예산 초과 없음

---

## 완료 기준 (Definition of Done)

1. 템플릿 플레이스홀더만 정의해도 template-first 실행 가능
2. 템플릿 생성 실패 또는 평가 전멸 시 fallback 탐색이 자동 수행됨
3. 기존 대비 성능 예산 내 동작
4. 신규 테스트 3종 통과

---

## 참고: 실제 템플릿 주입 예시(개념)

> 아래는 **예시 형식**이며 실제 값은 기획자가 넣는다.

- `tpl_all_nba_placeholder_1`
  - slots:
    - `PLAYER(min_tier=ALL_NBA) x1`
    - `PICK(round=1) x2`

- `tpl_mvp_placeholder_2`
  - slots:
    - `PLAYER(min_tier=ALL_STAR) x1`
    - `PLAYER(min_tier=STARTER) x1`
    - `PICK(round=1) x1`

이 형식으로만 채우면 코드 수정 없이 동작하도록 본 설계에서 인터페이스를 고정한다.
