# team_situation v2 니즈 태그에 ORtg/DRtg 반영: 구체 수정안

이 문서는 `team_situation`의 **v2 역할 기반 니즈 생성(OFF_*/DEF_*)** 을 유지하면서,
기존 방식의 장점이던 **효율 지표(ORtg/DRtg) 기반 보정**을 변형해 재도입하기 위한
구체 구현안을 정리합니다.

- 목표 1: ORtg/DRtg 하위권일수록 해당 phase(공/수) 니즈가 더 잘 생성되도록 한다.
- 목표 2: 생성된 니즈 강도(weight)가 더 높아지도록 한다.
- 목표 3: 현재 v2의 태그 체계(OFF_*/DEF_* + G/W/B prefix)를 유지한다.
- 목표 4: evidence 기반 설명 가능성을 유지한다.

---

## 1) 현재 구조 진단 (요약)

### 이미 확보된 것
- 팀별 ORtg/DRtg 및 리그 백분위(`ortg_pct`, `def_pct`)는 `build_team_situation_context()`에서
  `team_ratings_index`로 계산/저장되고,
- `evaluate_team()`에서 `TeamSituationSignals`로 주입된다.

즉, **데이터 수집 신규 개발은 거의 필요 없다**. 핵심은 니즈 weight 수식에 반영하는 것이다.

### 현재 비활성 상태
- `_boost_needs_by_efficiency_percentiles()`는 legacy pass-through(그대로 반환)다.
- v2 실제 니즈는 `_compute_role_fit_and_needs()` 내부 `_emit()` 수식으로 결정된다.

따라서 효율 보정은 `_emit()`에 phase-aware로 녹이는 방식이 가장 자연스럽다.

---

## 2) 수정 대상 파일과 변경 요약

## 파일 A) `data/team_situation_config.py`

### 변경 목적
효율 보정 로직의 파라미터를 하드코딩하지 않고 설정화한다.

### 추가 필드 (제안)
```python
# Efficiency-aware need amplification (phase-specific)
eff_bad_pct_center_off: float = 0.45
 eff_bad_pct_center_def: float = 0.45

eff_add_scale_off: float = 0.10
 eff_add_scale_def: float = 0.10

eff_mult_scale_off: float = 0.20
 eff_mult_scale_def: float = 0.20

# min_emit_weight dynamic relaxation at poor efficiency
eff_emit_relax_max_off: float = 0.05
 eff_emit_relax_max_def: float = 0.05

# Early-season dampening (apply with stat_trust-like signal)
eff_early_dampen_min: float = 0.50
```

> 주의: 들여쓰기 오타 없이 실제 코드에서는 각 필드를 정상 정렬해야 함.

### 왜 필요한가
- 운영 중 튜닝 가능
- 시즌/리그 환경 변화(득점 인플레/디플레)에 대응 쉬움
- OFF/DEF를 분리 튜닝 가능

---

## 파일 B) `data/team_situation.py`

### 변경 목적
`_compute_role_fit_and_needs()`의 `_emit()`에서
- ORtg/DRtg 약세에 따른 **weight 상향**
- ORtg/DRtg 약세에 따른 **emit 컷오프 완화**
를 적용한다.

### 변경 포인트 상세

#### B-1. 함수 시그니처 확장
현재:
```python
def _compute_role_fit_and_needs(self, team_id, roster, *, style_sig=None, roster_sig=None)
```

제안:
```python
def _compute_role_fit_and_needs(
    self,
    team_id,
    roster,
    *,
    style_sig=None,
    roster_sig=None,
    ortg_pct: float = 0.5,
    def_pct: float = 0.5,
    stat_trust: float = 1.0,
)
```

- `evaluate_team()`에서 `rt` 값을 바로 전달.
- `stat_trust`는 시즌 초반 과민반응 방지에 사용.

#### B-2. evaluate_team 호출부 전달
현재:
```python
role_sig, role_needs = self._compute_role_fit_and_needs(tid, roster, style_sig=style_sig, roster_sig=roster_sig)
```

제안:
```python
season_progress = _safe_float(self.ctx.records_index.get(tid, {}).get("season_progress"), 0.0)
stat_trust = _early_stat_trust(season_progress)

role_sig, role_needs = self._compute_role_fit_and_needs(
    tid,
    roster,
    style_sig=style_sig,
    roster_sig=roster_sig,
    ortg_pct=float(rt.get("ortg_pct", 0.5) or 0.5),
    def_pct=float(rt.get("def_pct", 0.5) or 0.5),
    stat_trust=float(stat_trust),
)
```

#### B-3. `_emit()` 내부 phase-aware 효율 보정 추가
기존 `w = clamp(base + adj, 0, 1)` 계산 이후를 다음처럼 확장:

1) phase별 약세 강도 계산
```python
if phase == "OFF":
    weak = clamp((cfg.eff_bad_pct_center_off - ortg_pct) / max(1e-9, cfg.eff_bad_pct_center_off), 0.0, 1.0)
    add_scale = cfg.eff_add_scale_off
    mult_scale = cfg.eff_mult_scale_off
    relax_max = cfg.eff_emit_relax_max_off
else:
    weak = clamp((cfg.eff_bad_pct_center_def - def_pct) / max(1e-9, cfg.eff_bad_pct_center_def), 0.0, 1.0)
    add_scale = cfg.eff_add_scale_def
    mult_scale = cfg.eff_mult_scale_def
    relax_max = cfg.eff_emit_relax_max_def
```

2) 시즌 초반 완화
```python
weak *= clamp(stat_trust, cfg.eff_early_dampen_min, 1.0)
```

3) 최종 weight 반영
```python
eff_add = weak * add_scale
eff_mult = 1.0 + weak * mult_scale
w = clamp((base + adj + eff_add) * eff_mult, 0.0, 1.0)
```

4) emit 컷오프 동적 완화
```python
effective_min_emit = max(0.0, cfg.min_emit_weight - weak * relax_max)
if w < effective_min_emit:
    return
```

#### B-4. evidence 확장
`TeamNeed.evidence`에 아래 필드 추가:
- `ortg_pct`, `def_pct`
- `eff_weakness`
- `eff_add`, `eff_mult`
- `effective_min_emit`

예시:
```python
"efficiency_context": {
    "ortg_pct": float(ortg_pct),
    "def_pct": float(def_pct),
    "phase_weakness": float(weak),
    "eff_add": float(eff_add),
    "eff_mult": float(eff_mult),
    "effective_min_emit": float(effective_min_emit),
}
```

이렇게 하면 "왜 이 니즈가 강해졌는지"를 로그/툴팁에서 바로 설명 가능.

---

## 파일 C) `docs/issue/team_situation_니즈_태그_생성_쉬운설명v2.md`

### 변경 목적
문서와 코드 동기화.

### 수정 항목
- "효율 보정은 legacy" 문구를 제거/수정.
- "역할 기반 단일 파이프라인 + phase별 효율 보정"으로 서술 변경.
- weight 공식 섹션에 `efficiency_adj` 추가.
- evidence 설명에 `efficiency_context` 필드 추가.

예시 문장:
- "OFF 역할은 ORtg percentile 하위권일수록, DEF 역할은 DRtg percentile 하위권일수록
  동일 조건에서 더 높은 weight와 더 낮은 emit threshold를 적용한다."

---

## 3) 알고리즘 정의 (최종 제안)

역할별 기존 계산:
- `base = max(low_score, low_cov)`
- `adj = usage_coverage_adjustment`

추가 계산:
- `weak_off = f(ortg_pct)`
- `weak_def = f(def_pct)`
- phase에 따라 `weak = weak_off or weak_def`
- `weak *= early_dampen(stat_trust)`

최종:
- `w = clamp((base + adj + weak * add_scale) * (1 + weak * mult_scale), 0, 1)`
- `emit_if w >= min_emit_weight - weak * relax_max`

성격:
- **additive**: "아예 안 뜨는" 문제 완화
- **multiplicative**: 이미 큰 니즈의 우선순위 상승
- **threshold relax**: 하위권 팀에서 경계선 니즈 생존

---

## 4) 기대 동작 예시

- A팀: `ortg_pct=0.18`, `def_pct=0.62`
  - OFF 역할 니즈: 생성 빈도↑, weight↑
  - DEF 역할 니즈: 기존과 유사

- B팀: `ortg_pct=0.55`, `def_pct=0.12`
  - DEF 역할 니즈: 생성 빈도↑, weight↑
  - OFF 역할 니즈: 기존과 유사

- C팀: 양쪽 모두 하위권
  - OFF/DEF 양축 니즈가 모두 강해짐

---

## 5) 롤아웃/검증 계획

### 1단계: 기능 플래그 없이 기본 적용 (권장)
- 기존 태그 체계를 건드리지 않아 downstream 충돌이 적다.

### 2단계: 리그 전수 비교 스냅샷
비교 지표:
- 팀별 니즈 개수 평균
- OFF/DEF 태그 비중 변화
- 하위 25% ORtg 팀의 OFF 니즈 평균 weight
- 하위 25% DRtg 팀의 DEF 니즈 평균 weight
- 상위권 팀에서 과증폭 여부(상위권 평균 weight 변화)

### 3단계: 파라미터 튜닝
- 과증폭 시 `eff_mult_scale_*` 먼저 하향
- 과소반영 시 `eff_add_scale_*`와 `eff_emit_relax_max_*` 상향

---

## 6) 리스크 및 대응

### 리스크 1: early season 과민반응
- 대응: `stat_trust` 기반 dampening 적용

### 리스크 2: 태그 과다 생성
- 대응: 동적 emit 완화 상한(`relax_max`) 제한 + 기존 상위 10개 clip 유지

### 리스크 3: 역할 점수 약세와 효율 약세의 이중 가중 과다
- 대응: `eff_add_scale` 낮게 시작(0.08~0.10) 후 점진 튜닝

---

## 7) 구현 체크리스트

- [ ] `data/team_situation_config.py`에 efficiency 파라미터 추가
- [ ] `data/team_situation.py` `_compute_role_fit_and_needs()` 시그니처/호출부 확장
- [ ] `_emit()`에 phase-aware efficiency boost + dynamic emit threshold 반영
- [ ] evidence에 efficiency_context 필드 추가
- [ ] `docs/issue/team_situation_니즈_태그_생성_쉬운설명v2.md` 문서 업데이트
- [ ] 샘플 팀 비교 로그(최소 3팀) 확인

---

## 8) 요약

이 수정안은 v2의 핵심 장점(세분화된 역할 태그, usage/coverage 기반 계산)을 유지하면서,
v1의 강점(팀 효율 하위권 반영)을 **태그 생성 단계 자체**에 통합한다.

결과적으로,
- OFF/DEF 방향성은 더 명확해지고,
- 하위 효율팀의 니즈 신호는 더 강해지며,
- evidence로 설명 가능한 구조도 유지된다.
