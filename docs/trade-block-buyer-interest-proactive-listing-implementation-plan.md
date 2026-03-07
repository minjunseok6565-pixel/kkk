# 트레이드 블록 고도화 구현 설계서 (상업용/NBA 팬 몰입형)

> 목표
> 1) 트레이드 블록 효과를 seller 공격성 부스팅에서 **buyer 타깃 탐색 우선순위 부스팅**으로 전환한다.
> 2) AI가 제안 기반 등록을 유지하면서, **제안 없이도 팔고 싶은 자산을 현실적으로 proactive 등록**하도록 확장한다.
> 3) 단, proactive 의사결정은 반드시 **게임 코드가 이미 제공하는 정보만 사용**한다.

---

## 0. 설계 원칙 (절대 규칙)

1. **Listing = Availability Signal, Not Value Signal**
   - 트레이드 블록은 "거래 가능성" 시그널이다.
   - valuation/decision(시장가치, 수용 판정)에는 직접 가중치를 넣지 않는다.

2. **신호 위치 분리**
   - BUY target ordering에만 listing 관심도 가중치를 넣는다.
   - SELL target ordering/actor selection에서 listing 기반 seller-side boost 제거.

3. **SSOT 우선 + 기존 데이터만 활용**
   - `tick_ctx`, `asset_catalog`, `team_situation`, `agency_state_by_player`, `trade_market(listings/events)`만 사용.
   - 외부 추론 모델/숨은 메타 데이터 생성 금지.

4. **상업용 안정성**
   - 스팸 방지(팀 cap/선수 재등록 쿨다운/TTL).
   - 개발 단계에서는 불필요한 레거시 호환 레이어를 만들지 않고 단순한 구조를 유지한다.

---

## 1. 파일별 구체 수정안

## 1-1) `trades/generation/dealgen/types.py`

### 변경 목적
- BUY 관심도 부스팅/SELL 측 제거를 설정값으로 명시.

### 수정 내용
1. 기존 SELL listing boost 설정 삭제
   - `listed_player_priority_boost`
   - `listed_public_request_synergy_boost`
   - `priority_signal_boost_cap`에서 listing 전용 문맥 제거(필요 시 public request 전용 cap로 명칭 재정의)

2. BUY listing interest 전용 설정 추가
   - `buy_target_listing_interest_enabled: bool = True`
   - `buy_target_listing_interest_boost_base: float = 0.25`
   - `buy_target_listing_interest_priority_scale: float = 0.35`
   - `buy_target_listing_interest_recency_half_life_days: float = 7.0`
   - `buy_target_listing_interest_need_weight_scale: float = 0.25`
   - `buy_target_listing_interest_cap: float = 0.85`

3. Proactive listing 설정 추가
   - `ai_proactive_listing_enabled: bool = True`
   - `ai_proactive_listing_team_daily_cap: int = 2`
   - `ai_proactive_listing_team_active_cap: int = 4`
   - `ai_proactive_listing_player_cooldown_days: int = 7`
   - `ai_proactive_listing_ttl_days_sell: int = 12`
   - `ai_proactive_listing_ttl_days_soft_sell: int = 7`
   - `ai_proactive_listing_ttl_days_default: int = 5`
   - `ai_proactive_listing_min_score: float = 0.25`
   - `ai_proactive_listing_priority_base: float = 0.45`
   - `ai_proactive_listing_priority_span: float = 0.35`

---

## 1-2) `trades/orchestration/types.py`

### 변경 목적
- 오케스트레이션 단계에서 listing으로 actor(주체팀) 우선순위가 오르지 않게 전환.

### 수정 내용
1. `trade_block_actor_weight_multiplier` 삭제
2. listing 유무 분기용 `trade_block_affects_actor_selection` 플래그도 추가하지 않음
   - 목적은 seller-side listing boost를 완전히 제거하는 것이므로, 복구 토글 없이 코드 단순화

---

## 1-3) `trades/orchestration/actor_selection.py`

### 변경 목적
- listing 팀에 대한 actor weight boost 제거(혹은 feature flag로 비활성).

### 수정 내용
1. `_weight()` 내 다음 블록 완전 제거
   - `if p.team_id in active_listing_team_ids: w *= listing_mult`

2. `public_req_with_listing_mult / no_listing_mult` 경로 단순화
   - 공개 트레이드 요청(public request) 시그널은 유지 가능
   - listing 유무로 public request를 추가 증폭하지 않도록 축소
   - 즉, `public_req_mult` 단일계수 기반으로 정리

3. 코드 단순화
   - listing 관련 actor weight 계산 변수(`listing_mult`, listing-synergy multiplier)도 함께 제거

---

## 1-4) `trades/generation/dealgen/targets.py`

### 변경 목적
- BUY 타깃 정렬에 listing 관심도 부스팅 도입.
- SELL 타깃 정렬에서 listing boost 제거.

### 수정 내용

#### A) BUY 경로 (`select_targets_buy`)
1. helper 추가
   - `_active_public_listing_meta_by_player(...) -> Dict[player_id, {priority, updated_at, team_id}]`
   - 데이터 소스 우선순위:
     1) `tick_ctx.team_situation_ctx.trade_market` snapshot
     2) `load_trade_market()` fallback
   - 반드시 `visibility=PUBLIC`, `status=ACTIVE`, `expires_on` 유효 체크

2. `rank` 계산에 "interest boost"만 추가
   - 기존:
     - `tag_strength`, `need`, `market_total`, `salary` 기반
   - 추가:
     - listed 선수면 `interest_boost = base + priority_scale*priority`
     - recency decay(half-life) 반영
     - need weight 반영 (`need_map` 높은 팀일수록 availability 시그널에 더 민감)
     - 최종 `interest_boost`는 cap 제한
     - `rank += interest_boost`

3. Value contamination 금지
   - `market_total`, 이후 valuation/evaluation 데이터에는 손대지 않음

#### B) SELL 경로 (`select_targets_sell`)
1. 아래 로직 제거
   - `listed_priority_by_player` 조회 및 `signal_boost`에서 listing 항목 반영
2. SELL 정렬은 유지
   - `bucket priority -> surplus -> expiring -> value -> id`
3. public request boost는 선택사항
   - 남길 경우에도 listing과 시너지 결합(`listed_public_request_synergy_boost`)은 제거

---

## 1-5) `trades/orchestration/tick_loop.py`

### 변경 목적
- 기존 제안 기반 AI listing 유지 + 신규 proactive listing 병행.

### 수정 내용

1. 기존 블록 유지
   - `props[0]` outgoing에서 등록하는 `AI_MARKET_SIGNAL` 경로 유지.

2. 신규 함수 호출 추가
   - actor 루프에서 proposal 처리 뒤:
   - `maybe_auto_list_proactive_assets(team_id=a.team_id, tick_ctx=tick_ctx, trade_market=trade_market, today=today, cfg=cfg, props=props)`

3. 신규 함수 내부 로직 (같은 파일 또는 helper 모듈로 분리)
   - 입력 데이터: `tick_ctx`/`catalog`/`team_situation`/`trade_market`
   - 선수 후보 풀: 해당 팀 `outgoing_by_team[team_id].players`
   - hard filter:
     - CORE 제외
     - locked 제외
     - recent signing ban 제외
     - 이미 ACTIVE listing 제외
   - soft score:
     - bucket 가중치(SELL 친화 버킷 우대)
     - surplus_score
     - expiring 보너스
     - trade_request_level(agency_state_by_player) 보너스
   - 운영 guard:
     - 팀 일일 등록 cap
     - 팀 active listing cap
     - 선수별 재등록 쿨다운(최근 이벤트/updated_at 기반)
   - 등록 실행:
     - `upsert_trade_listing(... listed_by="AI_GM", reason_code="AI_PROACTIVE_SHOP", priority=<score-map>, expires_on=<posture 기반>)`
     - `record_market_event(... event_type="TRADE_BLOCK_LISTED", payload{origin:"PROACTIVE"})`

4. props 기반 등록에는 payload에 `origin:"FROM_PROPOSAL"` 추가
   - 분석/튜닝에서 proactive와 구분 가능

---

## 1-6) 신규 helper 파일

### 후보 파일
- `trades/orchestration/listing_policy.py` (신규, 필수)

### 목적
- tick_loop를 비대하게 만들지 않고, proactive listing 판단/스코어/제약을 모듈화.

### 포함 함수 예시
- `build_proactive_listing_candidates(...)`
- `score_proactive_listing_candidate(...)`
- `compute_listing_ttl_days(posture, config)`
- `can_list_player_now(...)`

> 대규모 상업 프로젝트 관점에서 테스트/리팩토링 내구성이 높아지므로 필수로 분리한다.

---

## 1-7) 테스트 파일 수정/추가

## A. 수정
1. `trades/orchestration/test_actor_selection_priority_tuning.py`
   - listing team boost 전제를 제거/수정
   - 새 기준: listing 유무가 actor pick을 뒤집지 않음

2. `trades/generation/dealgen/test_targets_priority_signals.py`
   - SELL에서 listed 우선 테스트 제거 또는 expected 변경
   - BUY에서 listed target 관심도 상승 테스트로 전환

## B. 신규
3. `trades/generation/dealgen/test_targets_buy_listing_interest.py` (신규)
   - 동일 need/tag 조건에서 listed 선수가 상위 노출되는지 검증
   - priority/recency/need weight에 따른 미세 조정 검증
   - listing이 가치평가를 바꾸지 않음을 검증(가능한 범위에서 점수 경로 분리 assertion)

4. `trades/orchestration/test_proactive_listing.py` (신규)
   - props 없는 상황에서도 proactive 등록 발생
   - CORE/lock/ban 제외
   - daily cap/active cap/cooldown 준수
   - reason_code/origin 메타 검증

---

## 1-8) 문서/운영 가이드 업데이트

### 대상
- `docs/` 내 trade block 관련 계획 문서들
- 운영 설정 샘플(있다면)

### 반영 내용
- listing 의미 정의 변경(판매 의사 강화 X, 구매 탐색 신호 O)
- 신규 설정 파라미터 설명
- 튜닝 가이드:
  - 관심도 과대 시 `buy_target_listing_interest_cap`부터 조정
  - 스팸 발생 시 `team_daily_cap`, `player_cooldown_days` 상향

---

## 2. "이미 내려주는 정보만 사용" 체크리스트

신규 proactive 로직에서 사용할 수 있는 정보(허용):
- 팀 posture/urgency/constraints (`TeamSituation`)
- asset catalog 후보 정보 (`buckets`, `surplus_score`, `is_expiring`, `lock`, `recent_signing_banned_until`)
- agency 공개 신호 (`trade_request_level`)
- trade market listing/events (`status`, `updated_at`, `reason_code`, `expires_on`)
- current date/tick context

사용 금지(비허용):
- 외부 API, 비공개 히든 파라미터, 랜덤한 임의 스토리 데이터
- valuation 엔진 결과를 listing 단계에서 직접 변조하는 행위

---

## 3. 최적 작업 순서 (실패/회귀 최소화)

1. **Config/Types 선작업**
   - 신규 파라미터 추가 + 기존 seller-side listing 관련 필드 삭제

2. **SELL-side boost 제거**
   - actor_selection listing boost off
   - select_targets_sell listing boost off
   - 관련 테스트 수정

3. **BUY-side interest boost 도입**
   - listing meta helper + rank boost 추가
   - BUY 타깃 테스트 신설

4. **Proactive listing 추가**
   - tick_loop or listing_policy helper에 구현
   - cap/cooldown/ttl/event meta 포함
   - 단위 테스트 추가

5. **회귀/균형 검증**
   - 기존 trade generation 테스트 + 신규 테스트 전체 실행
   - 로그 샘플 확인(등록량, 오퍼 분포, 반복도)

6. **튜닝 라운드**
   - 너무 공격적이면: interest cap/priority scale 하향
   - 너무 소극적이면: proactive min_score 완화, daily cap 소폭 상향

---

## 4. 상업용 품질 기준 (NBA 팬 체감 품질)

1. **현실적인 소문시장 느낌**
   - proactive listing은 과도하지 않게(팀당 소수)
   - expiring/surplus/veteran sale 위주로 노출

2. **가격 왜곡 방지**
   - listing은 "연락 빈도"만 높이고 "몸값"은 그대로

3. **설명 가능성(디버그 가능성)**
   - event payload에 origin/reason/priority 근거 남김
   - 운영자가 "왜 올라갔는지" 추적 가능

4. **개발 단계 단순성 우선**
   - 기존 키/분기/레거시 토글을 남기지 않고 제거해 설계와 구현을 1:1로 맞춘다.

---

## 5. 구현 완료 정의 (DoD)

- [ ] SELL 타깃 정렬에서 listing 가중 제거
- [ ] actor selection에서 listing 팀 우대 제거(기본)
- [ ] BUY 타깃 정렬에서 listing 관심도 가중 반영
- [ ] valuation/evaluation 경로 불변 확인
- [ ] props 기반 AI 등록 유지
- [ ] props 없이도 proactive 등록 동작
- [ ] cap/cooldown/ttl 가드레일 통과
- [ ] 테스트 추가/수정 + 전체 green
- [ ] 관련 문서/설정 설명 업데이트
