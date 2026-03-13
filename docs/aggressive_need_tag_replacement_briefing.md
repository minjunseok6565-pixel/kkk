# Aggressive Need Tag 교체 구체화 브리핑

## 1) 사용량-커버리지 보정에서 “역할별 사용량” 측정 방식

### 제안 결론
- 공격 역할 사용량은 `팀 action breakdown` 기반으로 role-family에 매핑해 측정한다.
- 수비 역할 사용량은 `수비 스킴 사용 비중 x 스킴 내 role 가중치`로 분해해 측정한다.
- 단기(최근 10경기)와 장기(시즌 누적)를 혼합하고, 시즌 초반은 신뢰도 감쇠를 적용한다.

### 공격 사용량 측정
- 입력: `offense_breakdowns.off_actions`(PnR, Drive, SpotUp, DHO, PostUp, TransitionEarly, ISO 등)
- 매핑: action -> 공격 role-family 기여 테이블
  - 예) PnR -> ENGINE_PRIMARY/ROLL_MAN/SHORTROLL_HUB 비중 분배
  - 예) SpotUp -> SPOTUP_SPACER/MOVEMENT_SHOOTER 분배
- 산식:
  - `usage_off_role_raw[r] = Σ(action_share[a] * map[a,r])`

### 수비 사용량 측정
- 입력: 수비 스킴 사용 비중 + `GROUP_SCHEME_ROLE_WEIGHTS`
- 산식:
  - `usage_def_role_raw[r] = Σ(scheme_share[s] * role_weight[s,r])`
- 스킴별로 해당 role이 없으면 0 처리

### 시간 혼합/신뢰도
- `usage = 0.6 * season + 0.4 * recent10`
- 시즌 초반 감쇠:
  - `usage_adj = neutral + (usage - neutral) * stat_trust`

### 커버리지와 보정
- `coverage_role`: 상위3 role 후보 수/점수 기반 0~1
- `delta = usage_adj - coverage_role`
- `delta > th`면 가점, `delta < -th2`면 감점, 그 외 무보정

---

## 2) 최종 need_map에 나올 수 있는 태그 전체

태그 정책:
- 기본 태그: `OFF_*`, `DEF_*`
- 조건부 태그: `G_*`, `W_*`, `B_*` 접두
- 접두는 포지션 부족(상대 비율 부족 + 절대 인원<=3) 동시 충족 시에만 생성

### 기본 공격 태그
- OFF_ENGINE_PRIMARY
- OFF_ENGINE_SECONDARY
- OFF_TRANSITION_ENGINE
- OFF_SHOT_CREATOR
- OFF_RIM_PRESSURE
- OFF_SPOTUP_SPACER
- OFF_MOVEMENT_SHOOTER
- OFF_CUTTER_FINISHER
- OFF_CONNECTOR
- OFF_ROLL_MAN
- OFF_SHORTROLL_HUB
- OFF_POP_THREAT
- OFF_POST_ANCHOR

### 기본 수비 태그
- DEF_ZONE_TOP_LEFT
- DEF_ZONE_TOP_RIGHT
- DEF_PNR_POA_DEFENDER
- DEF_PNR_POA_BLITZ
- DEF_PNR_POA_SWITCH
- DEF_PNR_POA_SWITCH_1_4
- DEF_PNR_POA_AT_THE_LEVEL
- DEF_LOWMAN_HELPER
- DEF_NAIL_HELPER
- DEF_WEAKSIDE_ROTATOR
- DEF_SWITCH_WING_STRONG
- DEF_SWITCH_WING_WEAK
- DEF_SWITCH_WING_STRONG_1_4
- DEF_SWITCH_WING_WEAK_1_4
- DEF_ZONE_BOTTOM_LEFT
- DEF_ZONE_BOTTOM_RIGHT
- DEF_ZONE_BOTTOM_CENTER
- DEF_PNR_COVER_BIG_DROP
- DEF_PNR_COVER_BIG_BLITZ
- DEF_BACKLINE_ANCHOR
- DEF_PNR_COVER_BIG_SWITCH
- DEF_PNR_COVER_BIG_SWITCH_1_4
- DEF_PNR_COVER_BIG_HEDGE_RECOVER
- DEF_PNR_COVER_BIG_AT_THE_LEVEL

### 조건부 접두 태그
- `G_` + 가드 매핑 태그
- `W_` + 윙 매핑 태그
- `B_` + 빅 매핑 태그

---

## 3) 전수 교체 호출부/소비부(정확 목록)

### 반드시 수정
1. `data/team_situation.py`
2. `decision_context.py`
3. `role_need_tags.py` (삭제)
4. `need_attr_profiles.py` (신설)
5. `trades/valuation/fit_engine.py`
6. `trades/valuation/team_utility.py`
7. `trades/valuation/package_effects.py`
8. `trades/valuation/service.py`
9. `trades/valuation/types.py`
10. `trades/generation/generation_tick.py`
11. `trades/generation/asset_catalog.py`
12. `app/api/routes/trades.py`

### 영향 가능성이 큰 추가 점검 영역
- `trades/generation/dealgen/*` need_map 참조 코드
- 프론트/상수(`static/js/**`) 중 need label/tag 하드코딩
- 테스트 파일:
  - `trades/valuation/test_*.py`
  - `trades/generation/test_*.py`
  - `data/team_situation` 관련 테스트

---

## 4) 추가 아이디어 평가

아이디어:
- “패키지가 같은 니즈를 채워도, 더 적은 선수 수로 채우면 가점”

### 현실성 평가
- 장점:
  - 실제 트레이드/로스터 운영에서는 슬롯 효율이 중요하므로 현실적이다.
  - 같은 공급량이면 ‘로스터 spot 절약’ 가치 반영이 가능하다.
- 리스크:
  - 스타 1명 과대평가/깊이 과소평가로 이어질 수 있다.
  - 부상/플레이오프 매치업 등 분산의 가치를 깎을 위험이 있다.

### 게임성 평가
- 장점:
  - 2-for-1, 3-for-1 구조 차별화가 명확해져 딜 다양성이 늘어난다.
  - 사용자 입장에서 “왜 이 패키지가 높은가” 설명이 쉬워진다.
- 리스크:
  - AI가 과도하게 consolidation만 선호하면 게임이 단조로워질 수 있다.
  - 벤치 깊이 메타가 무너질 수 있다.

### 권장 결론
- 도입 자체는 긍정적이나, **무조건 가점**이 아니라 **조건부 가점**이 안전하다.
- 권장 안전장치:
  1. need 충족률이 임계치 이상일 때만 슬롯효율 가점 적용
  2. depth 부족 태그가 높은 팀은 슬롯효율 가점을 약화/비활성
  3. 가점 상한(cap) 적용
  4. 플레이오프/부상위험 환경에서는 분산 가치 보정

권장 보정 예시:
- `slot_eff_bonus = min(cap, alpha * fulfilled_need_mass * (1 / max(incoming_players,1)))`
- 단, `depth_stress > x`면 `slot_eff_bonus *= beta(<1)`

---

## 요약
- 역할 사용량은 공격(액션분해) + 수비(스킴×role weights)로 계량화하는 것이 가장 일관적이다.
- 최종 태그는 OFF/DEF 기본태그 + 조건부 G/W/B 접두태그 집합으로 운영한다.
- 전수 교체 대상은 team_situation/decision_context/valuation/generation/API/테스트 전반이다.
- “적은 선수로 충족 가점”은 유효한 아이디어이며, depth/분산 붕괴를 막는 상한·조건부 적용이 필수다.
