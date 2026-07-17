import tempfile
import unittest
from pathlib import Path

from sql_self_healing_agent.memory.keyword_list import KeywordList
from sql_self_healing_agent.memory.memory_models import ConfirmedExperienceInput
from sql_self_healing_agent.memory.memory_retriever import MemoryRetriever
from sql_self_healing_agent.memory.memory_store import MemoryStore
from sql_self_healing_agent.memory.memory_writer import MemoryWriter


class MemoryFrontmatterTest(unittest.TestCase):
    def test_keyword_list_flattens_values_only(self) -> None:
        keywords = KeywordList().values
        self.assertIn("column_not_found", keywords)
        self.assertIn("unknown", keywords)
        self.assertNotIn("COLUMN_NOT_FOUND", keywords)

    def test_write_markdown_multi_keyword_and_retrieve_deduplicates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            writer = MemoryWriter(directory)
            data = ConfirmedExperienceInput(session_id="s", attempt_id="a", original_sql="SELECT bad", confirmed_sql="SELECT good", diagnosed_keywords=["column_not_found", "missing_field"], description="missing column", modification_summary="replace bad with good")
            experience_id = writer.write_confirmed_experience(data)
            path = Path(directory) / "experiences" / f"{experience_id}.md"
            self.assertTrue(path.exists())
            self.assertEqual(set(MemoryStore(directory).read_frontmatter(experience_id).model_dump()), {"keyword", "description"})
            index = MemoryStore(directory).load_index()
            self.assertEqual(index["column_not_found"], [experience_id])
            result = MemoryRetriever(directory).retrieve_keywords(["column_not_found", "missing_field"])
            self.assertEqual(len(result.matched_experiences), 1)
            self.assertEqual(set(result.matched_experiences[0].matched_by), {"column_not_found", "missing_field"})
            self.assertNotIn("fingerprint", path.read_text().casefold())

    def test_unknown_scans_all_frontmatter_and_discards_unmatched_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            writer = MemoryWriter(directory)
            for index, description in enumerate(("payment column issue", "partition issue", "syntax issue")):
                writer.write_confirmed_experience(ConfirmedExperienceInput(session_id="s", attempt_id=str(index), original_sql="x", confirmed_sql="y", diagnosed_keywords=["column_not_found"], description=description, modification_summary="fix"))
            result = MemoryRetriever(directory).retrieve_keywords([], "payment")
            self.assertEqual(result.scanned_count, 3)
            self.assertEqual(len(result.matched_experiences), 1)
            self.assertEqual(result.discarded_count, 2)

    def test_logical_key_is_idempotent_and_index_can_rebuild(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            writer = MemoryWriter(directory)
            data = ConfirmedExperienceInput(session_id="s", attempt_id="a", original_sql="x", confirmed_sql="y", diagnosed_keywords=["bad_keyword"], description="d", modification_summary="m")
            first = writer.write_confirmed_experience(data)
            second = writer.write_confirmed_experience(data)
            self.assertEqual(first, second)
            self.assertEqual(MemoryStore(directory).load_index(), {"unknown": [first]})
            (Path(directory) / "index/keyword_index.json").write_text("{}")
            self.assertEqual(MemoryStore(directory).rebuild_index(), {"unknown": [first]})


class MemoryRetrievalPolicyTest(unittest.TestCase):
    def test_unknown_scan_budget_warns(self) -> None:
        from sql_self_healing_agent.memory.memory_retriever import MemoryRetriever
        with tempfile.TemporaryDirectory() as tmp:
            store=MemoryStore(tmp)
            for index in range(3):
                store.save_markdown(f"exp_{index}", f"---\nkeyword:\n  - unknown\ndescription: error item {index}\n---\nbody")
            result=MemoryRetriever(tmp,unknown_scan_budget=2).retrieve_keywords(["unknown"],"error")
            self.assertEqual(result.scanned_count,2)
            self.assertIn("UNKNOWN_SCAN_BUDGET_EXCEEDED",result.warnings)
