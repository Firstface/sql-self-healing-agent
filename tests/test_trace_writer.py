import json
import tempfile
import unittest
from pathlib import Path

from sql_self_healing_agent.trace.trace_writer import TraceWriter


class TraceWriterTest(unittest.TestCase):
    def test_emit_appends_one_json_event_per_line(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            writer = TraceWriter(Path(temporary_directory) / "sessions")
            writer.emit("sess_task", "attempt_created", "orchestrator", {})
            writer.emit("sess_task", "human_required_returned", "orchestrator", {})

            lines = writer.trace_path("sess_task").read_text().splitlines()
            self.assertEqual(len(lines), 2)
            self.assertEqual(json.loads(lines[0])["event_type"], "attempt_created")
            self.assertEqual(
                json.loads(lines[1])["event_type"], "human_required_returned"
            )
