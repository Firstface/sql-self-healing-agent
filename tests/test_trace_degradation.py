import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sql_self_healing_agent.trace.trace_writer import TraceWriter


class TraceDegradationTest(unittest.TestCase):
    def test_trace_write_failure_does_not_break_business_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            writer=TraceWriter(Path(tmp))
            with patch("pathlib.Path.open", side_effect=OSError("disk fail")):
                writer.emit("session","operation_started","LLM_CALL",{"safe":"summary"})
