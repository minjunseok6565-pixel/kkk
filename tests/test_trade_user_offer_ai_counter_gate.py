from trades.orchestration.promotion import _ai_verdict_allows_user_offer
from trades.orchestration.types import OrchestrationConfig
from trades.valuation.types import DealVerdict


def test_ai_accept_always_allows_user_offer() -> None:
    cfg = OrchestrationConfig(allow_user_offers_on_ai_counter=False)
    assert _ai_verdict_allows_user_offer(DealVerdict.ACCEPT, cfg) is True


def test_ai_counter_allows_user_offer_when_enabled() -> None:
    cfg = OrchestrationConfig(allow_user_offers_on_ai_counter=True)
    assert _ai_verdict_allows_user_offer(DealVerdict.COUNTER, cfg) is True


def test_ai_counter_blocks_user_offer_when_disabled() -> None:
    cfg = OrchestrationConfig(allow_user_offers_on_ai_counter=False)
    assert _ai_verdict_allows_user_offer(DealVerdict.COUNTER, cfg) is False


def test_ai_reject_allows_user_offer_when_enabled() -> None:
    cfg = OrchestrationConfig(allow_user_offers_on_ai_counter=True, allow_user_offers_on_ai_reject=True)
    assert _ai_verdict_allows_user_offer(DealVerdict.REJECT, cfg) is True


def test_ai_reject_blocks_user_offer_when_disabled() -> None:
    cfg = OrchestrationConfig(allow_user_offers_on_ai_counter=True, allow_user_offers_on_ai_reject=False)
    assert _ai_verdict_allows_user_offer(DealVerdict.REJECT, cfg) is False
