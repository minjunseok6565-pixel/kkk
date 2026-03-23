# Fan Favorite (Outgoing-only) 반영 설계안

## 1) 목표

- 엑셀 로스터에 추가한 `Fan Favorite`(1~10) 컬럼을 `players.attrs_json`으로 읽어온다.
- **오직 Outgoing(내보내는 자산) 평가 시점**에만 팀 밸류(team_value)에 보호 보정을 적용한다.
- 점수 미기재/파싱 실패/범위 밖 값은 `0`으로 처리한다.
- 초기 관찰 단계이므로 **상한(cap) 없이** 반영한다.

---

## 2) 반영 수식

### 2.1 점수 정규화

- 원시 점수: `ff_raw` (attrs에서 읽은 값)
- 정규화 점수:

```text
ff = clamp(ff_raw, 0, 10)
```

> 주의: 이번 단계는 cap 미적용이지만, 입력 유효성(0~10 범위)은 유지한다.

### 2.2 Outgoing 보호 보정 배율 (무상한)

```text
outgoing_ff_factor = 1 + ff_scale * ff
```

- `ff_scale`: 신규 설정값(예: 0.06)
- 예시(0.06일 때):
  - ff=0  -> 1.00
  - ff=5  -> 1.30
  - ff=10 -> 1.60

### 2.3 적용 위치

- TeamUtility에서 플레이어 자산의 basketball value 조정이 끝난 뒤,
- **direction == outgoing** 일 때만:

```text
bball' = bball * outgoing_ff_factor
```

- incoming에는 적용하지 않는다.
- contract value에는 적용하지 않는다(현재 팀 선호 계열 처리 방식과 동일 일관성).

---

## 3) 데이터 흐름

1. Excel import 시 `Fan Favorite` 컬럼은 core 컬럼이 아니므로 attrs로 저장됨.
2. ValuationDataContext에서 PlayerSnapshot.attrs로 로드됨.
3. DealEvaluator가 incoming/outgoing leg를 계산해 방향 메타를 부착함.
4. TeamUtilityAdjuster에서 PlayerSnapshot.meta.direction을 읽어 outgoing인지 판단.
5. outgoing일 때만 fan favorite 배율 적용.

---

## 4) 수정 파일 계획

## 4.1 `trades/valuation/team_utility.py`

### 변경 목적

- Outgoing 전용 fan favorite 보정 로직 추가

### 구체 변경

- `TeamUtilityConfig`에 신규 파라미터 추가
  - `fan_favorite_attr_keys: Tuple[str, ...]`
    - 기본 예: `("Fan Favorite", "fan_favorite", "FAN_FAVORITE")`
  - `fan_favorite_outgoing_scale: float`
    - 기본 예: `0.06`
- 내부 helper 추가
  - `_extract_fan_favorite_score(snap: PlayerSnapshot) -> float`
    - attrs에서 우선순위 키 탐색
    - 숫자 파싱
    - 0~10 범위로 보정
- 내부 helper 추가
  - `_is_outgoing_leg(snap: PlayerSnapshot) -> bool`
    - `snap.meta["direction"] == "outgoing"` 판별
- player path(`value_asset`) 내 단계 추가
  - risk/finance 이후, `outgoing`일 때만 `FAN_FAVORITE_OUTGOING_GUARD` step 적용
  - `mode=MUL`, `factor=outgoing_ff_factor`
  - meta에 `ff_score`, `ff_scale`, `direction` 기록

---

## 4.2 `trades/valuation/deal_evaluator.py`

### 변경 목적

- team utility 계산 이전에 방향 정보를 snapshot에 안전하게 전달

### 구체 변경

- `_value_one_asset(...)`에 `direction` 인자 추가 (`"incoming" | "outgoing"`)
- `evaluate_team_side` 루프에서
  - incoming 호출 시 `direction="incoming"`
  - outgoing 호출 시 `direction="outgoing"`
- `_value_one_asset` 내부에서 player snapshot의 `meta`에 direction 주입
  - `replace(snap, meta={...existing_meta, "direction": direction})`
- 이후 market/team valuation 호출은 기존 동일

> 이유: 현재 leg metadata는 TeamValuation 생성 후 부착되므로, TeamUtility가 방향을 알 수 없음.

---

## 4.3 `trades/valuation/service.py` (선택)

### 변경 목적

- trade_rules로 스케일 튜닝 가능하게 열어두기(초기값만 써도 무방)

### 구체 변경(선택)

- `_build_team_config`에서 `fan_favorite_outgoing_scale` 오버라이드 키 수용
- 지금은 고정값으로 시작해도 되므로 선택 항목

---

## 4.4 테스트 파일

### 신규 테스트 권장

- `trades/valuation/test_team_utility_fan_favorite.py`
  1. outgoing + ff=10이면 factor 반영되어 team_value 증가
  2. incoming + ff=10이면 변화 없음
  3. ff 누락/비정상값이면 0 처리
  4. ff=1,5,10에서 선형 증가 검증
  5. step code/meta 기록 검증

- `trades/valuation/test_deal_evaluator_direction_meta.py`
  - incoming/outgoing 경로에서 snapshot meta.direction 전달 검증

---

## 5) 적용 순서

1. team_utility config/helper/step 추가
2. deal_evaluator에서 direction 전달
3. 단위 테스트 추가
4. 샘플 엑셀 컬럼(`Fan Favorite`)로 스모크 검증

---

## 6) 로그/관찰 포인트

- 평가 로그에서 `FAN_FAVORITE_OUTGOING_GUARD` step의 factor 분포 확인
- 실제 딜에서
  - outgoing_total 증가 폭
  - net_surplus 감소 폭
  - ACCEPT -> COUNTER/REJECT 전환 비율
  을 모니터링해 이후 cap 도입 기준 수립

---

## 7) 비범위(이번 작업에서 제외)

- 팀 맥락(리빌딩/컨텐더) 연동
- 시즌 이벤트 기반 fan favorite 증감
- fan favorite 상한(cap) 및 비선형 함수

