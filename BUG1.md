중복 항목을 합쳐서 다시 정리했다.
반복된 동일 이슈들(`default_offer_privacy`, `select_targets_sell`, 세션 재사용 시 top offer 덮어쓰기 등)은 1건으로 통합했고, **총 58건**이다.

## 협상 / 트레이드 / 마켓

1. **협상 시작 시 기본 공개설정이 실제 커밋에 반영되지 않음** — `default_offer_privacy`를 시작 시 저장하지만, 커밋 시에는 항상 `req.offer_privacy`와 `PRIVATE` 기본값만 사용해서 세션 기본값이 사실상 무력화된다.
2. **직렬화된 딜의 `legs` 구조 불일치로 grievance 로직 비활성화** — helper는 `legs`가 list일 때만 처리하는데, 실제 `serialize_deal` 결과는 team ID keyed dict라서 항상 빈 결과가 된다.
3. **공개화(publicized) idempotency 키가 너무 넓음** — session key 또는 deal key 중 하나만 publicized여도 이미 처리된 것으로 간주해서, 같은 세션의 이후 leaked deal 부작용(리스팅/이벤트/grievance)이 스킵된다.
4. **공개 요청 × 리스팅 결합 가중치가 잘못 발동될 수 있음** — `min(len(listings), team_public_request_count)` 기준 때문에, 요청 대상과 무관한 리스팅에도 multiplier가 적용될 수 있다.
5. **PUBLIC 리스팅인데 priority=0이면 미리스팅으로 오인** — `select_targets_sell`이 `listed_pri > 0.0`으로만 판단해서, 유효한 PUBLIC 리스팅이 시장 신호 경로에서 사라진다.
6. **같은 상대 팀 쌍에 대해 더 낮은 점수의 제안이 더 좋은 제안을 덮어쓸 수 있음** — duplicate-opponent guard 제거로 한 tick 안에 같은 pair의 여러 proposal이 처리되면서, 나중 proposal이 `set_last_offer`로 top offer를 덮어쓴다.
7. **`ai_proactive_listing_player_cooldown_days=0` 설정이 실제로는 쿨다운 해제가 안 됨** — `or 7` 처리 때문에 0이 다시 7로 바뀌어 튜닝이 무효가 된다.
8. **listing-interest scoring을 끄면 PUBLIC 리스팅도 발견되지 않음** — `select_targets_buy`에서 `is_public_listing`이 `listing_meta_by_player`에만 의존해서, 해당 플래그가 꺼지면 PUBLIC 리스팅 예외도 함께 사라진다.
9. **음수 `min_surplus_required` 허용 시 confidence 계산이 과대평가될 수 있음** — 실제 reject 경계는 `accept_threshold`인데, `_compute_confidence()`는 여전히 `overpay_floor` 기준으로 거리를 재서 reject confidence가 부정확해진다.

## 전술 / 라인업 / 스카우팅 UI

10. **빈 `{}`로 defensive override를 지우려 해도 지워지지 않음** — `or` 체인 때문에 빈 map이 falsy로 처리되어 레거시 값이나 context 값이 다시 살아난다.
11. **수비 역할 중복 검사 범위가 너무 넓어 편집이 사실상 불가능함** — starters와 rotation을 하나의 풀로 검사해서, 10개 행에서 5개 역할만 돌려 쓰는 현재 UI 구조와 충돌한다.
12. **팀/세이브 변경 후에도 이전 전술 draft가 남음** — `showTacticsScreen`이 `state.tacticsDraft`를 한 번만 초기화해서, 이전 팀의 player ID와 minutes가 새 팀 화면에 섞인다.
13. **스카우팅 assign/unassign 실패가 사용자에게 보이지 않음** — async click flow가 `fetchJson` 실패를 잡지 않아, 409 같은 일반적인 API 거절이 “버튼 눌러도 아무 일도 없음”처럼 보인다.
14. **비표준 division 또는 빈 division 팀이 팀 선택 UI에서 누락됨** — `renderTeams`가 사실상 `divisionOrder[conference]`만 순회해서 fallback이 죽어 있다.
15. **전술 플랜의 active 상태가 DB가 아니라 오래된 payload를 따라감** — `_plan_from_row`가 `payload_json.is_active`를 유지해서, 이미 비활성화된 플랜이 여전히 active처럼 반환될 수 있다.
16. **라인업 검증이 5+5 구조를 강제하지 않음** — 총 10명만 맞으면 통과해서, 10 starters / 0 rotation 같은 잘못된 플랜도 저장된다.
17. **자동 분배가 소수점 분 단위 입력을 깨뜨림** — `autoBalanceTacticsMinutes`가 정수 단위로만 diff를 줄여서 총합 240.0을 정확히 맞추지 못한다.

## 시뮬레이션 / 매치업 / 시즌 진행 흐름

18. **임시 매치업 락이 시그니처에 반영되지 않아 DENY hunt가 적용되지 않음** — `_matchups_instr_signature()`에서 `matchups_temp_locks`가 빠져 cache hit로 오인된다.
19. **플레이인 진행 중인데 플레이오프 자동 진행 API를 먼저 호출함** — playoffs가 아직 초기화되지 않은 상태에서 `auto-advance-round`를 호출해 DEV 흐름이 중단될 수 있다.
20. **만료 계약 선수 재협상 버튼이 일반적인 오프시즌 흐름에서 실패함** — 선수는 이미 FA로 이동했는데 `mode: "RE_SIGN"`으로 협상을 시작해서 서비스에서 거절된다.
21. **이미 FA인 선수에게도 ‘방출’ 호출을 보내 에러가 남** — common path에서 `/api/contracts/release-to-fa`가 의미 없이 실패한다.

## 일정 / 대시보드 / 스탠딩 / 팀 상세

22. **팀 일정 API가 상태 스냅샷을 두 번 떠서 성능이 나빠짐** — `/api/team-schedule/{team_id}` 한 번에 전체 state deep-copy가 두 번 일어난다.
23. **무패 팀 승률 표기가 `.000`으로 깨짐** — `f"{win_pct:.3f}"[1:]` 때문에 `1.000`의 첫 글자도 잘려 나간다.
24. **`as_of_date`를 줘도 7일 일정 부하 수치가 현재 날짜 기준으로 계산됨** — 과거 시점 스냅샷과 현재 일정 창이 한 응답에 섞인다.
25. **존재하지 않는 team ID가 404가 아니라 500으로 터짐** — `get_team_detail()`의 `ValueError`를 `HTTPException`으로 변환하지 않는다.
26. **Home 캐시가 시뮬레이션 직후에도 이전 상태를 줄 수 있음** — 캐시 키가 팀/쿼리 옵션만 보고 state version이나 current date를 보지 않는다.
27. **prediction payload가 비어 있는데도 `available=true`로 내려감** — `user_win_prob`와 `model`이 모두 `None`인데 UI는 예측 블록을 렌더링할 수 있다.
28. **일정 로드 실패 후에도 이전 팀의 schedule 데이터가 남음** — `state.scheduleGames`가 성공 시에만 갱신되어, 실패 직후 필터 조작 시 stale 데이터가 다시 렌더링된다.
29. **다음 경기 카운트다운이 실제 시뮬레이션 날짜가 아니라 브라우저 현재 시각 기준임** — 과거/미래 시즌 세이브에서 긴급도 표시가 틀어진다.
30. **스탠딩 API 실패 시 KPI가 ‘미확인’이 아니라 가짜 `0-0 / 0.0%`로 보임** — `normalizeRecord(null)` 결과가 그대로 UI에 들어간다.
31. **다가오는 경기가 없을 때 next-game 카드 일부가 영구 로딩처럼 보임** — early return으로 priority/quick-intel/timeline/feed가 loading placeholder 상태에 남는다.
32. **부상 이벤트 피드 제목이 선수명을 못 읽고 `Unknown`으로 떨어짐** — 이벤트는 `name`을 쓰는데 렌더러는 `player_name`을 찾는다.
33. **다가오는 경기가 없으면 KPI snapshot 자체를 렌더링하지 않음** — `refreshMainDashboard`의 `!nextGame` 분기에서 record/standing/health 값 갱신이 생략된다.

## 메디컬 / 부상 캘린더

34. **부상 이벤트 수가 과소집계될 수 있음** — 캘린더는 `top_n=30`을 요청하지만 실제 helper는 20으로 clamp해서 그 이상 이벤트가 잘린다.
35. **일자별 위험/OUT/복귀 카운트가 전 구간에서 고정됨** — `high_pids`, `out_pids`, `returning_pids`를 시작일 기준으로 한 번만 계산해 매일 재사용한다.
36. **`unavailable_count`가 상위 N명만 기준이라 건강 위험도가 낮게 계산됨** — watchlist truncation 때문에 부상자가 많은 팀이 과소평가된다.
37. **메디컬 fallback 경고창이 OUT/RETURNING 날짜를 잃어버림** — `injury_state`에 있는 날짜를 `injury_current`에서 읽으려 해서 항상 `-`로 보일 수 있다.

## 입력 검증 / 메타 API 계약

38. **잘못된 `date_iso` 입력이 400이 아니라 500으로 처리됨** — practice preview에서 `require_date_iso` 예외를 잡지 않아 클라이언트 입력 실수가 서버 에러가 된다.
39. **속성 그룹 API와 선수 상세 API의 attr key 체계가 서로 맞지 않음** — `/api/meta/attribute-groups`는 `DRIVING_LAYUP`식이고 `/api/player-detail`은 `Layup`, `Close Shot` 식이라 클라이언트 매핑이 실패한다.

## 가치평가 / 드래프트 / 픽 / 딜 스코어링

40. **`SURPLUS_EXPENDABLE` fallback이 일부 레거시 override를 무시할 수 있음** — 하나만 override된 경우에도 `default_threshold`가 더 크면 override가 사라진다.
41. **`_safe_team_signal`이 유효한 `0.0` 신호를 missing으로 취급함** — `... or default` 때문에 낮은 flexibility 팀이 잘못 보정된다.
42. **잘못된 lottery key가 validation 결과가 아니라 예외 크래시로 이어짐** — `DraftLotteryRules.validate()`가 `int(k)`를 try/except 없이 수행한다.
43. **시즌별 기본 lottery rule이 같은 nested PMF dict를 공유함** — 한 시즌 수정이 같은 era의 다른 시즌에도 전염된다.
44. **ledger 구성 대상 팀 집합이 불완전함** — `posture_by_team`에만 있는 팀은 누락되어 coverage diagnostic과 cap telemetry가 깨질 수 있다.
45. **dual-read v1/v2 비교가 실제 비교가 아님** — `collect_v1_v2_diff`에 항상 `v1_metrics={}`를 넣어 delta가 사실상 v2 raw 값이 된다.
46. **같은 소유자끼리의 swap 기록에서 두 픽 모두 더 좋은 슬롯을 받는 것으로 계산됨** — 실제로는 no-op여야 하는 same-owner/stale swap을 과대평가한다.
47. **config의 `defense_role_groups`가 문자열이면 글자 단위 role로 분해됨** — `"PnR_POA_Defender"`가 `["P","n","R",...]`처럼 처리되어 fit 계산이 무너진다.
48. **사용자 제공 `pick_expectations` override가 무시될 수 있음** — standings가 있으면 EV 기반 값이 먼저 채워져 caller override가 적용되지 않는다.
49. **지원되지 않는 분포의 `ev_pick=0.0`을 오히려 최고급 픽처럼 가격 매김** — missing-data case가 neutral fallback이 아니라 premium으로 변질된다.
50. **swap 행사 확률이 분포 부재 상황에서도 무조건 1.0임** — fallback 경로에서 근거 없는 과대가치가 반영된다.
51. **tick-context fast path에서 `order` 미정의로 v2 context 빌드가 깨질 수 있음** — 어떤 경로에서는 조용히 비활성화되고, 어떤 경로에서는 딜 평가 자체가 실패해 proposal이 드롭된다.
52. **`use_valuation_context_v2=false`여도 v2 package effect가 계속 적용됨** — feature flag rollback 의미가 깨진다.
53. **`before_ledgers` 기준선이 실제 팀 상태가 아니라 outgoing package만 반영함** — CAP_FLEX delta 방향이 반대로 뒤집힐 수 있다.
54. **fit-swap 게이트가 여전히 구식 threshold를 사용함** — 이제는 ecosystem total score를 비교하는데도 예전 `0.03` 기준을 그대로 써 후보가 과도하게 걸러진다.
55. **`_team_supply`가 누락 태그를 분모에서 빼서 공급 충족도를 과대평가함** — 한 명의 specialist만 있어도 team supply가 충분한 것처럼 보일 수 있다.
56. **좋은 계약도 toxic 계약처럼 취급될 수 있음** — `contract_gap_cap_share`에 `abs(...)`를 씌워 underpaid/overpaid를 구분하지 못한다.
57. **previous-tier 안정화 캐시가 사실상 거의 hit되지 않음** — key가 너무 세분화되어 `prev_tier`가 대부분 비어 있게 된다.
58. **동점 tie-break가 hysteresis 이후 결정을 다시 뒤집을 수 있음** — 경계값 부근에서 의도한 tier 안정화가 보장되지 않는다.


기준 표기:

* **문제** = 존재 / Needs Fix / 유효
* **문제 아님** = 해결됨 / 해소 / 무효
* **보류** = 판단보류 / Needs Repro / Inconclusive
* **누락** = 해당 보고서에서 명시 판정 없음

---

## 1) 4가지 보고서 전부 문제라고 본 사항

이건 4개 보고서가 모두 같은 방향으로 본 항목들이야.

1. **#1** 기본 공개설정 `default_offer_privacy`가 commit 시 반영되지 않음
2. **#2** grievance helper의 `legs` 구조 가정이 현재 `serialize_deal` 구조와 불일치
3. **#3** publicized 판정이 session/deal 중 하나만 true여도 true 처리됨
4. **#7** `ai_proactive_listing_player_cooldown_days=0`이 `or 7` 때문에 무력화됨
5. **#8** listing-interest 비활성화 시 PUBLIC listing 탐지 경로가 사라짐
6. **#14** 팀 선택 UI가 고정 `divisionOrder` 기준이라 비표준 division 누락 가능
7. **#16** tactics 저장 변환에서 **5+5 강제 검증 부재**
8. **#34** medical overview의 `top_n`이 **20으로 clamp**됨

---

## 2) 4가지 보고서 전부 문제가 아니라고 본 사항

(= 실질적 4개 의견축이 모두 해결/해소/무효라고 본 항목)

1. **#18** `_matchups_instr_signature()`에 `matchups_temp_locks` 반영됨
2. **#23** 승률 포맷 관련 기존 절단 버그는 현재 코드에서 성립하지 않음
3. **#25** 존재하지 않는 team ID 처리 시 500이 아니라 404 변환됨
4. **#32** injury feed title이 `player_name` / `name` 모두 허용됨

---

## 3) 보고서별로 의견이 갈리는 사항

여기부터는 전부 “합의 없음”이야.
다만 성격이 달라서 4개 하위 그룹으로 나눠볼게.

---

### 3-1) **문제 쪽으로 기울지만 완전 합의는 아닌 항목**

이 항목들은 일부 보고서는 “문제”, 일부는 “보류”로 봤어.

* **#9** `_compute_confidence()` REJECT 경계 / `overpay_floor`

  * 1: 문제 / 2: 보류 / 3: 문제 / 4: 문제

* **#10** defensive override 로딩의 `or` 체인 때문에 빈 `{}` 초기화 의도 미반영

  * 1: 문제 / 2: 보류 / 3: 보류 / 4: 문제

* **#20** 만료 계약자 액션에서 여전히 `mode: "RE_SIGN"` 호출 경로 존재

  * 1: 문제 / 2: 보류 / 3: 문제 / 4: 문제

* **#21** 같은 경로에서 `/api/contracts/release-to-fa` 호출 유지

  * 1: 문제 / 2: 보류 / 3: 문제 / 4: 문제

* **#33** `!nextGame` 분기에서 KPI snapshot 갱신 누락

  * 1: 보류 / 2: 문제 / 3: 문제 / 4: 보류

* **#35** risk / OUT / 복귀 집합을 일자별이 아니라 시작일 기준으로 재사용

  * 1: 보류 / 2: 문제 / 3: 문제 / 4: 보류

* **#41** `_safe_team_signal(... or default)` 때문에 유효한 `0.0`이 default로 치환될 수 있음

  * 1: 문제 / 2: 문제 / 3: 보류 / 4: 문제

---

### 3-2) **해결 쪽으로 기울지만 완전 합의는 아닌 항목**

이 항목들은 일부 보고서는 “해결”, 일부는 “보류” 또는 “누락”으로 봤어.

* **#5** `select_targets_sell`의 `listed_pri > 0` 관련 지적

  * 1: 해결 / 2: 보류 / 3: 해결 / 4: 해결

* **#12** `showTacticsScreen → applyTacticsDetail` 진입 시 draft 재설정

  * 1: 해결 / 2: 보류 / 3: 해결 / 4: 해결

* **#13** scouting assign/unassign 실패 피드백

  * 1: 해결 / 2: **누락** / 3: 해결 / 4: 해결

* **#17** `autoBalanceTacticsMinutes` 관련 지적

  * 1: 보류 / 2: 보류 / 3: 해결 / 4: 보류

* **#19** DEV 플레이인/플레이오프 호출 순서 문제

  * 1: 보류 / 2: 보류 / 3: 해결 / 4: 보류

* **#22** `/api/team-schedule` deep-copy 성능 이슈

  * 1: 해결 / 2: 해결 / 3: 보류 / 4: 해결

* **#28** schedule load 실패 후 stale state 재사용

  * 1: 보류 / 2: 해결 / 3: 해결 / 4: 보류

* **#30** standings 실패 시 KPI fallback 표기

  * 1: 보류 / 2: 보류 / 3: 해결 / 4: 보류

* **#31** no next-game 시 placeholder 잔존

  * 1: 보류 / 2: 보류 / 3: 해결 / 4: 보류

* **#36** `unavailable_count`가 top_n truncation 영향을 받는지

  * 1: 보류 / 2: 보류 / 3: 해결 / 4: 보류

* **#37** fallback 데이터에 `injury_state` out/returning 날짜 포함

  * 1: 해결 / 2: 보류 / 3: 해결 / 4: 해결

* **#38** practice preview의 `require_date_iso` 예외 매핑

  * 1: 보류 / 2: 보류 / 3: 해결 / 4: 보류

* **#42** `DraftLotteryRules.validate()` 관련 지적

  * 1: 보류 / 2: 보류 / 3: 해결 / 4: 보류

* **#47** `defense_role_groups` 설정 경로

  * 1: 보류 / 2: 보류 / 3: 해결 / 4: 보류

* **#48** `pick_expectations` caller override 우선순위

  * 1: 보류 / 2: 보류 / 3: 해결 / 4: 보류

---

### 3-3) **보고서끼리 직접 충돌하는 항목**

이건 “보류”가 아니라 실제로 **문제 vs 해결**이 정면으로 갈린 항목이야.

* **#11** 중복 검사 대상 범위

  * 1: 해결
  * 2: **누락**
  * 3: 문제
  * 4: 해결

즉, 이 항목은 보고서 간 해석 차이가 가장 크고, 실제 코드 확인이 가장 필요해 보여.

---

### 3-4) **사실상 전부 보류인 항목**

이 항목들은 4개 보고서가 모두 “보류/재현 필요” 쪽이어서, 아직 결론을 못 낸 항목들이야.

* **#4** 가중치 결합 로직 의도 대비 오동작 여부
* **#6** 같은 pair overwrite 여부
* **#15** `_plan_from_row` 경로 관련
* **#24** `as_of_date`와 7일 부하 계산 기준일 혼재
* **#26** Home 캐시 stale 가능성
* **#27** prediction `available=true` 조건식
* **#29** next-game countdown 로직
* **#39** attribute key 체계 불일치
* **#40** `SURPLUS_EXPENDABLE` fallback/override 우선순위
* **#43** lottery nested PMF 공유 여부
* **#44** ledger 대상 팀 집합 누락
* **#45** v1/v2 diff 비교 데이터 주입
* **#46** same-owner swap 과대평가 여부
* **#49** unsupported distribution fallback에서 `ev_pick=0.0` premium 취급
* **#50** swap exercise prob fallback = 1.0 여부
* **#51** tick-context fast path `order` 미정의
* **#52** `use_valuation_context_v2=false` 시 package effect 적용 잔존 여부
* **#53** `before_ledgers` 기준선 구성 로직
* **#54** fit-swap gate threshold 구식값 사용 여부
* **#55** `_team_supply` 분모 처리
* **#56** `contract_gap_cap_share abs` 적용 여부
* **#57** prev-tier cache hit율 이슈
* **#58** hysteresis 이후 tie-break 역전 여부

---

## 한눈에 보는 요약

### 공통으로 “문제”라고 본 항목

**#1, #2, #3, #7, #8, #14, #16, #34**

### 공통으로 “문제 아님”이라고 본 항목

**#18, #23, #25, #32**

### 의견이 갈린 항목

**#4, #5, #6, #9, #10, #11, #12, #13, #15, #17, #19, #20, #21, #22, #24, #26, #27, #28, #29, #30, #31, #33, #35, #36, #37, #38, #39, #40, #41, #42, #43, #44, #45, #46, #47, #48, #49, #50, #51, #52, #53, #54, #55, #56, #57, #58**

---

원하면 다음 답변에서 이걸 바로 이어서
**“지금 당장 직접 코드 재검증할 우선순위 목록”** 형태로 다시 압축해서 정리해줄게.

