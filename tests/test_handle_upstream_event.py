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
            self.assertEqual(service.handle_upstream_event(event), result)
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
            first_result = service.handle_upstream_event(first)
            second_result = service.handle_upstream_event(second)
            self.assertEqual(first_result.status, "SUCCESS_ACK")
            self.assertEqual(second_result, first_result)
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
            self.assertEqual(service.handle_upstream_event(event), result)
            session = json.loads((root / "sessions/sess_task_failure/session.json").read_text())
            attempt = json.loads((root / "sessions/sess_task_failure/attempts/attempt_001.json").read_text())
            self.assertEqual(session["status"], "HUMAN_REQUIRED")
            self.assertEqual(attempt["status"], "HUMAN_REQUIRED")

class RegeneratingClient(FakeLLMClient):
    def __init__(self) -> None:
        self.generation_calls = 0
        self.reflection_calls = 0

    def generate_structured(self, prompt, response_model):
        from sql_self_healing_agent.repair.reflection import PreReflectionResult
        if response_model is SQLGeneratorLLMOutput:
            self.generation_calls += 1
            return response_model.model_validate({
                "generated": True,
                "sql_candidate": "SELECT user_id, payment_amount FROM dwd_order_detail WHERE date = ",
                "cannot_generate_safely": False,
                "reason": None,
                "changed_fragments": [{"before": "pay_amt", "after": "payment_amount", "action_type": "REPLACE_COLUMN", "reason": "execute plan"}],
            })
        if response_model is PreReflectionResult:
            self.reflection_calls += 1
            if self.reflection_calls == 1:
                return response_model.model_validate({
                    "decision": "REGENERATE",
                    "confidence": 0.8,
                    "follows_repair_plan": True,
                    "minimal_change": True,
                    "semantic_risk_level": "LOW",
                    "reasons": ["regenerate once"],
                    "violated_constraints": [],
                    "regeneration_instruction": "严格按原 RepairPlan 重新生成一次",
                })
        return super().generate_structured(prompt, response_model)


class RegenerationFlowTest(unittest.TestCase):
    def test_regenerates_at_most_once_and_revalidates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            log_path = root / "task.log"
            log_path.write_text("SemanticException: Invalid column reference pay_amt\n")
            client = RegeneratingClient()
            service = RepairAgentService(root / "sessions", llm_client=client, metadata_path=Path(__file__).parents[1] / "mocks/metadata/tables.json")
            event = UpstreamTaskEvent(id="task_regenerate", status="FAILED", sql="SELECT user_id, pay_amt FROM dwd_order_detail WHERE date = ", error_message="Task failed", log_path=str(log_path))
            result = service.handle_upstream_event(event)
            self.assertEqual(result.status, "SQL_READY")
            self.assertEqual(client.generation_calls, 2)
            self.assertEqual(client.reflection_calls, 2)
            artifact_dir = root / "sessions/sess_task_regenerate/artifacts/attempt_001"
            self.assertTrue((artifact_dir / "sql_regeneration_result.json").exists())
            validation = json.loads((artifact_dir / "validation_result.json").read_text())
            self.assertTrue(validation["allow_return_sql"])


class InsertCandidateClient(FakeLLMClient):
    def generate_structured(self, prompt, response_model):
        if response_model is SQLGeneratorLLMOutput:
            return response_model.model_validate({
                "generated": True,
                "sql_candidate": "INSERT INTO audit_table SELECT payment_amount FROM dwd_order_detail",
                "cannot_generate_safely": False,
                "reason": None,
                "changed_fragments": [{"before": "pay_amt", "after": "payment_amount", "action_type": "REPLACE_COLUMN", "reason": "claimed plan change"}],
            })
        return super().generate_structured(prompt, response_model)


class InsertValidationServiceTest(unittest.TestCase):
    def test_parseable_insert_is_validation_blocked_not_system_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            log_path = root / "task.log"
            log_path.write_text("SemanticException: Invalid column reference pay_amt\n")
            service = RepairAgentService(root / "sessions", llm_client=InsertCandidateClient(), metadata_path=Path(__file__).parents[1] / "mocks/metadata/tables.json")
            event = UpstreamTaskEvent(id="task_insert", status="FAILED", sql="SELECT pay_amt FROM dwd_order_detail", error_message="Task failed", log_path=str(log_path))
            result = service.handle_upstream_event(event)
            self.assertEqual(result.status, "NO_SQL")
            self.assertEqual(service.handle_upstream_event(event), result)
            attempt = json.loads((root / "sessions/sess_task_insert/attempts/attempt_001.json").read_text())
            self.assertEqual(attempt["status"], "VALIDATION_BLOCKED")
            trace_events = [json.loads(line) for line in (root / "sessions/sess_task_insert/trace.jsonl").read_text().splitlines()]
            self.assertNotIn("system_error", {event["event_type"] for event in trace_events})


class BlockingReflectionClient(FakeLLMClient):
    def generate_structured(self, prompt, response_model):
        from sql_self_healing_agent.repair.reflection import PreReflectionResult
        if response_model is PreReflectionResult:
            return response_model.model_validate({
                "decision": "BLOCK",
                "confidence": 0.9,
                "follows_repair_plan": True,
                "minimal_change": True,
                "semantic_risk_level": "LOW",
                "reasons": ["blocked for test"],
                "violated_constraints": [],
                "regeneration_instruction": None,
            })
        return super().generate_structured(prompt, response_model)


class TerminalResultIdempotencyTest(unittest.TestCase):
    def test_reflection_blocked_result_is_replayed_exactly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            log_path = root / "task.log"
            log_path.write_text("SemanticException: Invalid column reference pay_amt\n")
            service = RepairAgentService(root / "sessions", llm_client=BlockingReflectionClient(), metadata_path=Path(__file__).parents[1] / "mocks/metadata/tables.json")
            event = UpstreamTaskEvent(id="task_reflection_block", status="FAILED", sql="SELECT user_id, pay_amt FROM dwd_order_detail WHERE date = ", error_message="Task failed", log_path=str(log_path))
            first = service.handle_upstream_event(event)
            second = service.handle_upstream_event(event)
            self.assertEqual(first.status, "NO_SQL")
            self.assertEqual(second, first)

    def test_missing_diagnostic_input_result_is_replayed_exactly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            service = RepairAgentService(root / "sessions", llm_client=FakeLLMClient(), metadata_path=Path(__file__).parents[1] / "mocks/metadata/tables.json")
            event = UpstreamTaskEvent(id="task_missing_input", status="FAILED", sql="SELECT pay_amt FROM dwd_order_detail")
            first = service.handle_upstream_event(event)
            second = service.handle_upstream_event(event)
            self.assertEqual(first.status, "HUMAN_REQUIRED")
            self.assertEqual(second, first)


class HiddenChangeClient(FakeLLMClient):
    def generate_structured(self, prompt, response_model):
        if response_model is SQLGeneratorLLMOutput:
            return response_model.model_validate({
                "generated": True,
                "sql_candidate": "SELECT user_id, payment_amount, hacked_col FROM dwd_order_detail WHERE date = ",
                "cannot_generate_safely": False,
                "reason": None,
                "changed_fragments": [{"before": "pay_amt", "after": "payment_amount", "action_type": "REPLACE_COLUMN", "reason": "claimed plan change"}],
            })
        return super().generate_structured(prompt, response_model)


class AdversarialServiceTest(unittest.TestCase):
    def test_hidden_plan_external_change_never_reaches_sql_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            log_path = root / "task.log"
            log_path.write_text("SemanticException: Invalid column reference pay_amt\n")
            service = RepairAgentService(root / "sessions", llm_client=HiddenChangeClient(), metadata_path=Path(__file__).parents[1] / "mocks/metadata/tables.json")
            event = UpstreamTaskEvent(id="task_hidden_change", status="FAILED", sql="SELECT user_id, pay_amt FROM dwd_order_detail WHERE date = ", error_message="Task failed", log_path=str(log_path))
            result = service.handle_upstream_event(event)
            self.assertEqual(result.status, "NO_SQL")
            session = json.loads((root / "sessions/sess_task_hidden_change/session.json").read_text())
            attempt = json.loads((root / "sessions/sess_task_hidden_change/attempts/attempt_001.json").read_text())
            self.assertNotEqual(session["status"], "SQL_READY_PENDING_UPSTREAM")
            self.assertEqual(attempt["status"], "VALIDATION_BLOCKED")
