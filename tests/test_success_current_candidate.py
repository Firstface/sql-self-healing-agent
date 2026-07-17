import tempfile
import unittest
from pathlib import Path

from sql_self_healing_agent.core.enums import AttemptStatus, SessionStatus
from sql_self_healing_agent.core.models import UpstreamTaskEvent
from sql_self_healing_agent.orchestrator.repair_agent_service import RepairAgentService
from sql_self_healing_agent.session.session_models import RepairAttempt
from sql_self_healing_agent.core.time_utils import utc_now_iso


class SuccessCurrentCandidateTest(unittest.TestCase):
    def test_success_never_matches_historical_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = RepairAgentService(Path(directory) / ".sessions")
            session = service.session_store.load_or_create_for_event(
                UpstreamTaskEvent(id="task", status="SUCCESS", sql="SELECT current")
            )
            now = utc_now_iso()
            old = RepairAttempt(
                attempt_id="attempt_001",
                attempt_no=1,
                status=AttemptStatus.SQL_READY,
                source_event_key="old",
                input_event_id="old",
                input_failed_sql="SELECT broken",
                sql_candidate="SELECT old",
                created_at=now,
                updated_at=now,
            )
            current = old.model_copy(update={
                "attempt_id": "attempt_002",
                "attempt_no": 2,
                "source_event_key": "current",
                "input_event_id": "current",
                "sql_candidate": "SELECT current",
            })
            session.attempt_ids = [old.attempt_id, current.attempt_id]
            session.latest_sql_candidate = current.sql_candidate
            session.latest_sql_candidate_attempt_id = current.attempt_id
            session.status = SessionStatus.SQL_READY_PENDING_UPSTREAM
            service.session_store.save_attempt(session, old)
            service.session_store.save_attempt(session, current)
            service.session_store.save_session(session)

            result = service.handle_upstream_event(
                UpstreamTaskEvent(id="task", status="SUCCESS", sql="SELECT old")
            )
            reloaded = service.session_store.load_for_task("task")
            self.assertEqual(result.status, "SUCCESS_ACK")
            self.assertIsNone(reloaded.confirmed_attempt_id)
            self.assertEqual(
                service.session_store.load_attempt(reloaded, old.attempt_id).status,
                AttemptStatus.SQL_READY,
            )
