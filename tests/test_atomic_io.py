import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sql_self_healing_agent.core.atomic_io import write_json_atomic, write_text_atomic


class AtomicIOTest(unittest.TestCase):
    def test_atomic_json_and_text_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            json_path = root / "state.json"
            text_path = root / "candidate.sql"
            write_json_atomic(json_path, {"value": 1})
            write_text_atomic(text_path, "SELECT 1")
            self.assertEqual(json.loads(json_path.read_text()), {"value": 1})
            self.assertEqual(text_path.read_text(), "SELECT 1")
            self.assertEqual(list(root.glob("*.tmp")), [])

    def test_failed_replace_cleans_temporary_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            with patch(
                "sql_self_healing_agent.core.atomic_io.os.replace",
                side_effect=OSError("replace failed"),
            ):
                with self.assertRaises(OSError):
                    write_json_atomic(root / "state.json", {"value": 1})
            self.assertEqual(list(root.iterdir()), [])
