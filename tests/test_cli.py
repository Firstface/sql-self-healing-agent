import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from sql_self_healing_agent.cli import run_handle_upstream_event
from sql_self_healing_agent.core.models import AgentExternalResult


class CLITest(unittest.TestCase):
    def test_human_required_response_retains_null_sql(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            event_path = Path(temporary_directory) / "event.json"
            event_path.write_text(
                json.dumps({"id": "task_123", "status": "FAILED", "sql": "SELECT 1"})
            )
            output = io.StringIO()
            with patch("sql_self_healing_agent.cli.RepairAgentService") as service:
                service.return_value.handle_upstream_event.return_value = AgentExternalResult(
                    status="HUMAN_REQUIRED", message="M1 skeleton only"
                )
                with redirect_stdout(output):
                    run_handle_upstream_event(str(event_path))
            self.assertEqual(
                json.loads(output.getvalue()),
                {"status": "HUMAN_REQUIRED", "sql": None, "message": "M1 skeleton only"},
            )
