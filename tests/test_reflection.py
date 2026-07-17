import unittest
from tests.llm_test_adapter import build_test_llm_adapter

from sql_self_healing_agent.core.enums import DiagnosedErrorType, RiskLevel
from sql_self_healing_agent.diagnostics.diagnosis_models import DiagnosisResult
from sql_self_healing_agent.llm.llm_client import LLMClientError
from sql_self_healing_agent.repair.evaluator import RepairEvaluator
from sql_self_healing_agent.repair.reflection import PreReflectionDecision, PreReflectionInput
from sql_self_healing_agent.repair.repair_models import RepairAction, RepairActionType, RepairPlan, SQLDiffSummary, ValidationResult
from tests.fakes import FakeLLMClient


class ReflectionTest(unittest.TestCase):
    def test_validation_block_cannot_be_overridden(self) -> None:
        plan = RepairPlan(plan_id="plan", repairable=True, actions=[RepairAction(action_type=RepairActionType.REPLACE_COLUMN, target_fragment="a", replacement_fragment="b", reason="test", risk_level="LOW")], confidence=0.9)
        diagnosis = DiagnosisResult(diagnosed_error_type=DiagnosedErrorType.COLUMN_NOT_FOUND, diagnosed_keywords=["column_not_found"], error_fingerprint="x", confidence=0.9, is_repairable=True)
        reflection_input = PreReflectionInput(failed_sql="SELECT a", sql_candidate="SELECT b", diagnosis=diagnosis, repair_plan=plan, validation_result=ValidationResult(risk_level=RiskLevel.BLOCKED, passed=False, allow_return_sql=False, reason="blocked"), sql_diff_summary=SQLDiffSummary(changed_fragment_count=1, changed_fragments=[], parse_success=True))
        result = RepairEvaluator((client := FakeLLMClient()), build_test_llm_adapter(client)).pre_reflect(reflection_input)
        self.assertEqual(result.decision, PreReflectionDecision.BLOCK)


class FailingReflectionClient:
    def generate_structured(self, prompt, response_model):
        raise LLMClientError("invalid structured reflection output")


class ReflectionFailureTest(unittest.TestCase):
    def test_llm_failure_blocks_candidate(self) -> None:
        plan = RepairPlan(plan_id="plan", repairable=True, actions=[RepairAction(action_type=RepairActionType.REPLACE_COLUMN, target_fragment="a", replacement_fragment="b", reason="test", risk_level="LOW")], confidence=0.9)
        diagnosis = DiagnosisResult(diagnosed_error_type=DiagnosedErrorType.COLUMN_NOT_FOUND, diagnosed_keywords=["column_not_found"], error_fingerprint="x", confidence=0.9, is_repairable=True)
        validation = ValidationResult(risk_level=RiskLevel.LOW, passed=True, allow_return_sql=True)
        reflection_input = PreReflectionInput(failed_sql="SELECT a", sql_candidate="SELECT b", diagnosis=diagnosis, repair_plan=plan, validation_result=validation, sql_diff_summary=SQLDiffSummary(changed_fragment_count=1, changed_fragments=[], parse_success=True))
        result = RepairEvaluator((client := FailingReflectionClient()), build_test_llm_adapter(client)).pre_reflect(reflection_input)
        self.assertEqual(result.decision, PreReflectionDecision.BLOCK)
        self.assertEqual(result.confidence, 0.0)

class ContradictoryReturnClient:
    def generate_structured(self, prompt, response_model):
        return response_model.model_validate({
            "decision": "RETURN_SQL",
            "confidence": 0.99,
            "follows_repair_plan": False,
            "minimal_change": False,
            "semantic_risk_level": "HIGH",
            "reasons": ["unsafe"],
            "violated_constraints": ["extra change"],
            "regeneration_instruction": None,
        })


class ReflectionConsistencyTest(unittest.TestCase):
    def test_contradictory_return_sql_is_blocked(self) -> None:
        plan = RepairPlan(plan_id="plan", repairable=True, actions=[RepairAction(action_type=RepairActionType.REPLACE_COLUMN, target_fragment="a", replacement_fragment="b", reason="test", risk_level="LOW")], confidence=0.9)
        diagnosis = DiagnosisResult(diagnosed_error_type=DiagnosedErrorType.COLUMN_NOT_FOUND, diagnosed_keywords=["column_not_found"], error_fingerprint="x", confidence=0.9, is_repairable=True)
        validation = ValidationResult(risk_level=RiskLevel.LOW, passed=True, allow_return_sql=True)
        reflection_input = PreReflectionInput(failed_sql="SELECT a", sql_candidate="SELECT b", diagnosis=diagnosis, repair_plan=plan, validation_result=validation, sql_diff_summary=SQLDiffSummary(changed_fragment_count=1, changed_fragments=[], parse_success=True))
        result = RepairEvaluator((client := ContradictoryReturnClient()), build_test_llm_adapter(client)).pre_reflect(reflection_input)
        self.assertEqual(result.decision, PreReflectionDecision.BLOCK)

class PostReflectionTest(unittest.TestCase):
    def _history_item(self, attempt_id: str, fingerprint: str):
        from sql_self_healing_agent.diagnostics.diagnosis_models import DiagnosisHistoryItem
        return DiagnosisHistoryItem(
            attempt_id=attempt_id,
            diagnosed_error_type="COLUMN_NOT_FOUND" if fingerprint.startswith("A") else "TYPE_MISMATCH",
            diagnosed_keywords=["column_not_found"] if fingerprint.startswith("A") else ["type_mismatch"],
            error_fingerprint=fingerprint,
            confidence=0.9,
            created_at="2026-07-15T00:00:00Z",
        )

    def _input(self, current_type="TYPE_MISMATCH", current_fingerprint="B"):
        from sql_self_healing_agent.diagnostics.diagnosis_models import DiagnosisResult
        from sql_self_healing_agent.logs.log_models import LogDigest
        from sql_self_healing_agent.repair.reflection import PostReflectionInput
        from sql_self_healing_agent.session.session_models import RepairAttempt
        previous_diagnosis = DiagnosisResult(
            diagnosed_error_type="COLUMN_NOT_FOUND",
            diagnosed_keywords=["column_not_found"],
            error_fingerprint="A",
            confidence=0.9,
            is_repairable=True,
        )
        current_diagnosis = DiagnosisResult(
            diagnosed_error_type=current_type,
            diagnosed_keywords=["type_mismatch"] if current_type == "TYPE_MISMATCH" else ["column_not_found"],
            error_fingerprint=current_fingerprint,
            confidence=0.9,
            is_repairable=True,
        )
        plan = RepairPlan(plan_id="p", repairable=True, actions=[], confidence=1.0)
        attempt = RepairAttempt(
            attempt_id="attempt_001",
            attempt_no=1,
            source_event_key="evt",
            input_event_id="evt",
            input_failed_sql="SELECT a",
            created_at="2026-07-15T00:00:00Z",
            updated_at="2026-07-15T00:00:00Z",
        )
        return PostReflectionInput(
            previous_attempt=attempt,
            previous_diagnosis=previous_diagnosis,
            previous_repair_plan=plan,
            previous_sql_candidate="SELECT b",
            current_failed_sql="SELECT b",
            current_log_digest=LogDigest(log_readable=False),
            current_diagnosis=current_diagnosis,
            diagnosis_history=[],
        )

    def test_post_reflection_failed_but_progressing(self) -> None:
        from sql_self_healing_agent.repair.reflection import PostReflectionStatus
        result = RepairEvaluator().post_reflect(self._input())
        self.assertEqual(result.status, PostReflectionStatus.FAILED_BUT_PROGRESSING)
        self.assertTrue(result.previous_error_resolved)

    def test_post_reflection_failed_unchanged(self) -> None:
        from sql_self_healing_agent.repair.reflection import PostReflectionStatus
        result = RepairEvaluator().post_reflect(self._input("COLUMN_NOT_FOUND", "A"))
        self.assertEqual(result.status, PostReflectionStatus.FAILED_UNCHANGED)

    def test_post_reflection_detects_oscillation(self) -> None:
        from sql_self_healing_agent.repair.reflection import PostReflectionStatus
        reflection_input = self._input("COLUMN_NOT_FOUND", "A")
        reflection_input.diagnosis_history = [
            self._history_item("attempt_001", "A"),
            self._history_item("attempt_002", "B"),
            self._history_item("attempt_003", "A"),
        ]
        result = RepairEvaluator().post_reflect(reflection_input)
        self.assertEqual(result.status, PostReflectionStatus.OSCILLATING)

    def test_post_reflection_has_no_succeeded_status(self) -> None:
        from sql_self_healing_agent.repair.reflection import PostReflectionStatus
        self.assertNotIn("SUCCEEDED", {item.value for item in PostReflectionStatus})
