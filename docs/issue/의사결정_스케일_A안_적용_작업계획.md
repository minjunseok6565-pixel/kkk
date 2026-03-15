# 의사결정 스케일 A안 적용 작업계획

## 1) 목표 요약

현재 `DecisionPolicy`의 스케일이 `scale = max(outgoing_total, 6.0)` 단일축 + 고정 하한이라,
`outgoing_total`이 작게 잡히는 딜(픽/스왑 위주, 상쇄형 자산 포함)에서 임계값이 실제 딜 체급을 충분히 반영하지 못합니다.

이번 A안의 목표는 아래 2가지입니다.

1. **고정 최소값 6.0 삭제**
2. **max 기반은 유지하되 다축 스케일로 확장**
   - `outgoing_total`
   - `incoming_total`
   - 절대 질량 기반 `mass_scale`

---

## 2) 최종 목표 함수(정확한 형태)

아래 형태를 최종 목표로 합니다. (`decision_policy.py` 내부 helper로 추가)

```python
def _deal_scale(
    evaluation: TeamDealEvaluation,
    *,
    eps: float,
) -> float:
    """A안 다축 스케일: max 기반 유지 + 6.0 제거.

    scale 축:
      1) outgoing_total
      2) incoming_total
      3) mass_scale = max(in_abs_mass, out_abs_mass)
         - in_abs_mass = abs(in_now) + abs(in_future)
         - out_abs_mass = abs(out_now) + abs(out_future)

    Returns:
      max(outgoing, incoming, mass_scale, eps)
    """
    outgoing = _safe_float(evaluation.outgoing_total, 0.0)
    incoming = _safe_float(evaluation.incoming_total, 0.0)

    in_comp = evaluation.side.incoming_totals.value
    out_comp = evaluation.side.outgoing_totals.value

    in_abs_mass = abs(_safe_float(in_comp.now, 0.0)) + abs(_safe_float(in_comp.future, 0.0))
    out_abs_mass = abs(_safe_float(out_comp.now, 0.0)) + abs(_safe_float(out_comp.future, 0.0))
    mass_scale = max(in_abs_mass, out_abs_mass)

    return max(outgoing, incoming, mass_scale, float(eps))
```

그리고 `decide()`에서는 기존

```python
scale = max(outgoing, cfg.min_outgoing_scale)
```

대신

```python
scale = _deal_scale(evaluation, eps=cfg.eps)
```

를 사용합니다.

> 유지되는 점
> - `required_surplus = min_surplus_ratio * scale`
> - `overpay_allowed = overpay_ratio * scale`
> - `corridor = counter_corridor_ratio * scale`
>
> 즉, 정책 구조는 그대로 두고 스케일 산정만 현실화하는 접근입니다.

---

## 3) 파일별 작업 계획

## A. `trades/valuation/decision_policy.py`

### A-1. 설정 필드 정리
- `DecisionPolicyConfig.min_outgoing_scale` 삭제
  - 고정값 `6.0` 정책을 제거하기 위함
- 주석도 `outgoing_total only` 문구에서 `multi-axis deal scale`로 교체

### A-2. helper 추가
- `_deal_scale(...)` 함수 추가 (위 "최종 목표 함수" 그대로)
- 입력은 `TeamDealEvaluation`만 사용
- `side.incoming_totals/outgoing_totals`를 통해 now/future 절대 질량 계산

### A-3. `decide()` 본문 치환
- 기존 `scale = max(outgoing, cfg.min_outgoing_scale)` 제거
- `scale = _deal_scale(evaluation, eps=cfg.eps)` 적용

### A-4. 설명 메타 확장
`DecisionReason(code="THRESHOLDS")`의 `meta`에 아래 세부 축 기록:
- `scale` (최종값)
- `scale_outgoing`
- `scale_incoming`
- `scale_mass`

> 목적: 디버그/설명에서 "왜 이 임계가 나왔는지"를 축별로 확인 가능하게 하기 위함

---

## B. `trades/valuation/test_decision_policy_counter_softness.py`

A안 적용 시 기존 테스트는 대부분 통과 가능성이 높지만,
스케일 원천이 단일 outgoing이 아니므로 아래 보강이 필요합니다.

### B-1. 회귀 방지 테스트(신규)
1. **incoming > outgoing 케이스**
   - outgoing이 작아도 incoming이 큰 딜에서 scale이 incoming 기준으로 커지는지 검증
2. **mass_scale 지배 케이스**
   - now/future 상쇄 또는 음수 자산 포함 구조에서 mass_scale이 스케일을 결정하는지 검증
3. **eps 가드 케이스**
   - incoming/outgoing/mass가 모두 0에 가까울 때 scale이 0으로 붕괴하지 않는지 검증

### B-2. 기존 테스트 보정
- 테스트 내부에서 `scale = outgoing`로 가정한 부분은
  `policy가 산출한 실제 scale` 기반으로 기대값 계산하도록 조정

---

## C. `docs/issue/트레이드_이슈_4_13_15_17_18_22_쉬운설명.md` (선택)

이번 패치와 함께 문서 최신화를 하려면,
4번 이슈 섹션의 "한 줄 개선 아이디어"를
"A안(다축 max)으로 구현 완료" 상태로 업데이트합니다.

- 필수는 아님 (코드 패치와 동시 반영 시 선택)
- 릴리즈 노트/튜닝 문서와의 일관성을 위해 권장

---

## D. `trades/generation/dealgen/sweetener.py` (후속 권장)

현재 sweetener 근접 판정도 `max(outgoing_total, 6.0)` 계열입니다.
결정정책만 A안으로 바꾸면, "수락 임계"와 "스위트너 진입 임계"의 스케일 철학이 달라질 수 있습니다.

- 본 작업의 **필수 범위는 아님**
- 하지만 후속으로 아래 정렬을 권장:
  - sweetener close corridor 계산도 동일 다축 scale helper(또는 동등 로직) 사용

---

## 4) 구현 순서 (실행 플로우)

1. `decision_policy.py`
   - config 정리 (`min_outgoing_scale` 제거)
   - `_deal_scale` helper 추가
   - `decide()` scale 계산 치환 + reason meta 확장
2. `test_decision_policy_counter_softness.py`
   - scale 가정 제거/보정
   - 다축 scale 신규 테스트 추가
3. 테스트 실행
   - valuation 단위 테스트 우선
   - 필요 시 관련 dealgen 테스트 스모크
4. (선택) docs 동기화

---

## 5) 수용 기준 (Definition of Done)

- [ ] `decision_policy.py`에서 `6.0` 고정 바닥 제거
- [ ] `scale`이 outgoing 단일축이 아닌 다축 max로 계산
- [ ] `required_surplus/overpay/corridor` 계산식은 기존 구조 유지
- [ ] 축별 스케일 상세가 decision reasons/meta에 노출
- [ ] 기존 decision policy 테스트 통과
- [ ] 신규 다축 스케일 회귀 테스트 통과

---

## 6) 패치 전/후 게이머 체감 변화

## 패치 전 체감
- 작은 outgoing 위주의 제안에서 AI가 지나치게 비슷한 강도로 반응할 수 있습니다.
- 딜 구조가 복잡하거나 자산이 상쇄되는 경우에도 임계가 "작은 딜"처럼 계산되어,
  어떤 팀은 불필요하게 딱딱하게 굴거나 반대로 과하게 쉽게 수용하는 등 일관성이 흔들릴 수 있습니다.
- 유저 입장에서 "이 정도면 꽤 큰 거래인데 왜 반응이 이렇지?"라는 위화감이 발생합니다.

## 패치 후 체감
- AI가 딜 체급을 outgoing 하나로만 보지 않고 incoming/질량까지 함께 보므로,
  실제 체감 규모에 맞는 협상 강도를 보입니다.
- 픽/스왑 중심, 상쇄형 자산, 복합 패키지에서 반응이 더 자연스럽고 설명가능해집니다.
- 같은 팀 성향이라도 딜의 실제 구조 차이에 따라 accept/counter/reject가 더 납득 가능하게 달라집니다.
- 결과적으로 "AI가 숫자 트릭에 덜 흔들리고 거래 맥락을 읽는다"는 플레이 감각이 강화됩니다.
