import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from sql_self_healing_agent.cli import run_memory_consolidate, run_memory_list
from sql_self_healing_agent.memory.memory_store import MemoryStore
from tests.test_memory_m4 import make_experience


class MemoryCLITest(unittest.TestCase):
    def test_memory_list_supports_filters(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            previous = Path.cwd()
            os.chdir(directory)
            try:
                MemoryStore().save(make_experience("exp_cli"))
                output = io.StringIO()
                with redirect_stdout(output):
                    run_memory_list("COLUMN_NOT_FOUND", "column_not_found")
                self.assertIn("exp_cli", output.getvalue())
                output = io.StringIO()
                with redirect_stdout(output):
                    run_memory_list("TYPE_MISMATCH", None)
                self.assertIn("(empty)", output.getvalue())
            finally:
                os.chdir(previous)

    def test_memory_consolidate_prints_proposal_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            previous = Path.cwd()
            os.chdir(directory)
            try:
                MemoryStore().save(make_experience("exp_cli_consolidate"))
                output = io.StringIO()
                with redirect_stdout(output):
                    run_memory_consolidate()
                self.assertIn("Memory consolidation finished", output.getvalue())
                self.assertIn("proposal_path:", output.getvalue())
            finally:
                os.chdir(previous)


if __name__ == "__main__":
    unittest.main()
