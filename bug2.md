중복·유사 내용을 합쳐서, 지적사항별로 번호를 붙여 정리한 통합본이야.

---

## 중복 제거 후 정리본

### 드래프트 / 오프시즌 / 계약

**1. 드래프트 번들에서 컴바인 데이터 추출 경로가 잘못됨**
`extractCombineRowsFromBundle`가 `p.meta.combine`를 읽고 있는데, 실제 `/api/offseason/draft/bundle` 응답에서는 `combine`이 `pool.prospects[].combine`의 최상위에 있다. 그 결과 모든 row의 `combine`이 `null`이 되어 Top-10 카드와 상세 화면이 비어 보인다.

**2. 만료 계약 선수의 “방출” 버튼이 구조적으로 실패함**
오프시즌 처리 단계에서 이미 만료 계약 선수를 대부분 `FA`로 옮기는데, 이후 다시 `/api/contracts/release-to-fa`를 호출한다. 이때 `from_team == "FA"`이면 `release_player_to_free_agency`가 에러를 내므로, 해당 행의 “방출” 버튼은 사실상 항상 실패한다.

**3. 워크아웃 추가 진행 플로우가 빈 후보 상태에서 막힘**
`round < workoutMaxRounds`만으로 `워크아웃 추가 진행`이 활성화되어, 실제로는 더 초청할 선수가 없어도 `WORKOUT_INVITE_SELECT`로 진입할 수 있다. 이 상태에서는 “추가로 초청 가능한 선수가 없습니다”만 보이고 다음 진행 버튼도 비활성화되어, 플로우가 교착된다.

**4. standalone 로스터 import CLI 경로가 깨짐**
`_state_season_year_ssot()`가 무조건 예외를 던지도록 바뀌면서, `Salary == "--"`인 행을 처리하는 `import_roster_excel()`의 standalone CLI 경로가 실패한다. 이전에는 게임 상태 초기화 없이도 import가 가능했는데, الآن `season_year` 부재로 `RuntimeError`가 난다.

---

### 전술 / 훈련 / 프론트 UI

**5. `Preset_Defense`가 현재 전술 저장 흐름에서 사실상 사용 불가**
새 `Preset_Defense`가 5개 수비 슬롯 모두를 동일한 role key(`preset-role`)로 정의하고 있다. 그래서 스타터 역할 중복이 필연적으로 발생하고, UI 저장 시 duplicate defense role 검증에 걸려 저장이 실패한다.
또한 저장 검증을 우회하더라도 백엔드 serializer가 role→pid 맵으로 저장하기 때문에, 중복 role key는 하나로 덮여써진다.

**6. Player Training 화면에서 선수 변경 시 잘못된 선수 플랜이 저장될 수 있음**
선수 선택 변경 직후 저장 버튼을 즉시 비활성화하거나 draft를 리셋하지 않아, 이전 선수의 dirty state가 남은 채 새 선수 ID로 저장될 수 있다.
또한 `selectPlayerForTraining`이 비동기 fetch 후 “아직 같은 선수가 선택된 상태인지” 확인하지 않아서, 느린 이전 응답이 나중에 도착하면 현재 선택된 선수의 UI/state를 덮어쓸 수 있다.

**7. 숫자 정렬 CSS가 Trade Block 테이블까지 오염시킴**
새 정렬 selector가 `.market-fa-table` 전체 컬럼에 적용되는데, Trade Block 테이블도 동일 클래스를 공유한다. 그 결과 포지션/액션 같은 비숫자 컬럼까지 우측 정렬되어 가독성이 떨어진다.

---

### 태그 / 수요-공급 / 팀 상황

**8. `tag_supply()`가 항상 빈 객체를 반환해 수요-공급 로직이 사실상 꺼짐**
`FitEngine.compute_player_supply_vector`와 `PackageEffects._package_supply_vector`가 모두 `tag_supply()`를 유일한 공급 소스로 쓰는데, 현재 이 함수가 항상 `{}`를 반환한다. 그 결과 모든 선수의 공급 벡터가 비어 fit 점수가 중립화되고, 새 need-supply package 로직이 무력화된다.

**9. `_emit`의 noise floor가 사실상 사라져 저신호 태그가 과도하게 살아남음**
기존에는 떨어져야 할 작은 weight가 새 sigmoid gate 이후 `1e-9`만 넘으면 거의 다 통과한다. 그 결과 낮은 신호의 태그까지 `needs`에 들어가고, downstream 분기(`if not need_map`)와 fit 계산을 왜곡한다.

---

### 트레이드 / 협상 / 오퍼 프로모션

**10. ACTIVE 협상 세션 수 제한이 사라져 한 tick에 무제한 세션이 열릴 수 있음**
현재 로직은 active session 수를 캐시만 하고 실제 제한은 걸지 않는다. 따라서 특정 인간 팀을 상대로 제안이 많이 몰리면 한 번의 tick에서 ACTIVE negotiation이 무제한으로 생길 수 있고, `create_session` 및 `create_committed_deal`까지 이어져 lock/state가 급격히 불어난다.

**11. REJECT 오퍼가 과도하게 `LOWBALL`로 승격되어 억제 장치가 무력화됨**
`exceed_overpay > probe_max`인 REJECT 제안이 전부 `LOWBALL`로 강제된다. 이 때문에 기존의 상한 억제 경로가 사라지고 `lowball_exceed_*` 안전장치가 듣지 않아, 지나치게 터무니없는 제안이 실제 유저 세션으로 승격된다.

**12. `overpay_allowed`와 `probe_max`가 서로 다른 스케일을 써서 톤 분류가 틀어짐**
`DecisionPolicy.decide`는 multi-axis scale 기반으로 `overpay_allowed`를 계산하지만, reject-offer tone 분류는 여전히 `max(outgoing_total, 6.0)` 기준의 `probe_max`를 사용한다.
픽/스왑 비중이 큰 딜에서는 같은 경계 근처 제안도 `PROBE` 대신 `LOWBALL`로 오분류되어, 더 강한 cooldown/cap이 걸릴 수 있다.

**13. 동일 팀 페어의 세션 재사용 시 `created_at`이 갱신되지 않아 즉시 만료될 수 있음**
유저 오퍼 promotion 흐름에서 세션을 재사용할 때 offer 데이터는 새로고침하지만 `created_at`은 그대로 둔다. 그래서 오래된 세션은 새 제안으로 갱신됐더라도 다음 open 시점에 hard cap age 판정으로 바로 auto-end될 수 있다.

---

### 트레이드 생성 로직 / Skeleton / Template / Sweetener

**14. `buy_target_basketball_norm_mode="FIXED"`가 실제로는 무시됨**
설정상 `FIXED | PERCENTILE | HYBRID`를 지원한다고 되어 있고, `FIXED`는 legacy `(basketball_total + 15) / 45` 롤백 경로여야 한다. 하지만 `_build_basketball_norm_context`가 `PERCENTILE/HYBRID` 외 값을 전부 `PERCENTILE`로 강제해, `FIXED`를 써도 percentile scoring이 적용된다.

**15. `classify_target_tier` 제거로 기존 import가 바로 깨짐**
기존 호출부가 아직 `classify_target_tier`를 import하고 있어서, 제거 직후 `ImportError`가 발생하고 테스트 수집 자체가 막힌다. thin wrapper라도 유지하지 않으면 호환성이 깨진다.

**16. `PackageEffectsConfig`가 legacy constructor 필드를 더 이상 받지 않아 런타임 실패함**
예전에는 허용되던 필드들(`depth_need_scale`, `cap_flex_scale`, `cap_room_weight_base`, `upgrade_scale` 등)이 제거되어, 구버전 rule key를 넘기는 환경에서 `TypeError`가 난다. 단순 점수 변화가 아니라 config 생성 자체가 깨지는 호환성 문제다.

**17. 아웃고잉 후보 빌더가 실제로는 내보낼 수 없는 선수까지 포함함**
빌더가 `out_cat.players` 전체를 순회하기 때문에, lock 상태이거나 recent-signing 제한 등으로 아웃고잉 불가인 선수도 skeleton 후보에 들어간다. 이후 검증에서 반드시 실패할 딜이 만들어져 search/repair 예산을 낭비한다.

**18. 탐욕적 fill이 고가치 선수를 먼저 집어 넣어 저·중티어 딜에서 과지출을 유발함**
선수 풀을 높은 점수순으로 정렬한 뒤 greedy fill을 돌리기 때문에, 더 싼 선수로도 조건을 만족할 상황에서 MVP급 자산이 먼저 소모될 수 있다. 그 결과 제안 품질이 체계적으로 악화된다.

**19. preset override label 추출이 action alias를 처리하지 못함**
`_extract_preset_override_label`가 `actions.get(base_action)`만 보므로 exact key 매치만 된다. 반면 validator는 alias key/value를 받아들이기 때문에, alias로 저장된 override는 런타임에서 무시될 수 있다.

**20. `skeleton_overhaul_enabled` 플래그가 더 이상 존중되지 않음**
관련 함수가 항상 v3 registry path를 타도록 바뀌어, `skeleton_overhaul_enabled=False`로 legacy skeleton 동작을 유지하려는 호출자도 강제로 새 로직을 타게 된다. 여전히 노출된 config flag인데 동작이 무시되는 회귀다.

**21. `template_only`에서 빈 route 설정이 “비활성화”가 아니라 “전체 허용”으로 해석됨**
빈 `skeleton_route_template_*` 튜플이 downstream에서 “route 제약 없음”으로 처리되어, 해당 tier의 모든 template spec이 선택된다. 즉, 특정 tier의 template 단계를 끄려고 route를 비워도 실제로는 오히려 다 실행된다.

**22. template wrapper가 `expected_tag`를 하드코딩해 유효 후보를 전부 버릴 수 있음**
`tpl_<tier>_placeholder_<n>` 형태만 기대하고, tag가 정확히 그 이름과 다르면 built candidate를 모두 드롭한다. planner가 `template_id`를 rename하거나 placeholder 외 ID를 쓰면 유효한 deal도 0건 처리될 수 있다.

**23. template-first 비활성 시 combined routing이 아예 비어버릴 수 있음**
`build_offer_skeletons_buy/sell`의 combined-phase가 `should_run_fallback and fallback_enabled` 블록 안에서만 실행되어, `template_first_enabled=False`이면서 `template_first_fallback_enabled=False`이면 어떤 route도 돌지 않는다. 즉, template-first를 껐는데 legacy combined routing도 함께 꺼지는 회귀다.

**24. pick protection 변형 생성이 sweetener 흐름에 종속되어 버림**
`maybe_apply_pick_protection_variants`가 `maybe_apply_sweeteners` 안으로 들어가면서, sweetener가 꺼져 있거나 `sweetener_max_additions=0`이면 pick protection도 같이 완전히 비활성화된다. 예전에는 sweetener와 독립적으로 적용되던 동작이 사라졌다.

**25. 보호 변형 평가에서 `opponent_repeat_count=0`이 하드코딩되어 파트너 반복 패널티가 무시됨**
기본 proposal은 repeat-partner 패널티를 받는데, protection variant 재평가는 항상 0으로 계산된다. 그 결과 이미 반복 거래 상대인 경우에도 보호 변형 점수가 부당하게 높아져 다양성 유도가 깨진다.

**26. `repair_until_valid_many` 도입 후 repaired variant 루프에서 예산/하드캡 체크가 빠짐**
한 base candidate가 여러 repaired candidate를 생성해도 루프 내부에서 `_budget_or_hard_cap_reached`를 다시 확인하지 않는다. 직전에 예산 한계에 도달했더라도 단일 iteration에서 평가/검증/스위트너 작업이 추가로 많이 실행될 수 있다.

**27. candidate clone 경로가 현재 구조와 맞지 않아 repair 분기가 작동하지 않음**
`_clone_candidate`가 `cand.deal.metadata`와 `metadata=`를 사용하지만 실제 `Deal` 필드는 `meta`다.
또한 `DealCandidate` 생성 시 존재하지 않는 `focal_rank`를 넘기고, 필요한 `buyer_id`, `seller_id`, `archetype` 등을 빠뜨려 `TypeError`까지 유발한다. 결과적으로 repair clone 경로 자체가 실패한다.

---

### 자산 / API / Trade Lab

**28. “tradable assets” API가 실제로는 거래 불가능한 픽까지 반환함**
픽 필터가 owner/round 정도만 보거나 `get_draft_picks_map()` 같은 전체 상태를 그대로 사용해, 이미 소비된 픽·과거 픽·`trade_locked` 픽·DB lock 픽이 응답에 섞인다. 유저는 이 픽들을 선택할 수 있지만 `/api/trade/evaluate`는 확정적으로 거절한다.

**29. `renderAssetList`가 서버 데이터로 `innerHTML`을 직접 구성해 stored XSS 위험이 있음**
player/pick 문자열이 escape 없이 그대로 삽입되므로, 저장된 악성 문자열이 있으면 Trade Lab 진입 시 HTML/JS가 실행될 수 있다. 이미 `escapeHtml`이 있으므로 반드시 적용해야 한다.

**30. asset catalog의 `LockInfo`/`lock` surface 제거가 기존 호출부와 테스트를 깨뜨림**
기존 코드와 테스트가 여전히 `lock=...` 또는 `LockInfo`를 사용하고 있어, 제거 직후 `AttributeError`가 발생한다. 같은 커밋에서 호출부를 모두 옮기지 않았다면 즉시 호환성 문제가 생긴다.

---

### 티어링 / 평가 / 성능

**31. bubble bonus가 실제 cutoff 경쟁 중이 아닌 팀에도 잘못 적용됨**
`gb_to_6th`, `gb_to_10th`가 음수가 아니라 0 이상의 거리값인데 상한(`<=`)만 검사해서, 이미 안전권인 상위 시드 팀도 bonus를 받는다. 그 결과 `performance_score`가 부정확하게 올라간다.

**32. blended score 기준치가 너무 높아 강팀이 `TANK/REBUILD`로 오분류됨**
현재 공식 조합으로는 꽤 강한 팀 프로필도 blended score가 0.733 정도인데, `_map_blended_score_to_tier`는 0.809 미만을 `TANK`로 보낼 수 있다. 이로 인해 posture/horizon 등 downstream 판단이 크게 왜곡된다.

**33. tier 분류가 항상 league-rank 기반 전체 스냅샷 경로를 타서 hot path 성능이 나빠짐**
`_classify_tier_by_league_rank`가 lazy snapshot을 만들면서 모든 active team의 성능/로스터 신호를 다시 계산한다. evaluator를 요청마다 새로 만드는 경로에서는 캐시 이점이 사라져, 단일 팀 평가도 리그 전체 작업으로 번진다.

**34. soft-boundary 확률 계산이 작은 `tau`에서 overflow를 일으킬 수 있음**
`math.exp(d / max(tau, eps))`에 clamp가 없고, config validation은 양수면 통과시킨다. 따라서 `tau=1e-6` 같은 값으로 경계 근처 평가를 하면 `OverflowError`가 나서 tier classification이 런타임에 크래시할 수 있다.

---



---


세 보고서 전부 “지금은 문제 아니다”고 본 사항

총 **7건**

1. **#5 Preset_Defense role 중복 저장 실패**
2. **#8 `tag_supply()`가 항상 빈 객체를 반환**
3. **#15 `classify_target_tier` import 깨짐**
4. **#27 `_clone_candidate` 구조 불일치 / TypeError**
5. **30. asset catalog의 `LockInfo`/`lock` surface 제거가 기존 호출부와 테스트를 깨뜨림**
6. **32. blended score 기준치가 너무 높아 강팀이 `TANK/REBUILD`로 오분류됨**
7. **9. `_emit`의 noise floor가 사실상 사라져 저신호 태그가 과도하게 살아남음**

---






