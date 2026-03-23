# 팀 티어 임계값 고정 리스크 검증 브리핑 (2026-03-21)

## 결론 요약
- **우려는 실제로 발생할 수 있는 수준이 아니라, 현재 설정상 거의 필연적으로 발생**합니다.
- 현재 티어 임계값(`0.853/0.840/0.821/0.809`)은 사실상 `overall_score` 단독 분포(로스터 기반)에서만 맞춰진 값입니다.
- 그런데 시즌 후반으로 갈수록 `performance_score` 가중치가 최대 `0.80`까지 올라갑니다. 이때는 blended 점수가 성능 점수 중심으로 재정의되는데, 임계값은 그대로라서 **대부분 팀이 REBUILD/TANK로 쏠리는 구조적 편향**이 생깁니다.

## 현재 코드 기준 사실 확인
- `overall_score`는 `0.35 * star_power + 0.65 * depth` 입니다.
- `performance_score`는 `win_pct/net_rating/point_diff/trend/bubble` 가중합입니다.
- 시즌 진행 가중치는 `performance_weight_points`로 정의되며, 후반에는 `w_perf=0.80`까지 상승합니다.
- 티어 임계값은 현재 다음과 같습니다.
  - CONTENDER `>=0.853`
  - PLAYOFF_BUYER `>=0.840`
  - FRINGE `>=0.821`
  - REBUILD `>=0.809`
  - else TANK

## 엑셀 로스터(30팀) 기준 재현 결과
- 로스터의 팀별 `top3/top8 OVR`로 `overall_score`를 계산해 현 임계값에 매핑하면 분포는 정확히 **7 / 7 / 8 / 5 / 3**이 재현됩니다.
- 즉, 질문에서 말한 “overall-only 기준으로 맞춘 임계값” 설명과 실제 데이터가 일치합니다.

## 왜 시즌 후반에 분류가 깨지는가 (정량)

blended 식:

`blended = (1-w_perf)*overall + w_perf*performance`

티어 유지(예: FRINGE 이상) 조건을 performance에 대해 풀면:

`performance >= (0.821 - (1-w_perf)*overall) / w_perf`

### 1) w_perf=0.80(시즌 후반)에서의 필요 성능
- 엑셀 30팀 전체에 대해 계산한 결과, FRINGE 이상을 유지하려면 팀별로 `performance_score`가 대략 **0.808~0.830** 범위를 요구합니다.
- 30팀 평균 필요치: **0.817**.

### 2) 현실적인 performance_score 분포와의 충돌
- 현재 performance 공식을 기반으로 현실적 범위(승률/넷레이팅/득실/트렌드, 버블보너스 포함) 몬테카를로 샘플링 시:
  - 중앙값(p50): **0.488**
  - p75: **0.621**
  - p90: **0.697**
  - p95: **0.726**
  - 최대 관측치(샘플 내): **0.829**
- 즉 후반에 FRINGE 커트(필요치 ~0.817)를 넘길 확률이 극히 낮아집니다.

### 3) 단순 고정 performance 가정에서도 붕괴 확인
- `w_perf=0.80`일 때 performance를 0.45~0.75로 고정해 30팀 분류를 돌리면 **모든 경우 30팀 전부 TANK**가 됩니다.

## 해석
- 문제의 본질은 “임계값 자체가 높다”라기보다,
  - 임계값은 `overall-only` 분포에 캘리브레이션되어 있는데,
  - 실제 운영은 `overall/performance` 혼합 점수로 이동하고,
  - 특히 후반에는 `performance`의 절대 스케일과 분산에 비해 임계값이 과도하게 높게 남는다는 점입니다.
- 따라서 현재 우려는 타당하며, 코드/수식 관점에서 **재현 가능하고 이미 잠재 내재된 이슈**로 보는 것이 맞습니다.

## 권장 대응 (우선순위)
1. **가중치 구간별 동적 임계값(권장)**
   - `w_perf` 구간마다 티어 커트를 재캘리브레이션.
   - 최소 기준: 각 구간에서 과거 의도한 팀수 분포(또는 목표 분포)를 유지하도록 quantile 매핑.
2. **단일 임계값 유지가 필요하면 스케일 정규화 추가**
   - `overall_score`와 `performance_score`를 동일 분포(예: z-score/quantile normalize)로 맞춘 후 blend.
3. **안전장치 추가**
   - 시즌 후반에도 티어 하한 보호(예: elite overall 팀은 FRINGE 미만으로 과도 하락 금지).

## 운영 체크포인트
- telemetry에 아래를 꼭 남겨야 합니다.
  - `overall_score`, `performance_score`, `w_perf`, `blended`, `final_tier`
  - 진행률 구간별 tier 분포 히스토그램
- 목표 분포와 실제 분포의 KL-divergence 또는 절대오차를 모니터링해 자동 경보 기준 설정 권장.
