중복 항목을 합쳐서 다시 정리했다.
반복된 동일 이슈들(`default_offer_privacy`, 세션 재사용 시 top offer 덮어쓰기 등)은 1건으로 통합했고, **총 58건**이다.

## 협상 / 트레이드 / 마켓

1. **협상 시작 시 기본 공개설정이 실제 커밋에 반영되지 않음** — `default_offer_privacy`를 시작 시 저장하지만, 커밋 시에는 항상 `req.offer_privacy`와 `PRIVATE` 기본값만 사용해서 세션 기본값이 사실상 무력화된다.
2. **직렬화된 딜의 `legs` 구조 불일치로 grievance 로직 비활성화** — helper는 `legs`가 list일 때만 처리하는데, 실제 `serialize_deal` 결과는 team ID keyed dict라서 항상 빈 결과가 된다.
3. **공개화(publicized) idempotency 키가 너무 넓음** — session key 또는 deal key 중 하나만 publicized여도 이미 처리된 것으로 간주해서, 같은 세션의 이후 leaked deal 부작용(리스팅/이벤트/grievance)이 스킵된다.
6. **같은 상대 팀 쌍에 대해 더 낮은 점수의 제안이 더 좋은 제안을 덮어쓸 수 있음** — duplicate-opponent guard 제거로 한 tick 안에 같은 pair의 여러 proposal이 처리되면서, 나중 proposal이 `set_last_offer`로 top offer를 덮어쓴다.
7. **`ai_proactive_listing_player_cooldown_days=0` 설정이 실제로는 쿨다운 해제가 안 됨** — `or 7` 처리 때문에 0이 다시 7로 바뀌어 튜닝이 무효가 된다.
8. **listing-interest scoring을 끄면 PUBLIC 리스팅도 발견되지 않음** — `select_targets_buy`에서 `is_public_listing`이 `listing_meta_by_player`에만 의존해서, 해당 플래그가 꺼지면 PUBLIC 리스팅 예외도 함께 사라진다.
9. **음수 `min_surplus_required` 허용 시 confidence 계산이 과대평가될 수 있음** — 실제 reject 경계는 `accept_threshold`인데, `_compute_confidence()`는 여전히 `overpay_floor` 기준으로 거리를 재서 reject confidence가 부정확해진다.

## 전술 / 라인업 / 스카우팅 UI

10. **빈 `{}`로 defensive override를 지우려 해도 지워지지 않음** — `or` 체인 때문에 빈 map이 falsy로 처리되어 레거시 값이나 context 값이 다시 살아난다.
14. **비표준 division 또는 빈 division 팀이 팀 선택 UI에서 누락됨** — `renderTeams`가 사실상 `divisionOrder[conference]`만 순회해서 fallback이 죽어 있다.
16. **라인업 검증이 5+5 구조를 강제하지 않음** — 총 10명만 맞으면 통과해서, 10 starters / 0 rotation 같은 잘못된 플랜도 저장된다.

## 시뮬레이션 / 매치업 / 시즌 진행 흐름

20. **만료 계약 선수 재협상 버튼이 일반적인 오프시즌 흐름에서 실패함** — 선수는 이미 FA로 이동했는데 `mode: "RE_SIGN"`으로 협상을 시작해서 서비스에서 거절된다.
21. **이미 FA인 선수에게도 ‘방출’ 호출을 보내 에러가 남** — common path에서 `/api/contracts/release-to-fa`가 의미 없이 실패한다.

## 일정 / 대시보드 / 스탠딩 / 팀 상세

24. **`as_of_date`를 줘도 7일 일정 부하 수치가 현재 날짜 기준으로 계산됨** — 과거 시점 스냅샷과 현재 일정 창이 한 응답에 섞인다.
30. **스탠딩 API 실패 시 KPI가 ‘미확인’이 아니라 가짜 `0-0 / 0.0%`로 보임** — `normalizeRecord(null)` 결과가 그대로 UI에 들어간다.
31. **다가오는 경기가 없을 때 next-game 카드 일부가 영구 로딩처럼 보임** — early return으로 priority/quick-intel/timeline/feed가 loading placeholder 상태에 남는다.
33. **다가오는 경기가 없으면 KPI snapshot 자체를 렌더링하지 않음** — `refreshMainDashboard`의 `!nextGame` 분기에서 record/standing/health 값 갱신이 생략된다.

## 메디컬 / 부상 캘린더

34. **부상 이벤트 수가 과소집계될 수 있음** — 캘린더는 `top_n=30`을 요청하지만 실제 helper는 20으로 clamp해서 그 이상 이벤트가 잘린다.
35. **일자별 위험/OUT/복귀 카운트가 전 구간에서 고정됨** — `high_pids`, `out_pids`, `returning_pids`를 시작일 기준으로 한 번만 계산해 매일 재사용한다.

## 입력 검증 / 메타 API 계약

38. **잘못된 `date_iso` 입력이 400이 아니라 500으로 처리됨** — practice preview에서 `require_date_iso` 예외를 잡지 않아 클라이언트 입력 실수가 서버 에러가 된다.

## 가치평가 / 드래프트 / 픽 / 딜 스코어링

41. **`_safe_team_signal`이 유효한 `0.0` 신호를 missing으로 취급함** — `... or default` 때문에 낮은 flexibility 팀이 잘못 보정된다.


기준 표기:

* **문제** = 존재 / Needs Fix / 유효
* **보류** = 판단보류 / Needs Repro / Inconclusive
* **누락** = 해당 보고서에서 명시 판정 없음

---

## 1) 4가지 보고서 전부 문제라고 본 사항

이건 4개 보고서가 모두 같은 방향으로 본 항목들이야.

1. **#1** 기본 공개설정 `default_offer_privacy`가 commit 시 반영되지 않음
2. **#2** grievance helper의 `legs` 구조 가정이 현재 `serialize_deal` 구조와 불일치
3. **#3** publicized 판정이 session/deal 중 하나만 true여도 true 처리됨
4. **#6** 같은 상대 팀 쌍에서 낮은 점수 제안이 top offer를 덮어쓸 수 있음
5. **#7** `ai_proactive_listing_player_cooldown_days=0`이 `or 7` 때문에 무력화됨
6. **#8** listing-interest 비활성화 시 PUBLIC listing 탐지 경로가 사라짐
7. **#14** 팀 선택 UI가 고정 `divisionOrder` 기준이라 비표준 division 누락 가능
8. **#16** tactics 저장 변환에서 **5+5 강제 검증 부재**
9. **#20** 만료 계약자 액션에서 여전히 `mode: "RE_SIGN"` 호출 경로 존재
10. **#21** 같은 경로에서 `/api/contracts/release-to-fa` 호출 유지
11. **#24** `as_of_date`와 7일 일정 부하 계산 기준일이 혼재됨
12. **#33** `!nextGame` 분기에서 KPI snapshot 갱신 누락
13. **#34** medical overview의 `top_n`이 **20으로 clamp**됨
14. **#35** risk / OUT / 복귀 집합이 일자별이 아니라 시작일 기준으로 재사용됨
15. **#38** practice preview의 `require_date_iso` 예외 매핑
16. **#41** `_safe_team_signal(... or default)` 때문에 유효한 `0.0`이 치환될 수 있음

---

## 2) 보고서별로 의견이 갈리는 사항

여기에는 요청대로 **#9, #10, #30, #31만** 남겼어.

1. **#9** `_compute_confidence()` REJECT 경계 / `overpay_floor`
2. **#10** defensive override 로딩의 `or` 체인 때문에 빈 `{}` 초기화 의도 미반영
3. **#30** standings 실패 시 KPI fallback 표기
4. **#31** no next-game 시 placeholder 잔존

---

## 한눈에 보는 요약

### 공통으로 “문제”라고 본 항목

**#1, #2, #3, #6, #7, #8, #14, #16, #20, #21, #24, #33, #34, #35, #38, #41**

### 의견이 갈린 항목

**#9, #10, #30, #31**

---

원하면 다음 답변에서 이걸 바로 이어서
**“지금 당장 직접 코드 재검증할 우선순위 목록”** 형태로 다시 압축해서 정리해줄게.
