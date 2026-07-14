import unittest

from sql_self_healing_agent.repair.repair_models import ChangedFragment, RepairAction, RepairActionType, RepairPlan, SQLDiffSummary
from sql_self_healing_agent.repair.validator import Validator


class ValidationTest(unittest.TestCase):
    def _plan(self) -> RepairPlan:
        return RepairPlan(plan_id="plan", repairable=True, actions=[RepairAction(action_type=RepairActionType.REPLACE_COLUMN, target_fragment="pay_amt", replacement_fragment="payment_amount", reason="metadata", risk_level="LOW")], confidence=0.9)

    def test_allows_authorized_minimal_change(self) -> None:
        changed = ChangedFragment(before="pay_amt", after="payment_amount", action_type=RepairActionType.REPLACE_COLUMN, reason="metadata")
        diff = SQLDiffSummary(changed_fragment_count=1, changed_fragments=[changed], parse_success=True)
        result = Validator().validate("SELECT pay_amt FROM t WHERE date = ", "SELECT payment_amount FROM t WHERE date = ", self._plan(), diff)
        self.assertTrue(result.allow_return_sql)

    def test_blocks_where_removal_and_dangerous_sql(self) -> None:
        diff = SQLDiffSummary(changed_fragment_count=1, changed_fragments=[], removed_where=True, parse_success=True)
        result = Validator().validate("SELECT pay_amt FROM t WHERE x = 1", "DELETE FROM t", self._plan(), diff)
        self.assertFalse(result.allow_return_sql)
        self.assertEqual(result.risk_level.value, "BLOCKED")
