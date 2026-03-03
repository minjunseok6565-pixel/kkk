# 메인 화면(Home) 정보 고도화 설계안 (코드 기반 데이터 감사 결과)

## 0) 목적
- 현재 Home 화면이 `다음 경기`만 보여서 밋밋한 문제를 해결하기 위해, **현재 코드/API가 실제로 제공 가능한 데이터만** 사용한 정보 설계를 제안한다.
- 이 문서는 구현 전 단계의 설계 문서이며, UI 코드 변경은 포함하지 않는다.

---

## 1) 현재 코드 기준 Home의 실제 한계
현재 Home은 사실상 아래 2개 데이터만 읽어온다.
1. 인게임 날짜: `/api/state/summary`의 `workflow_state.league.current_date`
2. 다음 경기: `/api/team-schedule/{team_id}`에서 다음 미완료 경기 1건

즉, 화면이 빈약한 이유는 "데이터가 없는 것"보다 **Home에서 데이터를 거의 안 쓰고 있는 구조**에 가깝다.

---

## 2) 메인 화면에 즉시 활용 가능한 기존 조회 API

## 2-1. 팀/시즌 요약
### A. `/api/team-detail/{team_id}`
- 요약(`summary`): `wins`, `losses`, `win_pct`, `rank`, `gb`, `payroll`, `cap_space`, `point_diff`
- 로스터(`roster`): `ovr`, `salary`, `sharpness`, `short_term_stamina`, `long_term_stamina`, `pts/ast/reb` 등
- 활용:
  - 상단 KPI 스트립(성적/순위/재정/컨디션)
  - "컨디션 주의 선수 수", "평균 샤프니스" 등 Home용 계산

### B. `/api/standings/table`
- 팀별 `rank`, `win_pct`, `gb_display`, `l10`, `strk`, `home`, `away`, `conf`, `div`, `pf`, `pa`, `diff`
- 활용:
  - 내 팀의 "최근 10경기", "연승/연패", "홈/원정 성적" 미니 카드
  - 다음 상대와의 단순 비교(승률/득실마진)

## 2-2. 일정/경기 컨텍스트
### C. `/api/team-schedule/{team_id}`
- 각 경기: `date`, `is_home`, `opponent_team_id`, `tipoff_time`, 완료 경기의 `result`, `record_after_game`, `leaders`
- 활용:
  - Hero 영역(다음 경기 카드)
  - 최근 경기 3~5개 결과 피드
  - 향후 7일 경기 수, B2B(백투백) 위험 계산

## 2-3. 건강/리스크
### D. `/api/medical/team/{team_id}/overview`
- `summary.injury_status_counts`, `summary.risk_tier_counts`, `summary.health_frustration`
- `watchlists.highest_risk`, `currently_unavailable`, `recent_injury_events`
- 활용:
  - "결장 n명 / 고위험 n명" 경고 배지
  - 핵심 리스크 선수 카드 Top1~3

### E. `/api/medical/team/{team_id}/alerts`
- `alert_level`, `primary_alert_player`, `team_load_context.next_7d_game_count`, `next_7d_back_to_back_count`, `kpi_delta_7d`
- 활용:
  - Home 최상단 "오늘의 경고 바" (critical/warn/info)
  - 지난 7일 대비 증감(리스크가 악화되는지)

### F. `/api/medical/team/{team_id}/risk-calendar?days=14`
- 날짜별 `is_game_day`, `is_back_to_back`, `high_risk_player_count`, `out_player_count`
- 활용:
  - "2주 부하 캘린더" 미니 히트맵

## 2-4. 기타 활용 가능 데이터
### G. `/api/two-way/summary/{team_id}`
- `used_two_way_slots`, `open_two_way_slots`, 선수별 `games_remaining`
- 활용:
  - 벤치 운용 여지(콜업/로스터 유연성) 알림

### H. `/api/stats/leaders`
- 리그 리더보드(PTS/AST/REB/3PM 상위)
- 활용:
  - Home 하단 "리그 트렌드" 위젯 (선택)

---

## 3) Home 정보 구조 제안 (깔끔 + 고퀄 중심)

## 3-1. 상단 1행: Franchise Snapshot
- 좌측: 팀명 + 현재 날짜
- 우측 KPI 4칸:
  1) Record (`wins-losses`)
  2) Standing (`rank`, `gb_display`)
  3) Form (`l10`, `strk`)
  4) Condition (`OUT 수`, `HIGH risk 수`)
- 데이터 출처: `team-detail` + `standings/table` + `medical/overview`

## 3-2. 2행 Hero: Next Game Intel
- 기존 VS 카드 유지하되 정보 추가:
  - 경기일시, 홈/원정, 상대팀
  - 상대 vs 우리팀 비교: `win_pct`, `diff`, `home/away` split
  - 최근 맞대결이 없으면 "이번 시즌 첫 맞대결"로 처리
- 데이터 출처: `team-schedule` + `standings/table`

## 3-3. 3행 좌측: Today Priority (의사결정 패널)
- 규칙 기반 최대 3개 항목만 노출
  - 예: "백투백 임박", "결장 2명", "고위험 선수 3명"
- 각 항목은 1줄 요약 + 액션 버튼(전술/메디컬/훈련)
- 데이터 출처: `medical/alerts`, `medical/overview`, `risk-calendar`

## 3-4. 3행 우측: Recent Activity Feed
- 최신순 5개
  - 최근 경기 결과 (`team-schedule.result`)
  - 최근 부상 이벤트 (`medical/overview.watchlists.recent_injury_events`)
- 데이터 출처: `team-schedule`, `medical/overview`

## 3-5. 4행: 2주 일정/리스크 캘린더
- 날짜별 점 표기:
  - 경기일, B2B, OUT 수, HIGH risk 수
- hover 또는 클릭 시 간단 툴팁
- 데이터 출처: `medical/risk-calendar`

---

## 4) 신규 API 필요성 판단

## 4-1. 결론
- **1차 구현은 신규 API 없이 가능**하다.
- 다만 Home 진입 시 API 호출 수가 많아질 수 있으므로, 2차에서 집계 API를 권장.

## 4-2. 권장 신규 API(선택): `/api/home/dashboard/{team_id}`
- 목적: Home 렌더용 파편 데이터를 1회 호출로 제공
- 내부적으로 조합:
  - `team-detail`
  - `standings/table`
  - `team-schedule`
  - `medical/overview`
  - `medical/alerts`
  - `medical/risk-calendar(days=14)`
- 반환 예시 구조:
  - `snapshot`: 성적/순위/재정/컨디션
  - `next_game`: 상대/시간/간단 매치업 비교
  - `priorities`: 규칙 기반 경고 1~3개
  - `feed`: 최근 경기/부상 이벤트
  - `risk_calendar_14d`

---

## 5) 화면 품질을 위한 표시 규칙(핵심)
- 정보량은 늘리되 카드 수는 제한: 한 화면 5~6개 블록을 넘기지 않는다.
- 모든 숫자에는 기준일(`as_of_date`)을 표시한다.
- 경고 색상은 3단계만 사용: `info / warn / critical`.
- 값 부재는 `--`로 통일하고, 카드 전체 공백은 금지(빈 상태 메시지 제공).
- Home은 "행동 유도"가 목적이므로 각 경고 카드에 바로가기 CTA 1개를 둔다.

---

## 6) 구현 우선순위(개발 착수 시)
1. **P1**: Snapshot + Hero + Priority 3종 (`team-detail`, `standings/table`, `team-schedule`, `medical/alerts`)
2. **P2**: Activity Feed + 14일 리스크 캘린더 (`medical/overview`, `medical/risk-calendar`)
3. **P3**: 집계 API(`/api/home/dashboard/{team_id}`)로 성능/유지보수 최적화

---

## 7) 비범위(이번 설계에서 제외)
- 코드가 제공하지 않는 외부 데이터(실제 NBA 뉴스/실시간 베팅/실제 이동거리 등)
- 생성형 모델이 필요한 서사형 코멘터리 자동 생성
- 타 화면(전술/훈련/시장)의 UI 재구성

