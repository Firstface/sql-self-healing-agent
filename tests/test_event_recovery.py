import json
import tempfile
import unittest
from pathlib import Path

from sql_self_healing_agent.core.models import UpstreamTaskEvent
from sql_self_healing_agent.orchestrator.repair_agent_service import RepairAgentService
from sql_self_healing_agent.agent.config import AgentConfig
from tests.fakes import FakeLLMClient


ROOT = Path(__file__).parents[1]


class EventRecoveryTest(unittest.TestCase):
    def test_created_attempt_without_artifacts_resumes_same_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            service = RepairAgentService(root / "sessions", llm_client=FakeLLMClient(), agent_config=AgentConfig(llm_main_agent_enabled=False), metadata_path=ROOT / "mocks/metadata/tables.json")
            event = UpstreamTaskEvent(id="recover", status="FAILED", sql="SELECT user_id, pay_amt FROM dwd_order_detail WHERE date = ", error_message="Invalid column reference pay_amt")
            session = service.session_store.load_or_create_for_event(event)
            record = service.session_store.create_event_record(session, event)
            service.session_store.append_upstream_event(session, record)
            attempt = service.session_store.create_attempt(session, record)
            result = service.handle_upstream_event(event)
            self.assertEqual(result.status, "SQL_READY")
            loaded = service.session_store.load_for_task(event.id)
            self.assertEqual(loaded.attempt_ids, [attempt.attempt_id])
            self.assertEqual(loaded.upstream_events[0].processing_status, "SUCCEEDED")

    def test_partial_state_is_terminal_not_rerun(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            service = RepairAgentService(root / "sessions", llm_client=FakeLLMClient(), agent_config=AgentConfig(llm_main_agent_enabled=False), metadata_path=ROOT / "mocks/metadata/tables.json")
            event = UpstreamTaskEvent(id="partial", status="FAILED", sql="SELECT user_id, pay_amt FROM dwd_order_detail WHERE date = ", error_message="Invalid column reference pay_amt")
            session = service.session_store.load_or_create_for_event(event)
            record = service.session_store.create_event_record(session, event)
            service.session_store.append_upstream_event(session, record)
            attempt = service.session_store.create_attempt(session, record)
            attempt.log_digest_path = "artifact://partial"
            service.session_store.save_attempt(session, attempt)
            result = service.handle_upstream_event(event)
            self.assertEqual(result.status, "HUMAN_REQUIRED")
            loaded = service.session_store.load_for_task(event.id)
            self.assertEqual(loaded.attempt_ids, [attempt.attempt_id])
            self.assertEqual(loaded.upstream_events[0].processing_status, "SYSTEM_ERROR")
            self.assertEqual(loaded.upstream_events[0].error_code, "RECOVERY_PARTIAL_STATE_UNSAFE")
