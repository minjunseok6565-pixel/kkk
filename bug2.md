중복·유사 내용을 합쳐서, **현재 기준으로 실제 대응이 필요한 항목만** 남긴 통합 정리본입니다.  
(세 보고서에서 공통으로 “지금은 문제 아님”으로 합의된 7건은 제거했고, 남은 27건을 재번호화했습니다.)

---

## 재정리본 (총 27건)

### 드래프트 / 오프시즌 / 계약

**1. 드래프트 번들에서 컴바인 데이터 추출 경로가 잘못됨**  
`extractCombineRowsFromBundle`가 `p.meta.combine`를 읽고 있는데, 실제 `/api/offseason/draft/bundle` 응답의 combine은 `pool.prospects[].combine` 상위 필드에 있다. 그 결과 Top-10 카드와 상세의 combine 정보가 빈 값으로 보인다.  
→ **쉽게 설명하면:** 서버는 정상 데이터를 줬는데, 프론트가 다른 위치를 읽어서 “데이터가 없는 것처럼” 잘못 표시하는 문제다.

**2. 만료 계약 선수의 “방출” 버튼이 구조적으로 실패함**  
오프시즌 단계에서 이미 만료 계약 선수를 FA로 옮긴 뒤, UI가 다시 `/api/contracts/release-to-fa`를 호출한다. 이때 `from_team == "FA"`인 선수는 release 로직에서 에러가 나므로 방출 버튼이 반복 실패한다.  
→ **쉽게 설명하면:** 이미 방출된 선수를 또 방출하려 해서 실패하는 “중복 처리” 문제다.

**3. 워크아웃 추가 진행 플로우가 빈 후보 상태에서 막힘**  
`round < workoutMaxRounds` 조건만으로 `워크아웃 추가 진행`이 열려, 실제 초청 가능 선수가 0명이어도 `WORKOUT_INVITE_SELECT`로 진입한다. 이후 “추가 초청 가능한 선수가 없습니다”만 뜨고 진행 버튼은 비활성화된다.  
→ **쉽게 설명하면:** “다음 단계로 갈 수 있는 버튼”은 켜져 있는데, 들어가 보면 실제로 할 수 있는 작업이 없어 진행이 멈춘다.

**4. standalone 로스터 import CLI 경로가 깨짐**  
`_state_season_year_ssot()`가 season_year 미존재 시 예외를 던지는 구조로 바뀌면서, `import_roster_excel()` standalone 실행(특히 `Salary == "--"` 보정 경로)에서 `RuntimeError`가 난다.  
→ **쉽게 설명하면:** 예전엔 단독 import가 됐는데, 지금은 “리그 상태가 먼저 준비돼야만” 돌아서 독립 실행성이 깨진 상태다.

---

### 전술 / 훈련 / 프론트 UI

**5. Player Training 화면에서 선수 변경 직후 잘못된 선수 플랜 저장 가능**  
선수 전환 직후 저장 버튼 비활성/초기화 처리가 부족해, 이전 선수의 dirty draft가 남은 채 새 선수 ID로 저장될 수 있다. 또한 `selectPlayerForTraining` 비동기 응답 도착 순서가 역전되면, 느린 이전 응답이 현재 선택 선수 UI를 덮어쓸 수 있다.  
→ **쉽게 설명하면:** 화면에는 B 선수를 보고 있는데, 내부 임시데이터는 A 선수 기준으로 남아 “다른 선수에게 잘못 저장”될 수 있는 상태다.

**6. 숫자 정렬 CSS가 Trade Block 테이블까지 오염됨**  
숫자 컬럼 우측 정렬 selector가 `.market-fa-table` 전체에 걸려 있는데, Trade Block 테이블도 같은 클래스를 공유한다. 그 결과 숫자가 아닌 포지션/액션 열까지 우측 정렬된다.  
→ **쉽게 설명하면:** 한 화면 스타일을 고치다가 같은 클래스를 쓰는 다른 표까지 같이 깨진 “범위 누수”다.

---

### 트레이드 / 협상 / 오퍼 프로모션

**7. ACTIVE 협상 세션 수 제한이 사실상 사라짐**  
active session 수를 집계/캐시하더라도 실제 생성 경로에서 hard cap을 강제하지 않아, 특정 tick에서 ACTIVE 세션이 과도하게 늘어날 수 있다. 이는 lock/state 증가와 commit 경로 부담으로 이어진다.  
→ **쉽게 설명하면:** 동시 협상 개수 제한판이 달려 있는데 실제로는 제동이 안 걸려 한꺼번에 폭증할 수 있다.

**8. REJECT 오퍼가 과도하게 `LOWBALL`로 승격됨**  
`exceed_overpay > probe_max` 조건에서 REJECT 제안이 광범위하게 LOWBALL로 넘어가면서, 원래의 상한 억제/완충 경로가 우회될 수 있다.  
→ **쉽게 설명하면:** “거절로 끝내야 할 제안”이 오히려 “공격적 재협상 대상”으로 잘못 분류되는 문제다.

**9. `overpay_allowed` vs `probe_max` 스케일 불일치로 톤 오분류**  
`DecisionPolicy.decide`는 multi-axis 기반 허용치를 쓰는데, reject tone 판단은 `max(outgoing_total, 6.0)` 중심 `probe_max`를 사용한다. 픽/스왑 비중 딜에서 경계값 주변 오분류가 생긴다.  
→ **쉽게 설명하면:** 같은 제안을 두 계산기가 서로 다른 자로 재서, 한쪽은 “탐색” 한쪽은 “저가 공세”로 다르게 판정하는 상태다.

**10. 세션 재사용 시 `created_at` 미갱신으로 즉시 만료 가능**  
promotion 흐름에서 기존 세션을 재사용할 때 offer payload는 바꾸지만 `created_at`이 유지되어, 다음 open 시 age hard-cap에 곧바로 걸릴 수 있다.  
→ **쉽게 설명하면:** 새 제안으로 갱신했는데 주민등록상 생일은 옛날 그대로라 “오래된 세션”으로 즉시 퇴장되는 셈이다.

---

### 트레이드 생성 로직 / Skeleton / Template / Sweetener

**11. `buy_target_basketball_norm_mode="FIXED"`가 실제로 무시됨**  
설정상 FIXED/PERCENTILE/HYBRID를 지원하지만, norm context 구성에서 비인식 값이 사실상 PERCENTILE로 귀결되어 FIXED 의도가 반영되지 않는다.  
→ **쉽게 설명하면:** 설정 파일에서 FIXED를 골라도 엔진은 거의 항상 PERCENTILE 방식으로 계산한다.

**12. `PackageEffectsConfig`가 legacy constructor 필드를 받지 않아 호환성 런타임 실패**  
기존에 전달되던 필드(`depth_need_scale`, `cap_flex_scale`, `cap_room_weight_base`, `upgrade_scale` 등) 제거로, 구버전 rule key를 넘기는 환경에서 `TypeError`가 난다.  
→ **쉽게 설명하면:** 예전 옵션명을 아직 쓰는 환경은 “점수가 조금 달라지는” 수준이 아니라 객체 생성부터 터진다.

**13. 아웃고잉 후보 빌더가 실제 불가 선수까지 후보에 포함**  
후보 수집이 `out_cat.players` 전체 순회에 치우쳐 lock/recent-signing 등 거래 금지 조건이 충분히 걸러지지 않으면, 어차피 실패할 skeleton을 대량 생성한다.  
→ **쉽게 설명하면:** 처음부터 팔 수 없는 선수를 장바구니에 넣어 놓고, 나중 단계에서 계속 반려당해 시간만 쓰는 구조다.

**14. greedy fill이 고가치 자산을 먼저 소모해 저·중티어 딜 효율 저하**  
높은 점수순 선집중 후 채우는 방식 때문에, 더 싼 조합으로 성립 가능한 케이스에서도 상위 자산이 먼저 소모될 수 있다.  
→ **쉽게 설명하면:** 작은 물건 사는데 고액권부터 깨는 방식이라 전체 거래 효율이 떨어진다.



**16. `skeleton_overhaul_enabled` 플래그가 실질적으로 무시됨**  
호출 경로가 v3 registry를 기본으로 타면서 `False` 설정으로 legacy 동작을 강제하려는 의도가 반영되지 않는 구간이 있다.  
→ **쉽게 설명하면:** 설정 스위치를 꺼도 내부 배선이 이미 새 회로에 고정돼 있어 이전 방식으로 못 돌아간다.

**17. `template_only`에서 빈 route가 “비활성”이 아니라 “전체 허용”으로 해석됨**  
`skeleton_route_template_*`가 빈 튜플일 때 downstream이 제약 없음으로 간주하면, 특정 tier를 끄려던 의도와 반대로 전체 template가 열릴 수 있다.  
→ **쉽게 설명하면:** “아무 것도 실행하지 마”가 아니라 “아무 제한 없이 다 실행”으로 읽히는 의미 역전이다.

**18. template wrapper의 `expected_tag` 하드코딩으로 유효 후보 전량 드롭 가능**  
`tpl_<tier>_placeholder_<n>` 형태를 강제해, planner가 tag naming을 조금만 바꿔도 실질적으로 정상 후보가 필터에서 사라질 수 있다.  
→ **쉽게 설명하면:** 내용은 맞는데 라벨 이름 철자 규칙이 다르다는 이유로 전부 버려지는 상황이다.



**20. pick protection 변형 생성이 sweetener ON/OFF에 종속됨**  
`maybe_apply_pick_protection_variants`가 sweetener 흐름 내부에 묶여, sweetener 비활성/추가치 0일 때 protection도 통째로 비활성화된다.  
→ **쉽게 설명하면:** 원래 별개 옵션이었는데, 지금은 한 스위치를 끄면 다른 기능도 같이 꺼지는 결합 문제가 생겼다.

**21. 보호 변형 평가에서 `opponent_repeat_count=0` 하드코딩**  
기본 proposal 평가에는 repeat-partner 패널티가 적용되는데, protection 재평가에서 0 고정이면 반복 거래 억제 신호가 사라진다.  
→ **쉽게 설명하면:** 본심사에는 감점 규칙이 있는데 재심사 단계에서 그 규칙을 빼버려 점수가 왜곡된다.

**22. `repair_until_valid_many` 이후 repaired variant 루프의 예산/하드캡 재확인 누락**  
한 base에서 복수 repaired 후보가 생겨도 loop 내부에서 `_budget_or_hard_cap_reached`를 즉시 재체크하지 않으면, 제한 도달 후에도 불필요한 평가/검증이 추가 실행된다.  
→ **쉽게 설명하면:** “예산 끝” 경고등이 켜졌는데 다음 작업칸으로 그냥 진행해 추가 비용을 쓰는 구조다.

---

### 자산 / API / Trade Lab

**23. “tradable assets” API가 실제 거래 불가 픽을 포함해 반환할 수 있음**  
owner/round 중심 필터나 상태 맵 직접 노출 방식은 consumed/과거/`trade_locked`/DB lock 픽을 완전히 배제하지 못해, 프론트 선택 후 evaluate 단계에서 확정 반려가 발생한다.  
→ **쉽게 설명하면:** 목록에서는 선택 가능해 보이는데 제출 순간 “원래 못 쓰는 자산”이라 거절되는 불일치다.

**24. `renderAssetList`의 `innerHTML` 직접 조합으로 stored XSS 위험**  
서버 문자열(player/pick label)을 escape 없이 HTML에 삽입하면 저장형 악성 문자열이 그대로 실행될 수 있다. Trade Lab 렌더 경로는 `escapeHtml` 적용이 필요하다.  
→ **쉽게 설명하면:** 데이터로 저장된 문자열이 화면에 그려질 때 코드로 실행될 수 있는 보안 취약점이다.

---

### 티어링 / 평가 / 성능

**25. bubble bonus가 실제 경쟁권 밖 팀에도 적용될 수 있음**  
`gb_to_6th`, `gb_to_10th`를 거리값으로 쓰면서 상한 조건만 검사하면, 안전권 상위팀도 보너스를 받는 케이스가 생긴다.  
→ **쉽게 설명하면:** 플레이오프 막차 경쟁팀에게 주려던 가산점이 이미 넉넉한 팀에도 잘못 붙는 문제다.

**26. tier 분류가 league-rank 전체 스냅샷 경로를 자주 타서 hot path 성능 저하**  
`_classify_tier_by_league_rank`가 호출 단위로 리그 전체 신호 계산을 동반하면, 단일 팀 평가 요청도 전체 리그 계산으로 확장되어 지연이 커진다.  
→ **쉽게 설명하면:** 한 팀만 물어봤는데 매번 30팀 전체를 다시 채점하는 식이라 느려진다.

**27. soft-boundary 확률 계산이 작은 `tau`에서 overflow 위험**  
`math.exp(d / max(tau, eps))` 형태에 충분한 clamp가 없으면 `tau`가 매우 작을 때 `OverflowError`가 발생해 tier 계산이 크래시할 수 있다.  
→ **쉽게 설명하면:** 경계값 보정식이 너무 가파른 설정을 만나면 수치가 폭발해 계산이 멈춘다.

---

## 참고

- 본 문서는 “지금은 문제 아님” 7건(#5, #8, #9, #15, #27, #30, #32 기존 번호)을 제거한 뒤 재정렬한 버전이다.
- 각 항목의 “쉽게 설명하면” 문구는 실제 코드 경로/함수명 기준으로 이해 중심 설명을 추가한 것이다.

**15. preset override label 추출이 action alias를 처리 못함**  
`_extract_preset_override_label`가 `actions.get(base_action)`의 exact key 접근에 의존해 alias 저장값을 놓칠 수 있다. validator 허용 범위와 runtime 해석 범위가 어긋난다.  
→ **쉽게 설명하면:** 저장은 됐는데 실행 때는 “그 키를 모른다”고 해서 override가 조용히 무시될 수 있다.

**19. template-first 비활성 시 combined routing 자체가 비는 회귀 가능**  
`build_offer_skeletons_buy/sell`에서 combined-phase 실행이 fallback 조건 블록에 종속되면, 특정 플래그 조합에서 route가 0개가 된다.  
→ **쉽게 설명하면:** “template-first만 끄자” 했는데, 결과적으로 전체 경로가 같이 꺼져 아무 후보도 못 만드는 상태다.
