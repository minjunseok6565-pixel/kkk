# News 패키지 개발 환경 가이드

이 문서는 `news/` 패키지와 이를 호출하는 `news_ai.py`/API 라우트를 기준으로, **빠르게 재현·디버깅·검증할 수 있는 개발 환경**을 만들기 위한 체크리스트입니다.

## 1) 현재 뉴스 파이프라인 구조(코드 기준)

주간 뉴스(`refresh_weekly_news`) 흐름:
1. 현재 인게임 날짜/시즌 ID 조회
2. `weekly_news` 캐시 신선도 확인
3. 워크플로 스냅샷에서 정규시즌 이벤트 추출 + 트랜잭션 이벤트 추가
4. 중요도 점수 부여(`apply_importance`) + 에디토리얼 선별(`select_top_events`)
5. 템플릿 렌더(`render_article`) 후 선택적으로 Gemini 리라이트
6. 캐시 기록 후 API 응답

플레이오프 뉴스(`refresh_playoff_news`) 흐름:
1. `postseason.playoffs` 존재 확인
2. `playoff_news` 캐시에서 `processed_game_ids` 로 중복 방지
3. 새로 끝난 경기만 이벤트 추출
4. 중요도 점수 부여 후 상위 최대 12개만 기사화
5. 누적 기사 수를 최대 250개로 제한하고 캐시 갱신

핵심 호출 지점:
- API: `/api/news/week`, `/api/news/playoffs` (`app/api/routes/news.py`)
- 파사드: `news_ai.py`

## 2) 개발 환경에서 먼저 고정해야 할 조건

### A. 상태(State) 기반 캐시 스키마를 깨지 않기
`news/cache.py`는 weekly/playoff 캐시에 대해 **exact key 검증**을 수행합니다.
- 키가 하나라도 빠지거나(extra/missing) 타입이 다르면 `ValueError`를 발생시킵니다.
- 따라서 수동 디버깅 시에도 `state.cached_views.weekly_news`, `state.cached_views.playoff_news` 구조를 임의로 축약하지 않는 것이 중요합니다.

### B. 주간 뉴스 API의 `apiKey`는 필수
`refresh_weekly_news` 및 `/api/news/week` 모두 `apiKey` 누락 시 에러를 반환합니다.
- 단, Gemini 리라이트는 선택 동작이며(`google.generativeai` import 실패 시 폴백), 템플릿 기반 기사 생성은 계속 동작합니다.

### C. 플레이오프 뉴스는 `processed_game_ids`가 사실상 기준선
플레이오프 뉴스 중복/누락 이슈는 대부분 `processed_game_ids` 추적 상태로 재현됩니다.
- 같은 `game_id`는 재생성하지 않음
- 새 완료 경기 감지는 `winner`가 있는 게임만 대상으로 수행

## 3) 추천 로컬 워크플로(빠른 반복용)

### 1단계: 진입점 기준 스모크 테스트
- 주간: `POST /api/news/week` with `{ "apiKey": "dummy" }`
- 플레이오프: `POST /api/news/playoffs` with `{}`

목적:
- 라우트/파사드/캐시 read-write가 한 번에 연결되는지 확인
- `ValueError`(캐시 스키마), `postseason` 부재 등 빠른 실패 포인트 확인

### 2단계: 캐시 무효화 시나리오 확인
주간은 아래 중 하나라도 바뀌면 재생성됩니다.
- `generator_version`
- `season_id`
- `last_generated_week_start`
- `last_generated_as_of_date`
- `items` 비어있음

플레이오프는 `processed_game_ids`에 없는 새 완료 경기(`winner` 존재)가 있을 때만 기사 추가가 발생합니다.

### 3단계: 이벤트 품질 확인 포인트
정규시즌 이벤트 추출(`news/extractors/weekly.py`)은 다음 규칙 기반입니다.
- 경기 단위: `UPSET`, `CLUTCH_OT`, `BLOWOUT`
- 선수 단위: `PLAYER_40PTS`, `PLAYER_TRIPLE_DOUBLE`, `PLAYER_10AST`, `PLAYER_20REB`, `PLAYER_5STL`, `PLAYER_5BLK`, `PLAYER_7_3PM`, `PLAYER_MASTERCLASS`
- 팀 단위: `STREAK_TEAM`

플레이오프 이벤트(`news/extractors/playoffs.py`)는 게임별로 최소 `PLAYOFF_GAME_RECAP`를 생성하고,
상황에 따라 `PLAYOFF_SERIES_SWING`, `PLAYOFF_MATCH_POINT`, `PLAYOFF_ELIMINATION`를 추가합니다.

## 4) 디버깅 우선순위(문제 발생 빈도 기준)

1. **캐시 shape 에러**
   - `news/cache.py`의 strict validator 메시지 먼저 확인
2. **데이터 소스 누락**
   - 주간: snapshot의 `games`, `game_results`
   - 플레이오프: `postseason.playoffs.bracket.*.games`
3. **중복/미생성 이슈**
   - `event_id` 생성 규칙(`news/ids.py`)과 `processed_game_ids` 확인
4. **문장 품질 이슈**
   - 템플릿 결과(`news/render/template_ko.py`)와 Gemini 리라이트(`news/render/gemini_rewrite.py`)를 분리해 점검

## 5) 실무 팁(코드 구조에 맞춘 최적화)

- **LLM 의존 최소화 개발**: 로컬에서는 Gemini가 없어도 주간 뉴스 생성이 가능하므로, 우선 템플릿 렌더 기준으로 기능 검증 후 리라이트를 나중에 확인합니다.
- **결정론 우선 검증**: 이벤트 추출/점수/선별은 규칙 기반이므로, 동일 스냅샷에서 결과가 안정적으로 재현되는지 먼저 확인합니다.
- **버전 기반 배포 체크**: 추출/렌더링 로직 변경 시 `WEEKLY_GENERATOR_VERSION`, `PLAYOFF_GENERATOR_VERSION`과 캐시 영향 범위를 같이 검토합니다.

## 6) 관련 파일 맵

- 파사드/오케스트레이션: `news_ai.py`
- API 라우트: `app/api/routes/news.py`
- 캐시 검증/정책: `news/cache.py`
- 이벤트 추출: `news/extractors/weekly.py`, `news/extractors/playoffs.py`
- 점수/편집: `news/scoring.py`, `news/editorial.py`
- 렌더링: `news/render/template_ko.py`, `news/render/gemini_rewrite.py`
- 타입/ID: `news/models.py`, `news/ids.py`
