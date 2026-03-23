# OVR 단일축 8-Tier 하드컷 분류 전환 구상

## 목표
기존 `skill_score(0~1 percentile)` 기반 tier 분류를 제거하고, **OVR 단일 기준 하드컷**으로 완전 대체한다.

- MVP: `ovr >= 97`
- ALL_NBA: `93 <= ovr <= 96`
- ALL_STAR: `90 <= ovr <= 92`
- HIGH_STARTER: `85 <= ovr <= 89`
- STARTER: `80 <= ovr <= 84`
- HIGH_ROTATION: `77 <= ovr <= 79`
- ROTATION: `75 <= ovr <= 76`
- GARBAGE: `ovr <= 74`

---

## 어떤 파일에 작업이 필요한가

## 1) 핵심 분류 함수
### `trades/generation/dealgen/utils.py`
`classify_target_profile()`이 현재 tier를 percentile/EMA/hysteresis/soft-membership으로 계산한다.

- 교체 대상(사실상 제거 후보)
  - `_resolve_skill_score_percentile_base`
  - `_resolve_skill_score_percentile_ema`
  - `_soft_tier_memberships`
  - `_apply_tier_hysteresis`
- 변경 방향
  - `classify_target_profile()` 내부 tier 결정 로직을 `ovr` 하드컷 분기로 단순화
  - `skill_score` 계열 출력 키는 하위호환 정책을 정해서
    - 완전 제거하거나,
    - 남겨야 하면 `ovr` 기반 정규화 값(예: `(ovr-60)/40 clamp`)으로 대체

> 이 파일이 실질적인 "티어 판정 SSOT" 역할을 하고 있으므로 1순위 수정 대상.

## 2) 분류 입력 전달 경로(OVR 주입)
### `trades/generation/asset_catalog.py`
`IncomingPlayerRef` 생성 시점에 현재 OVR 필드가 없다. 분류를 OVR로 하려면 ref에 OVR이 실려야 한다.

- 수정 후보
  - `IncomingPlayerRef` dataclass에 `ovr: Optional[float]` 필드 추가
  - `all_ref = IncomingPlayerRef(...)` 생성부에서 `c.snap.ovr` 주입

## 3) 점수화 빌더의 probe 구성
### `trades/generation/dealgen/skeleton_builders_tier_score_common.py`
`_player_points()`가 `SimpleNamespace` probe에 `market_total/basketball_total`만 넣고 `classify_target_profile()`를 호출한다.

- 변경 방향
  - probe에 `ovr`를 반드시 포함 (`cand.snap.ovr`)
  - 가능하면 market_total fallback에 기대지 않도록 명시

## 4) 테스트 보정
### 대상 테스트들
- `trades/generation/dealgen/test_skeleton_phase4_config.py`
- `trades/generation/dealgen/test_skeleton_builders_tier_score_common.py`
- `trades/generation/dealgen/test_skeleton_score_ssot.py`
- `trades/generation/dealgen/test_targets_buy_tiered_retrieval.py` (tier 관련 assertion이 있으면)

변경 후에는 percentile 경계가 아니라 **OVR 경계값 기반 기대 tier**로 assertion을 재작성해야 한다.

---

## 프로젝트에서 OVR은 어디서 받아올 수 있나

## A. 원천 주입 지점(이미 있음)
### `trades/generation/asset_catalog.py`
`PlayerSnapshot` 생성 시 이미 DB row에서 `ovr`를 읽어 `snap.ovr`에 넣고 있다.

즉, generation catalog 단계에서 OVR 데이터는 이미 확보된 상태다.

## B. 후보 객체에서 접근 가능
### `trades/generation/asset_catalog.py`
`PlayerTradeCandidate`는 `snap: PlayerSnapshot`를 보유한다.

따라서 skeleton builder에서 `cand.snap.ovr`로 직접 접근 가능하다.

## C. league-wide incoming 레퍼런스에는 현재 누락
### `trades/generation/asset_catalog.py`
`IncomingPlayerRef`에는 현재 OVR 필드가 없어, BUY 타깃/분류 쪽에서 OVR 단일축 적용 시 전달 경로를 추가해야 한다.

---

## 구현 시 주의 포인트

- 하드컷 경계 포함 규칙(예: 96은 ALL_NBA)을 코드/테스트에 동일하게 고정.
- `ovr is None` 처리 규칙 필요
  - 권장: `None -> GARBAGE` (보수적)
- 기존 percentile 관련 config(`target_tier_skill_center/scale`, `target_tier_soft_band`, EMA/hysteresis)는 미사용화되므로
  - 즉시 제거 vs deprecated 유지 전략을 결정.

---

## 최소 변경 순서(권장)
1. `classify_target_profile()`를 OVR 하드컷으로 교체.
2. `IncomingPlayerRef`에 `ovr` 추가 + 생성부 주입.
3. `_player_points()` probe에 `ovr` 전달.
4. tier 관련 단위테스트를 경계값 중심으로 갱신.
5. 남는 percentile 로직/설정 정리(후속 PR로 분리 가능).
