import json
import tempfile
import unittest
from pathlib import Path

from sql_self_healing_agent.core.models import UpstreamTaskEvent
from sql_self_healing_agent.orchestrator.repair_agent_service import RepairAgentService
from tests.fakes import FakeLLMClient


PROJECT_ROOT = Path(__file__).parents[1]


class MemoryWriterTest(unittest.TestCase):
    def test_memory_written_only_after_matching_success_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            log_path = root / "task.log"
            log_path.write_text("SemanticException: Invalid column reference pay_amt\n")
            service = RepairAgentService(
                root / "sessions",
                llm_client=FakeLLMClient(),
                metadata_path=PROJECT_ROOT / "mocks/metadata/tables.json",
                memory_dir=root / "memory_store",
            )
            failed = UpstreamTaskEvent(id="task_memory", status="FAILED", sql="SELECT user_id, pay_amt FROM dwd_order_detail WHERE date = ", error_message="failed", log_path=str(log_path))
            ready = service.handle_upstream_event(failed)
            self.assertEqual(ready.status, "SQL_READY")
            self.assertFalse((root / "memory_store/experiences").exists())

            unmatched = UpstreamTaskEvent(id="task_memory", status="SUCCESS", sql="SELECT 1")
            self.assertEqual(service.handle_upstream_event(unmatched).status, "SUCCESS_ACK")
            self.assertFalse((root / "memory_store/experiences").exists())

            matched = UpstreamTaskEvent(id="task_memory", status="SUCCESS", sql=f" /* comment */ {ready.sql}; ")
            self.assertEqual(service.handle_upstream_event(matched).status, "SUCCESS_ACK")
            experiences = list((root / "memory_store/experiences").glob("*.json"))
            self.assertEqual(len(experiences), 1)
            experience = json.loads(experiences[0].read_text())
            self.assertEqual(experience["source_attempt_id"], "attempt_001")
            self.assertEqual(experience["confirmed_sql"], matched.sql)
            self.assertEqual(service.handle_upstream_event(matched).status, "SUCCESS_ACK")
            self.assertEqual(len(list((root / "memory_store/experiences").glob("*.json"))), 1)
