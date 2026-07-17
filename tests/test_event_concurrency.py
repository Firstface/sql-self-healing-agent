import json
import tempfile
import threading
import unittest
from pathlib import Path

from sql_self_healing_agent.core.models import UpstreamTaskEvent
from sql_self_healing_agent.orchestrator.repair_agent_service import RepairAgentService
from tests.fakes import FakeLLMClient


class EventConcurrencyTest(unittest.TestCase):
    def test_concurrent_duplicate_failed_creates_one_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "task.log"
            log.write_text("SemanticException: Invalid column reference pay_amt\n")
            service = RepairAgentService(
                root / ".sessions",
                llm_client=FakeLLMClient(),
                metadata_path=Path(__file__).parents[1] / "mocks/metadata/tables.json",
                memory_dir=root / ".memory",
            )
            event = UpstreamTaskEvent(
                id="concurrent",
                status="FAILED",
                sql="SELECT pay_amt FROM dwd_order_detail",
                error_message="Invalid column reference pay_amt",
                log_path=str(log),
            )
            barrier = threading.Barrier(2)
            results = []

            def run() -> None:
                barrier.wait()
                results.append(service.handle_upstream_event(event))

            threads = [threading.Thread(target=run) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            session = json.loads(
                (root / ".sessions/sess_concurrent/session.json").read_text()
            )
            self.assertEqual(session["attempt_ids"], ["attempt_001"])
            self.assertEqual(len(session["upstream_events"]), 1)
            self.assertIn("HUMAN_REQUIRED", {result.status for result in results})
            self.assertTrue({result.status for result in results} <= {"SQL_READY", "NO_SQL", "HUMAN_REQUIRED"})
