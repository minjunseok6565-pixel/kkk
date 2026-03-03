# 메디컬 센터 화면 기획안 (실행 가능한 UI/데이터 설계)

## 0) 문서 목적
- 목적: 현재 `메디컬 센터` 화면을 **상업용 게임 품질**로 고도화하기 위한 실제 기획안을 제시한다.
- 범위: 디자인/정보구조/데이터 소스/API 계약.
- 제약: **게임 코드에 이미 존재하는 데이터만 사용**한다. 새로운 화면 정보가 필요하면, 기존 데이터 조합으로 계산 가능한 API만 설계한다.

---

## 1) 현재 구현 기준 (SSOT)
현재 메디컬 화면은 아래 UI와 API를 사용하고 있다.

### 1-1. 현재 UI 구조
- KPI 카드: 로스터 인원 / 현재 결장 / 고위험 선수 / 건강 불만.
- 본문 테이블: 리스크 워치리스트, 결장/복귀 현황, 건강 불만 Top.
- 선수 타임라인: 워치리스트 클릭 시 최근 부상 이벤트.

### 1-2. 현재 API (이미 존재)
1. `GET /api/medical/team/{team_id}/overview`
   - 사용 데이터: `summary`, `watchlists.highest_risk`, `watchlists.currently_unavailable`, `watchlists.health_frustration_high`, `watchlists.recent_injury_events`.
2. `GET /api/medical/team/{team_id}/players/{player_id}/timeline`
   - 사용 데이터: 선수 상태/리스크/헬스 심리 + `timeline.events`.
3. 보조 API
   - `GET /api/medical/team/{team_id}/injury-risk`
   - `GET /api/medical/team/{team_id}/injured`
   - `GET /api/team-schedule/{team_id}`
   - `GET /api/readiness/team/{team_id}/sharpness`
   - `GET /api/practice/team/{team_id}/sessions`
   - `POST /api/practice/team/{team_id}/preview`

---

## 2) 목표 UX: “정보 확인”에서 “즉시 의사결정”으로

## 2-1. 화면 IA (Information Architecture)

### A. 상단 Hero Alert Bar (신규 영역)
- 목적: 지금 당장 조치가 필요한 이슈를 1줄로 노출.
- 노출 정보:
  - 최우선 위험 선수명
  - 현재 상태(OUT/RETURNING/HEALTHY)
  - 위험 점수/등급
  - 복귀 예정일(있으면)
  - 다음 7일 경기 밀집도(예: 7일 4경기, B2B 1회)
- UX:
  - “선수 상세 보기” 버튼
  - “권고안 보기” 버튼

### B. KPI Strip (기존 4카드 고도화)
- 카드 유지 + 증감/맥락 추가:
  1) 로스터 인원
  2) 현재 결장 (RETURNING 포함 별도 수치)
  3) 고위험 선수 (HIGH)
  4) 건강 불만 임계 초과 (`health_frustration >= 0.5`)
- 각 카드 하단에 `지난 7일 대비` 변화값 추가.

### C. 좌측 Primary Table: 리스크 워치리스트 (핵심 작업영역)
- 컬럼 재설계:
  - 선수(포지션, 나이)
  - 상태 배지
  - 리스크 (점수+티어+미니바)
  - 컨디션(ST/LT stamina)
  - Sharpness
  - 재부상 카운트 합계
  - 최근 이벤트일
- 정렬 기본값: 리스크 점수 내림차순.

### D. 우측 Context Panel: 선택 선수 상세
- 섹션:
  1) 현재 상태 요약 (부위/부상유형/복귀 윈도우)
  2) 최근 타임라인 (기존 유지)
  3) 액션 프리뷰 (신규: 권고안 비교)

### E. 하단 Risk Calendar Strip (신규)
- 다음 7일 기준으로 경기/훈련/위험선수 변동 가능성을 한 줄 캘린더로 표시.
- 목표: “일정과 메디컬 상태를 같이” 보이도록 한다.

---

## 3) 컴포넌트별 데이터 소스 매핑 (어디서 가져오는지 명확화)

## 3-1. 이미 존재 API만으로 구성 가능한 항목

| UI 컴포넌트 | 필드 | 소스 API | 소스 필드 |
|---|---|---|---|
| KPI: 로스터 인원 | roster_count | `/api/medical/team/{team_id}/overview` | `summary.roster_count` |
| KPI: 현재 결장/복귀 | OUT, RETURNING | 동일 | `summary.injury_status_counts.OUT`, `RETURNING` |
| KPI: 고위험 선수 | HIGH count | 동일 | `summary.risk_tier_counts.HIGH` |
| KPI: 건강 불만 | high_count | 동일 | `summary.health_frustration.high_count` |
| 리스크 워치리스트 | 선수/상태/리스크/ST-LT/Sharp | 동일 | `watchlists.highest_risk[*]` |
| 결장/복귀 표 | 부위/부상유형/복귀일정 | 동일 | `watchlists.currently_unavailable[*].injury_current.*` |
| 건강 불만 Top | 불만도/요청단계/에스컬레이션 | 동일 | `watchlists.health_frustration_high[*]` |
| 선수 타임라인 | 이벤트 목록 | `/api/medical/team/{team_id}/players/{player_id}/timeline` | `timeline.events[*]` |
| 일정 맥락(다음 경기들) | date/opponent/status | `/api/team-schedule/{team_id}` | `games[*]` |

## 3-2. “현재 UI에는 없지만 코드에 데이터가 존재”하는 항목

| 추가 표시 정보 | 사용 이유 | 소스 API | 소스 필드 |
|---|---|---|---|
| 나이(age) | 고령 선수 위험 맥락 | `/api/medical/team/{team_id}/overview` | `watchlists.highest_risk[*].age` |
| 리스크 입력값 디테일 | 왜 위험한지 설명 | `/api/medical/team/{team_id}/overview` | `watchlists.highest_risk[*].risk_inputs.*` |
| 최근 부상 이벤트 Top | 팀 차원의 최근 사건 | `/api/medical/team/{team_id}/overview` | `watchlists.recent_injury_events[*]` |
| 선수별 health psychology | 트레이드 요청 위험 연계 | `/api/medical/team/{team_id}/players/{player_id}/timeline` | `current.health_psychology.*` |
| 팀 Sharpness 분포 | 메디컬/훈련 통합 지표 | `/api/readiness/team/{team_id}/sharpness?include_players=true` | `distribution.*`, `players[*]` |
| 훈련 세션 이력 | 회복/강훈련 추세 해석 | `/api/practice/team/{team_id}/sessions` | `sessions[*]` |

---

## 4) 신규 API 설계 (필요 시) — 단, 기존 데이터 조합만 사용

아래는 “신규 화면 경험”을 위해 필요하지만 현재 단일 API로는 바로 제공되지 않는 합성 데이터다.

## 4-1. `GET /api/medical/team/{team_id}/alerts`
- 목적: 상단 Hero Alert Bar 전용.
- 데이터 출처:
  - `/api/medical/team/{team_id}/overview`
  - `/api/team-schedule/{team_id}`
- 계산 규칙:
  - `primary_alert_player` = `highest_risk[0]`
  - `next_7d_game_count` = `games` 중 `current_date <= date < current_date+7` 개수
  - `next_7d_back_to_back_count` = 위 기간 내 연속일 경기 쌍 개수

### 응답 스키마 (제안)
```json
{
  "team_id": "GSW",
  "as_of_date": "2025-10-19",
  "alert_level": "info|warn|critical",
  "primary_alert_player": {
    "player_id": "p_xxx",
    "name": "...",
    "pos": "PG",
    "injury_status": "HEALTHY|OUT|RETURNING",
    "risk_score": 72,
    "risk_tier": "HIGH",
    "out_until_date": "2025-10-25",
    "returning_until_date": "2025-10-29"
  },
  "team_load_context": {
    "next_7d_game_count": 4,
    "next_7d_back_to_back_count": 1
  },
  "kpi_delta_7d": {
    "out_count_delta": 1,
    "high_risk_count_delta": 2,
    "health_high_count_delta": 0
  }
}
```

> 주의: `kpi_delta_7d` 계산은 같은 API를 `as_of_date` 기준으로 7일 전 재계산하는 방식으로 구현 가능(신규 DB 필드 필요 없음).

## 4-2. `GET /api/medical/team/{team_id}/risk-calendar`
- 목적: 하단 Risk Calendar Strip.
- 데이터 출처:
  - `/api/team-schedule/{team_id}` (경기)
  - `/api/practice/team/{team_id}/sessions` (훈련)
  - `/api/medical/team/{team_id}/overview` + `/timeline` (리스크/이벤트)
- 기간: 기본 14일.

### 응답 스키마 (제안)
```json
{
  "team_id": "GSW",
  "date_from": "2025-10-19",
  "date_to": "2025-11-01",
  "days": [
    {
      "date": "2025-10-20",
      "is_game_day": true,
      "opponent_team_id": "LAL",
      "is_back_to_back": false,
      "practice_session_type": "RECOVERY|REST|OFF_TACTICS|DEF_TACTICS|FILM|SCRIMMAGE|null",
      "high_risk_player_count": 2,
      "out_player_count": 1,
      "returning_player_count": 1,
      "injury_event_count": 0
    }
  ]
}
```

## 4-3. `GET /api/medical/team/{team_id}/players/{player_id}/action-recommendations`
- 목적: 우측 패널에서 “조치 전/후 비교” 제공.
- 데이터 출처:
  - 현재 상태: `/api/medical/team/{team_id}/players/{player_id}/timeline`
  - 팀 훈련 효과 프리뷰: `POST /api/practice/team/{team_id}/preview`
- 제약:
  - **실제 시뮬 결과를 단정하지 않고**, “예상 변화(heuristic)”로만 반환.
  - 근거값(`basis`)을 반드시 노출해 블랙박스 느낌 제거.

### 응답 스키마 (제안)
```json
{
  "team_id": "GSW",
  "player_id": "p_xxx",
  "as_of_date": "2025-10-19",
  "current": {
    "injury_status": "HEALTHY|OUT|RETURNING",
    "risk_score": 58,
    "risk_tier": "MEDIUM",
    "short_term_fatigue": 0.31,
    "long_term_fatigue": 0.22,
    "sharpness": 51.0,
    "health_frustration": 0.34
  },
  "recommendations": [
    {
      "action_id": "RECOVERY_SESSION_NEXT_DAY",
      "label": "다음 훈련일 회복 세션 배치",
      "expected_delta": {
        "short_term_fatigue": -0.04,
        "sharpness": -0.5,
        "risk_score": -3
      },
      "basis": {
        "practice_preview_used": true,
        "risk_formula_version": "core._risk_tier_from_inputs"
      }
    }
  ]
}
```

---

## 5) 화면별 상세 기획 (디자이너/프론트 전달용)

## 5-1. Hero Alert Bar
- 레이아웃: 좌(경고 아이콘+문구) / 우(CTA 2개)
- 문구 예시:
  - `주의: Stephen Curry 리스크 HIGH(72) · 다음 7일 4경기(B2B 1회)`
- 상태 컬러:
  - info: 블루, warn: 앰버, critical: 레드
- 클릭 액션:
  - 선수 상세 열기
  - 권고안 drawer 열기

## 5-2. 리스크 워치리스트 테이블
- 행 높이 56px, 헤더 고정.
- 셀 규칙:
  - 리스크: `숫자 + 바(0~100)`
  - 상태: 배지(HEALTHY/OUT/RETURNING)
  - 컨디션: `ST stamina / LT stamina`를 %로 표기.
- 행 클릭 시:
  - 우측 패널 업데이트
  - 타임라인 API 재호출

## 5-3. 우측 상세 패널
- `상태 요약 카드` + `타임라인` + `권고안`
- 타임라인 아이템에 최소 표시:
  - 날짜, context, body_part, injury_type, severity, out/returning window

## 5-4. 하단 Risk Calendar Strip
- 14칸(일자별) 수평 스크롤.
- 각 일자 칩:
  - 경기 여부 아이콘
  - 훈련 타입 라벨
  - 위험 인원 뱃지(HIGH n명)

---

## 6) “절대 가져오면 안 되는 정보” 명시
다음은 현재 코드/DB에서 근거가 확인되지 않으므로 사용 금지.

- 의료진 인원/의료진 능력치/치료 슬롯 수
- 선수별 실제 생체 데이터(심박, 수면시간, GPS 부하 등)
- 부상 확률의 외부 리그 데이터 연동값
- 보험/재정 기반 치료비 관련 수치

> 위 정보가 필요하면, 먼저 데이터 모델과 저장 로직을 신규로 추가한 뒤 API를 설계해야 한다.

---

## 7) 구현 우선순위 (디자인/개발 순차 적용)
1. **P1 (즉시 가능)**: 기존 `overview + timeline`으로 레이아웃/스타일/정보 위계 개편.
2. **P2 (중간 난이도)**: `alerts`, `risk-calendar` 합성 API 추가.
3. **P3 (고급 UX)**: `action-recommendations`(근거 포함) 추가.

---

## 8) QA 체크리스트
- [ ] 모든 신규 숫자/문구가 기존 API 응답 필드로 추적 가능한가?
- [ ] 신규 API 필드가 기존 테이블/상태에서 계산 가능한가?
- [ ] `알 수 없음` 상태는 명시적으로 `null`/`-` 처리했는가?
- [ ] 상태 배지 색/텍스트가 전체 화면에서 일관적인가?
- [ ] 빈 상태(Empty state)에 다음 행동 버튼이 있는가?


---


## 9) 구현 완료 상태 (2026-03)
- 아래 조회 API가 실제 서버 라우트에 추가되어 UI 작업 착수 가능 상태이다.
  - `GET /api/medical/team/{team_id}/alerts`
  - `GET /api/medical/team/{team_id}/risk-calendar`
  - `GET /api/medical/team/{team_id}/players/{player_id}/action-recommendations`
- 기존 API 재사용 공통 로직(`overview` 계산)을 내부 헬퍼로 통합하여 동일 기준으로 데이터가 계산된다.

