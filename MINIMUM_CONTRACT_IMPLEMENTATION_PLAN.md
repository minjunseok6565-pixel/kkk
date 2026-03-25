# MINIMUM 계약 독립화 구현 계획서

## 1) 목표 (Goal)

이 문서의 목표는 **현재 `MINIMUM` 채널이 `STANDARD_FA`를 우회적으로 재사용하는 상태를 제거하고**, 게임 규칙에 맞는 **독립적인 MINIMUM 계약 체계**를 구현하는 것이다.

완료 기준은 다음과 같다.

1. `SIGN_FA` 협상 세션에서 `MINIMUM` 채널을 정상적으로 선택할 수 있다.
2. `MINIMUM` 오퍼는 다음 룰을 강제한다.
   - 연차(exp) 기준 연봉 테이블(25-26 시즌 기준) 적용
   - 시즌이 지날수록 10% 상승
   - 계약 기간 최대 2년
   - 2년 계약 시 2년차 연봉 = 1년차 연봉(고정)
3. `MINIMUM` 계약은 cap room / apron / MLE budget 소진 여부와 무관하게 체결된다.
4. 실제 사인 시 `contract_channel="MINIMUM"`가 계약 레코드에 저장된다.
5. 협상/사인 API 응답과 UI 흐름에서 MINIMUM이 명확히 구분된다.

---

## 2) 완료 시 기대 동작 (Definition of Done / E2E Behavior)

### 시나리오 A: FA MINIMUM 협상 시작

- 클라이언트가 `/api/contracts/negotiation/start` (`mode=SIGN_FA`) 호출
- 세션 응답의 `available_contract_channels`에 `MINIMUM` 포함
- MLE eligibility와 무관하게 항상 MINIMUM 가능

### 시나리오 B: MINIMUM 오퍼 제출

- 클라이언트가 `offer.contract_channel="MINIMUM"`로 제출
- 서버는 아래를 검증:
  - `years`는 1 또는 2
  - `start_season_year` 기준, 선수 exp에 대응하는 MINIMUM 첫해 연봉이 정확히 적용되었는지
  - 2년이면 2년차 연봉이 첫해와 정확히 동일한지
  - 옵션(team/player options) 미허용
- 검증 실패 시 `NEGOTIATION_INVALID_OFFER` + 구조화된 사유 반환

### 시나리오 C: MINIMUM 계약 확정

- 협상 accepted 후 commit 호출 시,
  - `sign_free_agent_with_channel(..., contract_channel="MINIMUM")`가
  - `sign_free_agent()`(STANDARD cap-check)로 가지 않고,
  - **MINIMUM 전용 사인 경로**로 진입
- cap space 부족이어도 정상 체결
- 계약 레코드에 `contract_channel="MINIMUM"` 저장
- roster salary는 첫해 MINIMUM 연봉으로 반영

---

## 3) 룰 SSOT 정의 (정책 고정값)

### 3.1 기준 시즌 및 성장률

- 기준 시즌(year): `2025` (25-26)
- 시즌 성장률: `10%` (복리)
  - `scaled = round(base * (1.10 ** (season_year - 2025)))`

### 3.2 exp별 기준 MINIMUM (2025)

```text
0  : 1,272,870
1  : 2,048,494
2  : 2,296,274
3  : 2,378,870
4  : 2,461,463
5  : 2,667,947
6  : 2,874,436
7  : 3,080,921
8  : 3,287,409
9  : 3,303,774
10 : 3,634,153
```

### 3.3 exp 경계 처리

- `exp < 0` → 0으로 클램프
- `exp > 10` → 10 슬롯 금액 사용

### 3.4 계약 길이 및 연봉 커브

- 허용 `years`: 1, 2
- salary curve:
  - `years=1`: `{start_year: min_salary}`
  - `years=2`: `{start_year: min_salary, start_year+1: min_salary}`

---

## 4) 파일별 패치 계획 (Patch Plan by File)

> 아래 내용은 **바로 구현 가능한 수준**으로 함수 단위/검증 포인트를 명시한다.

## A. `contracts/minimum_policy.py` (신규 파일)

### 목적
MINIMUM 정책의 단일 진실 공급원(SSOT) 제공.

### 추가할 주요 구성

1. 상수
   - `MINIMUM_BASE_SEASON_YEAR = 2025`
   - `MINIMUM_GROWTH_RATE = 0.10`
   - `MINIMUM_BY_EXP_2025: dict[int, int]`

2. 유틸 함수
   - `_clamp_exp(exp: Any) -> int`
   - `_safe_int_round(value: float) -> int`

3. 핵심 함수
   - `minimum_first_year_salary(exp: Any, season_year: Any) -> int`
     - exp 클램프 + 시즌 오프셋 기반 성장 적용
   - `build_minimum_salary_by_year(exp: Any, start_season_year: Any, years: Any) -> dict[str, float]`
     - years=1/2 검증
     - 2년차 동일 연봉 스케줄 생성

4. 검증 모델/함수
   - dataclass `MinimumOfferValidation(ok, reasons, expected_first_year_salary, expected_years)`
   - `validate_minimum_offer(offer: Mapping[str, Any], player_exp: Any, season_year: Any) -> MinimumOfferValidation`
     - years/curve/options 검증

### 구현 메모
- MLE 정책 모듈(`contracts/mle_policy.py`) 스타일과 동일하게 pure 함수 + 구조화된 payload 제공.
- DB 접근 금지.

---

## B. `contracts/policy/raise_limits.py`

### 목적
MINIMUM 채널 raise 룰 명시화.

### 패치
- `DEFAULT_MAX_RAISE_PCT_BY_CHANNEL`에
  - `"MINIMUM": 0.0` 추가

### 이유
- MINIMUM 2년차는 첫해와 동일해야 하므로 raise 허용치 0으로 명시.
- 추후 다른 레이어에서 curve 검증 시 일관성 확보.

---

## C. `contracts/negotiation/service.py`

### 목적
협상 시작/오퍼 검증/하드캡 검증에서 MINIMUM 독립 정책 반영.

### 패치 1: start negotiation
- `start_contract_negotiation()`의 `SIGN_FA` 분기에서
  - `available_contract_channels` 초기값을 `['STANDARD_FA', 'MINIMUM']`로 확장
  - MLE 채널 append 시 MINIMUM 유지

### 패치 2: channel policy 검증
- `_validate_offer_channel_policy()`에서 `channel == "MINIMUM"`일 때
  - MLE eligibility/cap 검사 경로로 진입하지 않도록 조기 분기
  - `contracts.minimum_policy.validate_minimum_offer()` 호출
  - 실패 시 `ContractNegotiationError(NEGOTIATION_INVALID_OFFER, ...evidence...)`

### 패치 3: exp AAV hard-cap 우회
- `_validate_offer_exp_aav_hard_cap()` 또는 호출부에서
  - `offer.contract_channel == "MINIMUM"`이면 검증 스킵

### 패치 4: player exp 조회 연결
- validation 호출 시 session의 `player_snapshot.exp`를 전달
- season_year는 session constraints의 `negotiation_season_year` 사용

---

## D. `contracts/negotiation/engine.py`

### 목적
협상 엔진의 years cap/raise curve가 MINIMUM 규칙을 침범하지 않도록 보정.

### 패치
1. `_channel_years_cap()`
   - `MINIMUM`일 때 2년 cap 강제
2. (필요시) counter 제안 생성 로직
   - MINIMUM 채널이면 money/years counter를 MINIMUM 허용 범위로 clip
   - 가장 단순한 정책: MINIMUM 채널의 money counter는 고정값 유지

### 구현 주의
- 엔진은 pure이므로 session+offer로만 결정.
- MINIMUM 특수 케이스를 최소 범위로 제한해서 기존 STANDARD/MLE 로직 영향 최소화.

---

## E. `league_service.py`

### 목적
실제 계약 확정 단계에서 MINIMUM을 STANDARD cap-check와 분리.

### 패치 1: 채널 라우팅 수정
- `sign_free_agent_with_channel()`에서
  - `MINIMUM`을 `sign_free_agent()`에 위임하지 않고
  - `_sign_free_agent_minimum()`(신규 private 메서드)로 분기

### 패치 2: `_sign_free_agent_minimum()` 신규 구현
- 흐름은 `_sign_free_agent_mle()`를 참고하되 아래 차이 유지:
  1. cap room / apron / MLE budget 검증 **미실행**
  2. 입력 `salary_by_year`는 무시하거나, 정책 검증 후 정책값과 불일치 시 에러
  3. `contracts.minimum_policy.build_minimum_salary_by_year()`로 급여 스케줄 생성
  4. 계약 생성 후 `contract['contract_channel'] = 'MINIMUM'` 명시
  5. 트랜잭션/로스터 업데이트/transactions 로깅은 기존 sign flow 재사용

### 패치 3: 옵션 처리 제한
- MINIMUM 경로에서 options/team_option_years/team_option_last_year 비허용
- 전달되면 ValueError

---

## F. `contracts/negotiation/types.py`

### 목적
이미 `MINIMUM`이 허용 목록에 있으므로 변경 최소화.

### 패치
- 구조 변경은 불필요.
- 주석/도큐먼트 보강만 필요 시 추가.

---

## G. `static/js/features/market/marketScreen.js`

### 목적
UI에서 MINIMUM 선택/제출 가능하게 함.

### 패치
1. 협상 세션의 `available_contract_channels`를 state에 보존
2. 오퍼 생성 함수(`buildAutoFaOfferFromSession`)에 채널 입력 반영
3. MINIMUM 선택 시:
   - years 선택 UI를 1~2로 제한
   - salary 입력 필드를 read-only 또는 숨김(서버 정책값 사용)
   - submit payload에 `contract_channel: "MINIMUM"` 포함
4. 실패 응답 메시지에서 MINIMUM 검증 사유 표시

### 선택 구현
- 1차 패치에서는 UI 단순화를 위해 “MINIMUM 제안” 버튼 추가 + 자동 years=1.
- 2차 패치에서 years 토글 지원.

---

## H. 테스트 파일 (신규/수정)

### 권장 추가

1. `tests/contracts/test_minimum_policy.py` (신규)
   - exp 경계, season 성장, years=1/2 스케줄, 11년차 이상 처리

2. `tests/contracts/test_negotiation_minimum_channel.py` (신규)
   - start 시 available 채널에 MINIMUM 포함
   - invalid years/options/curve 거절
   - exp 하드캡 스킵 확인

3. `tests/test_league_service_minimum_signing.py` (신규)
   - cap over 상태에서도 MINIMUM 사인 성공
   - contract_channel 저장 확인
   - roster salary 반영 확인

4. 기존 회귀
   - STANDARD_FA cap violation 기존대로 실패
   - MLE budget 소비/검증 기존대로 동작

---

## 5) API/에러 계약 (Client Contract)

## `/api/contracts/negotiation/offer` 실패 예시 (MINIMUM)

- code: `NEGOTIATION_INVALID_OFFER`
- message: `Offer violates MINIMUM channel rules.`
- evidence 예시:
  - `contract_channel: "MINIMUM"`
  - `minimum_validation: { reasons: [...] }`
  - `expected_first_year_salary`
  - `expected_years`

## `/api/contracts/sign-free-agent` 성공 예시

- event payload 내 `contract.contract_channel = "MINIMUM"`
- salary_by_year는 정책값으로 기록

---

## 6) 구현 순서 (권장 작업 순서)

1. `contracts/minimum_policy.py` 작성 + 단위 테스트
2. negotiation service에 MINIMUM 채널 노출/검증 연결
3. exp hard-cap 스킵 분기 반영
4. league_service에 `_sign_free_agent_minimum` 추가 + 라우팅 변경
5. UI 최소 연결(채널 선택 + payload 반영)
6. 통합 테스트/회귀 테스트

---

## 7) 리스크 및 대응

1. **협상 엔진 counter가 MINIMUM 고정 금액을 깨는 리스크**
   - 대응: MINIMUM 채널은 counter money를 정책값으로 클램프

2. **UI가 임의 salary를 보낼 가능성**
   - 대응: 서버에서 정책값으로 overwrite 또는 strict reject (권장: reject + 근거 반환)

---

## 8) 출시 체크리스트

- [ ] `SIGN_FA` 협상 세션에서 MINIMUM 표시
- [ ] MINIMUM 1년/2년 오퍼 성공
- [ ] MINIMUM 3년 오퍼 실패
- [ ] MINIMUM 옵션 포함 오퍼 실패
- [ ] cap over 팀도 MINIMUM 사인 성공
- [ ] MLE 소진 팀도 MINIMUM 사인 성공
- [ ] 계약 레코드에 `contract_channel="MINIMUM"` 저장
- [ ] STANDARD_FA/MLE 회귀 테스트 통과

---

## 9) 명시적 비목표 (Non-goals)

- MINIMUM 계약에 Bird rights 특별 예외 추가
- MINIMUM 계약에 옵션 허용
- MINIMUM 계약을 RE_SIGN/EXTEND 채널로 확장

---

## 10) 부록: 구현 정책 요약 (한 줄 규칙)

> MINIMUM은 “exp+시즌으로 결정되는 고정 연봉표 기반, 최대 2년, cap/MLE 제약 무관”인 독립 채널로 처리한다.
