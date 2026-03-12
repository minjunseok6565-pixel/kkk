from __future__ import annotations

import copy
from typing import Any, Iterable, Mapping, Optional, Sequence

from .base import Rule, TradeContext


class RuleRegistry:
    def __init__(self, rules: Optional[Iterable[Rule]] = None) -> None:
        self._rules: dict[str, Rule] = {}
        if rules:
            for rule in rules:
                self.register(rule)

    def register(self, rule: Rule) -> None:
        self._rules[rule.rule_id] = rule

    def unregister(self, rule_id: str) -> None:
        self._rules.pop(rule_id, None)

    def set_enabled(self, rule_id: str, enabled: bool) -> None:
        rule = self._rules.get(rule_id)
        if rule is not None:
            rule.enabled = enabled

    def list_rules(self) -> list[Rule]:
        return list(self._rules.values())


def validate_all(
    deal,
    ctx: TradeContext,
    registry: Optional[RuleRegistry] = None,
    prepared_rules: Optional[Sequence[Rule]] = None,
) -> None:
    if prepared_rules is not None:
        for rule in prepared_rules:
            rule.validate(deal, ctx)
        return
    registry = registry or get_default_registry()
    enabled_rules = [rule for rule in registry.list_rules() if rule.enabled]
    for rule in sorted(enabled_rules, key=lambda rule: (rule.priority, rule.rule_id)):
        rule.validate(deal, ctx)


def _coerce_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return bool(default)
    if isinstance(value, (int, float)):
        return bool(value)
    s = str(value).strip().lower()
    if s in {"1", "true", "t", "yes", "y", "on"}:
        return True
    if s in {"0", "false", "f", "no", "n", "off"}:
        return False
    return bool(default)


def get_default_registry(*, trade_rules: Optional[Mapping[str, Any]] = None) -> RuleRegistry:
    from .builtin import BUILTIN_RULES

    # Registry rules are mutable (enabled flag), so deep-copy to avoid mutating
    # module-level BUILTIN_RULES across requests/ticks.
    registry = RuleRegistry(copy.deepcopy(BUILTIN_RULES))

    tr = trade_rules if isinstance(trade_rules, Mapping) else {}
    roster_limit_enabled = _coerce_bool(tr.get("roster_limit_rule_enabled"), False)
    registry.set_enabled("roster_limit", roster_limit_enabled)

    return registry
