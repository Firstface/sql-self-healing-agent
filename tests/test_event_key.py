import unittest

from sql_self_healing_agent.core.models import UpstreamTaskEvent
from sql_self_healing_agent.session.event_key_builder import build_event_key


class EventKeyTest(unittest.TestCase):
    def test_normalizes_whitespace_but_keeps_event_fields(self) -> None:
        first = UpstreamTaskEvent(
            id="task", status="FAILED", sql="SELECT  1\nFROM t", error_message=" x\r\n"
        )
        same = first.model_copy(update={"sql": " SELECT 1 FROM t ", "error_message": "x"})
        changed = first.model_copy(update={"log_path": "other.log"})
        self.assertEqual(build_event_key(first), build_event_key(same))
        self.assertNotEqual(build_event_key(first), build_event_key(changed))
