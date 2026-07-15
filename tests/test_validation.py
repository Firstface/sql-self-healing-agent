import unittest

from sql_self_healing_agent.repair.repair_models import ChangedFragment, RepairAction, RepairActionType, RepairPlan, SQLDiffSummary, SQLGenerationResult
from sql_self_healing_agent.repair.sql_generator import build_diff
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

    def test_blocks_update_introduction(self) -> None:
        plan = RepairPlan(plan_id="p", repairable=True, actions=[], confidence=1.0)
        generation = SQLGenerationResult(generated=True, sql_candidate="UPDATE t SET x = 1")
        diff = build_diff("SELECT * FROM t", generation, plan)
        result = Validator().validate("SELECT * FROM t", generation.sql_candidate or "", plan, diff)
        self.assertFalse(result.allow_return_sql)
        self.assertIn("WRITE_INTRODUCED", {issue.code for issue in result.issues})

    def test_blocks_select_to_parseable_insert(self) -> None:
        original = "SELECT pay_amt FROM t"
        candidate = "INSERT INTO x SELECT payment_amount FROM t"
        generation = SQLGenerationResult(generated=True, sql_candidate=candidate)
        diff = build_diff(original, generation, self._plan())
        result = Validator().validate(original, candidate, self._plan(), diff)
        self.assertFalse(result.allow_return_sql)
        self.assertIn("WRITE_INTRODUCED", {issue.code for issue in result.issues})

    def test_allows_same_insert_target_and_partition(self) -> None:
        original = "INSERT OVERWRITE TABLE x PARTITION (ds='2026-01-01') SELECT pay_amt FROM t"
        candidate = "INSERT OVERWRITE TABLE x PARTITION (ds='2026-01-01') SELECT payment_amount FROM t"
        generation = SQLGenerationResult(generated=True, sql_candidate=candidate)
        diff = build_diff(original, generation, self._plan())
        result = Validator().validate(original, candidate, self._plan(), diff)
        self.assertTrue(diff.parse_success)
        self.assertFalse(diff.changed_insert_target)
        self.assertFalse(diff.changed_static_partition)
        self.assertTrue(result.allow_return_sql)

    def test_blocks_insert_target_change(self) -> None:
        original = "INSERT INTO x SELECT pay_amt FROM t"
        candidate = "INSERT INTO y SELECT payment_amount FROM t"
        generation = SQLGenerationResult(generated=True, sql_candidate=candidate)
        diff = build_diff(original, generation, self._plan())
        result = Validator().validate(original, candidate, self._plan(), diff)
        self.assertTrue(diff.changed_insert_target)
        self.assertFalse(result.allow_return_sql)
        self.assertIn("INSERT_TARGET_UNVERIFIED", {issue.code for issue in result.issues})

    def test_blocks_static_partition_change(self) -> None:
        original = "INSERT OVERWRITE TABLE x PARTITION (ds='2026-01-01') SELECT pay_amt FROM t"
        candidate = "INSERT OVERWRITE TABLE x PARTITION (ds='2026-01-02') SELECT payment_amount FROM t"
        generation = SQLGenerationResult(generated=True, sql_candidate=candidate)
        diff = build_diff(original, generation, self._plan())
        result = Validator().validate(original, candidate, self._plan(), diff)
        self.assertTrue(diff.changed_static_partition)
        self.assertFalse(result.allow_return_sql)
        self.assertIn("INSERT_TARGET_UNVERIFIED", {issue.code for issue in result.issues})

    def test_insert_root_without_where_does_not_raise(self) -> None:
        original = "INSERT INTO x SELECT pay_amt FROM t"
        candidate = "INSERT INTO x SELECT payment_amount FROM t"
        generation = SQLGenerationResult(generated=True, sql_candidate=candidate)
        diff = build_diff(original, generation, self._plan())
        self.assertTrue(diff.parse_success)
        self.assertFalse(diff.removed_where)

    def test_blocks_where_weakening(self) -> None:
        plan = RepairPlan(plan_id="p", repairable=True, actions=[], confidence=1.0)
        original = "SELECT * FROM t WHERE x = 1 AND y = 2"
        candidate = "SELECT * FROM t WHERE x = 1"
        diff = build_diff(original, SQLGenerationResult(generated=True, sql_candidate=candidate), plan)
        result = Validator().validate(original, candidate, plan, diff)
        self.assertFalse(result.allow_return_sql)
        self.assertIn("WHERE_WEAKENED", {issue.code for issue in result.issues})

    def test_blocks_join_condition_change(self) -> None:
        plan = RepairPlan(plan_id="p", repairable=True, actions=[], confidence=1.0)
        original = "SELECT * FROM a JOIN b ON a.id = b.id"
        candidate = "SELECT * FROM a JOIN b ON 1 = 1"
        diff = build_diff(original, SQLGenerationResult(generated=True, sql_candidate=candidate), plan)
        result = Validator().validate(original, candidate, plan, diff)
        self.assertFalse(result.allow_return_sql)
        self.assertIn("JOIN_CONDITION_CHANGED", {issue.code for issue in result.issues})

    def test_blocks_group_by_expression_change(self) -> None:
        plan = RepairPlan(plan_id="p", repairable=True, actions=[], confidence=1.0)
        original = "SELECT pay_amt, count(*) FROM t GROUP BY pay_amt"
        candidate = "SELECT other_col, count(*) FROM t GROUP BY other_col"
        diff = build_diff(original, SQLGenerationResult(generated=True, sql_candidate=candidate), plan)
        result = Validator().validate(original, candidate, plan, diff)
        self.assertFalse(result.allow_return_sql)
        self.assertIn("GROUP_BY_CHANGED", {issue.code for issue in result.issues})

    def test_blocks_unreported_extra_change(self) -> None:
        plan = RepairPlan(plan_id="p", repairable=True, actions=[RepairAction(action_type=RepairActionType.REPLACE_COLUMN, target_fragment="pay_amt", replacement_fragment="payment_amount", reason="fix", risk_level="LOW")], confidence=1.0)
        original = "SELECT user_id, pay_amt FROM t WHERE x = 1"
        candidate = "SELECT user_id, payment_amount, hacked_col FROM t WHERE x = 1"
        diff = build_diff(original, SQLGenerationResult(generated=True, sql_candidate=candidate), plan)
        result = Validator().validate(original, candidate, plan, diff)
        self.assertFalse(result.allow_return_sql)
        self.assertIn("UNAUTHORIZED_CHANGE", {issue.code for issue in result.issues})
