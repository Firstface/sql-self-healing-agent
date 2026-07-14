import unittest

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
        result = RepairEvaluator(FakeLLMClient()).pre_reflect(reflection_input)
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
        result = RepairEvaluator(FailingReflectionClient()).pre_reflect(reflection_input)
        self.assertEqual(result.decision, PreReflectionDecision.BLOCK)
        self.assertEqual(result.confidence, 0.0)
