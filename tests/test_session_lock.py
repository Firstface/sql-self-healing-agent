import tempfile
import unittest
from pathlib import Path

from sql_self_healing_agent.session.session_lock import SessionLock, SessionLockTimeout


class SessionLockTest(unittest.TestCase):
    def test_timeout_and_exception_release(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with SessionLock(directory, "task", timeout_seconds=0.1):
                with self.assertRaises(SessionLockTimeout):
                    with SessionLock(directory, "task", timeout_seconds=0.02):
                        pass
            try:
                with SessionLock(directory, "task", timeout_seconds=0.1):
                    raise RuntimeError("boom")
            except RuntimeError:
                pass
            self.assertEqual(list(Path(directory).glob("*.lock")), [])
