# 홈 대시보드 `확인 필요` 탭 구현 패치 계획

## 1) 목표

홈 대시보드에 **즉시 확인이 필요한 이슈 전용 탭**을 추가한다.

- 홈 미리보기: 최근 5건
- 전체 보기: 누적 이슈 전체(또는 페이지네이션)
- 읽음 처리: 프론트 `localStorage` 기반으로 이슈 숨김(소식함에서 제거)

이슈 유형은 아래 3가지로 한정한다.

1. 트레이드 제안
2. 부상 발생
3. 불만 이벤트

---

## 2) 구현 범위 (이번 패치)

### 포함

- 통합 이슈 API 신규 추가
- 홈 화면에 `확인 필요` 미리보기 카드(최근 5건) 추가
- `확인 필요` 전체 보기 UI(패널 또는 모달) 추가
- `읽음` 버튼 동작 및 localStorage 반영
- 이슈 문구 생성 규칙 고정(아래 4장)
- 테스트(백엔드 포맷/정렬, 프론트 읽음 필터 최소 단위)

### 제외

- 서버 영속 읽음 상태(DB 저장)
- PRIVATE / AGENT / PUBLIC 채널별 톤 분기
- 기존 마켓/메디컬 화면 구조 대수술

---

## 3) 통합 API 설계

## 3.1 엔드포인트

`GET /api/home/attention/{team_id}`

### Query Params

- `limit: int = 50` (1~200)
- `offset: int = 0`
- `include_read: bool = false`  
  > 현재는 localStorage 읽음이므로 서버는 실제 read를 알 수 없음. 호환성 확보 차원에서 남겨두되, 이번 버전에서는 무시하거나 항상 동일 응답.

## 3.2 응답 스키마

```json
{
  "ok": true,
  "team_id": "GSW",
  "current_date": "2026-01-15",
  "total": 17,
  "items": [
    {
      "issue_id": "trade:S1234:2026-01-15",
      "type": "TRADE_OFFER",
      "occurred_at": "2026-01-15",
      "title": "미네소타 (으)로부터 온 트레이드 제안",
      "detail": null,
      "meta": {
        "session_id": "S1234",
        "other_team_id": "MIN"
      }
    },
    {
      "issue_id": "injury:player_001:2026-01-14",
      "type": "INJURY",
      "occurred_at": "2026-01-14",
      "title": "Stephen Curry, ankle에 1~2주 부상",
      "detail": null,
      "meta": {
        "player_id": "player_001",
        "body_part": "ankle",
        "out_until_date": "2026-01-24"
      }
    },
    {
      "issue_id": "agency:evt_abc",
      "type": "DISSATISFACTION",
      "occurred_at": "2026-01-13",
      "title": "Stephen Curry (이)가 출전 시간에 대해 불만을 제기했습니다.",
      "detail": null,
      "meta": {
        "event_id": "evt_abc",
        "event_type": "MINUTES",
        "player_id": "player_001"
      }
    }
  ]
}
```

## 3.3 정렬/페이징 규칙

- 기본 정렬: `occurred_at DESC`, tie-breaker는 생성시각/ID
- `limit/offset`는 정렬 후 적용
- 홈 미리보기는 프론트에서 상위 5건만 렌더

## 3.4 issue_id 규칙

중복 제거/읽음 처리 키 안정성을 위해 아래처럼 deterministic 하게 구성:

- TRADE_OFFER: `trade:{session_id}:{date}`
- INJURY: `injury:{player_id}:{date}:{out_until_date}`
- DISSATISFACTION: `agency:{event_id}`

---

## 4) 문구 생성 규칙(고정)

## 4.1 트레이드 제안

- 포맷:
  - `"[상대팀명] (으)로부터 온 트레이드 제안"`
- 선수명/타겟 선수 표기 없음

예)
- `미네소타 (으)로부터 온 트레이드 제안`

---

## 4.2 부상 문구

기본 포맷:

- `"{선수명}, {body_part}에 {기간표현} 부상"`

기간 계산은 `current_date`와 `out_until_date` 차이(`delta_days`)로 처리.

### 규칙

1. `out_until_date` 없음
   - `기간표현 = "부상"` (최소 정보)

2. `delta_days <= 7`
   - `"{n}일"` (정확 일수)

3. `delta_days >= 8`
   - 주/월 단위 **러프 범위**로 표기

### 러프 범위 산출

- `weeks = ceil(delta_days / 7)`
- `months = ceil(delta_days / 30)`

#### 주 단위 구간(8~55일 권장)

- 8~14일: `1~2주`
- 15~21일: `2~3주`
- 22~28일: `3~4주`
- 29~35일: `4~5주`
- 36~42일: `5~6주`
- 43~49일: `6~7주`
- 50~55일: `7~8주` (팀에서 월 전환 직전 구간)

#### 월 단위 구간(56일 이상)

- 56~89일: `2~3개월`
- 90~119일: `3~4개월`
- 이후: `{m-1}~{m}개월` (m=ceil(delta_days/30), 하한은 max(1, m-1))

> 실무적으로는 단순화 가능:
> - 8~55일 = 주 구간
> - 56일 이상 = 월 구간

예)
- 10일: `1~2주 부상`
- 50일: `7~8주 부상` (또는 정책상 `1~2개월`로 통일 가능. 단, 한 정책으로 일관되게 적용)

### 권장 최종 정책 (혼선 방지)

- 8~49일: 주 단위
- 50일 이상: 월 단위

이 정책이면 예시와 자연스럽게 맞출 수 있다.

---

## 4.3 불만 이벤트 문구 매핑

PRIVATE/AGENT/PUBLIC 맥락은 무시하고, 이벤트 종류만 매핑한다.

| event_type | 문구 |
|---|---|
| MINUTES | `{선수} (이)가 출전 시간에 대해 불만을 제기했습니다.` |
| ROLE | `{선수} (이)가 자신의 코트 위 역할에 대해 불만을 제기했습니다.` |
| CONTRACT | `{선수} (이)가 계약 관련 불만을 표출했습니다.` |
| HEALTH | `{선수} (이)가 몸 상태 관련 불만을 제기했습니다.` |
| TEAM | `{선수} (이)가 팀 전력에 관한 불만을 제기했습니다.` |
| CHEMISTRY | `{선수} (이)가 라커룸 상황에 대한 불만을 제기했습니다.` |
| TRADE | `{선수} (이)가 자신을 트레이드 해줄 것을 요청했습니다.` |
| LOCKER_ROOM_MEETING | `선수단이 현재 팀 상황과 관련하여 정식으로 팀 미팅을 가질 것을 요구했습니다.` |
| BROKEN_PROMISE | `{선수} (이)가 약속 사항이 지켜지지 않았다고 느낍니다.` |
| TRADE_TARGETED_OFFER_PUBLIC | `{선수} (이)가 자신을 트레이드 하려고 했다는 사실에 서운함을 느낍니다.` |
| TRADE_TARGETED_OFFER_LEAKED | `{선수} (이)가 자신을 몰래 트레이드하려고 했다는 사실에 분노합니다.` |
| SAME_POS_RECRUIT_ATTEMPT | `{선수} (이)가 자신의 입지가 위협받고 있다고 느낍니다.` |

### fallback

- 위 매핑 외 타입:
  - `{선수} 관련 불만 이벤트가 발생했습니다.`

---

## 5) 백엔드 패치 상세

## 5.1 `app/api/routes/core.py`

### 추가할 함수

1. `_format_injury_duration_label(current_date_iso: str, out_until_iso: Optional[str]) -> str`
   - 위 4.2 규칙 적용
   - 날짜 파싱 실패 시 `"부상"`

2. `_build_attention_trade_items(team_id: str) -> List[Dict[str, Any]]`
   - 소스: `state.negotiations_get()` + inbox 필터와 동일 기준
   - 결과 type = `TRADE_OFFER`

3. `_build_attention_injury_items(team_id: str, season_year: int, as_of: date) -> List[Dict[str, Any]]`
   - 소스: `_medical_team_overview_payload(...).watchlists.recent_injury_events`
   - 결과 type = `INJURY`

4. `_map_agency_event_to_attention_text(event: Dict[str, Any], player_name: str) -> str`
   - 4.3 매핑 테이블 적용

5. `_build_attention_dissatisfaction_items(team_id: str, season_year: int) -> List[Dict[str, Any]]`
   - 소스: `agency_repo.list_agency_events(..., team_id=tid, season_year=sy)`
   - `event_type` 정규화(upper)
   - 결과 type = `DISSATISFACTION`

### 신규 라우트

`@router.get("/api/home/attention/{team_id}")`

- 입력 검증: team_id normalize
- `current_date`/`season_year` 조회
- 위 3개 item list 생성 후 병합
- 정렬/페이징 적용
- 응답 스키마 반환

### 주의사항

- 기존 `/api/home/dashboard/{team_id}`는 하위호환 유지
- 홈 dashboard 응답에 `attention_preview`를 넣을지 여부:
  - **권장**: 분리 API 유지(캐시/책임 분리)

---

## 5.2 `app/api/router.py`

- `core.router`에 신규 엔드포인트가 자동 포함되는지 확인
- 별도 include 필요 없으면 변경 없음

---

## 5.3 테스트 추가

### `tests/test_home_attention_api.py` (신규)

테스트 케이스:

1. `test_home_attention_returns_merged_items_sorted_desc`
2. `test_injury_duration_label_days_under_equal_7`
3. `test_injury_duration_label_weeks_and_months`
4. `test_dissatisfaction_event_mapping_table`
5. `test_trade_offer_title_format`

Mock 포인트:

- `state.negotiations_get`
- `agency_repo.list_agency_events`
- `_medical_team_overview_payload`

---

## 6) 프론트 패치 상세

## 6.1 HTML

### 파일: `static/NBA.html`

`main-screen` 내부에 `확인 필요` 영역 추가:

- 미리보기 리스트 컨테이너
- `전체 보기` 버튼
- (선택) 전체 이슈 모달/패널 마크업

권장 ID:

- `home-attention-preview`
- `home-attention-open-all-btn`
- `home-attention-panel` (or modal)
- `home-attention-list`

---

## 6.2 DOM 바인딩

### 파일: `static/js/app/dom.js`

신규 element reference 추가:

- `homeAttentionPreview`
- `homeAttentionOpenAllBtn`
- `homeAttentionPanel`
- `homeAttentionList`

---

## 6.3 API 유틸

### 파일: `static/js/core/api.js`

함수 추가:

- `fetchHomeAttention(teamId, { limit = 50, offset = 0 } = {}, { signal } = {})`
  - `GET /api/home/attention/{team_id}` 호출

---

## 6.4 상태/읽음 처리

### 파일: `static/js/app/state.js`

필드 추가:

- `homeAttentionItems`
- `homeAttentionLoaded`
- `homeAttentionPanelOpen`

### 파일: `static/js/features/main/mainScreen.js`

- 대시보드 refresh 시 attention API도 함께 호출
- 미리보기 5건 렌더 호출
- 전체 보기 버튼 클릭 핸들러 연결

### 파일: `static/js/features/main/homeWidgets.js`

함수 추가:

1. `getHomeAttentionReadStorageKey(teamId)`
2. `getHomeAttentionReadMap(teamId)`
3. `markHomeAttentionIssueRead(teamId, issueId)`
4. `isHomeAttentionIssueRead(teamId, issueId)`
5. `renderHomeAttentionPreview(items)`
   - 읽지 않은 항목 기준 상위 5건
6. `renderHomeAttentionFullList(items)`
   - 각 행에 `읽음` 버튼
   - 클릭 시 localStorage 반영 + 리스트 재렌더

읽음 저장 키:

- `nba.home.attention.read.{team_id}`

값 구조:

```json
{
  "issue_id_1": "2026-03-23T10:00:00.000Z",
  "issue_id_2": "2026-03-23T10:05:00.000Z"
}
```

---

## 6.5 이벤트 등록

### 파일: `static/js/app/events.js`

- `home-attention-open-all-btn` click 핸들러
- 전체 패널 닫기 이벤트(닫기 버튼/백드롭 사용 시)

---

## 6.6 스타일

### 파일: `static/css/screens/main.css`

추가 스타일:

- 미리보기 리스트 아이템
- type 배지 (TRADE_OFFER / INJURY / DISSATISFACTION)
- 읽음 버튼
- 전체 리스트 패널/모달

---

## 7) 이슈 생성 소스별 변환 규칙

## 7.1 Trade source → Attention item

입력 소스: 협상 세션(inbox 기준)

- `other_team_id` → 팀명 변환
- title = `"{팀명} (으)로부터 온 트레이드 제안"`

## 7.2 Injury source → Attention item

입력 소스: `recent_injury_events`

- name, body_part, out_until_date 사용
- title = `"{name}, {body_part_norm}에 {duration} 부상"`
- body_part_norm: 소문자/표준화(예: ANKLE→ankle)

## 7.3 Dissatisfaction source → Attention item

입력 소스: `agency_events`

- `event_type` upper 정규화
- `payload`는 선택적으로 reason 보강
- 매핑테이블로 title 생성

---

## 8) 성능/캐시 고려

- 홈 진입 시 API 1개 추가 호출 발생
- 캐시 정책 권장:
  - 짧은 TTL(예: 15~30초)
  - 경기 진행 직후 invalidation
- 추후 dashboard API에 preview를 인라인 포함하면 왕복 줄일 수 있음

---

## 9) 단계별 패치 순서(실행 계획)

1. 백엔드 helper + `/api/home/attention/{team_id}` 구현
2. 백엔드 테스트 작성/통과
3. 프론트 DOM/API/state/render 추가
4. 읽음(localStorage) 처리 및 재렌더 루프 완성
5. 스타일 적용
6. 수동 시나리오 점검
   - 트레이드만 있을 때
   - 부상만 있을 때
   - 불만만 있을 때
   - 읽음 처리 후 목록 제거

---

## 10) 완료 정의(DoD)

- 홈에서 `확인 필요` 미리보기 5건 표시
- 전체 보기에서 누적 이슈 확인 가능
- 각 이슈 `읽음` 버튼 클릭 시 즉시 리스트에서 사라짐
- 부상 기간 문구가 규칙대로 생성됨(<=7일 n일, 8일 이상 주/월)
- 불만 이벤트 문구가 지정 매핑과 일치
- 테스트 통과 및 기존 홈 대시보드 기능 회귀 없음

---

## 11) 후속 확장(옵션)

- 읽음 상태 서버 저장(세이브 파일 동기화)
- 이벤트 타입 필터(전체/트레이드/부상/불만)
- 중요도 태그(critical/warn/info)
- deep-link: 항목 클릭 시 해당 화면(시장/메디컬/선수상세)으로 이동
