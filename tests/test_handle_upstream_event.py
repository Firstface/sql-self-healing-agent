import json
import os
import tempfile
import unittest
from pathlib import Path

from sql_self_healing_agent.core.models import UpstreamTaskEvent
from sql_self_healing_agent.orchestrator.repair_agent_service import RepairAgentService


class HandleUpstreamEventTest(unittest.TestCase):
    def test_failed_event_returns_m1_stub_and_persists_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            sessions_dir = Path(temporary_directory) / "sessions"
            service = RepairAgentService(sessions_dir)
            event = UpstreamTaskEvent(
                id="task_123",
                status="FAILED",
                sql="SELECT missing_column FROM example_table",
                error_message="Task failed",
                log_path="example.log",
            )

            result = service.handle_upstream_event(event)

            self.assertEqual(result.status, "HUMAN_REQUIRED")
            session_dir = sessions_dir / "sess_task_123"
            self.assertTrue((session_dir / "session.json").exists())
            self.assertTrue(
                (session_dir / "attempts" / "attempt_001.json").exists()
            )
            self.assertTrue((session_dir / "trace.jsonl").exists())

            service.handle_upstream_event(event)
            session = json.loads((session_dir / "session.json").read_text())
            self.assertEqual(len(session["upstream_events"]), 1)
            self.assertEqual(session["attempt_ids"], ["attempt_001"])
            self.assertEqual(len((session_dir / "trace.jsonl").read_text().splitlines()), 3)


    def test_success_idempotency_ignores_log_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            sessions_dir = Path(temporary_directory) / "sessions"
            service = RepairAgentService(sessions_dir)
            first = UpstreamTaskEvent(
                id="task_success", status="SUCCESS", sql="SELECT 1", log_path="first.log"
            )
            second = first.model_copy(update={"log_path": "second.log"})
            self.assertEqual(service.handle_upstream_event(first).status, "SUCCESS_ACK")
            self.assertEqual(service.handle_upstream_event(second).status, "SUCCESS_ACK")
            session_dir = sessions_dir / "sess_task_success"
            session = json.loads((session_dir / "session.json").read_text())
            self.assertEqual(len(session["upstream_events"]), 1)
            self.assertEqual(session["attempt_ids"], [])
            self.assertEqual(len((session_dir / "trace.jsonl").read_text().splitlines()), 1)
