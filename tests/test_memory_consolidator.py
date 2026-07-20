import tempfile
import unittest
from pathlib import Path

from sql_self_healing_agent.memory.memory_consolidator import MemoryConsolidator
from sql_self_healing_agent.memory.memory_store import MemoryStore
from sql_self_healing_agent.memory.memory_writer import MemoryWriter
from sql_self_healing_agent.memory.memory_models import ConfirmedExperienceInput


class MemoryConsolidatorTest(unittest.TestCase):
    def test_dry_run_and_apply_merge_equivalent_confirmed_experiences(self):
        with tempfile.TemporaryDirectory() as tmp:
            writer = MemoryWriter(tmp)
            for i in range(2):
                writer.write_confirmed_experience(ConfirmedExperienceInput(session_id=f"s{i}", attempt_id="a", original_sql=f"select bad_{i} from t", confirmed_sql=f"select good_{i} from t", diagnosed_keywords=["column_not_found"], description="missing column", modification_summary="replace invalid column using metadata", error_summary="not found"))
            store = MemoryStore(tmp)
            dry = MemoryConsolidator(store).consolidate()
            self.assertEqual(dry.duplicate_group_count, 1)
            self.assertEqual(len(store.list_experience_ids()), 2)
            applied = MemoryConsolidator(store).consolidate(dry_run=False)
            self.assertEqual(applied.merged_count, 1)
            self.assertEqual(len(store.list_experience_ids()), 1)
            body = store.read_body(store.list_experience_ids()[0])
            self.assertIn("s0:a", body)
            self.assertIn("s1:a", body)
            self.assertEqual(sum(len(ids) for ids in store.load_index().values()), 1)
            rerun = MemoryConsolidator(store).consolidate(dry_run=False)
            self.assertEqual(rerun.merged_count, 0)

    def test_different_repair_shapes_are_not_merged(self):
        with tempfile.TemporaryDirectory() as tmp:
            writer = MemoryWriter(tmp)
            for i, change in enumerate(("replace invalid column", "add cast")):
                writer.write_confirmed_experience(ConfirmedExperienceInput(session_id=f"s{i}", attempt_id="a", original_sql="select x", confirmed_sql="select y", diagnosed_keywords=["column_not_found"], description="error", modification_summary=change))
            report = MemoryConsolidator(MemoryStore(tmp)).consolidate()
            self.assertEqual(report.duplicate_group_count, 0)
