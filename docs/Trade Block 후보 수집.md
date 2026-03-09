# Trade Block 후보 수집 개선안 (임계점 + 주 단위 업데이트)

## 1) 현재 구조 확인 (현행 동작)

### 1-1. 트레이드 블록 후보 수집은 어떤 방식인가?

현재 `apply_ai_proactive_listings()`는 아래 순서로 동작한다.

1. 허용 버킷 집합에서 후보 수집
   - `VETERAN_SALE`, `SURPLUS_LOW_FIT`, `SURPLUS_REDUNDANT`, `FILLER_BAD_CONTRACT`, `FILLER_CHEAP`, `CONSOLIDATE`
2. 제외 필터 적용
   - 이미 active listing
   - lock 걸린 선수
   - `recent_signing_banned_until` 기간 선수
   - proactive cooldown 기간 선수
3. 우선순위 정렬 후 `remaining` cap만큼 상위 선수만 listing

즉, “버킷에 있으면 무조건 listing”이 아니라 **팀 active cap / daily cap**과 정렬 우선순위(버킷 index + surplus_score)에 의해 잘린다.

### 1-2. 매일 판정 구조인가?

네. 오케스트레이션 tick은 같은 날짜에 1회만 성공 실행되도록 막혀 있고(`ALREADY_RAN_TODAY`), 그 tick 내에서 actor 루프마다 `apply_ai_proactive_listings()`가 호출된다.

- 따라서 현재 proactive listing 판정은 **일 단위**로 시도되는 구조다.
- 다만 팀별 `team_daily_cap`과 player cooldown이 있어 과도한 반복을 일부 억제한다.

### 1-3. 트레이드 제안 생성과 블록 업데이트 결합도

`tick_loop`에서 제안 생성(`gen.generate_for_team`) 이후 proactive listing을 수행한다. 이 둘은 같은 tick에서 실행되지만, 블록 listing 로직은 독립 함수이므로 cadence를 분리 가능하다.

핵심 목표인 “트레이드 제안은 기존 유지, 트레이드 블록만 주 단위 갱신”은 구조적으로 구현 가능하다.

---

## 2) 팀 상황 판정 구조 요약 (임계점 차등의 근거)

팀 상황은 `data/team_situation.py`의 `TeamSituation`으로 제공되며, 최소 아래 축을 활용하는 것이 적합하다.

- `competitive_tier`: `CONTENDER`, `PLAYOFF_BUYER`, `FRINGE`, `RESET`, `REBUILD`, `TANK`
- `trade_posture`: `AGGRESSIVE_BUY`, `SOFT_BUY`, `STAND_PAT`, `SOFT_SELL`, `SELL`
- `time_horizon`: `WIN_NOW`, `RE_TOOL`, `REBUILD`
- 보조 신호: `urgency`, `signals.re_sign_pressure`, `constraints.apron_status`, `constraints.cooldown_active`

실제 posture는 tier/성적/트렌드/데드라인 압박/샐러리 제약/인내심 등을 반영해 계산된다. 따라서 버킷 임계점은 posture 중심으로 두고, horizon/urgency를 보정 계수로 얹는 방식이 안정적이다.

---

## 3) 요구사항 반영 설계


### 3-1. “버킷 내 선수라도 임계점 이상만 블록 후보”

#### 제안: 2단계 임계점 게이트

후보 선수 `p`에 대해 버킷 `b`별로 listing score를 계산하고, posture별 threshold 이상일 때만 후보로 포함한다.

- 공통 베이스 스코어:
  - `base = clamp01(p.surplus_score)`
- 버킷별 보정 예시:
  - `SURPLUS_LOW_FIT`: `score = 0.7*base + 0.3*(1 - fit_vs_team)`
  - `SURPLUS_REDUNDANT`: `score = 0.8*base + 0.2*redundancy_proxy`
    - `redundancy_proxy`는 catalog 생성 시 저장 가능한 값(없으면 base 사용)
  - `FILLER_BAD_CONTRACT`: `score = 0.7*base + 0.3*bad_contract_proxy`
    - `bad_contract_proxy` 예: 고연봉/저시장가치 비율
  - `FILLER_CHEAP`: `score = base` (또는 depth 관련 보정)
  - `CONSOLIDATE`: `score = 0.8*base + 0.2*mid_tier_tradeability`
  - `VETERAN_SALE`: `score = 0.7*base + 0.3*veteran_timing_proxy`

> 구현 난이도를 낮추려면 1차 릴리즈에서는 `score = surplus_score` 단일 기준 + 버킷별 threshold만 다르게 두고, 2차에서 버킷 프록시를 도입한다.


### 3-2. posture / horizon별 버킷 threshold 매트릭스

`DealGeneratorConfig`에 아래 형태를 추가한다.

```python
ai_proactive_listing_bucket_thresholds: Dict[str, Dict[str, float]] = {
    "AGGRESSIVE_BUY": {
        "SURPLUS_LOW_FIT": 0.30,
        "SURPLUS_REDUNDANT": 0.35,
        "CONSOLIDATE": 0.55,
        "FILLER_CHEAP": 0.65,
        "FILLER_BAD_CONTRACT": 0.80,
        "VETERAN_SALE": 0.90,
    },
    "SOFT_BUY": {
        "SURPLUS_LOW_FIT": 0.38,
        "SURPLUS_REDUNDANT": 0.42,
        "CONSOLIDATE": 0.60,
        "FILLER_CHEAP": 0.68,
        "FILLER_BAD_CONTRACT": 0.82,
        "VETERAN_SALE": 0.92,
    },
    "STAND_PAT": {
        "SURPLUS_LOW_FIT": 0.50,
        "SURPLUS_REDUNDANT": 0.55,
        "CONSOLIDATE": 0.70,
        "FILLER_CHEAP": 0.72,
        "FILLER_BAD_CONTRACT": 0.86,
        "VETERAN_SALE": 0.95,
    },
    "SOFT_SELL": {
        "SURPLUS_LOW_FIT": 0.40,
        "SURPLUS_REDUNDANT": 0.45,
        "CONSOLIDATE": 0.85,
        "FILLER_CHEAP": 0.62,
        "FILLER_BAD_CONTRACT": 0.70,
        "VETERAN_SALE": 0.45,
    },
    "SELL": {
        "SURPLUS_LOW_FIT": 0.32,
        "SURPLUS_REDUNDANT": 0.38,
        "CONSOLIDATE": 0.90,
        "FILLER_CHEAP": 0.58,
        "FILLER_BAD_CONTRACT": 0.62,
        "VETERAN_SALE": 0.35,
    },
}
```

#### NBA-like 동작 의도

- WIN NOW 계열(`AGGRESSIVE_BUY`, `SOFT_BUY`)은 `SURPLUS_LOW_FIT`/`SURPLUS_REDUNDANT` 임계점을 낮춰 핏 안 맞는 자원 정리를 적극화.
- SELL 계열은 `VETERAN_SALE` 임계점을 크게 낮춰 베테랑 처분을 촉진.
- `CONSOLIDATE`는 BUY 계열에서만 현실적으로 활성화되도록 임계점을 상대적으로 낮추고, SELL 계열은 높게 둬 사실상 억제.

#### 추가 보정(선택)

- `time_horizon == WIN_NOW`이면 `SURPLUS_*` threshold를 `-0.03`
- `time_horizon == REBUILD`이면 `VETERAN_SALE` threshold를 `-0.05`
- `urgency >= 0.75`이면 전체 threshold를 `-0.03`
- `constraints.cooldown_active == True`이면 전체 threshold를 `+0.05`

> clamp 범위는 `[0.10, 0.95]` 권장.

---

## 4) 주 단위 업데이트(트레이드 블록만) 설계


### 4-1. 왜 tick 자체를 주 단위로 바꾸면 안 되는가?

tick 전체를 주 단위로 낮추면 트레이드 제안 생성 빈도까지 함께 줄어든다. 요구사항은 “트레이드 제안은 현행 유지”이므로, **listing 단계만 cadence gate**를 적용해야 한다.

### 4-2. cadence gate 방식

`apply_ai_proactive_listings()` 시작부에 아래 gate를 추가한다.

- config
  - `ai_proactive_listing_cadence: Literal["DAILY", "WEEKLY"] = "WEEKLY"`
  - `ai_proactive_listing_anchor_weekday: int = 0  # Monday`
- state (`trade_market`)에 팀별 마지막 proactive listing 평가일 저장
  - 예: `trade_market["proactive_listing_meta"][team_id] = {"last_eval_at": "YYYY-MM-DD"}`

#### 주 단위 판정 규칙 (권장)

1. `cadence == DAILY`면 기존 동작 유지.
2. `cadence == WEEKLY`면,
   - `today.weekday() != anchor_weekday`인 날은 스킵.
   - 또는 `last_eval_at`이 있고 `today - last_eval_at < 7`이면 스킵.
3. 평가가 실행된 날(실제로 0명이 올라가도) `last_eval_at` 갱신.

이렇게 하면 “자주 올라오는 느낌”을 줄이고, 특정 요일 기준으로 시장 업데이트 리듬이 생긴다.

### 4-3. 기존 daily cap / cooldown과의 관계

- `team_daily_cap`은 그대로 두어도 무방(주간 실행일 하루에만 적용됨).
- player cooldown은 유지(재상장 빈도 안정화).
- 필요 시 `team_weekly_cap`를 추가해 주간 상한을 명시적으로 둘 수 있다.

---

## 5) 실제 코드 변경 포인트(구현 순서)

1. `trades/generation/dealgen/types.py`
   - threshold/cadence 관련 신규 config 필드 추가.

2. `trades/orchestration/listing_policy.py`
   - `team_situation = tick_ctx.get_team_situation(tid)` 활용.
   - posture/horizon/urgency 기반 threshold resolve 함수 추가.
   - 선수별 `passes_listing_threshold(player, bucket, team_situation, config)` 추가.
   - candidate 수집 시 threshold 미달 제외.
   - weekly cadence gate + `proactive_listing_meta` read/write 추가.

3. `trades/orchestration/market_state.py`
   - `trade_market` schema에 `proactive_listing_meta` 기본 키 보강(정합성/안전성).

4. 테스트
   - `trades/orchestration/test_proactive_listing.py` 확장:
     - threshold 미달 제외
     - posture별 서로 다른 threshold 적용
     - WEEKLY cadence에서 비앵커 요일 skip
     - WEEKLY cadence에서 7일 이내 재평가 skip
     - 제안 생성 경로와 무관(기존 테스트 회귀)

---

## 6) 리스크 및 완화

- 리스크: threshold를 너무 높게 잡으면 블록이 과소공급됨.
  - 완화: posture별 기본값을 보수적으로 시작 + telemetry 추가(`candidate_before`, `after_threshold`, `listed_count`).

- 리스크: 주간 cadence로 시장 반응이 둔해질 수 있음.
  - 완화: deadline 압박이 높은 기간에는 임시로 cadence를 DAILY로 override할 수 있는 config 스위치 추가.

- 리스크: 버킷 score 프록시 계산 복잡도 증가.
  - 완화: 1차는 `surplus_score` 단일 기준, 2차에서 버킷별 고급 스코어 확장.

---

## 7) 기대 효과

- “버킷에만 올라간 선수”와 “실제 블록 게시 선수”가 분리되면서, 유저는 트레이드 블록을 볼 때
  **‘정말 시장에 나온 선수’라는 신뢰감**을 느끼게 된다.
- 팀 성향(윈나우/리빌드/스탠드팻)에 따라 블록 공개 강도가 달라져,
  같은 리그 내에서도 팀별 프런트 성향이 다르게 보이는 **서사적 일관성**이 생긴다.
- 트레이드 제안 엔진은 그대로 유지하면서 블록 업데이트만 주 단위화하므로,
  **딜 기회는 유지**하면서도 화면/뉴스피드의 **반복 노이즈는 감소**한다.

### 7-1. 게이머 체감: “시장에 올라온 이유가 납득된다”

- 임계점 미달 선수는 버킷에만 남고 공개 블록에는 올라오지 않으므로,
  블록에 노출된 선수는 “팀이 실제로 내보낼 의도가 큰 자산”으로 해석된다.
- 유저 입장에서 `왜 이 선수가 갑자기 블록에 있지?` 같은 이질감이 줄고,
  스카우팅/협상 우선순위를 세우기 쉬워진다.

### 7-2. 게이머 체감: “팀 성향이 살아 있는 리그”

- WIN NOW 팀은 low-fit/중복 자원을 더 빠르게 노출하고,
  REBUILD/SELL 팀은 veteran sale 성격 자산을 더 공격적으로 노출한다.
- 결과적으로 유저는 AI를 상대할 때
  `이 팀은 지금 우승창이라 로스터 핏 정리에 적극적이다`,
  `저 팀은 리빌드라 베테랑 매각에 열려 있다` 같은
  **읽을 수 있는 패턴**을 체감하게 된다.

### 7-3. 게이머 체감: “시장 리듬이 주간 사이클로 안정된다”

- 일 단위 재판정에서 발생하던 잦은 블록 변동이 줄어,
  유저는 매일 같은 화면을 새로 스캔하는 피로가 줄어든다.
- 주간 앵커 요일 기준으로 “이번 주 시장 업데이트”를 확인하는 루틴이 생겨
  체감상 운영 게임의 시즌 진행 리듬이 더 명확해진다.

### 7-4. 게이머 체감: “딜은 계속 오는데 블록은 덜 시끄럽다”

- 트레이드 제안 생성 cadence를 유지하므로,
  유저는 거래 기회가 줄었다는 손실감 없이 플레이할 수 있다.
- 동시에 블록 노출은 주 단위로 정돈되어,
  제안/협상 이벤트의 의미가 상대적으로 또렷해지고
  “매일 비슷한 선수가 반복 노출되는 느낌”이 완화된다.
