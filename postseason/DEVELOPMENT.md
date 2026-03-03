# Postseason 개발 환경 가이드

이 문서는 `postseason/` 패키지의 **현재 코드**를 기준으로, 플레이인/플레이오프 개발을 빠르게 반복하기 위한 실무 가이드입니다.

## 1) 패키지 구조(책임 분리)

- `director.py`
  - postseason 플로우의 **상태 변경 단일 진입점**입니다.
  - `initialize_postseason`, `play_my_team_play_in_game`, `advance_my_team_one_game`, `auto_advance_current_round` 같은 퍼블릭 API를 제공합니다.
- `seeding.py`
  - 정규시즌 순위 기반 필드 생성(`build_postseason_field_from_standings`)과 랜덤 필드 생성(`build_random_postseason_field`)을 담당합니다.
  - 상태 변경은 하지 않습니다.
- `play_in.py`
  - 플레이인 상태 템플릿 생성, 결과 반영(`apply_play_in_results`), 자동 진행(`auto_play_in_conf`)을 담당합니다.
- `bracket.py`
  - 플레이오프 브래킷 생성, 시리즈 단위 경기 시뮬레이션, 라운드 전환(`advance_round_if_ready`)을 담당합니다.
- `schedule.py`
  - 날짜 파싱/보정, 플레이인 윈도우 계산, 시리즈 다음 경기일 계산(2-2-1-1-1 패턴 기반)을 담당합니다.
- `ids.py`
  - 플레이인/플레이오프 게임 및 시리즈에 대해 **결정론적 ID**를 생성합니다.
- `reset.py`
  - postseason + 관련 캐시/결과 컨테이너 초기화 로직을 담당합니다.
- `models.py`
  - 런타임 dict 구조를 설명하는 TypedDict 타입 힌트 모음입니다(런타임 강제 아님).

## 2) 개발 시 반드시 지킬 핵심 규칙

1. **상태 변경은 `director.py` 경유**
   - 모듈 설명대로 postseason 흐름에서 상태를 바꾸는 책임은 director에 모여 있습니다.
2. **phase 문자열 일관성 유지**
   - 플레이인 결과는 `phase="play_in"`, 플레이오프 결과는 `phase="playoffs"`로 기록됩니다.
3. **결정론적 ID 유지**
   - 플레이인: `PI{year}_{CONF}_{KEY}`
   - 시리즈: `PO{year}_{CONF}_{ROUND}_{LABEL}`
   - 시리즈 경기: `{series_id}_G{n}`
4. **브래킷/시리즈 dict shape 유지**
   - UI/뉴스 호환성을 위해 키 구조를 바꾸지 않는 것이 안전합니다.

## 3) 빠른 개발 루프(권장)

### Step A. 코드 변경 후 즉시 문법 검증

```bash
python -m compileall postseason
```

### Step B. 초기화/진행 API를 기준으로 동작 확인

서버 엔드포인트에서 직접 쓰는 퍼블릭 API는 패키지 레벨에서 재수출됩니다.

```python
from postseason import (
    initialize_postseason,
    play_my_team_play_in_game,
    advance_my_team_one_game,
    auto_advance_current_round,
)
```

개발 중에는 이 함수들 중심으로 회귀 확인을 하면, 내부 모듈 리팩토링 시에도 외부 계약(호출점) 안정성을 점검하기 쉽습니다.

### Step C. 라운드 전환/시드 확정 포인트 집중 확인

- 플레이인 완료 판단: `is_play_in_complete`
- 플레이인 결과 반영: `apply_play_in_results`
- 플레이오프 시작 트리거: `_maybe_start_playoffs_from_play_in`
- 라운드 전환/우승자 확정: `advance_round_if_ready`

이 4개 지점은 변경 시 체감 버그가 가장 크게 나타나는 경계입니다.

## 4) 디버깅 포인트 요약

- 날짜 관련 이슈
  - `schedule.safe_date_fromisoformat`가 `None`을 반환하는 입력을 먼저 의심합니다.
  - 시리즈 경기일은 `compute_series_next_game_date`가 이전 경기 장소 동일/원정 전환 여부로 +1/+2일을 계산합니다.
- 플레이인 결과 반영 이슈
  - `apply_play_in_results`는 `seven_vs_eight`, `nine_vs_ten`, `final` 결과를 기반으로 `seed7`, `seed8`, `eliminated`를 재구성합니다.
- 사용자 팀 진행 이슈
  - 플레이인: `play_my_team_next_game`
  - 플레이오프: `find_my_series` + `simulate_one_series_game`

## 5) 리팩토링 시 안전 체크리스트

- [ ] `postseason/__init__.py`의 퍼블릭 export 목록을 유지했는가?
- [ ] 플레이인/플레이오프 결과 요약에 `phase`가 올바르게 들어가는가?
- [ ] ID 생성이 `ids.py`를 통해서만 이뤄지는가?
- [ ] 시리즈 홈/원정 패턴이 `HOME_PATTERN_7` 규칙을 유지하는가?
- [ ] `reset_postseason_state`가 phase 결과/뉴스/stats 캐시를 계속 정리하는가?

## 6) 팀 개발환경 최적화 제안(코드 기반)

- **작업 단위를 director API 기준으로 통일**
  - 내부 모듈을 건드려도 최종 확인은 `initialize_postseason`/`advance_my_team_one_game` 경로로 맞추면 협업 시 공통 회귀 기준이 생깁니다.
- **날짜/ID 유틸은 단일 소스 유지**
  - 날짜 계산은 `schedule.py`, ID 생성은 `ids.py`만 사용하도록 습관화하면 중복 버그를 줄일 수 있습니다.
- **플레이인/브래킷 상태 shape 변경 최소화**
  - 현재 코드는 UI/뉴스 호환을 전제로 하므로, 상태 키 변경은 가능하면 피하고 확장이 필요하면 optional 필드 방식이 안전합니다.
