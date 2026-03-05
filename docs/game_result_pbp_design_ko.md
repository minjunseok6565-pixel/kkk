# 경기 결과 화면 Play-by-Play(PBP) 로그 설계안 (2026-03-05)

## 목적
- `GameResultV2.replay_events`를 활용해 ESPN 스타일의 **읽기 좋은 이벤트 로그**를 만든다.
- 단, replay 이벤트를 모두 노출하지 않고, 사용자에게 의미 있는 이벤트만 선별한다.

---

## 1) 데이터 소스(어디서 가져올지)

## 1.1 기본 원천
- 경기 단위 원천: `workflow_state.game_results[game_id]`
- PBP 원천 필드: `game_results[game_id].replay_events`
- 팀/선수 표시 보강:
  - `game_results[game_id].teams[team_id].players[]` (선수명/ID 매핑)
  - `game_results[game_id].final` (최종 점수 검증/표시)

> 현재 코드 기준으로 `replay_events`는 matchengine 결과에서 어댑터를 통해 그대로 포함된다.

## 1.2 replay_events 확보 경로(현재 구현)
1. 우선 `raw.replay_events`에서 가져옴
2. 없으면 `raw.game_state.replay_events` 폴백
3. list[dict] 형태만 가볍게 검증 후 `game_result`에 저장

---

## 2) 왜 “전체 replay 이벤트 노출”이 안 되는가

replay stream에는 UI 관점에서 불필요한 이벤트가 섞일 수 있다. 예:
- 내부 계산/상태 동기화 성격의 이벤트
- 점수 변화 없는 미시 이벤트(포제션 내부 세부 단계)
- 사용자에게 문장으로 보여줄 가치가 낮은 중복 이벤트

따라서 PBP는 **도메인 이벤트 -> UI 이벤트로 재분류**하는 정제 단계가 반드시 필요하다.

---

## 3) PBP 이벤트 모델(서버 ViewModel)

프론트에는 replay 원본 대신 아래 형태의 `pbp_items[]`를 내려준다.

```json
{
  "seq": 381,
  "period": 1,
  "clock": "11:18",
  "team_id": "CHA",
  "event_key": "free_throw_made",
  "title": "+1 Point",
  "description": "Miles Bridges makes free throw 1 of 2",
  "score": {"home": 4, "away": 2},
  "score_change": 1,
  "tags": ["scoring", "free_throw"],
  "context": {
    "player_id": "1628970",
    "assist_player_id": null,
    "shot_type": "FT",
    "is_clutch_window": false
  }
}
```

핵심 원칙:
- `title`은 짧고 유형 중심(예: Made 3PT, Turnover, Timeout)
- `description`은 자연어 문장(선수명, 거리/유형, 보조 정보)
- `score`는 항상 누적 점수 스냅샷
- `score_change`는 해당 이벤트의 득점 변화량(0/1/2/3)

---

## 4) 어떤 replay 이벤트를 PBP로 보여줄지 (선별 규칙)

## 4.1 기본 노출군 (항상 후보)
1. **득점 이벤트**
   - 2점 성공, 3점 성공, 자유투 성공
   - And-1, 3점 파울 + 자유투 등 득점 연결 상황 포함
2. **샷 실패 + 리바운드 핵심 흐름**
   - Missed FG, Missed FT
   - Offensive/Defensive rebound (팀 리바운드 포함)
3. **턴오버/스틸**
4. **개인 파울(슈팅/공격자/루즈볼 등)**
5. **블락(가능하면 슛 이벤트와 묶어 보조 표기)**
6. **타임아웃(팀 전략 흐름용)**
7. **쿼터 시작/종료**
8. **교체(Substitution)**

## 4.2 조건부 노출군 (잡음 방지용)
- 점프볼: 경기 시작, 연장 시작, 클러치 구간(예: 4Q 2:00 이내)만 노출
- 바이얼레이션(3초, 8초 등): 포제션 전환이 실제 발생한 경우만 노출
- 리뷰/챌린지류: 결과가 판정 변경 또는 득점/파울 취소를 만든 경우만 노출

## 4.3 비노출군 (원칙적으로 숨김)
- 내부 상태 갱신/확률 샘플링/애니메이션 트리거 성격 이벤트
- 동일 시점 중복 로그(동일 seq, 동일 의미)
- UI 문장화 이점이 없는 저수준 telemetry

---

## 5) 텍스트 생성 규칙(ESPN 스타일)

## 5.1 제목(Title)
- `made_2pt` -> `Made 2PT`
- `made_3pt` -> `Made 3PT`
- `miss_2pt|miss_3pt` -> `Missed FG`
- `free_throw_made` -> `+1 Point`
- `foul` -> `Foul`
- `turnover` -> `Turnover`
- `rebound` -> `Rebound`
- `substitution` -> `Substitution`
- `timeout` -> `Timeout`

## 5.2 설명(Description)
- 템플릿 예시:
  - 슛 성공: `{선수명} makes {거리/타입} {2|3}-pt shot ({어시스트선수명} assists)`
  - 슛 실패: `{선수명} misses {거리/타입} {2|3}-pt shot`
  - 자유투: `{선수명} makes/misses free throw {i} of {n}`
  - 턴오버: `{선수명} turnover ({사유}), {스틸선수명} steals`
  - 파울: `{선수명} shooting foul`
  - 리바운드: `{팀명} offensive/defensive team rebound` 또는 `{선수명} defensive rebound`
  - 교체: `Substitution: {in} in for {out}`

## 5.3 시간/정렬
- 정렬: `period ASC`, `clock DESC`, `seq ASC` (실제 경기 시계 흐름 유지)
- 표시: `11:18 - 1st` 형태로 렌더링
- 동일 clock 다중 이벤트는 `seq`로 안정 정렬

---

## 6) 클러스터링(이벤트 묶기) 규칙

replay의 세부 이벤트를 그대로 나열하면 너무 길어지므로 **포제션 단위 요약 묶음**을 지원한다.

예시 묶음:
- `슈팅 파울 -> 자유투 1/2 성공 -> 2/2 실패 -> 수비 리바운드`
  - 기본 화면에는 2~3줄로 묶어 표시
  - 펼치기(expand) 시 원본 단위 이벤트 노출

클러스터 키 제안:
- 동일 `period`
- `clock` 차이 <= 2초
- 같은 dead-ball window 내 이벤트
- 연속 free-throw sequence (`ft_index`, `ft_total`)는 하나의 묶음으로 처리

---

## 7) 강조/보조 정보 (선택)

ESPN처럼 로그마다 보조 정보 칩을 추가할 수 있다.
- `Win %` 변화량(이미 gamecast용 확률 시계열이 있으면 연결 가능)
- `Run` 정보(예: 8-0 run)
- `Lead change`, `Tie`
- `Clutch` 뱃지(4Q/OT 2:00 이내)

초기 버전에서는 아래만 우선 추천:
1. `Lead change`
2. `Tie game`
3. `End of period`

---

## 8) API 형태 제안

`GET /api/game/result/{game_id}?user_team_id=...` 응답에 섹션 추가:

```json
"play_by_play": {
  "available": true,
  "source": "replay_events",
  "items": [ ...pbp_items... ],
  "meta": {
    "total_replay_events": 612,
    "exposed_pbp_items": 178,
    "filtered_out": 434,
    "collapsed_groups": 29
  }
}
```

의도:
- 사용자에게 보여준 로그 수와 필터링 수를 분리해 디버깅 가능성 확보
- replay 품질 편차가 있어도 `available=false, items=[]`로 안전 폴백

---

## 9) 구현 순서(현실적인 단계)

1. **이벤트 사전(Event Dictionary) 정의**
   - replay `event_type/action` -> `event_key/title/template/tags`
2. **정규화 레이어 추가**
   - clock, period, seq, 팀/선수 ID 정리
3. **필터 레이어 추가**
   - 노출군/비노출군/조건부 노출군 적용
4. **클러스터 레이어 추가**
   - FT 시퀀스/동일 dead-ball window 묶기
5. **ViewModel 직렬화 + API 응답 연결**
6. **샘플 경기 20개 검수**
   - 경기당 로그 개수, 가독성, 오표기 체크

---

## 10) 품질 체크리스트

- [ ] 점수합 일치: 마지막 PBP `score` == `final` 점수
- [ ] 시간 역행 없음: 정렬 후 clock/seq 모순 없음
- [ ] 선수명 누락 시 안전 폴백(`Unknown Player #{id}`)
- [ ] team rebound/technical FT 등 예외 문장 템플릿 존재
- [ ] OT(5Q 이상) 표기 정상
- [ ] 필터 후 로그가 0개인 경기에서 빈 상태 UI 정상

---

## 결론
- 핵심은 **replay_events를 그대로 노출하지 않고, PBP 목적에 맞는 “의미 이벤트”로 재가공**하는 것이다.
- 첫 릴리스는 득점/실패/리바운드/파울/턴오버/타임아웃/교체/쿼터 경계 중심으로 시작하고,
  이후 리뷰/챌린지/고급 컨텍스트를 점진 확장하는 전략이 가장 안전하다.
