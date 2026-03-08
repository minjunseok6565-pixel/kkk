# BUY 타깃 탐색 재설계 브랜치 감사 리포트 (2026-03-08)

## 대상
- 기준 문서
  - `docs/trade-market-realism-audit-2026-03-08.md`의 2-1 이슈
  - `docs/trade-buy-target-retrieval-redesign-plan-2026-03-08.md`
  - `docs/trade-buy-target-retrieval-implementation-spec-2026-03-08.md`
- 검증 범위
  - 현재 브랜치의 BUY target retrieval 관련 실제 코드/테스트 반영 상태

---

## 결론 요약
- **핵심 구조 전환(need 필터 → need 가중치, 전역 풀 스캔, tiered retrieval, deadline 확장)은 코드상 반영됨.**
- **회귀 방지 테스트도 주요 항목은 갖춰져 있고 실행 성공함.**
- 다만, “시즌 초 listed 위주 체감” 관점에서 보면 **초기 non-listed 노출량이 여전히 큰 편일 수 있어** 체감 목표를 100% 달성했다고 단정하기는 어렵다.

판정:
- 구현 충족도: **높음 (약 85~90%)**
- 게이머 체감(시즌 초 listed 중심성): **부분 충족, 추가 튜닝 권장**

---

## 항목별 검증

### 1) need-tag를 후보 포함/배제가 아닌 가중치로 전환
**판정: 충족**

- 후보 소스가 `incoming_all_players` 전역 풀로 바뀌었고, need는 `_need_similarity_for_ref()`로 점수 가중치에 반영된다.
- need 불일치 시에도 배제하지 않고, 최종 rank에서 `need_bonus`로 가감만 한다.

---

### 2) 리그 전체 스캔 + listed always-on
**판정: 충족**

- 카탈로그 빌드에서 리그 전역 `incoming_all_players` 인덱스를 구축한다.
- BUY 타깃 선택이 이 전역 풀을 순회한다.
- 활성 PUBLIC listing은 Tier0로 분리되어 팀/선수 cap(non-listed용)과 무관하게 항상 수집 가능하다.

---

### 3) deadline 압력 기반 non-listed 확장
**판정: 충족**

- `compute_buy_retrieval_caps()`에서 deadline/urgency 기반 intensity 및 smoothstep으로 `teams_cap`, `players_cap`을 산출한다.
- `non_listed_quota = base + bonus*intensity`로 마감 압력 증가 시 비listed 탐색량이 증가한다.
- 관련 단위 테스트에서 monotonic 증가를 검증한다.

---

### 4) `scan_limit = need_n * 3` 병목 제거
**판정: 충족**

- 기존 need-tag 인덱스/고정 스캔 상한 기반 루프는 제거되었고, quota + caps + iteration cap 구조로 대체되었다.
- “후순번 고가치 후보 미누락” 회귀 테스트가 추가되어 star 후보 진입을 검증한다.

---

### 5) 예산 가드(성능/폭증 방지)
**판정: 충족**

- core에서 budget 수준을 보고 Tier2 활성/점유율/iteration cap을 스케일링한다.
- validations/evaluations 임계치 근접 시 soft stop으로 조기 중단한다.
- 관련 budget guard 테스트가 존재한다.

---

## 미세 갭(체감 리스크)

### A. 시즌 초 listed 중심성은 설정값에 크게 의존
- 현재 기본값은 `buy_target_non_listed_base_quota=8`, `buy_target_max_players_scanned_base=120`, Tier2도 기본 활성이라,
  시즌 초에도 non-listed 후보 노출이 적지 않을 수 있다.
- 즉 구조는 맞지만, “초반엔 블록 중심으로 보인다”는 체감은 리그 데이터/설정에 따라 약해질 여지가 있다.

### B. spec에 적힌 팀 다양성 soft-cap은 미구현
- 구현 명세에 있던 “동일 from_team 과밀 방지 soft cap” 로직은 현재 `select_targets_buy()` 내부에 직접 반영되어 있지 않다.
- 결과적으로 특정 팀의 고pre-score 후보가 비listed 슬롯을 상대적으로 많이 점유할 가능성이 남아 있다.

---

## 테스트 실행 결과
- `python -m unittest trades.generation.dealgen.test_targets_buy_tiered_retrieval trades.generation.dealgen.test_targets_buy_listing_interest trades.generation.dealgen.test_utils_retrieval_caps trades.generation.dealgen.test_core_budget_guard`
  - 결과: `Ran 19 tests ... OK`

---

## 최종 판단 (요청사항 기준)

질문하신 “계획대로 잘 이루어졌는지 + 게이머 체감까지 달성됐는지”에 대해:

1. **계획/명세의 핵심 아키텍처 전환은 실제 코드에 대부분 정확히 반영**되어 있다.  
2. 다만 **게이머 체감(시즌 초 listed 중심 탐색)**은 기본 파라미터 상 non-listed 비중이 아직 높게 나올 수 있어, 
   현재 상태를 “완전 달성”으로 보기는 어렵고 **튜닝 1회 이상이 필요**하다.

권장 즉시 튜닝(운영값):
- `buy_target_non_listed_base_quota` 8 → 3~5
- `buy_target_max_players_scanned_base` 120 → 60~90
- `buy_target_expand_tier2_budget_share` 0.35 → 0.15~0.25
- 필요 시 시즌 초 구간(deadline_pressure<0.25)에서 Tier2를 강제 off하는 조건 추가
