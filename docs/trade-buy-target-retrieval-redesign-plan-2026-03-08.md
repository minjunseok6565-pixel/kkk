# BUY 타깃 탐색/우선순위 재설계 실행 계획 (2026-03-08)

## 0) 목표
- 기존의 `need-tag index + 고정 scan_limit(need_n * 3)` 중심 구조를 대체하여,
  - **리그 전체에서 후보를 발견**할 수 있게 하고
  - **need는 배제 필터가 아니라 점수 가중치**로 동작하게 하며
  - **시즌 초엔 공개 매물(listed) 중심, 마감 임박 시 비공개(non-listed) 확장**이 일어나도록 만든다.
- 동시에, 계산량 폭증을 막기 위해 **예산 기반 다단계 탐색 + 가드레일**을 도입한다.

---


## 0-1) 구현 반영 상태 (업데이트)

아래 항목은 현재 코드 반영 상태 기준입니다.

- ✅ BUY 타깃 탐색이 `incoming_all_players` 기반 전역 경로로 전환됨
- ✅ `scan_limit = need_n * 3` 기반 BUY 스캔 루프 제거
- ✅ Tiered retrieval(Tier0 listed / Tier1 non-listed seed / Tier2 optional expand) 구현
- ✅ quota merge( listed 최소 보장 / listed 최대 비중 / non-listed 확보 ) 반영
- ✅ deadline/urgency 기반 cap 산출 유틸 및 budget 연동 가드(코어 조기 중단 포함) 반영
- ✅ 핵심 회귀 테스트( listed always-on, quota 분리, deadline 단계별 확장, 고가치 후보 미누락 ) 추가

---

## 1) 현재 문제 재정의
1. BUY 후보 진입이 need-tag 인덱스에 과도 의존
   - 특정 태그 상위권이 아니면 후보군 진입 자체가 어려움.
2. 고정 스캔 상한(`scan_limit = need_n * 3`)으로 앞순번에서 예산이 소모되면
   - 후순번의 고가치/유니크 자산을 보지 못하는 현상 발생.
3. listing 신호가 “랭크 boost” 수준이라
   - 시장 노출 신호를 탐색 단계에서 적극 활용하지 못함.

---

## 2) 목표 아키텍처 (실전형)

### 2-1. 3계층 Retrieval 파이프라인
- **Tier 0 (Always-on Listed Pool)**
  - 활성 PUBLIC listing 선수는 deadline pressure와 무관하게 항상 탐색 대상에 포함.
- **Tier 1 (Cheap Non-Listed Pool)**
  - 비listed 중 저비용 휴리스틱(팀 니즈/포지션/기본 fit/거래 가능성)으로 1차 압축.
  - 시즌 초엔 소량만 샘플링.
- **Tier 2 (Expanded Non-Listed Pool)**
  - deadline pressure가 높을수록 리그 전역에서 추가 샘플링.
  - 시즌 후반으로 갈수록 팀/선수 탐색 상한이 상승.

### 2-2. need 태그의 역할 전환
- 기존: need-tag로 후보 포함/배제.
- 변경: 후보 포함은 리그 전역 기반으로 하고,
  - need 일치도는 최종 점수의 가중치로 반영.
  - 즉 `retrieval`과 `ranking`을 분리.

### 2-3. scan_limit 재설계
- `need_n * 3` 고정 스캔 폐기.
- 대신 **계층별 quota + 보장 슬롯** 도입:
  - `listed_min_quota`: 항상 확보되는 최소 슬롯
  - `non_listed_base_quota`: 시즌 초 기본 슬롯
  - `non_listed_deadline_bonus`: deadline pressure 비례 추가 슬롯
- 특정 선두 구간에서 quota를 모두 써버려도, 다른 계층/후속 그룹에 대한 **탐색 보장 슬롯**을 남긴다.

---

## 3) 상세 구현 단계

### 단계 A. 데이터/인덱스 확장
1. BUY 전용 리그 전역 후보 소스 추가
   - 팀별 outgoing player catalog를 합쳐 `league_buy_pool` 구성.
   - lock/recent-signing ban/cooldown 등 하드 제약은 초기 필터로 유지.
2. listed metadata 조회를 retrieval 단계 의사결정에 사용
   - 현재 ranking boost에만 쓰던 listing 정보를 후보 선별 우선순위에 반영.

### 단계 B. Retrieval 정책 구현
1. Tier 0 구성
   - 활성 PUBLIC listed 중 거래 가능 후보를 전수 포함(또는 매우 높은 상한).
2. Tier 1 구성
   - 비listed 선수에 대해 초저비용 pre-score 계산 후 상위 일부 샘플링.
3. Tier 2 구성
   - `deadline_pressure`, `urgency`, `posture` 기반으로 확장 스캔 수행.
4. 계층 병합
   - 중복 제거(선수 ID 기준) 후 source tier를 메타데이터로 보존.

### 단계 C. Ranking 정책 구현
1. 기본 점수
   - market, fit, salary, contract horizon 등을 normalize하여 조합.
2. need 가중치
   - 후보의 다중 태그 공급/역할값과 팀 need map의 유사도로 가중.
   - need 불일치 시 “감점”은 주되 “배제”는 하지 않음.
3. listed 보정
   - listed는 rank boost + 탐색 우선권(이미 retrieval에서 반영).

### 단계 D. 예산/상한 통합
1. 기존 `DealGeneratorBudget`과 정합
   - `max_targets`, `max_evaluations`를 넘지 않도록 retrieval 단계에서 hard cap.
2. 동적 상한 함수
   - 시즌 초: 팀/선수 상한 낮게 유지.
   - 마감 임박: 상한 점진 상승.
3. 안전 중단(anytime)
   - budget 소진 임계치 도달 시 고비용 tier(Tier 2) 먼저 중단.

---

## 4) 권장 가드레일 반영 항목

### 4-1. Quota 분리 가드
- `listed_quota`와 `non_listed_quota`를 분리해 한쪽이 전부 잠식하지 못하게 함.
- 최소/최대 비율 가드 도입(예: listed가 과도할 때도 non-listed 탐색 슬롯 보장).

### 4-2. 점진 상한 가드
- 선형 대신 완만한 S-curve 상한 함수 사용.
- 마감 직전 급격한 계산 폭증을 방지.

### 4-3. 스코어 정규화 가드
- need/market/salary 항을 동일 스케일로 정규화.
- 특정 항(예: salary penalty)이 전체 점수를 지배하지 않도록 clamp.

### 4-4. 다양성/반복 억제 가드
- 동일 player/team 반복 노출 soft penalty 강화.
- 최근 reject/counter된 조합에 재시도 cooldown 적용.

### 4-5. 성능 가드
- retrieval 단계별 soft timeout(또는 iteration cap) 적용.
- 상위 N 후보만 다음 단계로 전달.

---

## 5) 설정값(신규/수정) 제안

### 현재 운용 대상 config 키
- `buy_target_listed_min_quota`
- `buy_target_listed_max_share`
- `buy_target_non_listed_base_quota`
- `buy_target_non_listed_deadline_bonus_max`
- `buy_target_max_teams_scanned_base`
- `buy_target_max_teams_scanned_deadline_bonus`
- `buy_target_max_players_scanned_base`
- `buy_target_max_players_scanned_deadline_bonus`
- `buy_target_expand_tier2_enabled`
- `buy_target_expand_tier2_budget_share`
- `buy_target_retrieval_iteration_cap`
- `buy_target_need_weight_scale`
- `buy_target_need_mismatch_floor`
- `buy_target_market_weight`
- `buy_target_fit_weight`
- `buy_target_salary_penalty_weight`
- `buy_target_salary_penalty_cap`

### 운영상 재튜닝 포인트
- `buy_target_listing_interest_*`는 retrieval 우선권 + rank 보정을 함께 고려해 조정
- budget 압박 구간에서는 core의 budget guard가 Tier2/iteration을 자동 축소

---

## 6) 테스트/검증 계획

### 6-1. 단위 테스트
1. listed always-on 보장
   - deadline pressure=0이어도 listed 후보가 타깃 풀에 포함되는지 검증.
2. non-listed 확장
   - deadline pressure 증가에 따라 non-listed 후보 수가 단조 증가하는지 검증.
3. need 필터 제거 검증
   - need 불일치 후보도 후보군에 남되, 점수는 낮아지는지 검증.
4. quota 보호
   - listed 과밀 상황에서도 non-listed 슬롯이 보장되는지 검증.
5. scan_limit 제거 회귀
   - 선두 구간 소모로 후속 고가치 자산이 완전 누락되지 않는지 검증.

### 6-2. 시뮬레이션 회귀
1. 시즌 초 30일/중반 30일/마감 전 14일 구간별 비교.
2. 지표:
   - 스타급 자산 오퍼 도달률
   - listed vs non-listed 오퍼 비중
   - 제안 다양성(고유 선수/상대팀)
   - 평균 validations/evaluations 소모량
   - reject/counter 비율

### 6-3. 성능 기준선
- 기존 대비 tick당 계산시간 증가율 상한 설정(예: +15~20% 이내).
- 초과 시 Tier 2 budget share 자동 축소 fallback 적용.

---

## 7) 배포 순서 (리스크 완화)
1. Feature flag로 신규 retrieval 모드 병행 탑재.
2. 내부 시뮬레이션에서 지표 비교.
3. config 튜닝(쿼터/상한/가중치) 1~2회 반복.
4. 안정화 후 신규 모드 기본값 전환.

---

## 8) 예상 리스크와 대응
- **리스크:** listed 과다 시 시장이 블록 중심으로 과도하게 수렴
  - **대응:** non-listed 최소 quota 강제 + listed cap 도입
- **리스크:** 마감 임박 시 계산량 급증
  - **대응:** Tier 2 budget share 상한 + 조기 중단
- **리스크:** need 영향력 약화로 팀 정체성 흐려짐
  - **대응:** need score floor/bonus 재튜닝, posture별 계수 차등
- **리스크:** 후보 다양성은 늘지만 실제 성사율 저하
  - **대응:** acceptance prior(거래 가능성 사전확률) 반영한 pre-score 필터 강화

---

## 9) 게이머 체감 변화 (최하단 설명)
- 시즌 초에는 “트레이드 블록에 나온 선수” 위주로 문의가 들어와서, 시장이 조용하지만 설득력 있게 시작됩니다.
- 시즌이 진행되고 마감이 다가오면, 블록에 없던 의외의 선수에게도 실제로 문의가 늘어나 “마감 전 시장 과열”이 자연스럽게 보입니다.
- 슈퍼스타/고가치 자산도 팀 니즈와 완벽히 일치하지 않더라도 완전히 무시되지 않고, 상황에 따라 진지한 오퍼가 들어올 가능성이 높아집니다.
- 동시에 AI는 무작정 모든 선수를 다 계산하지 않고, 단계적으로 후보를 좁혀 계산하므로 게임 속도 저하를 최소화합니다.
- 결과적으로 유저 입장에서는 “항상 같은 선수만 도는 시장”이 줄고, 시즌 맥락(초반 탐색 ↔ 마감 압박)에 맞는 현실적인 트레이드 생태계를 체감하게 됩니다.


## 10) 운영 튜닝 참고
- 상세 파라미터 조정 방법: `docs/trade-buy-target-retrieval-tuning-guide.md`
