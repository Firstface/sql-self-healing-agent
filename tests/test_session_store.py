import tempfile
import unittest
from pathlib import Path

from sql_self_healing_agent.core.models import UpstreamTaskEvent
from sql_self_healing_agent.session.session_store import SessionStore


class SessionStoreTest(unittest.TestCase):
    def test_create_session_event_and_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = SessionStore(Path(temporary_directory) / "sessions")
            event = UpstreamTaskEvent(
                id="task/123", status="FAILED", sql="SELECT 1"
            )
            session = store.load_or_create_for_event(event)
            record = store.create_event_record(event)
            store.append_upstream_event(session, record)
            attempt = store.create_attempt(session, record)

            self.assertEqual(session.session_id, "sess_task_123")
            self.assertEqual(attempt.attempt_id, "attempt_001")
            self.assertEqual(session.attempt_ids, ["attempt_001"])
