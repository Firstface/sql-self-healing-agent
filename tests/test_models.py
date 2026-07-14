import unittest

from pydantic import ValidationError

from sql_self_healing_agent.core.models import AgentExternalResult, UpstreamTaskEvent
from sql_self_healing_agent.session.session_models import RepairSession


class StrictModelsTest(unittest.TestCase):
    def test_external_models_reject_unknown_fields(self) -> None:
        with self.assertRaises(ValidationError):
            UpstreamTaskEvent.model_validate(
                {"id": "task_123", "status": "FAILED", "sql": "SELECT 1", "unexpected": 1}
            )
        with self.assertRaises(ValidationError):
            AgentExternalResult.model_validate({"status": "SUCCESS_ACK", "unexpected": 1})

    def test_persisted_models_reject_unknown_fields(self) -> None:
        with self.assertRaises(ValidationError):
            RepairSession.model_validate(
                {
                    "session_id": "sess_task_123",
                    "task_id": "task_123",
                    "original_sql": "SELECT 1",
                    "trace_path": "trace.jsonl",
                    "artifact_dir": "artifacts",
                    "created_at": "2026-07-14T00:00:00Z",
                    "updated_at": "2026-07-14T00:00:00Z",
                    "unexpected": 1,
                }
            )
