# 트레이드 오퍼 스켈레톤 전면 개편 구체 수정안 (2026-03-08)

> 기준 문서: `docs/trade-offer-skeleton-overhaul-plan-2026-03-08.md`
> 목표: 위 상위 계획의 철학/방향을 유지한 채, **실제 코드 수정 시 누락 없이 바로 구현 가능한 수준**으로 상세화한다.

---

## 0) 완료 정의 (Definition of Done)

아래 5개가 모두 충족되면 이번 개편 목표가 달성된 것으로 본다.

1. 기존 4개 archetype(`picks_only`, `young_plus_pick`, `p4p_salary`, `consolidate_2_for_1`)이 유지되면서도,
   내부적으로 신규 `skeleton_id` 체계로 분해되어 로그/텔레메트리에 기록된다.
2. BUY/SELL 생성 경로에서 타깃 등급별 활성 skeleton 라우팅이 적용되어,
   타깃당 최소 6개 이상(상황에 따라 10개+)의 현실적 패키지가 생성 가능하다.
3. protection/swap가 독립 자산 레버로 동작한다.
   - 1st 포함 skeleton에서 protection 강도 변형
   - protected first 대체(swap+2nds) 경로
4. 3-for-1, 2-for-2, bench bundle, rental, bad money swap, first split 등 핵심 형태가 실제 제안 풀에서 관측된다.
5. 계산량이 폭주하지 않도록 절대 하드캡(max validations/evaluations hard)은 유지된다.

---

## 1) 변경 범위 총괄

## 1-1. 수정 대상 기존 파일

- `trades/generation/dealgen/types.py`
  - generator config 확장(신규 skeleton/라우팅/modifier 관련 파라미터)
- `trades/generation/dealgen/skeletons.py`
  - 기존 단일 구현을 오케스트레이터화
  - registry 기반 호출로 전환
  - 기존 archetype alias 유지
- `trades/generation/dealgen/utils.py`
  - 다인원/다픽 shape 검증 유틸 보강
  - 신규 게이트 함수(타깃 tier 분류, rental 판단, bad-money 판단 등)
- `trades/generation/dealgen/pick_protection_decorator.py`
  - skeleton intent 기반 protection 강도 힌트 입력 수용
- `trades/generation/dealgen/core.py`
  - skeleton_id/domain/tier 메타를 proposal stats에 반영

## 1-2. 신규 파일

- `trades/generation/dealgen/skeleton_registry.py`
- `trades/generation/dealgen/skeleton_builders_player_swap.py`
- `trades/generation/dealgen/skeleton_builders_timeline.py`
- `trades/generation/dealgen/skeleton_builders_salary_cleanup.py`
- `trades/generation/dealgen/skeleton_builders_pick_engineering.py`
- `trades/generation/dealgen/skeleton_modifiers.py`
- `trades/tests/test_skeleton_registry_routing.py`
- `trades/tests/test_skeleton_builders_core_shapes.py`
- `trades/tests/test_skeleton_modifiers_protection_swap.py`
- `trades/tests/test_skeleton_compat_aliases.py`

---

## 2) 데이터 모델/타입 상세 수정안

## 2-1. `DealCandidate` 메타 확장

`trades/generation/dealgen/types.py`의 `DealCandidate`에 아래 필드를 추가한다.

- `skeleton_id: str`  (예: `player_swap.role_swap_small_delta`)
- `skeleton_domain: str`  (`player_swap` / `timeline` / `salary_cleanup` / `pick_engineering`)
- `target_tier: str` (`ROLE`, `STARTER`, `HIGH_STARTER`, `STAR`, `PICK_ONLY`)
- `compat_archetype: str` (기존 archetype alias. 기존 `archetype`과 동일값 유지 가능)
- `modifier_trace: List[str]` (적용된 modifier 기록)

> 주의: 기존 소비 코드가 `archetype`만 읽더라도 동작하도록 `archetype` 필드는 유지한다.

## 2-2. `DealGeneratorConfig` 신규 키

기존 키를 유지한 상태에서 아래를 추가한다.

### a) shape 완화/탐색 확장
- `skeleton_overhaul_enabled: bool = True`
- `max_players_per_side: int = 4`
- `max_players_moved_total: int = 7`
- `max_assets_per_side: int = 9`
- `max_picks_per_side: int = 4`
- `max_seconds_per_side: int = 4`
- `base_beam_width: int = 12`

### b) 타깃 티어 라우팅
- `skeleton_route_role: Tuple[str, ...]`
- `skeleton_route_starter: Tuple[str, ...]`
- `skeleton_route_high_starter: Tuple[str, ...]`
- `skeleton_route_pick_only: Tuple[str, ...]`

### c) modifier
- `skeleton_modifiers_enabled: bool = True`
- `modifier_max_variants_per_candidate: int = 3`
- `modifier_protection_enabled: bool = True`
- `modifier_swap_substitute_enabled: bool = True`
- `modifier_protection_default_ladder: Tuple[str, ...] = ("prot_light", "prot_mid", "prot_heavy")`

### d) 현실성 관측 우선 완화
- `skeleton_gate_strictness: float = 0.35`  (0에 가까울수록 관대한 게이트)
- `skeleton_false_negative_bias: float = 0.75` (거래 미생성 방지 우선)

---

## 3) 레지스트리/빌더 아키텍처 상세

## 3-1. `SkeletonSpec` 정의 (`skeleton_registry.py`)

```python
@dataclass(frozen=True, slots=True)
class SkeletonSpec:
    skeleton_id: str
    domain: str
    compat_archetype: str
    mode_allow: Tuple[str, ...]          # ("BUY", "SELL")
    target_tiers: Tuple[str, ...]
    priority: int
    build_fn: Callable[[BuildContext], List[DealCandidate]]
    gate_fn: Optional[Callable[[BuildContext], bool]] = None
    default_tags: Tuple[str, ...] = ()
    allows_modifiers: bool = True
```

`registry`는 `Dict[str, SkeletonSpec]`로 관리하고,
`get_specs_for_mode_and_tier(mode, tier, config)`에서 활성 목록을 반환한다.

## 3-2. 빌더 컨텍스트 (`BuildContext`)

입력 컨텍스트 필드:
- buyer/seller/team situations
- target or sale_asset
- buyer_out/seller_out
- need maps
- budget/config/rng
- ban sets

출력은 `List[DealCandidate]`.

모든 빌더는 아래 규칙 준수:
1. 기본 `Deal` 구성 후 자산 추가
2. `_shape_ok()` 1차 체크
3. 태그 + skeleton_id/domain 부착
4. candidates 반환

---

## 4) 도메인별 빌더 구현 명세

## 4-1. player_swap 빌더

### 4-1-1. `player_swap.role_swap_small_delta`
- 기본 shape: 1-for-1 + (선택) 2R/2R swap
- 게이트:
  - 연봉 차 절대값 <= `fit_swap_max_salary_diff_m` 또는 별도 `role_swap_max_salary_gap_m`
  - 양 팀 need tag가 서로 교차 개선 가능
- 구현:
  - 기존 `_pick_return_player_salaryish_with_need` 활용
  - sweetener는 SECOND/SWAP 우선

### 4-1-2. `player_swap.fit_swap_2_for_2`
- 기본 shape: 2-for-2 (양 팀 각각 rotation 1명 이상)
- 구현:
  - buyer 측 2명: `CONSOLIDATE` + `FILLER_CHEAP` 우선
  - seller 측은 target + 추가 반환자(빌더 내부 보조 선택)
- 게이트:
  - 양 팀 roster imbalance 지표(need top2 미스매치) 사용

### 4-1-3. `player_swap.starter_for_two_rotation`
- 기본 shape: 1(for target) ↔ 2(rotation)
- 게이트:
  - target tier가 `STARTER` 이상
  - buyer가 실 rotation value 합을 충족

### 4-1-4. `player_swap.three_for_one_upgrade`
- 기본 shape: 3 ↔ 1
- 슬롯 템플릿:
  - 템플릿 A: rotation + rotation + ballast
  - 템플릿 B: young flyer + rotation + expiring

### 4-1-5. `player_swap.bench_bundle_for_role`
- 기본 shape: bench 2명 + (2R 1~2장) ↔ role player 1명
- 게이트:
  - target이 스타 티어가 아님

### 4-1-6. `player_swap.change_of_scenery_young`
- 기본 shape: underused young ↔ underused young (+ 소액 sweetener)
- 게이트:
  - 연차/통제기간 조건(`young_min_control_years`) 충족

### 4-1-7. `player_swap.star_lateral_plus_delta`
- 기본 shape: core급 ↔ core급 + 보정자산(1R/swap/young flyer)
- 게이트:
  - target tier = `HIGH_STARTER` 이상
  - low strictness 모드에서는 gate 완화

### 4-1-8. `player_swap.one_for_two_depth`
- 기본 shape: 1 ↔ 2 depth
- `starter_for_two_rotation`의 저강도 버전.

## 4-2. timeline 빌더

### 4-2-1. `timeline.veteran_for_young`
- shape: veteran ↔ young flyer
- 게이트:
  - veteran: expiring 또는 1~2년 잔여
  - seller horizon: `REBUILD/RE_TOOL`

### 4-2-2. `timeline.veteran_for_young_plus_protected_first`
- shape: veteran starter ↔ young + protected first
- 구현:
  - first는 `FIRST_SAFE` 또는 `FIRST_SENSITIVE`에서 선택
  - protection modifier와 연동

### 4-2-3. `timeline.bluechip_plus_first_plus_swap`
- shape: blue-chip + first + swap + salary match
- 게이트:
  - target tier `HIGH_STARTER/STAR`

## 4-3. salary_cleanup 빌더

### 4-3-1. `salary_cleanup.rental_expiring_plus_second`
- shape: expiring salary match + second ↔ rental veteran
- 게이트:
  - target contract가 expiring 성격
  - buyer posture가 win-now 성향

### 4-3-2. `salary_cleanup.pure_absorb_for_asset`
- shape: 무선수(또는 ballast 최소) 흡수 + 보상자산
- 기존 `picks_only` 실질 대체 핵심

### 4-3-3. `salary_cleanup.partial_dump_for_expiring`
- shape: 불편한 다년 계약 ↔ 짧은 expiring + 보상
- pure absorb 불가능 시 우선 경로

### 4-3-4. `salary_cleanup.bad_money_swap`
- shape: bad money ↔ bad money + 소액자산
- 게이트:
  - 양측 모두 bad contract 후보 존재

## 4-4. pick_engineering 빌더

### 4-4-1. `pick_engineering.first_split`
- shape:
  - premium 1R ↔ lesser 1R 2장
  - premium 1R ↔ protected 1R + swap + 2R

### 4-4-2. `pick_engineering.second_ladder_to_protected_first`
- shape: 2R 2~4장 ↔ protected 1R

### 4-4-3. `pick_engineering.swap_purchase`
- shape:
  - 2R 여러 장 ↔ 1R swap
  - 2R 1장 ↔ 2R swap

### 4-4-4. `pick_engineering.swap_substitute_for_first`
- shape: protected first 대체로 swap 1~2 + 2R 1~2
- modifier와도 연동 가능하나, base skeleton로도 생성.

---

## 5) modifier 구체 설계 (`skeleton_modifiers.py`)

## 5-1. modifier 타입
- `protection_step_up_down`
- `swap_substitute_for_first`
- `second_round_rebalance` (2R 개수 미세조정)

## 5-2. protection ladder
- `prot_light`: top-4 / top-8
- `prot_mid`: lottery / top-14
- `prot_heavy`: top-20 / top-24
- `prot_rollover`: 미전달 시 다음 해 이월
- `prot_convert_2nds`: 미전달 시 2R 전환

`pick_protection_decorator`에 전달할 힌트 필드:
- `proposal_meta["protection_intent"] = "light|mid|heavy"`

## 5-3. modifier 적용 알고리즘

1. base 후보 생성
2. 후보당 최대 `modifier_max_variants_per_candidate`만 생성
3. 각 변형은 `_shape_ok` + validate/evaluate 재통과 필요
4. 동일 해시 중복 제거
5. 최종 score 상위만 유지

---

## 6) BUY/SELL 오케스트레이터 전환 상세 (`skeletons.py`)

## 6-1. BUY 경로

기존 `build_offer_skeletons_buy(...)`를 아래 흐름으로 교체:

1. `target_tier = classify_target_tier(target, tick_ctx, catalog)`
2. `specs = registry.get_specs(mode="BUY", tier=target_tier)`
3. 우선순위 순회하며 각 `build_fn(ctx)` 호출
4. candidate 누적 후 shape/filter
5. modifier 적용(활성 시)
6. beam cap 적용 및 반환

## 6-2. SELL 경로

`build_offer_skeletons_sell(...)`도 BUY와 동일 패턴으로 정렬.
- 단, `match_tag` 기반 SELL 맥락 태그를 기본 tags에 포함.

## 6-3. 기존 archetype 호환 보장

각 신규 후보 생성 시:
- `candidate.archetype = compat_archetype`
- `candidate.skeleton_id = ...`
- `tags`에 `arch_compat:<archetype>` 추가

---

## 7) 타깃 티어 분류 규칙 (`utils.py`)

`classify_target_tier(...)` 신설.

우선순위:
1. 픽 자산 중심 거래면 `PICK_ONLY`
2. expiring rental 성격 + 낮은 market => `ROLE`
3. market/fit/value 기준으로 `STARTER`
4. 상위값이면 `HIGH_STARTER` / `STAR`

초기 구현은 단순 threshold 기반 + 추후 튜닝 가능하게 config화.

---

## 8) shape 상한/검증 수정

## 8-1. `_shape_ok()` 보강
- 3-for-1, 3-for-2를 허용하도록 상한 업데이트.
- 단, 아래는 계속 차단:
  - 동일 선수/픽 중복
  - 한쪽 legs 비어 있는 비정상 딜(허용 스켈레톤 제외)

## 8-2. hard stop 유지
- `max_validations_hard`, `max_evaluations_hard`는 현행 유지.
- `core.py` budget guard에서 skeleton 과다 생성 시 조기 중단.

---

## 9) 텔레메트리/디버깅 강화

`core.py`/`trade_contract_telemetry.py`에 아래 필드 기록:
- `skeleton_id`
- `skeleton_domain`
- `target_tier`
- `modifier_trace`
- `arch_compat`

집계 메트릭:
- tick당 고유 skeleton 수
- 도메인별 생성 비율
- modifier 적용 성공률

---

## 10) 테스트 상세 계획

## 10-1. 단위 테스트

### `test_skeleton_registry_routing.py`
- tier별 활성 spec 집합이 계획과 일치하는지 검증.

### `test_skeleton_builders_core_shapes.py`
- 핵심 skeleton 12종 이상에서 최소 1개 candidate 생성 확인.
- shape 검증 통과 여부 확인.

### `test_skeleton_modifiers_protection_swap.py`
- protection step-up/down 생성 여부.
- swap_substitute 대체안 생성 여부.

### `test_skeleton_compat_aliases.py`
- 모든 신규 skeleton이 기존 archetype alias를 가진다는 것 검증.

## 10-2. 시뮬레이션 회귀

고정 seed로 시즌 구간별(초반/중반/마감전) 측정:
- 고유 skeleton_id 수
- 2-for-2, 3-for-1 출현률
- timeline/salary_cleanup/pick_engineering 출현률
- 스타급 타깃의 오퍼 도달률
- validations/evaluations 소모량

---

## 11) 구현 순서 (실행용 체크리스트)

## Phase 1: 기반 도입 (호환 유지)
- [x] `SkeletonSpec` + registry 도입
- [x] 기존 4개 skeleton을 registry builder로 이관
- [ ] 기존 테스트/시뮬레이션 회귀 통과

## Phase 2: 코어 스켈레톤 확장
- [x] player_swap 6종 우선 구현
- [x] timeline 3종 구현
- [x] salary_cleanup 4종 구현
- [x] pick_engineering 4종 구현

## Phase 3: modifier 결합
- [x] protection_step_up_down
- [x] swap_substitute_for_first
- [x] second_round_rebalance

## Phase 4: shape 완화/튜닝
- [x] config 상한 변경 반영
- [x] beam/variant 균등 슬롯 적용
- [x] hard cap 모니터링

## Phase 5: 관측/보정
- [ ] 텔레메트리 대시보드 지표 연결
- [ ] 과다 생성/비현실 생성 케이스 튜닝

---

## 12) 리스크와 방지책

1. **리스크: 제안 과다 폭증**
   - 방지: hard caps 유지 + domain별 슬롯 상한 + modifier variant 상한.
2. **리스크: 무의미한 다자산 딜 증가**
   - 방지: tier 라우팅 + 게이트(타임라인/contract/fit needs) 최소 적용.
3. **리스크: 기존 파이프라인 호환 깨짐**
   - 방지: archetype alias 유지 + 단계별 이관.
4. **리스크: protection decorator와 충돌**
   - 방지: modifier intent 힌트 방식으로 결합(SSOT 변형 금지).

---

## 13) 최종 인수 조건 (Acceptance Criteria)

아래를 모두 만족해야 배포 가능:

- [ ] 신규 `skeleton_id` 18개 이상이 registry에 등록됨
- [ ] BUY/SELL 모두 tier 라우팅이 활성화됨
- [ ] 최소 14개 핵심 skeleton이 단위테스트에서 candidate 생성 성공
- [ ] protection/swap modifier가 각각 1개 이상 성공 케이스 보유
- [ ] 회귀 시뮬레이션에서 고유 skeleton 수/도메인 다양성 지표가 기존 대비 유의미하게 증가
- [ ] validations/evaluations가 hard cap을 초과하지 않음

---

## 14) 구현 시 즉시 참고할 함수/포인트

- 기존 생성 진입점
  - `build_offer_skeletons_buy` / `build_offer_skeletons_sell`
- shape gate
  - `_shape_ok`
- young split / need helper
  - `_split_young_candidates`, `_get_need_map`, `_best_need_tag`
- salary-ish return picker
  - `_pick_return_player_salaryish_with_need`
- pick protection 후처리
  - `pick_protection_decorator` 내 variant 생성/선별 루프

본 문서를 기준으로 구현하면, 기존 계획 문서의 설계 철학을 유지하면서도
실제 코드 반영 시 필요한 수정 포인트가 파일 단위/함수 단위로 누락 없이 연결된다.
