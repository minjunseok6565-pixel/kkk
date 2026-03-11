# 타깃 Tier 분류 고도화 구상안 (NBA 유사성 강화)

## 배경
현재 `classify_target_tier()`는 고정 임계값(`market >= 86/72/52`)과 일부 예외 규칙(PICK, expiring+저가) 중심으로 `ROLE/STARTER/HIGH_STARTER/STAR/PICK_ONLY`를 분류한다. 이 방식은 구현이 단순하고 예측 가능하지만, 시즌 스케일 변화·팀 상황·역할 희소성·경계 자산의 연속성을 반영하기 어렵다.

본 문서는 **즉시 구현**이 아니라, 현행 분류기를 실제 NBA 의사결정 흐름에 가깝게 발전시키기 위한 **설계 방향**을 정리한다.

---

## 현행 방식의 핵심 한계 (요약)
1. **절대 임계값 의존**
   - 리그 가치 인플레이션/디플레이션, 시즌별 스케일 변화가 발생해도 컷이 고정되어 왜곡 가능.

2. **컨텍스트 미반영**
   - 팀 전략(컨텐딩/리빌딩), 데드라인 압박, 포지션 scarcity 반영 부족.

3. **경계 불연속성**
   - 임계값 근처 자산이 미세한 수치 변화로 다른 tier로 급변.

4. **확장 인터페이스 미활용**
   - `config` 인자는 존재하지만 현재 분류 정책에 실질 연결되지 않음.

---

## 목표 상태
- 단일 하드컷 분류기에서, **컨텍스트 적응형 점수 기반 분류기**로 전환.
- 결과 라벨은 동일(`ROLE/STARTER/HIGH_STARTER/STAR/PICK_ONLY`)을 유지하되,
  내부 계산을 NBA 현실성 높은 방식으로 고도화.
- “분류 정확도”보다도 **협상 경로의 자연스러움과 안정성**을 우선 최적화.

---

## 제안 아키텍처

## 1) 입력 신호 확장 (Feature Layer)
기존 신호(`market_total`, `salary_m`, `remaining_years`, `is_expiring`, tags) 위에 아래 축을 단계적으로 추가:

- **가치 상대지표(현재 연결됨)**: 리그 전체 market percentile + raw market 혼합
- **계약 질 지표(현재 연결됨)**: `contract_proxy_toxic/trigger/matching/control` 기반 보정
- **팀 전략 지표(현재 연결됨)**: `competitive_tier`, `trade_posture`, `time_horizon`, `urgency`, `deadline_pressure`
- **기본 자산 지표(기존 유지)**: `market_total`, `salary_m`, `remaining_years`, `is_expiring`, tags

> 원칙: 초기에는 "적은 feature + 높은 설명가능성"으로 시작하고, 분류 안정성이 검증되면 신호를 확장한다.

## 2) 컨텍스트 정규화 (Normalization Layer)
절대값 컷 대신 **상대 구간 컷** 사용:

- STAR: 상위 x%
- HIGH_STARTER: 상위 x~y%
- STARTER: 중간권
- ROLE: 하위권

정규화 축(현 프로젝트에서 즉시 사용 가능):
- 리그 전체 분포 기반 market percentile(기본)
- 팀 전략/데드라인 압박 기반 동적 오프셋
- 계약 질 프록시 기반 리스크/유틸리티 오프셋

효과:
- 동일 선수라도 시즌/환경 변화에 따른 상대적 위치를 자연스럽게 반영.

## 3) 정책 레버 연결 (Policy Layer)
`DealGeneratorConfig`를 실제 정책 제어에 연결:

- `tier_strictness`: 보수적/공격적 컷 이동
- `tier_market_percentile_weight`: raw market vs percentile anchor 혼합 강도
- `tier_strategy_weight`: 팀 전략/데드라인 압박 반영 강도
- `tier_contract_weight`: 계약 질 프록시 반영 강도

효과:
- 코드 변경 없이 운영/튜닝으로 분류 정책 조절 가능.

## 4) 라벨 안정화 (Decision Layer)
경계 급변 완화를 위한 선택지:

- **Soft score + argmax tier**
- **히스테리시스(완충구간)**: 직전 tier와 인접 구간에서 급변 억제
- **확률적 동률 해소**: 반복 생성 시 다양성 확보

효과:
- 임계값 근처 자산의 flip-flop 감소, 협상 트랙 급변 완화.

---

## NBA 유사성 강화를 위한 운영 규칙
1. **팀 상태 반영**
   - 컨텐더: 즉전력 가중 ↑, 미래가치 할인 ↑
   - 리빌더: 업사이드/계약 통제권 가중 ↑

2. **데드라인 시간가치 반영**
   - 데드라인 근접 시 만료 계약/로테이션 자원 tier 재평가 강도 상향.

3. **리그 상대가치 반영**
   - 리그 전체 market percentile을 활용해 시즌별 시장 스케일 변화에 대응.

4. **자산 타입 분리**
   - `PICK_ONLY`는 별도 체계 유지하되, 픽 자체도 보호조건/예상구간에 따른 하위 클래스 고려.

---


### 2) 현재 실제로 내려오는 값 (사용 가능)

#### A. `TargetCandidate`(BUY focal)
- `need_tag`
- `tag_strength`
- `market_total`
- `salary_m`
- `remaining_years`
- `age`

#### B. `SellAssetCandidate`(SELL focal)
- `market_total`
- `salary_m`
- `remaining_years`
- `is_expiring`
- `top_tags`

#### C. 분류 함수 내부에서 이미 쓰는 값
- `market_total`, `salary_m`, `remaining_years`, `need_tag`(BUY), `match_tag`(SELL), `is_expiring`(SELL)

#### D. Tick/팀 컨텍스트에서 조회되고 `TierContext`로 주입되는 값
- `TeamSituation`: `trade_posture`, `urgency`, `time_horizon`, `constraints.deadline_pressure`, `preferences`, `signals.*`
- `DecisionContext`: `need_map`, valuation knobs/policies

#### E. `classify_target_tier()`에 현재 직접 연결되어 계산 가능한 확장 입력
1. 리그 market percentile(포지션 분포 제외)
   - 근거: `TradeAssetCatalog.incoming_all_players` 전수 기준으로 `market_percentile_league`를 계산해 `TierContext.market_percentile_league`로 주입.
   - 적용: `_tier_market_anchor()`에서 절대값 `market_total`과 percentile anchor를 혼합.

2. 팀 전략 기반 동적 컷(컨텐더/리빌더별 가중)
   - 근거: `TeamSituation`의 `competitive_tier`, `trade_posture`, `time_horizon`, `urgency`, `deadline_pressure`를 `TierContext`로 주입.
   - 적용: `_tier_strategy_offset()`에서 시장값 오프셋 계산 후 tier 컷에 반영.

3. 계약 질 프록시(옵션/부분보장 직접값은 아님)
   - 근거: `IncomingPlayerRef`의 `contract_gap_cap_share`, cap share 편차, 잔여연수로 `contract_proxy_toxic/trigger/matching/control`을 계산해 `TierContext`로 전달.
   - 적용: `_tier_contract_offset()`에서 tier 보정치로 반영.


