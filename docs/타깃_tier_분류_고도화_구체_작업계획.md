# 타깃 Tier 분류 고도화 구체 작업계획 (레거시 완전 대체)

## 0) 작업 원칙
- 기존 하드코딩 임계값 기반 분류(`market >= 86/72/52`)를 **즉시 제거**하고, 컨텍스트 기반 상대 분류기로 **일괄 교체**한다.
- 점진 적용/안전 도입/세이브 마이그레이션/legacy fallback은 두지 않는다.
- 결과 라벨(`ROLE/STARTER/HIGH_STARTER/STAR/PICK_ONLY`)은 유지하되, 내부 계산 체계는 새 정책으로 전환한다.
- 분류 입력은 **현재 프로젝트에서 실제 내려오는 값만 사용**한다.

---

## 1) 사용 가능한 입력 신호 SSOT (이번 개편에서 허용)

### 1.1 BUY/SELL focal 자산 기본값
- `TargetCandidate`: `need_tag`, `tag_strength`, `market_total`, `salary_m`, `remaining_years`, `age`
- `SellAssetCandidate`: `market_total`, `salary_m`, `remaining_years`, `is_expiring`, `top_tags`

### 1.2 TierContext 기반 컨텍스트값
- 전략/상황: `buyer_competitive_tier`, `buyer_trade_posture`, `buyer_time_horizon`, `buyer_urgency`, `buyer_deadline_pressure`, `seller_time_horizon`
- 시장 상대값: `market_percentile_league`
- 계약 프록시: `contract_control_direction`, `contract_trigger_risk`, `contract_toxic_risk`, `contract_matching_utility`

### 1.3 PICK_ONLY 진입 판정에 즉시 활용 가능한 실데이터
- `match_tag`, `need_tag`, `sale_asset.top_tags`
- `TeamOutgoingCatalog.pick_ids_by_bucket` (`FIRST_SAFE`, `FIRST_SENSITIVE`, `SECOND`)
- `PickTradeCandidate.stepien_sensitive`, `PickAsset.protection`

### 1.4 금지(이번 작업에서 사용하지 않음)
- 현재 전달되지 않는 injury/availability/세부 옵션 계약항(옵트인/옵트아웃 직접값) 같은 미연결 신호
- 외부 API 의존 신규 신호

---

## 2) 목표 아키텍처 구현 설계 (파일별)

## 2.1 `trades/generation/dealgen/types.py`

### 변경 목표
정책 레버/결정 안정화에 필요한 config 및 context 구조를 확정한다.

### 구체 수정
1. `DealGeneratorConfig`에 tier 정책 레버를 SSOT로 확장
   - 추가/정비 필드
     - `tier_strictness: float` (기본 0.0, 범위 -1.0~+1.0)
     - `tier_market_percentile_weight: float` (기존 유지)
     - `tier_strategy_weight: float` (기존 유지)
     - `tier_contract_weight: float` (기존 유지)
     - `tier_hysteresis_band: float` (기존 유지, 의미를 “경계 완충 폭”으로 명시)
   - 퍼센타일 구간 컷 운영용 필드
     - `tier_star_pct_cut`, `tier_high_starter_pct_cut`, `tier_starter_pct_cut`
     - strictness에 의해 동적으로 이동하는 기준 컷으로 사용
2. `DealGeneratorConfig`에 PICK_ONLY 진입 레버 추가
   - `tier_pick_only_keyword_weight` (need/match tag 내 PICK 계열 힌트 강도)
   - `tier_pick_only_top_tags_weight` (`sale_asset.top_tags` 내 PICK 계열 힌트 강도)
   - `tier_pick_only_inventory_weight` (픽 인벤토리 가용성 반영 강도)
   - `tier_pick_only_stepien_penalty` (`FIRST_SENSITIVE` 비중 페널티)
   - `tier_pick_only_threshold` (최종 진입 임계)
3. `TierContext` 확장(현재 값으로 계산 가능한 범위만)
   - `prev_tier: str = ""` (히스테리시스용, 미주입 시 비활성)
   - `tie_break_seed: str = ""` (확률적 동률해소/재현성용)
4. PICK_ONLY 계산용 최소 컨텍스트 DTO 추가
   - 예: `PickOnlyContext` (`pick_supply_safe`, `pick_supply_sensitive`, `pick_supply_second`, `stepien_sensitive_ratio`, `has_pick_inventory`)
5. 문서 주석 정리
   - “legacy 하드컷 호환” 문구 삭제
   - “상대 구간 컷 + 오프셋 + 안정화 + PICK_ONLY score gate” 기준으로 설명 갱신

---

## 2.2 `trades/generation/dealgen/config.py`

### 변경 목표
정책 레버를 런타임에서 안전하게 clamp/정규화하여 `classify_target_tier()`가 그대로 사용할 수 있게 만든다.

### 구체 수정
1. `_normalize_tier_dynamic_knobs()` 확장
   - `tier_strictness` 범위 clamp(-1.0~+1.0)
   - 퍼센타일 컷(`tier_*_pct_cut`) 유효성 정렬/보정
     - 예: `star_cut > high_cut > starter_cut` 강제
2. PICK_ONLY 레버 clamp 추가
   - 각 weight는 0.0~1.0
   - `tier_pick_only_stepien_penalty`는 0.0~1.0
   - `tier_pick_only_threshold`는 0.0~1.0
3. config 기반 파생값 계산 유틸 추가
   - strictness에 따라 퍼센타일 컷을 보수/공격 방향으로 평행 이동
   - 과도 이동 방지 상한(예: ±0.08)
4. 호출부 연결
   - 분류 진입 전에 normalized knobs를 일관 전달하도록 연결(직접 전달 또는 classify 내부 조회 단일화)

---

## 2.3 `trades/generation/dealgen/utils.py`

### 변경 목표
현행 하드 임계값 분류기를 제거하고, 새 4-Layer 분류기(정규화→정책→결정)를 SSOT로 구현한다.

### 구체 수정
1. `classify_target_tier()` 전면 교체
   - 제거: `tier_ctx is None`일 때 legacy 86/72/52 fallback
   - 제거: `need_tag/match_tag`에 `"PICK"` 포함 시 **즉시** `PICK_ONLY` 반환
2. PICK_ONLY 진입 로직을 score+gate로 교체
   - 신규 함수 예: `_pick_only_entry_score(...)`
   - 입력: `need_tag`, `match_tag`, `sale_asset.top_tags`, `PickOnlyContext`, `config`
   - 산식(초기안)
     - keyword 힌트(need/match) + top_tags 힌트 + inventory 가용성 - stepien 민감도 페널티
   - 판정
     - `score >= tier_pick_only_threshold` 이고 `has_pick_inventory=True`일 때만 `PICK_ONLY`
3. Normalization Layer 구현
   - 입력 market를 그대로 컷하지 않고 `market_percentile_league` 중심 상대 지표로 변환
   - `tier_market_percentile_weight`로 raw market vs percentile anchor 혼합
4. Policy Layer 구현
   - 전략 오프셋: `buyer_competitive_tier`, `buyer_trade_posture`, `buyer_time_horizon`, `buyer_urgency`, `buyer_deadline_pressure`
   - 계약 오프셋: `contract_control_direction`, `contract_trigger_risk`, `contract_toxic_risk`, `contract_matching_utility`
   - strictness 적용: 최종 경계 컷 또는 score에 일괄 이동 적용
5. Decision Layer 구현
   - soft score 산출 후 tier별 점수 비교(argmax)
   - `tier_hysteresis_band` + `prev_tier`가 있을 때 인접 경계 flip 억제
   - 동점/초근접 점수는 `tie_break_seed` 기반 확률적 해소(동일 seed면 재현 가능)
6. 만료/저가 특례 재정의
   - 기존 `is_expiring and market<=58 and salary<=30 => ROLE` 규칙은 고정 하드컷이므로 삭제
   - 동일 효과는 계약/전략 오프셋에서 자연 반영(정책 레버로 조정 가능)

---

## 2.4 `trades/generation/dealgen/skeletons.py`

### 변경 목표
분류 입력 컨텍스트를 새 의사결정 레이어 요구사항까지 채워 넣는다.

### 구체 수정
1. `_build_tier_context_for_buy/_sell()` 보강
   - `prev_tier` 주입: 현재 tick 내 같은 focal player가 재평가되는 경우 직전 tier 전달
   - `tie_break_seed` 주입: `"{buyer_id}:{seller_id}:{player_id}:{mode}"` 형태로 생성
2. market percentile 주입 경로 단일화
   - `IncomingPlayerRef.market_percentile_league` 우선, 없을 때만 현재 fallback 계산
3. PICK_ONLY 컨텍스트 생성 함수 추가
   - 예: `_build_pick_only_context(...)`
   - source: `catalog.outgoing_by_team[buyer_id].pick_ids_by_bucket`
   - 계산: safe/sensitive/second 개수, `has_pick_inventory`, `stepien_sensitive_ratio`
4. BUY/SELL 모두 동일 classifier 호출 경로 강제
   - `classify_target_tier(..., pick_ctx=...)` 형태로 통일

---

## 2.5 `trades/generation/dealgen/core.py`

### 변경 목표
라벨 안정화용 최소 상태(prev tier)를 현재 generation 루프 내에서 관리한다.

### 구체 수정
1. generation 루프 로컬 맵 추가
   - key: `(mode, focal_player_id, buyer_id, seller_id)`
   - value: 직전 `target_tier`
2. skeletons 호출 직전 `prev_tier` 전달, 호출 후 갱신
   - 영속 저장 없이 tick 실행 중 일관성만 보장
3. 통계 집계 유지
   - `target_tier_counts`는 기존대로 유지(관측성 연속성 확보)

---

## 2.6 `trades/generation/dealgen/skeleton_registry.py`

### 변경 목표
새 분류 결과와 스켈레톤 라우팅 간 계약(Contract)을 재확인한다.

### 구체 수정
1. tier 라우팅 테이블 점검
   - `ROLE/STARTER/HIGH_STARTER/STAR/PICK_ONLY` 전부 route 보장
2. `PICK_ONLY` 처리 강화
   - pick 기반 스켈레톤 우선 유지
   - 플레이어 스왑 계열 라우트가 PICK_ONLY로 들어가지 않도록 필터 명시

---

## 2.7 `trades/generation/dealgen/targets.py`

### 변경 목표
`match_tag`를 `PICK_ONLY` 즉시 확정 신호가 아닌, score 입력용 힌트로 안정화한다.

### 구체 수정
1. `_choose_buyers_for_sell_asset()`의 `best_tag` 산출 유지
   - 다만 `best_tag == PICK*`라도 분류기에서 즉시 확정하지 않도록 역할 재정의
2. 필요 시 `best_tag` 외 보조 태그를 함께 전달하는 경량 확장
   - 예: 상위 2개 태그를 `match_tag_candidates`로 전달(구현 부담이 크면 보류)
3. 주석/테스트에서 “match_tag가 PICK이면 PICK_ONLY 확정” 가정 제거

---

## 2.8 `trades/generation/dealgen/test_skeleton_phase4_config.py`

### 변경 목표
기존 하드컷 기대 테스트를 제거하고, 새 정책 레버/상대구간 분류 테스트로 교체한다.

### 구체 수정
1. 제거
   - `78 => HIGH_STARTER`, `48 => ROLE` 같은 절대값 단정 테스트
   - `match_tag="pick_bridge"`만으로 `PICK_ONLY` 확정되는 테스트
2. 추가
   - 동일 market이라도 `market_percentile_league` 차이로 tier가 달라지는 테스트
   - `tier_strictness` 변경 시 컷 이동 테스트
   - `tier_strategy_weight`, `tier_contract_weight` 영향 테스트
   - `prev_tier + hysteresis`에서 flip 억제 테스트
   - `PICK_ONLY` score gate 테스트
     - keyword만 있고 inventory가 없으면 비진입
     - keyword+inventory+낮은 stepien 민감도면 진입

---

## 2.9 `trades/generation/dealgen/` 테스트 추가 파일 (신규)

### 파일 제안
- `trades/generation/dealgen/test_target_tier_policy_layer.py`
- `trades/generation/dealgen/test_target_tier_decision_stability.py`
- `trades/generation/dealgen/test_target_tier_pick_only_entry.py`

### 테스트 범위
1. Policy Layer
   - 컨텐더/리빌더, deadline pressure 변화에 따른 tier 이동 검증
2. Decision Layer
   - 경계값 근처에서 히스테리시스 적용 여부
   - tie-break seed 재현성 검증
3. PICK_ONLY Entry
   - keyword-only 오탐 방지
   - `pick_ids_by_bucket` 유무에 따른 게이트 검증
   - `FIRST_SENSITIVE` 비중 상승 시 진입 점수 하향 검증
4. Regression
   - BUY/SELL 모두 동일 규칙으로 분류되는지

---

## 2.10 `docs/타깃_tier_분류_고도화_구상안.md`

### 변경 목표
구상 문서와 실제 구현 SSOT를 동기화한다.

### 구체 수정
1. “설계 방향” 문구 일부를 “구현 완료/적용 규칙”으로 업데이트
2. 사용 신호 목록에서 실제 미사용 항목 제거
3. 정책 파라미터 기본값/범위 표 추가
4. PICK_ONLY 문구 정밀화
   - “별도 체계 유지”를 유지하되, “문자열 즉시 분기”가 아니라 “score+inventory gate”로 명시

---

## 3) PICK_ONLY 세부 운영 규칙 (현 데이터 기반)

## 적용 범위
- 별도 타입 체계는 유지하되, 현재 데이터에서 가능한 범위만 사용

## 즉시 구현
1. `PICK_ONLY`는 **즉시 분기 규칙을 폐기**하고 score gate로 전환
   - 기존: `"PICK" in need_tag or match_tag -> PICK_ONLY`
   - 변경: `pick_only_score >= threshold` + `has_pick_inventory`
2. `pick_only_score` 구성
   - (+) keyword signal: `need_tag`, `match_tag`
   - (+) supply signal: `sale_asset.top_tags` 내 pick 계열 태그 밀도
   - (+) inventory signal: `FIRST_SAFE/FIRST_SENSITIVE/SECOND` 보유량
   - (-) risk signal: `FIRST_SENSITIVE` 비중(`stepien_sensitive_ratio`) 페널티
3. pick 하위 클래스는 **신규 enum 추가 없이** 우선 버킷 기반으로 운영
   - `FIRST_SAFE` > `FIRST_SENSITIVE` > `SECOND`
4. 보호조건 기반 신호는 `PickAsset.protection`, `PickTradeCandidate.stepien_sensitive`에서만 사용

## 보류
- “예상 드래프트 구간” 정밀 클래스는 현재 직접 신호가 제한적이므로 추정값 신규 도입 없이 보류

---

## 4) 구현 순서 (공격적 일괄 전환)
1. `types.py` / `config.py`에서 정책 레버 및 컷 구조 확정
2. `utils.py` 분류기 전면 교체(legacy 제거 + PICK_ONLY score gate 반영)
3. `skeletons.py`/`core.py` 컨텍스트 및 prev-tier 전달 연결
4. `targets.py` match_tag 가정 정리
5. `skeleton_registry.py` PICK_ONLY 라우팅 검증
6. 테스트 전면 교체/추가
7. 문서 동기화

---

## 5) 완료 기준 (DoD)
- 코드에서 타깃 tier 결정에 `market >= 86/72/52` 하드 임계값이 제거된다.
- `DealGeneratorConfig` 레버(`tier_strictness`, percentile/strategy/contract/hysteresis)가 실제 분류 결과를 바꾼다.
- `PICK_ONLY`가 키워드 단일 매칭으로 즉시 확정되지 않고, score+inventory gate로 동작한다.
- BUY/SELL 모두 동일 classifier 경로를 사용한다.
- 경계값 자산에 대해 히스테리시스 또는 tie-break로 flip-flop이 감소한다.
- 테스트가 새 정책 기준으로 통과한다.
