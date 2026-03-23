# Need Tag 전면 교체 동작 설명서 (C/E 완료 가정)

이 문서는 다음 두 항목이 **추가로 완료되었다고 가정**하고, 교체 전/후 동작 차이를 설명한다.

- C. `tag_supply` 플레이스홀더 제거(실제 attrs 기반 공급 계산 완성)
- E. 문서상 전수 교체 대상 파일(valuation/generation/API/표시 계층) 미착수분 완료

---

## 0) 현재 기준 점검 요약 (C/E 제외)

현재 코드 기준으로, 핵심 교체 경로에서 구 need 태그 기반 생성/소비는 제거된 상태다.

- `team_situation`: role→need 생성은 `OFF_* / DEF_*`(+조건부 `G_/W_/B_`) 태그 기준으로 생성
- `decision_context`: `ALL_NEW_NEED_TAGS`(+접두 유효성) 필터를 통과한 need만 `need_map`에 반영
- `fit_engine`: `tag_supply(attrs)` 단일 경로 공급 + 신규 태그 집합 기반 fit 계산
- `package_effects`: 패키지 공급벡터/충족/초과 기반 보정으로 전환

즉, **C/E를 제외한 구조적 교체 목표는 달성된 상태**로 본다.

---

## 1) 교체 전 vs 교체 후: Team Need 생성

## 교체 전
- 역할 미스매치 기반 + 스타일 지표 + 포지션 뎁스 + CAP_FLEX 등 다중 규칙을 혼합해서 need를 생성.
- role->need 변환은 별도 매핑(`role_need_tags`)에 의존.
- 구 need 태그(`SPACING`, `PRIMARY_INITIATOR`, `GUARD_DEPTH`, `CAP_FLEX` 등)가 출력 중심.

## 교체 후
- 팀 니즈는 역할 점수/커버리지/사용량 차이를 중심으로 생성.
- 출력 태그는 `OFF_*`, `DEF_*` 기본 태그이며,
  포지션 부족(상대비율+절대인원<=3)일 때만 `G_/W_/B_` 접두를 붙임.
- 태그 문자열은 SSOT(`ALL_NEW_NEED_TAGS`)와 일치하는 명시 매핑으로 생성.

효과:
- "자주 쓰는 역할인데 커버가 약한" 케이스가 직접 반영.
- 태그 표준화로 downstream 파이프라인에서 필터 손실 감소.

---

## 2) 교체 전 vs 교체 후: DecisionContext need_map 계약

## 교체 전
- 상류에서 내려온 태그를 상대적으로 관대하게 수용.
- 구/신 태그 혼재 시 컨텍스트에 비표준 태그가 남을 가능성 존재.

## 교체 후
- `ALL_NEW_NEED_TAGS` + 접두 유효성(`G_/W_/B_ + base tag`)만 허용.
- 비표준 태그는 `need_map`에서 제거.
- downstream은 정규화된 태그만 소비.

효과:
- valuation/generation 계층에서 태그 호환성 방어 코드 감소.
- fit/package 계산의 재현성 향상.

---

## 3) 교체 전 vs 교체 후: Fit Engine (개별 선수 적합도)

## 교체 전
- role_fit 메타 우선 + 일부 attrs 휴리스틱 보강.
- 지원 태그가 구 role 기반 태그 집합 중심.
- 플레이어 공급벡터가 경로별/태그별로 일관성 부족.

## 교체 후
- 공급벡터는 단일 경로: `tag_supply(player.attrs)`.
- 지원 태그는 `ALL_NEW_NEED_TAGS` 기본 + 유효 접두 동적 확장.
- fit score는 `need_map × supply` 점곱 유지.
- 설명성 강화: 태그별 `unmet`/`excess` 제공.

효과:
- fit 계산 경로 단순화.
- need 정의와 supply 정의가 동일 SSOT를 바라봄.

---

## 4) 교체 전 vs 교체 후: Package Effects (패키지 보정)

## 교체 전
- 단일 archetype 버킷 감점, depth/CAP_FLEX/upgrade 같은 구 need 태그 의존 보정이 혼재.
- 패키지 전체가 실제로 팀 니즈를 얼마나 채우는지보다, 휴리스틱 구조 효과가 상대적으로 큼.

## 교체 후
- incoming 선수 attrs를 합산해 태그 공급벡터 생성.
- 태그별로
  - `fulfilled = min(need, supply)`
  - `excess = max(0, supply-need)`
  계산.
- need 가중 초과 공급량에 비선형 패널티 적용.
- 옵션: 동일 충족량을 더 적은 선수로 달성하면 슬롯 효율 보너스(깊이 스트레스 시 감쇠).
- 메타에 `need/supply/fulfilled/excess`를 그대로 남겨 튜닝 가능.

효과:
- "왜 이 패키지가 과잉/적정인지" 설명이 명확.
- 중복감점이 태그 중복 자체가 아니라 초과공급량 중심으로 동작.

---

## 5) C 완료(가정) 시 최종 동작 모습

`tag_supply`가 완성되면:
- 플레이어 attrs(0~99) → 태그별 공급량(0~1) 변환이 실제 동작.
- fit/package 두 계층이 동일 공급정의로 평가.
- 현재 placeholder로 인한 "공급 0 수렴" 문제 해소.

---

## 6) E 완료(가정) 시 최종 동작 모습

전수 교체 파일(valuation/generation/API/표시)까지 마무리되면:
- 어느 계층에서도 구 need 태그를 기대/출력/표시하지 않음.
- `need_map`는 신규 태그만 end-to-end 전파.
- 사용자/로그/디버그 화면도 신규 태그 체계로 일관.

---

## 7) 최종 한 줄 정리

(C/E 완료 가정 하에) 시스템은
**"팀 니즈 생성은 role·usage·coverage·포지션 부족 기반, 선수/패키지 충족 평가는 attrs 기반"**
이라는 목표 상태로 수렴한다.
