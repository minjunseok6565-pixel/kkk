# 트레이드 오퍼 스켈레톤 전면 개편 실행 계획 (2026-03-08)

## 0) 목적
- 현재 `build_offer_skeletons_buy/sell`가 사실상 4개 골격(`picks_only`, `young_plus_pick`, `p4p_salary`, `consolidate_2_for_1`)에 집중된 구조를,
  **현실 시장에서 자주 발생하는 딜 형태 중심의 다층 스켈레톤 체계**로 재편한다.
- 이번 작업의 1차 목표는 **성사율 튜닝 이전에 ‘현실적인 제안 생성 다양성’을 최대화**하는 것이다.
- 계산량은 “게임이 터지지 않는 최소한의 상한”만 남기고, 기존의 과도한 안전장치는 완화한다.

---

## 1) 현재 코드 기준 문제 정의

### 1-1. 스켈레톤 진입점이 4종으로 고정
- BUY/SSELL 모두 단일 함수(`trades/generation/dealgen/skeletons.py`)에서 4개 아키타입만 생성한다.
- 결과적으로 스타/준스타/롤플레이어/렌탈/계약정리/픽공학형 딜이 같은 좁은 틀로 압축된다.

### 1-2. 스켈레톤 이름과 실제 딜 의미가 뭉뚱그려져 있음
- 예: `p4p_salary` 하나에 role swap, fit swap, lateral star swap, bad money swap까지 사실상 몰려야 한다.
- 예: `young_plus_pick` 하나에 veteran-for-young, bluechip+1st+swap 같은 다른 난이도 패키지가 섞인다.

### 1-3. shape 상한이 “초기 탐색 다양성”을 조기 차단
- 현재 config는 `max_players_per_side=2`, `max_players_moved_total=4`라서 3-for-1/3-for-2류를 스켈레톤 단계에서 대부분 잃는다.
- 현재 프로젝트 목표(현실성 관측)에는 상한이 보수적이다.

### 1-4. 보호/스왑이 독립 협상 레버로 충분히 쓰이지 않음
- pick protection은 decorator 단계가 존재하지만, skeleton 단계에서 보호 강도/스왑 대체를 의도적으로 설계하는 흐름이 약하다.

---

## 2) 개편 원칙 (프로젝트 정합 버전)

1. **기존 4개를 삭제하지 않고 분해/세분화한다.**
   - 회귀 리스크를 줄이기 위해 기존 archetype은 compatibility alias로 유지.
2. **선수교환형 / 타임라인형 / 계약정리형 / 픽공학형으로 체계를 재편한다.**
3. **보호(protection)는 독립 skeleton보다 modifier로 운영한다.**
   - `pick_protection_decorator.py`와 결합하기 좋은 구조로 설계.
4. **타깃 등급별 활성 skeleton을 다르게 열어 계산량을 통제한다.**
   - “전체 스켈레톤 수 증가”와 “타깃당 시도 수 증가”를 분리.
5. **1차 릴리스는 안전장치 완화 우선**
   - 거래가 안 나오는 false negative를 줄이고, 과다 생성은 2차 튜닝으로 조정.

---

## 3) 신규 스켈레톤 카탈로그 (프로젝트 표준 네이밍)

> 표기 규칙: `domain.variant`
> - domain: `player_swap`, `timeline`, `salary_cleanup`, `pick_engineering`
> - variant: 구체 딜 형태

### 3-1) player_swap
- `player_swap.role_swap_small_delta`
- `player_swap.fit_swap_2_for_2`
- `player_swap.starter_for_two_rotation`
- `player_swap.three_for_one_upgrade`
- `player_swap.bench_bundle_for_role`
- `player_swap.change_of_scenery_young`
- `player_swap.star_lateral_plus_delta`
- `player_swap.one_for_two_depth`

### 3-2) timeline
- `timeline.veteran_for_young`
- `timeline.veteran_for_young_plus_protected_first`
- `timeline.bluechip_plus_first_plus_swap`

### 3-3) salary_cleanup
- `salary_cleanup.rental_expiring_plus_second`
- `salary_cleanup.pure_absorb_for_asset`
- `salary_cleanup.partial_dump_for_expiring`
- `salary_cleanup.bad_money_swap`

### 3-4) pick_engineering
- `pick_engineering.first_split`
- `pick_engineering.second_ladder_to_protected_first`
- `pick_engineering.swap_purchase`
- `pick_engineering.swap_substitute_for_first`

### 3-5) protection modifier (독립 skeleton 아님)
- `prot_light`, `prot_mid`, `prot_heavy`, `prot_rollover`, `prot_convert_2nds`

---

## 4) 기존 아키타입과 매핑(하위 호환)

- `picks_only`
  - `salary_cleanup.pure_absorb_for_asset`
  - `pick_engineering.first_split`
  - `pick_engineering.second_ladder_to_protected_first`
  - `pick_engineering.swap_purchase`

- `young_plus_pick`
  - `timeline.veteran_for_young`
  - `timeline.veteran_for_young_plus_protected_first`
  - `timeline.bluechip_plus_first_plus_swap`
  - `player_swap.change_of_scenery_young`

- `p4p_salary`
  - `player_swap.role_swap_small_delta`
  - `player_swap.fit_swap_2_for_2`
  - `player_swap.star_lateral_plus_delta`
  - `salary_cleanup.bad_money_swap`

- `consolidate_2_for_1`
  - `player_swap.starter_for_two_rotation`
  - `player_swap.three_for_one_upgrade`
  - `player_swap.bench_bundle_for_role`
  - `player_swap.one_for_two_depth`

> 운영 로그/텔레메트리에서는 기존 `archetype` 필드를 남기고,
> 신규 `skeleton_id`를 병행 기록해 점진 전환한다.

---

## 5) 구현 구조 변경안

### 5-1. 파일 구조
- `trades/generation/dealgen/skeletons.py`
  - 현 구조를 “오케스트레이터” 역할로 축소.
- 신규 파일:
  - `trades/generation/dealgen/skeleton_registry.py`
  - `trades/generation/dealgen/skeleton_builders_player_swap.py`
  - `trades/generation/dealgen/skeleton_builders_timeline.py`
  - `trades/generation/dealgen/skeleton_builders_salary_cleanup.py`
  - `trades/generation/dealgen/skeleton_builders_pick_engineering.py`
  - `trades/generation/dealgen/skeleton_modifiers.py`

### 5-2. 핵심 인터페이스
- `SkeletonSpec` (id, domain, base_archetype, build_fn, gates, tags)
- `build_candidates_for_target(target_ctx, enabled_specs, config, budget)`
- `apply_modifiers(candidate, modifier_bundle)`

### 5-3. 게이트 평가 입력(기존 데이터 재사용)
- 팀 타임라인: `TeamSituation.time_horizon`
- 팀 posture/urgency: generation tick 컨텍스트
- 자산 버킷: `TradeAssetCatalog`의 outgoing/bucket
- 연봉/계약기간/나이: candidate asset snapshot
- 니즈 정합성: `_get_need_map`, `_best_need_tag` 계열 유틸

---

## 6) 타깃 클래스별 활성 skeleton 정책 (1차안)

### 6-1. 롤플레이어 / expiring 타깃
- `player_swap.role_swap_small_delta`
- `player_swap.bench_bundle_for_role`
- `salary_cleanup.rental_expiring_plus_second`
- `salary_cleanup.pure_absorb_for_asset`
- `pick_engineering.swap_purchase`

### 6-2. 중간급 주전 타깃
- `player_swap.role_swap_small_delta`
- `player_swap.fit_swap_2_for_2`
- `player_swap.starter_for_two_rotation`
- `timeline.veteran_for_young_plus_protected_first`
- `salary_cleanup.partial_dump_for_expiring`
- `pick_engineering.swap_substitute_for_first`

### 6-3. 상급 주전 / 준스타 타깃
- `player_swap.starter_for_two_rotation`
- `player_swap.three_for_one_upgrade`
- `timeline.veteran_for_young_plus_protected_first`
- `timeline.bluechip_plus_first_plus_swap`
- `player_swap.star_lateral_plus_delta`
- `pick_engineering.first_split`

### 6-4. 순수 픽/보호/스왑 거래 타깃
- `pick_engineering.first_split`
- `pick_engineering.second_ladder_to_protected_first`
- `pick_engineering.swap_purchase`
- `protection_step_up_down` (modifier flow로 구현)

> 각 타깃당 기본 6~10개 skeleton을 열고, 후보 확장은 variant 단계에서 제한한다.

---

## 7) shape/예산 정책 변경안 (현실성 우선)

### 7-1. config 상한 완화 (1차)
- `max_players_per_side`: 2 → 4
- `max_players_moved_total`: 4 → 7
- `max_assets_per_side`: 6 → 9
- `max_picks_per_side`: 3 → 4
- `max_seconds_per_side`: 2 → 4

### 7-2. beam/variant 정책
- `base_beam_width`: 8 → 12 (초기 다양성 확대)
- `expand_variants()` hard cap은 유지하되, domain별 균등 슬롯 보장.

### 7-3. 최소 안전장치
- 절대 hard cap(`max_validations_hard`, `max_evaluations_hard`)는 유지.
- 팀/선수 중복 억제 penalty는 남기되 계수를 완화.

---

## 8) 보호/스왑 modifier 설계

### 8-1. 적용 시점
1. base skeleton 생성
2. salary/roster 수리
3. protection/swap modifier 적용
4. 재검증/재평가

### 8-2. 적용 규칙
- 1st 포함 skeleton은 modifier 후보를 기본 제공.
- `swap_substitute_for_first`: protected first 대신 swap+seconds 조합을 대체안으로 생성.
- protection은 `pick_protection_decorator`의 TOP-N 생성 로직과 충돌하지 않게,
  “skeleton 의도 강도”를 힌트로 전달한다.

---

## 9) 단계별 구현 순서

### 단계 A (기반 리팩터)
- 스켈레톤 레지스트리/빌더 인터페이스 도입.
- 기존 4개 스켈레톤을 레지스트리 기반으로 이관(동작 동일 유지).

### 단계 B (코어 신규 스켈레톤)
- 우선순위 1:
  - `role_swap_small_delta`
  - `fit_swap_2_for_2`
  - `starter_for_two_rotation`
  - `three_for_one_upgrade`
  - `veteran_for_young_plus_protected_first`
  - `pure_absorb_for_asset`
- 우선순위 2:
  - `bench_bundle_for_role`
  - `change_of_scenery_young`
  - `bad_money_swap`
  - `first_split`
  - `second_ladder_to_protected_first`
  - `swap_purchase`

### 단계 C (modifier + 라우팅)
- protection/swap modifier 파이프 연결.
- 타깃 클래스별 skeleton 활성 정책 연결.

### 단계 D (형상 상한 완화)
- config 상한 변경 + 회귀 점검.
- 제안 로그에서 신규 `skeleton_id/domain` 노출.

---

## 10) 검증 계획

### 10-1. 단위 테스트
- skeleton별 생성 최소 1건 보장 테스트.
- 타깃 클래스별 활성 set 라우팅 테스트.
- modifier 적용 후 deal 유효성(중복 자산/샐러리 매칭/로스터 제한) 테스트.

### 10-2. 시뮬레이션 회귀 지표
- 경기당 평균 오퍼 수, 고유 skeleton_id 수.
- 스타/준스타 타깃의 “2개 이상 대안 패키지” 비율.
- 2-for-2, 3-for-1, pick engineering 딜 출현률.
- validations/evaluations 사용량(폭주 여부 확인).

### 10-3. 관측 중심 운영
- 1차는 acceptance 엄격도보다 제안 다양성 관측에 초점.
- 과다 제안/비현실 제안은 2차에서 threshold 튜닝으로 정리.

---

## 11) 비목표(이번 라운드에서 하지 않음)
- 거래 성사 판정식(`required_surplus`, `overpay_budget`)의 대규모 재설계.
- proactive listing 정책의 근본 변경.
- UI/프론트 노출 정책 변경.

---

## 12) 기대 효과
- 단순 `young+pick`, `1-for-1`, `2-for-1` 반복에서 벗어나,
  실제 NBA 시장에 가까운 패키지 다양성이 즉시 증가한다.
- 스타급/준스타급 대상에서도 “현실적인 중간 단계 패키지”가 생성되어,
  오퍼가 전무하거나 극단적으로 단순한 문제를 완화한다.
- 보호/스왑을 독립 레버로 활용해 협상 경로가 늘어나고,
  같은 선수 조합에서도 조건 차등 제안이 가능해진다.
