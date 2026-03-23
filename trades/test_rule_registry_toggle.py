import unittest

from trades.rules import get_default_registry


class RuleRegistryToggleTests(unittest.TestCase):
    def _enabled_map(self, registry):
        return {r.rule_id: bool(getattr(r, "enabled", False)) for r in registry.list_rules()}

    def test_roster_limit_disabled_by_default(self):
        reg = get_default_registry()
        enabled = self._enabled_map(reg)
        self.assertIn("roster_limit", enabled)
        self.assertFalse(enabled["roster_limit"])

    def test_roster_limit_can_be_enabled_via_trade_rules(self):
        reg = get_default_registry(trade_rules={"roster_limit_rule_enabled": True})
        enabled = self._enabled_map(reg)
        self.assertTrue(enabled.get("roster_limit"))


if __name__ == "__main__":
    unittest.main()
