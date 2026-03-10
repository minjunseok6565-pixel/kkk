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

- **가치 상대지표**: 리그 전체/역할군 내 percentile, z-score
- **계약 질 지표**: 만료 프리미엄/장기 부담, 옵션·보장 구조 요약 플래그
- **선수 상태 지표**: 최근 추세(상승/하락), 가용성 리스크(결장/부상 대체지표)
- **팀 적합 지표**: need-fit, 로스터 공백도, timeline 적합성

> 원칙: 초기에는 "적은 feature + 높은 설명가능성"으로 시작하고, 분류 안정성이 검증되면 신호를 확장한다.

## 2) 컨텍스트 정규화 (Normalization Layer)
절대값 컷 대신 **상대 구간 컷** 사용:

- STAR: 상위 x%
- HIGH_STARTER: 상위 x~y%
- STARTER: 중간권
- ROLE: 하위권

정규화 축:
- 리그 전체 분포(기본)
- archetype/포지션별 분포(보정)
- 시즌 단계(오프시즌/중반/데드라인)별 분포 이동

효과:
- 동일 선수라도 시즌/환경 변화에 따른 상대적 위치를 자연스럽게 반영.

## 3) 정책 레버 연결 (Policy Layer)
`DealGeneratorConfig`를 실제 정책 제어에 연결:

- `tier_strictness`: 보수적/공격적 컷 이동
- `tier_inflation_anchor`: 분포 스케일 보정 강도
- `role_scarcity_weight`: 희소 역할 프리미엄
- `win_now_bias` / `future_bias`: 팀 방향성에 따른 라벨 편향

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

3. **희소성 반영**
   - 리그 내 공급이 얇은 archetype(예: 3&D 윙)의 tier 보정.

4. **자산 타입 분리**
   - `PICK_ONLY`는 별도 체계 유지하되, 픽 자체도 보호조건/예상구간에 따른 하위 클래스 고려.

---

## 단계별 도입 로드맵

### Phase 0: 계측/로그 (무영향)
- 기존 분류와 후보 분류를 동시 계산하여 로그만 수집.
- 주요 로그: 기존 tier, 신규 tier score, 팀 컨텍스트, 최종 딜 성과.

### Phase 1: 상대컷 도입 (저위험)
- 하드 임계값을 percentile 기반 컷으로 대체.
- 라벨 세트/호출 인터페이스는 동일 유지.

### Phase 2: 정책 레버 활성화
- `config` strictness/bias 계열 인자를 실제 컷 이동에 연결.

### Phase 3: 안정화/확률화
- 히스테리시스 또는 soft decision 적용.
- 경계 자산 flip rate를 KPI로 관리.

### Phase 4: 팀전략 동적화
- 팀 성향/시즌 단계/캡 상황을 동적으로 반영.

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
1. percentile 컷 도입
2. `config` strictness/bias 활성화
3. 히스테리시스 기반 라벨 안정화
4. 팀 전략/데드라인 반영 확장

> 위 순서는 구현 난이도 대비 체감 개선이 크고, 회귀 리스크를 관리하기 쉽다.

---

## 결론
핵심은 "고정 숫자 컷"을 유지·보수하기보다, **리그 컨텍스트와 팀 전략을 반영하는 적응형 분류 정책**으로 전환하는 것이다.
이 전환은 딜 생성 파이프라인 전반(스켈레톤 라우팅, 카운터 방향성, 최종 딜 다양성)의 자연스러움을 높이며,
유저가 느끼는 NBA 유사성을 실질적으로 개선할 수 있다.

---

## 재검토: 현재 프로젝트 구조 기준 달성 가능성 점검

아래 점검은 실제 코드 구조를 기준으로 "지금 당장 연결 가능한 신호"와 "추가 파이프라인 작업이 필요한 신호"를 분리한 것이다.

### 1) 함수 시그니처 관점의 현실성
현행 `classify_target_tier()` 입력은 `target | sale_asset | match_tag | config`로 제한되어 있고, `tick_ctx`/`team_situation`/`decision_context`를 직접 받지 않는다.

- 즉시 가능: `target`/`sale_asset`에 이미 담긴 값 + `config` 기반 컷 이동
- 추가 작업 필요: 팀 상태(urgency/posture/deadline), 리그 분포 기반 정규화, 동적 전략 반영

> 결론: "정책 레버(config)"는 단기 반영 가능하지만, "컨텍스트 적응형 분류"는 함수 입력/호출 체인 확장이 선행되어야 한다.

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

#### D. Tick/팀 컨텍스트에서 조회 가능한 값(단, 분류 함수에 직접 주입 필요)
- `TeamSituation`: `trade_posture`, `urgency`, `time_horizon`, `constraints.deadline_pressure`, `preferences`, `signals.*`
- `DecisionContext`: `need_map`, valuation knobs/policies

### 3) 현재 값만으로는 부족한 항목 (추가 연결 필요)
기존 문서에서 제안했던 지표 중 아래는 **현재 분류 함수 입력만으로는 직접 계산 불가**다.

1. 리그/포지션 percentile, z-score
   - 이유: 분포 계산용 리그 전수 집합 접근이 분류 함수에 직접 연결되어 있지 않음.

2. 선수 단기 추세(상승/하락) / 가용성 리스크
   - 이유: `TargetCandidate`/`SellAssetCandidate`에 관련 필드 부재.

3. 옵션/부분보장 기반 계약 질 지표
   - 이유: 계약 옵션 정보는 valuation 스냅샷 계층에 있으나 tier 분류 입력 DTO에는 전달되지 않음.

4. 팀 전략 기반 동적 컷(컨텐더/리빌더별 가중)
   - 이유: `TeamSituation`은 존재하지만 `classify_target_tier()` 시그니처에 없음.

### 4) 수정된 실행 우선순위(현 구조 친화)
1. **1차(즉시 가능)**: `config`를 실사용하는 컷 이동/완충구간 도입
   - 예: strictness, expiring 할인 강도, PICK 판정 민감도
2. **2차(중간 난이도)**: 분류 함수에 최소 컨텍스트 주입(`team_situation` 또는 경량 `TierContext`)
3. **3차(고도화)**: 리그 분포 캐시(틱 단위) 기반 percentile/z-score 정규화
4. **4차(확장)**: 계약 옵션/가용성/추세 등 feature 확장

### 5) 브리핑 결론
- 이전 구상안의 방향성(적응형/안정화/정책화)은 유효하다.
- 다만 현재 구조 기준으로는 **"즉시 구현 가능 범위"와 "입력 파이프라인 확장 전제 범위"를 분리해 추진해야 현실적**이다.
- 당장 실행 가능한 핵심은 `config` 활성화와 라벨 안정화(히스테리시스)이며, percentile/팀전략 동적화는 `classify_target_tier` 입력 확장 이후 단계가 적합하다.
