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

## 단계별 도입 로드맵

### Phase 0: 계측/로그 (무영향)
- 기존 분류와 후보 분류를 동시 계산하여 로그만 수집.
- 주요 로그: 기존 tier, 신규 tier score, 팀 컨텍스트, 최종 딜 성과.

### Phase 1: 상대컷 보정 도입 (저위험)
- 기존 하드컷은 유지하되, percentile anchor 혼합으로 경직성을 완화.
- 라벨 세트/호출 인터페이스는 동일 유지.

### Phase 2: 정책 레버 활성화
- `config` strictness/bias 계열 인자를 실제 컷 이동에 연결.

### Phase 3: 안정화/확률화
- 히스테리시스 또는 soft decision 적용.
- 경계 자산 flip rate를 KPI로 관리.

### Phase 4: 라벨 운영 안정화
- 관측 KPI 기반으로 weight 튜닝과 운영 가이드를 고정.

---

## 검증 지표 (KPI)
- **Tier Flip Rate**: 임계 근처 자산의 불필요한 등급 변동률
- **Routing Stability**: 유사 자산의 스켈레톤 라우팅 일관성
- **Deal Plausibility**: 성립 딜의 NBA 현실감(내부 평가/휴리스틱)
- **Outcome Diversity**: 단순 딜 쏠림 완화 여부
- **Operator Controllability**: config 조절 시 의도한 방향으로 분포 이동하는지

---

## 리스크 및 대응
1. **과도한 복잡화 리스크**
   - 대응: feature를 단계적으로 추가, 설명가능한 가중합 모델 우선.

2. **분포 드리프트 리스크**
   - 대응: 시즌/주기별 재보정, percentile 기반 기본 안전장치 유지.

3. **튜닝 난이도 증가**
   - 대응: config 레버 최소셋부터 시작, 관측 가능한 KPI 중심 운영.

4. **기존 밸런스 붕괴 리스크**
   - 대응: shadow mode + 점진 배포 + 즉시 fallback 스위치.

---

## 권장 우선순위 (실행 관점)
1. percentile anchor + strategy/contract 오프셋 운영값 확정
2. `config` weight(`tier_market_percentile_weight`, `tier_strategy_weight`, `tier_contract_weight`) 튜닝
3. 히스테리시스 기반 라벨 안정화
4. 로그/KPI 기반 회귀 모니터링 체계 고정

> 위 순서는 구현 난이도 대비 체감 개선이 크고, 회귀 리스크를 관리하기 쉽다.

---

## 결론
핵심은 "고정 숫자 컷"을 유지·보수하기보다, **리그 컨텍스트와 팀 전략을 반영하는 적응형 분류 정책**으로 전환하는 것이다.
이 전환은 딜 생성 파이프라인 전반(스켈레톤 라우팅, 카운터 방향성, 최종 딜 다양성)의 자연스러움을 높이며,
유저가 느끼는 NBA 유사성을 실질적으로 개선할 수 있다.

---

## 재검토: 현재 프로젝트 구조 기준 달성 가능성 점검

아래 점검은 실제 코드 구조를 기준으로 "지금 바로 운영 가능한 신호"를 정리한 것이다.

### 1) 함수 시그니처 관점의 현실성
현행 `classify_target_tier()`는 `tier_ctx`를 받을 수 있으며, 호출 경로에서 팀 상태/리그 분포/계약 프록시가 이미 연결된다.

- 즉시 가능: `target`/`sale_asset` 기본값 + `TierContext`(팀전략/percentile/계약프록시) + `config` weight 기반 컷 보정
- 운영 포인트: weight 튜닝과 라벨 안정화(히스테리시스)

> 결론: 현재 구조에서도 "컨텍스트 적응형 분류"의 핵심은 이미 사용 가능하며, 남은 과제는 신규 feature 연결이 아니라 운영 튜닝/안정화다.

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

### 3) 수정된 실행 우선순위(현 구조 친화)
1. **1차(즉시 가능)**: `tier_market_percentile_weight`/`tier_strategy_weight`/`tier_contract_weight` 기본 운영값 확정
2. **2차(중간 난이도)**: 팀 posture별(컨텐더/리빌더) 시나리오 튜닝 매트릭스 정리
3. **3차(운영 고도화)**: 히스테리시스 강도와 expiring 할인 규칙을 로그 기반으로 안정화
4. **4차(지속 운영)**: KPI 대시보드 기반 회귀 모니터링/재튜닝 루프 정착

### 4) 브리핑 결론
- 이전 구상안의 방향성(적응형/안정화/정책화)은 유효하다.
- 현재 구조 기준으로도 핵심 입력 신호(percentile/팀전략/계약프록시)는 이미 연결되어 있어 운영 튜닝 중심으로 추진하는 것이 현실적이다.
- 당장 실행 가능한 핵심은 `config` weight 튜닝과 라벨 안정화(히스테리시스)이며, 이미 연결된 percentile/팀전략/계약프록시 신호의 운영 가이드를 고정하는 것이다.
