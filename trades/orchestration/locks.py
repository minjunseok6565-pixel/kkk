from __future__ import annotations

"""trades.orchestration.locks

싱글 프로세스(프로세스-로컬, in-memory)로 동작하는 게임에서
"GM trade-orchestration tick"과 "유저 트레이드 커밋"을 직렬화하기 위한 락 모듈.

왜 필요한가?
- trade_market / trade_memory는 DB(SSOT)의 파생(read-model) 상태이며,
  tick과 유저 트레이드가 동시에 read-modify-write를 수행하면 lost update가 발생할 수 있다.
- 이 모듈의 락은 같은 프로세스 안에서만 동시 실행을 막아, 시장 반영 유실과
  룰/연출 불일치(예: 오늘 이미 트레이드함 제약 미반영)를 예방한다.

중요한 전제/제약:
- SQLite/DB(league_repo.py / LeagueService)가 SSOT다.
  trade_market/trade_memory는 UX/오케스트레이션을 위한 파생 상태이며,
  이 락이 SSOT를 대체하지 않는다.
- 이 락은 threading.RLock 기반 "process-local" 락이다.
  멀티프로세스(예: 여러 uvicorn worker)에서는 프로세스 간 동기화를 제공하지 않는다.
- 권장 락 순서: trade_exec_serial_lock -> state.transaction(...)
  (가능하면 state transaction 내부에서 이 락을 새로 획득하지 말 것. 락 순서가 섞이면
   데드락 위험이 커진다.)
"""

from contextlib import contextmanager
from threading import RLock
from typing import Iterator

# -------------------------------------------------------------------------
# 싱글 프로세스 전용 직렬화 락.
# - tick(오케스트레이션)과 trade submit(유저 커밋) 경로를 동시에 실행하지 않게 만든다.
# - RLock(재진입 가능): tick 내부에서 동일 락을 다시 획득하는 경로가 생겨도 데드락 방지.
# -------------------------------------------------------------------------
_TRADE_EXEC_SERIAL_LOCK = RLock()


@contextmanager
def trade_exec_serial_lock(*, reason: str = "", timeout_s: float | None = None) -> Iterator[None]:
    """Serialize trade-execution critical sections within a single process.

    이 컨텍스트 매니저는 아래 두 작업이 동시에 수행되지 않도록 직렬화한다.

    1) GM trade-orchestration tick
       - market/memory 로드/수정/저장
       - AI-AI 트레이드 커밋(= DB execute_trade) 및 projector

    2) 유저 트레이드 커밋 경로
       - DB execute_trade
       - projector(apply_trade_executed_effects_to_state)로 market/memory 반영

    Args:
        reason: 디버그/로그용 설명 문자열(선택).
        timeout_s: 락 획득 타임아웃(초). None이면 무제한 대기.

    Raises:
        TimeoutError: timeout_s 내에 락을 획득하지 못한 경우.
        ValueError: timeout_s가 숫자로 해석 불가한 경우.

    Usage:
        with trade_exec_serial_lock(reason="USER_TRADE"):
            ...  # execute_trade + market projection
    """

    acquired = False
    if timeout_s is None:
        _TRADE_EXEC_SERIAL_LOCK.acquire()
        acquired = True
    else:
        try:
            timeout = float(timeout_s)
        except Exception as exc:
            raise ValueError(f"timeout_s must be a float seconds value, got: {timeout_s!r}") from exc
        # Defensive: negative timeout behaves like non-blocking.
        if timeout < 0:
            timeout = 0.0
        acquired = _TRADE_EXEC_SERIAL_LOCK.acquire(timeout=timeout)

    if not acquired:
        msg = f"trade_exec_serial_lock timeout (timeout_s={timeout_s})"
        if reason:
            msg += f": {reason}"
        raise TimeoutError(msg)

    try:
        yield
    finally:
        _TRADE_EXEC_SERIAL_LOCK.release()


__all__ = [
    "trade_exec_serial_lock",
]
