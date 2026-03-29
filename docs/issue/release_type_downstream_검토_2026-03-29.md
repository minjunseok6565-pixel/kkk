# release 타입 변경(waive/stretch) downstream 누락 여부 점검 (2026-03-29)

## 결론
- **해당 문제는 현재 코드베이스에서도 여전히 재현 가능성이 있습니다.**
- 특히 release 이벤트를 `RELEASE_TO_FA` 대신 `WAIVE_TO_FA`, `STRETCH_TO_FA`로만 기록/사용하는 경로에서는,
  downstream 로직이 release 이동 이벤트를 놓칠 수 있습니다.

## 근거 1) move-event 수집 필터가 `RELEASE_TO_FA` 계열 중심
- `agency/service.py::_team_move_events_by_pid_since()`는 transactions_log 조회 시 tx_type allowlist를 아래로 제한합니다.
  - 포함: `trade`, `TRADE`, `SIGN_FREE_AGENT`, `RELEASE_TO_FA`, `signing`, `release_to_free_agency`
  - **미포함: `WAIVE_TO_FA`, `STRETCH_TO_FA`**
- 따라서 waive/stretch로 FA 이동한 선수는 월별 팀 이동 복원(`teams_asof_by_pid`) 계산에서 이벤트가 누락됩니다.

## 근거 2) 실제 서비스 레이어는 waive/stretch를 별도 타입으로 적재
- `league_service.py`에서
  - waive는 tx type/action_type = `WAIVE_TO_FA`
  - stretch는 tx type/action_type = `STRETCH_TO_FA`
  로 기록합니다.
- 즉, producer(적재 타입)와 consumer(조회 allowlist) 간 불일치가 존재합니다.

## 근거 3) rule-player-meta 계약 타입 allowlist도 `RELEASE_TO_FA` 중심
- `trades/rules/rule_player_meta.py`의 `CONTRACT_TYPES`는 release 관련으로 `RELEASE_TO_FA` 및 legacy lowercase(`release_to_free_agency`)만 포함합니다.
- **`WAIVE_TO_FA`, `STRETCH_TO_FA`가 제외**되어 있어,
  룰 메타의 `last_contract_action_type/date`가 실제 최신 계약 행위를 반영하지 못할 수 있습니다.

## 실제로 어떻게 터질 수 있는가 (예시)

### 시나리오 A: 월 컨텍스트 팀 소속 복원 오류
1. 2026-02-03: 팀 BOS 소속 선수 P가 `WAIVE_TO_FA` 처리되어 FA 이동.
2. 2026-02-20: 팀 LAL이 P를 FA로 영입(`SIGN_FREE_AGENT`).
3. month-context 생성 시 `_team_move_events_by_pid_since()`는 `WAIVE_TO_FA`를 읽지 못함.
4. 결과적으로 날짜별 소속 역산에서 2/03~2/19 구간의 중간 상태(FA)가 반영되지 않고,
   일부 집계(부상/출전/DNP를 팀별로 재분배하는 로직)에서 잘못된 팀 귀속이 발생할 수 있습니다.

### 시나리오 B: trade rule용 last_contract_action 오판
1. 선수가 최근 계약 이벤트로 `STRETCH_TO_FA`를 가짐.
2. rule meta 빌드 시 `CONTRACT_TYPES`에 `STRETCH_TO_FA`가 없어 해당 이벤트 무시.
3. `last_contract_action_type`이 더 과거의 다른 이벤트(예: `SIGN_FREE_AGENT`)로 남거나 None 처리될 수 있음.
4. downstream policy가 `last_contract_action_type`을 기준으로 ban/eligibility를 평가할 때,
   실제 최신 상태와 다른 판단을 내릴 여지가 생깁니다.

## 정리
- 질문에서 제시한 지적(“`RELEASE_WAIVE/RELEASE_STRETCH` 혹은 waive/stretch 신규 타입만 쓰면 누락”)의 취지는,
  현재 코드 기준 명명(`WAIVE_TO_FA`, `STRETCH_TO_FA`)으로도 **동일하게 유효**합니다.
- 즉 **release 계열 타입 확장 시 consumer allowlist 동기화가 누락된 상태**가 맞습니다.
