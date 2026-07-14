import json
import tempfile
import unittest
from pathlib import Path

from sql_self_healing_agent.core.models import UpstreamTaskEvent
from sql_self_healing_agent.diagnostics.diagnosis_models import LLMDiagnosisResult
from sql_self_healing_agent.llm.llm_client import LLMClientError
from sql_self_healing_agent.repair.repair_models import SQLGeneratorLLMOutput
from sql_self_healing_agent.orchestrator.repair_agent_service import RepairAgentService
from tests.fakes import FakeLLMClient


class HandleUpstreamEventTest(unittest.TestCase):
    def test_failed_event_returns_sql_ready_and_persists_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            log_path = root / "task.log"
            log_path.write_text("SemanticException: Invalid column reference pay_amt\n")
            service = RepairAgentService(root / "sessions", llm_client=FakeLLMClient(), metadata_path=Path(__file__).parents[1] / "mocks/metadata/tables.json")
            event = UpstreamTaskEvent(id="task_123", status="FAILED", sql="SELECT user_id, pay_amt FROM dwd_order_detail WHERE date = ", error_message="Task failed", log_path=str(log_path))
            result = service.handle_upstream_event(event)
            self.assertEqual(result.status, "SQL_READY")
            self.assertEqual(result.sql, "SELECT user_id, payment_amount FROM dwd_order_detail WHERE date = ")
            session_dir = root / "sessions/sess_task_123"
            required = {"log_digest.json", "diagnosis.json", "metadata_snapshot.json", "memory_retrieval.json", "repair_plan.json", "sql_candidate.sql", "validation_result.json", "pre_reflection_result.json"}
            self.assertTrue(required.issubset({path.name for path in (session_dir / "artifacts/attempt_001").iterdir()}))
            service.handle_upstream_event(event)
            session = json.loads((session_dir / "session.json").read_text())
            self.assertEqual(session["attempt_ids"], ["attempt_001"])
            self.assertEqual(session["status"], "SQL_READY_PENDING_UPSTREAM")

    def test_success_idempotency_ignores_log_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            sessions = Path(temporary_directory) / "sessions"
            service = RepairAgentService(sessions, llm_client=FakeLLMClient(), metadata_path=Path(__file__).parents[1] / "mocks/metadata/tables.json")
            first = UpstreamTaskEvent(id="task_success", status="SUCCESS", sql="SELECT 1", log_path="first.log")
            second = first.model_copy(update={"log_path": "second.log"})
            self.assertEqual(service.handle_upstream_event(first).status, "SUCCESS_ACK")
            self.assertEqual(service.handle_upstream_event(second).status, "SUCCESS_ACK")
            session_dir = sessions / "sess_task_success"
            session = json.loads((session_dir / "session.json").read_text())
            self.assertEqual(len(session["upstream_events"]), 1)


class GeneratorFailingClient:
    def generate_structured(self, prompt, response_model):
        if response_model is LLMDiagnosisResult:
            return response_model.model_validate({
                "diagnosed_error_type": "COLUMN_NOT_FOUND",
                "diagnosed_keywords": ["column_not_found"],
                "primary_evidence": "Invalid column reference pay_amt",
                "root_cause_summary": "pay_amt is missing",
                "confidence": 0.95,
                "is_repairable": True,
                "manual_repair_reason": None,
            })
        if response_model is SQLGeneratorLLMOutput:
            raise LLMClientError("invalid structured SQL output")
        raise AssertionError(response_model)


class HandleLLMFailureTest(unittest.TestCase):
    def test_generation_failure_is_human_required_not_system_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            log_path = root / "task.log"
            log_path.write_text("SemanticException: Invalid column reference pay_amt\n")
            service = RepairAgentService(root / "sessions", llm_client=GeneratorFailingClient(), metadata_path=Path(__file__).parents[1] / "mocks/metadata/tables.json")
            event = UpstreamTaskEvent(id="task_failure", status="FAILED", sql="SELECT pay_amt FROM dwd_order_detail", error_message="Invalid column reference pay_amt", log_path=str(log_path))
            result = service.handle_upstream_event(event)
            self.assertEqual(result.status, "HUMAN_REQUIRED")
            session = json.loads((root / "sessions/sess_task_failure/session.json").read_text())
            attempt = json.loads((root / "sessions/sess_task_failure/attempts/attempt_001.json").read_text())
            self.assertEqual(session["status"], "HUMAN_REQUIRED")
            self.assertEqual(attempt["status"], "HUMAN_REQUIRED")
