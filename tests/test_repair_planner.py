import json
import tempfile
import unittest
from pathlib import Path

from sql_self_healing_agent.core.enums import DiagnosedErrorType
from sql_self_healing_agent.diagnostics.diagnosis_models import DiagnosisResult
from sql_self_healing_agent.logs.log_models import LogDigest
from sql_self_healing_agent.metadata.metadata_models import MetadataSnapshot
from sql_self_healing_agent.metadata.mock_metadata_provider import MockMetadataProvider
from sql_self_healing_agent.metadata.sql_table_extractor import SQLTableExtractor
from sql_self_healing_agent.llm.llm_client import LLMClientError
from sql_self_healing_agent.repair.repair_models import RepairAction, RepairActionType, RepairPlan, RepairPlannerInput, SQLGeneratorInput
from sql_self_healing_agent.repair.sql_generator import SQLGenerator
from sql_self_healing_agent.repair.repair_planner import RepairPlanner
from sql_self_healing_agent.core.time_utils import utc_now_iso


class RepairPlannerTest(unittest.TestCase):
    def test_plans_metadata_verified_column_replacement(self) -> None:
        provider = MockMetadataProvider(Path(__file__).parents[1] / "mocks/metadata/tables.json")
        extraction = SQLTableExtractor().extract("SELECT pay_amt FROM dwd_order_detail")
        table = provider.get_table_metadata("dwd_order_detail")
        snapshot = MetadataSnapshot(extraction_result=extraction, tables=[table], created_at=utc_now_iso())
        diagnosis = DiagnosisResult(diagnosed_error_type=DiagnosedErrorType.COLUMN_NOT_FOUND, diagnosed_keywords=["column_not_found"], error_fingerprint="COLUMN_NOT_FOUND:pay_amt:hive", confidence=0.9, is_repairable=True, primary_entity="pay_amt")
        plan = RepairPlanner(provider).plan(RepairPlannerInput(failed_sql="SELECT pay_amt FROM dwd_order_detail", diagnosis=diagnosis, log_digest=LogDigest(log_readable=True), metadata_snapshot=snapshot))
        self.assertTrue(plan.repairable)
        self.assertEqual(plan.actions[0].replacement_fragment, "payment_amount")


class FailingSQLGeneratorClient:
    def generate_structured(self, prompt, response_model):
        raise LLMClientError("invalid structured SQL output")


class SQLGeneratorFailureTest(unittest.TestCase):
    def test_invalid_output_returns_cannot_generate_safely(self) -> None:
        plan = RepairPlan(plan_id="plan", repairable=True, actions=[RepairAction(action_type=RepairActionType.REPLACE_COLUMN, target_fragment="a", replacement_fragment="b", reason="test", risk_level="LOW")], confidence=0.9)
        result = SQLGenerator(FailingSQLGeneratorClient()).generate(SQLGeneratorInput(failed_sql="SELECT a", repair_plan=plan))
        self.assertFalse(result.generated)
        self.assertTrue(result.cannot_generate_safely)
        self.assertEqual(result.reason, "LLM 未能返回合法的结构化 SQL 结果。")

    def test_oscillation_blocks_column_not_found_before_planning(self) -> None:
        provider = MockMetadataProvider(Path(__file__).parents[1] / "mocks/metadata/tables.json")
        extraction = SQLTableExtractor().extract("SELECT pay_amt FROM dwd_order_detail")
        table = provider.get_table_metadata("dwd_order_detail")
        snapshot = MetadataSnapshot(extraction_result=extraction, tables=[table], created_at=utc_now_iso())
        diagnosis = DiagnosisResult(
            diagnosed_error_type=DiagnosedErrorType.COLUMN_NOT_FOUND,
            diagnosed_keywords=["column_not_found"],
            error_fingerprint="COLUMN_NOT_FOUND:pay_amt:hive",
            confidence=0.9,
            is_repairable=True,
            primary_entity="pay_amt",
        )
        plan = RepairPlanner(provider).plan(
            RepairPlannerInput(
                failed_sql="SELECT pay_amt FROM dwd_order_detail",
                diagnosis=diagnosis,
                log_digest=LogDigest(log_readable=True),
                metadata_snapshot=snapshot,
                post_reflection_result={
                    "status": "OSCILLATING",
                    "previous_error_resolved": False,
                    "new_error_introduced": True,
                    "recommendation_for_next_plan": "stop",
                    "reasons": ["A/B/A"],
                    "confidence": 1.0,
                },
            )
        )
        self.assertFalse(plan.repairable)
        self.assertEqual(plan.actions, [])
