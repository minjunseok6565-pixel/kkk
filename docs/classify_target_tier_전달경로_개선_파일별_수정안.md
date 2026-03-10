# classify_target_tier 전달경로 개선: 파일별 수정안 (가용성 신호 제외)

## 목적
`classify_target_tier()`가 현재 사용하는 정적 입력(`target/sale_asset/match_tag`)을 넘어서,
아래 항목을 전달받아 동적 분류에 활용할 수 있는 전달 경로를 설계한다.

- (A) 리그/팀 컨텍스트 기반 상대지표(예: percentile 성격의 스케일)
- (B) 계약 질 지표(옵션/계약 텍스처 계열)
- (C) 팀 전략 신호(competitive_tier, posture, horizon, urgency, deadline)

> 제외 범위: 선수 가용성(부상/결장) 계열 신호

---

## 전달 대상 항목 정의 (v1)

## 1) 팀 전략 신호 (Team Strategy)
- `buyer_competitive_tier`
- `buyer_trade_posture`
- `buyer_time_horizon`
- `buyer_urgency`
- `buyer_deadline_pressure`
- `seller_time_horizon` (SELL에서 패키지 성향 보정용)

## 2) 상대 스케일 신호 (Relative Scale)
- `market_percentile_league` (해당 target의 리그 내 market 위치)
- `market_percentile_team_context` (옵션: 상대팀/포지션군 기반)

## 3) 계약 질 신호 (Contract Texture)
- `contract_control_direction`
- `contract_trigger_risk`
- `contract_toxic_risk`
- `contract_matching_utility`

---

## 파일별 수정안

## 1) `trades/generation/dealgen/types.py`
### 수정 목표
`classify_target_tier()`에 전달될 컨텍스트를 구조화하는 경량 DTO를 추가한다.

### 구체 수정
1. 신규 dataclass 추가: `TierContext`
   - 필드(초기안)
     - `buyer_competitive_tier: str = ""`
     - `buyer_trade_posture: str = ""`
     - `buyer_time_horizon: str = ""`
     - `buyer_urgency: float = 0.0`
     - `buyer_deadline_pressure: float = 0.0`
     - `seller_time_horizon: str = ""`
     - `market_percentile_league: float = 0.5`
     - `contract_control_direction: float = 0.0`
     - `contract_trigger_risk: float = 0.0`
     - `contract_toxic_risk: float = 0.0`
     - `contract_matching_utility: float = 0.0`

2. 기존 `TargetCandidate`/`SellAssetCandidate`는 최소 변경
   - v1에서는 DTO 자체를 크게 늘리지 않고, 분류 시점에 `TierContext`를 별도 계산/주입
   - 필요 시 v2에서 `TargetCandidate`에 `market_percentile_league`, `contract_*` 미리 캐시

### 이유
- 분류 함수 시그니처에 dict를 흘리는 대신 타입 안정성 확보
- 테스트 작성 시 stub 세팅 단순화

---

## 2) `trades/generation/dealgen/utils.py`
### 수정 목표
`classify_target_tier()`가 새로운 컨텍스트를 받을 수 있게 시그니처 확장.

### 구체 수정
1. 함수 시그니처 확장
- 기존:
  - `classify_target_tier(..., config: Optional[DealGeneratorConfig] = None) -> str`
- 변경:
  - `classify_target_tier(..., config: Optional[DealGeneratorConfig] = None, tier_ctx: Optional[TierContext] = None) -> str`

2. 내부 사용 규칙 추가 (v1)
- `tier_ctx`가 없으면 기존 로직과 동일 동작(완전 backward-compatible)
- `tier_ctx`가 있으면 다음 보정값 계산:
  - 팀 전략 보정치: `buyer_competitive_tier / posture / urgency / deadline`
  - 계약 리스크 보정치: `contract_toxic_risk`, `contract_trigger_risk`, `contract_control_direction`
  - 상대 스케일 보정치: `market_percentile_league` 우선 사용

3. 헬퍼 함수 추가
- `_tier_strategy_offset(tier_ctx) -> float`
- `_tier_contract_offset(tier_ctx) -> float`
- `_tier_market_anchor(market, tier_ctx) -> float`

### 이유
- 핵심 분류 로직 변경 지점을 단일 파일로 집중
- 기존 테스트 회귀를 줄이면서 새 분기 추가 가능

---

## 3) `trades/generation/dealgen/skeletons.py`
### 수정 목표
BUY/SELL 스켈레톤 생성 직전에 `TierContext`를 조립해 `classify_target_tier()`로 전달.

### 구체 수정
1. BUY 경로 수정
- `_build_buy_skeletons(...)`에서:
  - `ts_buyer = tick_ctx.get_team_situation(buyer_id)` 조회
  - `ts_seller = tick_ctx.get_team_situation(seller_id)` 조회(선택)
  - `TierContext` 생성 후 `classify_target_tier(target=target, config=config, tier_ctx=tctx)` 호출

2. SELL 경로 수정
- `_build_sell_skeletons(...)`에서:
  - 동일하게 buyer/seller team situation 조회
  - `sale_asset` 기준 `TierContext` 생성 후 전달

3. 컨텍스트 조립 로직을 전용 헬퍼로 분리
- 예: `_build_tier_context_for_buy(...)`, `_build_tier_context_for_sell(...)`

### 이유
- 현재 `classify_target_tier()` 호출이 이 파일에 집중되어 있어 주입 지점으로 최적
- routing 전 단계이므로 tier 분류 컨텍스트 반영 효과가 즉시 나타남

---

## 4) `trades/generation/dealgen/targets.py`
### 수정 목표
`TargetCandidate`/`SellAssetCandidate` 생성 시점에서 tier 컨텍스트에 필요한 원재료를 확보.

### 구체 수정
1. BUY 대상 생성(`select_targets_buy`)에서 확장 가능한 필드 확보
- 현재 존재하는 `IncomingPlayerRef`의 `contract_total`, `contract_gap_cap_share`, `expected_cap_share_avg`, `actual_cap_share_avg`를
  TierContext 조립에 활용할 수 있도록 주석/헬퍼 추가
- 필요 시 `TargetCandidate` 확장안(v2): `market_percentile_league`, `contract_proxy_score`

2. SELL 대상 생성(`select_targets_sell`)에서도 동일한 계약 프록시 계산 경로 정의
- `out_cat.players[pid]`를 통해 contract 관련 보조점수 계산
- 단, v1에서는 실제 계산 위치를 `skeletons.py` 헬퍼로 두고 targets에는 원천값 접근만 유지

### 이유
- target/sale_asset 후보 생성 단계에서 이미 market/contract 관련 재료가 모임
- 분류 로직에 필요한 숫자를 재계산하지 않고 재사용 가능

---

## 5) `trades/generation/asset_catalog.py`
### 수정 목표
리그 상대 스케일/계약질 입력값을 generation에서 재사용 가능하게 노출.

### 구체 수정
1. `IncomingPlayerRef` 확장(권장)
- `market_percentile_league: float = 0.5`
- `contract_texture_proxy: float = 0.0` (또는 세부 필드 분리)

2. 인덱스 구축 시 계산
- `incoming_all_players_by_id` 생성 구간에서 `market_total` 분포 percentile 계산
- 계약 질은 v1에선 `contract_gap_cap_share` 기반 proxy,
  v2에선 valuation context의 contract texture와 연동

3. 최소 변경 대안
- DTO 확장을 미루고, `skeletons.py`에서 `catalog.incoming_players_by_need`를 역참조해 percentile 계산
- 다만 계산 중복/복잡도 증가 우려

### 이유
- tier 분류에 필요한 상대지표는 tick 단위 catalog에서 계산하는 것이 비용/일관성 측면에서 유리

---

## 6) `trades/generation/generation_tick.py`
### 수정 목표
필요 시 계약 텍스처/평가 컨텍스트 접근을 generation 단계에서 안전하게 제공.

### 구체 수정
1. `TradeGenerationTickContext`에 보조 accessor 추가 (선택)
- 예: `get_player_contract_texture(player_id)`
- 내부적으로 provider/context_v2 캐시를 사용

2. 실패 내성
- 텍스처 미구축 시 neutral 기본값 반환(0 또는 0.5)

### 이유
- 계약질 신호를 tier로 연결할 때 계층 간 결합을 완충
- 향후 가중치 실험 시 호출부 단순화

---

## 7) `trades/generation/dealgen/test_*` (테스트 파일들)
### 수정 목표
시그니처 확장/동적 보정이 기존 동작을 깨지 않음을 보장.

### 구체 수정
1. `test_utils_*` 계열
- `tier_ctx=None`일 때 기존 threshold 결과 동일성 테스트

2. `test_skeleton_registry_routing.py` 또는 신규 테스트
- buyer posture/tier/urgency에 따라 동일 market에서도 tier가 달라지는지 검증

3. 계약질 보정 테스트
- 동일 market에서 `contract_toxic_risk` high/low에 따른 tier 하향/유지 검증

### 이유
- 회귀 안정성 + 새 기능의 의도된 효과를 함께 잠금

---

## 8) `trades/generation/dealgen/config.py` + `types.py(DealGeneratorConfig)`
### 수정 목표
동적 컷에 필요한 파라미터를 config로 외부화.

### 구체 수정
1. `DealGeneratorConfig`에 tier 관련 파라미터 추가
- `tier_strategy_weight`
- `tier_contract_weight`
- `tier_market_percentile_weight`
- `tier_hysteresis_band`

2. `config.py`에서는 기본값 보정/스케일링 규칙 정의 (필요 시)

### 이유
- 하드코딩 분기를 줄이고 운영 튜닝 가능성 확보

---

## 구현 순서 권장 (파일 단위 묶음)

### Step 1) 타입/설정 뼈대 먼저 고정
**작업 파일**
- `trades/generation/dealgen/types.py`
- `trades/generation/dealgen/config.py`

**작업 내용**
- `TierContext` dataclass 추가(전달 필드 스키마 확정)
- `DealGeneratorConfig`에 tier 동적 보정 파라미터 추가(가중치/히스테리시스)

### Step 2) 분류 함수 확장 + 하위호환 확보
**작업 파일**
- `trades/generation/dealgen/utils.py`

**작업 내용**
- `classify_target_tier(..., tier_ctx: Optional[TierContext] = None)` 시그니처 확장
- `tier_ctx is None`일 때 기존 결과 100% 동일 보장
- 전략/계약/상대스케일 보정 헬퍼 도입 (`_tier_strategy_offset`, `_tier_contract_offset`, `_tier_market_anchor`)

### Step 3) 호출 경로 주입 (BUY/SELL 라우팅 입구)
**작업 파일**
- `trades/generation/dealgen/skeletons.py`
- (필요 시) `trades/generation/generation_tick.py`

**작업 내용**
- BUY/SELL 분기에서 `tick_ctx.get_team_situation(...)` 기반으로 `TierContext` 조립
- `classify_target_tier()` 호출에 `tier_ctx` 전달
- 필요하면 `generation_tick.py`에 계약질 접근 보조 accessor 추가

### Step 4) market/contract 원천값 연결 강화
**작업 파일**
- `trades/generation/asset_catalog.py`
- `trades/generation/dealgen/targets.py`

**작업 내용**
- incoming 인덱스 생성 시 `market_percentile_league` 계산/노출
- contract proxy(예: `contract_gap_cap_share` 기반) 전달 경로 고정
- `skeletons.py`의 `TierContext` 조립 시 재사용 가능하도록 데이터 경로 정리

### Step 5) 테스트 잠금 (회귀 + 신규 보정)
**작업 파일**
- `trades/generation/dealgen/test_utils_*.py` (또는 신규 `test_target_tier_context.py`)
- `trades/generation/dealgen/test_skeleton_registry_routing.py` (또는 동급 라우팅 테스트)

**작업 내용**
- `tier_ctx=None` 회귀 테스트(기존 threshold 결과 동일)
- 팀전략/계약질/상대스케일 주입 시 tier 변화 검증
- SELL/BUY 양 경로에서 `tier_ctx` 전달 누락 방지 테스트

---

## 리스크/주의사항
- `classify_target_tier()`는 라우팅 입구라 작은 변화도 후보군 분포를 크게 바꿀 수 있음
- v1은 "팀전략 + 시장상대스케일" 위주로 먼저 도입, 계약질은 보수적으로 가중치 시작 권장
- neutral fallback(컨텍스트 미존재 시 기존 로직) 유지가 필수

---

## 산출물 정의 (작업 착수 기준)
- [ ] `TierContext` 타입 도입
- [ ] `classify_target_tier(..., tier_ctx=...)` 확장
- [ ] BUY/SELL 호출 경로에서 `TierContext` 주입
- [ ] market percentile/contract proxy 최소 1개 이상 연결
- [ ] 회귀 테스트 + 새 보정 테스트 추가
