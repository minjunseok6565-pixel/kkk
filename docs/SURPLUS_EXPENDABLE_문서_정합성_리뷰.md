# SURPLUS_EXPENDABLE 문서 정합성 리뷰 (중립 검토)

검토 대상:
- `docs/SURPLUS_EXPENDABLE_통합_적용_계획.md`
- `docs/SURPLUS_EXPENDABLE_구체_수정안.md`

검토 기준:
1. 현재 프로젝트에 이미 존재하는 로직과의 중복 여부
2. 프로젝트에 없는 로직/API를 존재한다고 가정했는지 여부
3. 용어/데이터 흐름/파일 단위 수정안의 실현 가능성
4. 단계적 적용(호환성) 측면의 현실성

---

## 총평

두 문서는 **방향성(LOW_FIT 단독 SELL 진입 억제, 보호 신호 도입, 단계적 alias 운영)** 측면에서 프로젝트 현황과 대체로 정합합니다.
특히 "버킷명만 변경하면 연쇄 영향이 크다"는 전제가 실제 코드 구조와 일치합니다.

다만 일부 항목은 현재 코드베이스 기준으로 **직접 구현 전 보정이 필요**합니다.
핵심적으로는:
- `FitEngine`에 문서가 권장한 `compute_team_need_from_supply(...)` API가 현재 없음
- `PlayerTradeCandidate`는 아직 `raw_trade_block_score`, reason/protection flags 등을 보관하지 않음
- listing/정렬/sweetener는 아직 `surplus_score` + 구 surplus 2버킷 중심으로 고정되어 있음

즉, 문서의 큰 방향은 유효하지만, "현재 존재하는 함수/필드"로 보이게 읽힐 수 있는 표현은 일부 수정하는 것이 안전합니다.

---

## 1) 현재 코드와 잘 맞는 부분

### 1-1. outgoing surplus가 다중 모듈에 연결되어 있어 단순 rename이 위험하다는 진단
정합합니다. 실제로 surplus 버킷 문자열은 asset_catalog, SELL targets, proactive listing, counter-offer, dealgen 유틸/repair/fit_swap 등 여러 경로에서 직접 소비됩니다.

### 1-2. 현재 surplus 판단이 팀 need_map 기반이라는 진단
정합합니다. `asset_catalog`는 선수별 `fit_vs_team` 및 `surplus_score=1-fit_vs_team`를 만들고, redundancy도 팀 need_map 기반으로 산출합니다.

### 1-3. LOW_FIT 단독 진입 억제 + 보호 신호(코어/정체성/의존도) 도입 필요성
정책적 방향으로 타당합니다. 현재 SELL 정렬/상장 필터는 사실상 `surplus_score` 중심이라 보호 신호 개입 여지가 적습니다.

---

## 2) 보정이 필요한 가정/표현

### 2-1. `FitEngine.compute_team_need_from_supply(...)`는 현재 없음
문서 2.1의 "권장" 경로는 좋은 설계 아이디어지만, 현 시점 API로는 존재하지 않습니다.
현재 `FitEngine`은 `DecisionContext.need_map`을 **소비만**하고 팀 need를 생성하지 않는 구조입니다.

권장 보정 문구:
- "`compute_team_need_from_supply`는 신규 구현 필요"를 명시
- 또는 "R1은 역보정 근사 함수"를 기본안으로 격하

### 2-2. `PlayerTradeCandidate` 확장 전제를 명시적으로 분리 필요
문서는 신규 필드를 활용한 정렬/게이트를 제안하지만, 현재 candidate 모델에는 `fit_vs_team`, `surplus_score` 중심 필드만 존재합니다.
`raw_trade_block_score`/flags를 쓰려면 모델 확장이 선행되어야 합니다.

권장 보정:
- Batch 1 선행조건에 "dataclass 직렬화 영향(테스트 fixture/API schema 영향) 점검"을 명확히 추가

### 2-3. `core_proxy` 산식의 `market.now` 의존성은 데이터 결손 처리 기준을 더 명확히 해야 함
현재 코드에는 `market.now`가 산출되긴 하지만, 일부 fallback 경로에서 0.0이 들어올 수 있습니다.
문서가 fallback을 제시하긴 했으나, 실제 운영 기준(누락률 N% 이상이면 total rank 대체 등)을 수치로 못박아두면 안전합니다.

### 2-4. `basketball_fair_value_m` 역매핑은 현재 코드에 SSOT가 없음
문서는 역매핑 함수 언급이 있으나 현 코드에서 바로 재사용 가능한 표준 함수가 보이지 않습니다.
R1에서는 해당 분기를 0 처리(문서 제안대로)하는 쪽이 현실적입니다.

---

## 3) 중복 구현 리스크

### 3-1. BUY 타깃 쪽 계약/코어 유사 로직과의 부분 중복 가능성
`trades/generation/dealgen/targets.py`에는 이미 BUY 타깃 선정을 위한 `basketball_total`, `contract_gap_cap_share`, `team_contract_sensitivity` 계열 로직이 있습니다.
문서의 SELL 전용 신호를 만들 때 동일 산식을 별도로 복붙하면 유지보수 중복이 커질 수 있습니다.

권장:
- 공통화 가능한 계산(`contract_pressure` 변환, `basketball_norm`)은 helper로 추출
- BUY 전용 의미와 SELL 전용 의미가 다르면 이름을 분리하고 주석으로 경계 명시

### 3-2. 버킷 순회 하드코딩 분산은 문서 지적대로 실제 이슈
여러 파일에 surplus 버킷 tuple 하드코딩이 산재해 있으므로, 공통 상수 도입 제안은 실효성이 높습니다.

---

## 4) 용어/정책 측면 피드백

### 4-1. `trade_block_score` 명명
현재 `trade block`은 "시장에 올린다" 의미로도 읽혀 혼동 여지가 있습니다.
문서 의도는 "매도 적합성"에 가까우므로, 내부 점수명은 `expendable_score_raw` 등으로 분리하고,
최종 노출 레이어에서만 trade block 노출 여부를 결정하는 편이 해석 충돌을 줄입니다.

### 4-2. `misfit_peer` vs `fit_vs_team` 병행 로그
문서의 raw 분포 로그 제안은 좋습니다. 여기에 "기존 surplus_score와의 상관"을 추가하면 튜닝/회귀 판단이 쉬워집니다.

### 4-3. posture별 protection weight
제안값 자체는 출발점으로 무난하지만, 현재 시스템이 posture를 다양한 정책에서 이미 사용하므로
초기 롤아웃(R1)에서는 weight 범위를 더 좁게(예: ±0.1 내) 시작하는 것도 안정성 측면에서 고려할 만합니다.

---

## 5) 결론 (실행 가능성 평가)

- **적용 가능성:** 높음
- **즉시 구현 위험도:** 중간 (모델 확장 + 다수 소비 모듈 동시 수정 필요)
- **문서 품질:** 방향/단계 전략은 우수, 일부 API 존재 가정 표현만 보정 필요

최적화 관점에서의 권장 최종 정리:
1. 문서에 "현재 없음(신규 구현 필요)" 태그를 API/필드 단위로 명시
2. R1 목표를 "신호 계산 + alias + 로그"로 더 엄격히 제한
3. SELL/BY 공통 신호 변환 helper를 먼저 두어 중복 구현 방지
4. `trade_block_score` 명칭 혼동을 줄이는 내부 네이밍 가이드 추가

이 4가지를 반영하면, 현재 프로젝트에 적용하기 위한 정합성과 운영 안정성이 더 높아집니다.

---

## 6) 추가 재검토 반영 (타 채널 지적사항 교차 확인)

아래 항목은 별도 검토 결과와 비교해 **중복되지 않는 보완점만 추려** 반영한 내용입니다.

### 6-1. value_breakdown 계열 필드의 실제 위치 명시 필요
`contract_gap_cap_share`, `expected_cap_share_avg`, `actual_cap_share_avg`, `basketball_total`는
현재 `PlayerTradeCandidate`에 직접 들어있지 않고, `IncomingPlayerRef` 쪽에 실려 사용되는 구조입니다.

즉 SURPLUS_EXPENDABLE 구현 문서에서 해당 수치를 "후보 객체에서 바로 읽는 전제"로 쓰면 오해 소지가 있습니다.

권장 보정:
- Batch 1 선행 과업에 "`PlayerTradeCandidate`로 value_breakdown 기반 필드 이관/확장"을 명시하거나,
- 최소 R1에서는 `asset_catalog` 내부 임시 dict로 계산 후 최종 candidate 필드로 복사하는 단계를 구현 경로로 고정.

### 6-2. raw/trade_block 전환은 feature flag를 권장이 아니라 필수로 격상
정렬(`targets.py`)과 threshold(`listing_policy.py`)가 현재 `surplus_score` 중심으로 고정돼 있으므로,
`raw_trade_block_score` 전환은 테스트/회귀 충격이 큰 변경입니다.

권장 보정:
- feature flag를 "선택"이 아닌 "필수" 마이그레이션 장치로 명시
- 최소 1개 릴리즈 동안 dual-read를 강제:
  - 정렬/게이트에서 `raw_trade_block_score`(또는 `trade_block_score`)가 없으면 `surplus_score` fallback
  - telemetry에 신규/기존 키의 동시 분포를 저장해 전환 전후 drift를 비교

### 6-3. A 항목(need 생성 API 부재)은 기존 판단 유지
`fit_engine.compute_team_need_from_supply(...)` 부재 지적은 타당하며 기존 결론과 동일합니다.
따라서 본 문서 2-1의 보정안(신규 API 과제 명시 또는 R1 근사식 고정)을 그대로 유지합니다.
