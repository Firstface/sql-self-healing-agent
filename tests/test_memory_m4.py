import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sql_self_healing_agent.core.enums import DiagnosedErrorType, ExperienceStatus
from sql_self_healing_agent.core.time_utils import utc_now_iso
from sql_self_healing_agent.diagnostics.diagnosis_models import DiagnosisResult
from sql_self_healing_agent.memory.memory_consolidator import MemoryConsolidator
from sql_self_healing_agent.memory.memory_models import Experience, RepairStep
from sql_self_healing_agent.memory.memory_retriever import MemoryRetriever
from sql_self_healing_agent.memory.memory_store import MemoryStore
from sql_self_healing_agent.metadata.metadata_models import MetadataSnapshot
from sql_self_healing_agent.metadata.mock_metadata_provider import MockMetadataProvider
from sql_self_healing_agent.metadata.sql_table_extractor import SQLTableExtractor
from sql_self_healing_agent.repair.repair_models import RepairPlannerInput
from sql_self_healing_agent.repair.repair_planner import RepairPlanner
from sql_self_healing_agent.logs.log_models import LogDigest

PROJECT_ROOT = Path(__file__).parents[1]


def make_experience(
    experience_id: str,
    *,
    status: ExperienceStatus = ExperienceStatus.ACTIVE,
    fingerprint: str = "COLUMN_NOT_FOUND:pay_amt:hive",
    confirmed_sql: str = "SELECT payment_amount FROM dwd_order_detail",
    verified_count: int = 1,
    failed_count: int = 0,
) -> Experience:
    now = utc_now_iso()
    return Experience(
        experience_id=experience_id,
        status=status,
        source_session_id=f"sess_{experience_id}",
        source_attempt_id="attempt_001",
        task_id=experience_id,
        diagnosed_error_type=DiagnosedErrorType.COLUMN_NOT_FOUND,
        diagnosed_keywords=["column_not_found", "missing_field"],
        error_fingerprint=fingerprint,
        primary_entity="pay_amt",
        original_sql="SELECT pay_amt FROM dwd_order_detail",
        failed_sql="SELECT pay_amt FROM dwd_order_detail",
        confirmed_sql=confirmed_sql,
        repair_steps=[
            RepairStep(
                step_no=1,
                description="replace verified field",
                before_fragment="pay_amt",
                after_fragment="payment_amount",
            )
        ],
        verified_count=verified_count,
        failed_count=failed_count,
        created_at=now,
        updated_at=now,
        last_verified_at=now,
    )


class MemoryIndexAndRetrievalTest(unittest.TestCase):
    def test_save_builds_both_indices_and_filters_deprecated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = MemoryStore(directory)
            active = make_experience("exp_active")
            deprecated = make_experience(
                "exp_deprecated", status=ExperienceStatus.DEPRECATED
            )
            store.save(active)
            store.save(deprecated)
            self.assertEqual(
                store.lookup_fingerprint(active.error_fingerprint), ["exp_active"]
            )
            self.assertEqual(
                store.lookup_keyword("column_not_found"), ["exp_active"]
            )
            self.assertTrue(
                (Path(directory) / "index/keyword_index/column_not_found.json").exists()
            )

    def test_retriever_scores_fingerprint_before_keyword_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = MemoryStore(directory)
            exact = make_experience("exp_exact", verified_count=2)
            keyword_only = make_experience(
                "exp_keyword", fingerprint="COLUMN_NOT_FOUND:other:hive"
            )
            store.save(exact)
            store.save(keyword_only)
            diagnosis = DiagnosisResult(
                diagnosed_error_type=DiagnosedErrorType.COLUMN_NOT_FOUND,
                diagnosed_keywords=["column_not_found"],
                error_fingerprint=exact.error_fingerprint,
                confidence=0.9,
                is_repairable=True,
                primary_entity="pay_amt",
            )
            provider = MockMetadataProvider(PROJECT_ROOT / "mocks/metadata/tables.json")
            snapshot = MetadataSnapshot(
                extraction_result=SQLTableExtractor().extract(exact.failed_sql),
                tables=[provider.get_table_metadata("dwd_order_detail")],
                created_at=utc_now_iso(),
            )
            result = MemoryRetriever(directory).retrieve(
                diagnosis, exact.failed_sql, snapshot
            )
            self.assertEqual(result.retrieved[0].experience_id, "exp_exact")
            self.assertIn("fingerprint_match", result.retrieved[0].match_reasons)
            self.assertEqual(result.fingerprint_matches, ["exp_exact"])
            self.assertEqual(
                set(result.keyword_matches), {"exp_exact", "exp_keyword"}
            )

    def test_planner_references_memory_only_after_metadata_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = MemoryStore(directory)
            experience = make_experience("exp_memory")
            store.save(experience)
            diagnosis = DiagnosisResult(
                diagnosed_error_type=DiagnosedErrorType.COLUMN_NOT_FOUND,
                diagnosed_keywords=["column_not_found"],
                error_fingerprint=experience.error_fingerprint,
                confidence=0.9,
                is_repairable=True,
                primary_entity="pay_amt",
            )
            provider = MockMetadataProvider(PROJECT_ROOT / "mocks/metadata/tables.json")
            snapshot = MetadataSnapshot(
                extraction_result=SQLTableExtractor().extract(experience.failed_sql),
                tables=[provider.get_table_metadata("dwd_order_detail")],
                created_at=utc_now_iso(),
            )
            memory = MemoryRetriever(directory).retrieve(
                diagnosis, experience.failed_sql, snapshot
            )
            plan = RepairPlanner(provider).plan(
                RepairPlannerInput(
                    failed_sql=experience.failed_sql,
                    diagnosis=diagnosis,
                    log_digest=LogDigest(log_readable=True),
                    metadata_snapshot=snapshot,
                    memory_retrieval=memory,
                )
            )
            self.assertEqual(plan.referenced_experience_ids, ["exp_memory"])
            self.assertEqual(plan.actions[0].replacement_fragment, "payment_amount")

    def test_failure_feedback_updates_existing_experience_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = MemoryStore(directory)
            store.save(make_experience("exp_failed"))
            store.record_failure(["exp_failed", "missing"], "still failed")
            experience = store.get("exp_failed")
            self.assertEqual(experience.failed_count, 1)
            self.assertEqual(experience.last_failed_reason, "still failed")
            self.assertEqual(len(store.list_experiences()), 1)
            self.assertFalse((Path(directory) / "negative").exists())

    def test_failed_atomic_index_swap_preserves_old_index(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = MemoryStore(directory)
            store.save(make_experience("exp_one"))
            before = store.lookup_keyword("column_not_found")
            real_replace = os.replace

            def fail_new_index(source, target):
                if Path(target) == store.index_dir and ".index_tmp_" in str(source):
                    raise OSError("swap failed")
                real_replace(source, target)

            with patch("sql_self_healing_agent.memory.memory_store.os.replace", side_effect=fail_new_index):
                with self.assertRaises(OSError):
                    store.rebuild_indices()
            self.assertEqual(store.lookup_keyword("column_not_found"), before)
            self.assertEqual(list(Path(directory).glob(".index_tmp_*")), [])
            self.assertEqual(list(Path(directory).glob(".index_backup_*")), [])


class MemoryConsolidationTest(unittest.TestCase):
    def test_consolidation_writes_allowed_proposal_and_rebuilds_indices(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = MemoryStore(directory)
            store.save(make_experience("exp_a"))
            store.save(make_experience("exp_b"))
            store.save(
                make_experience(
                    "exp_conflict",
                    confirmed_sql="SELECT another_column FROM dwd_order_detail",
                )
            )
            store.save(
                make_experience(
                    "exp_update", fingerprint="COLUMN_NOT_FOUND:x:hive", failed_count=2
                )
            )
            deprecated = make_experience(
                "exp_old",
                status=ExperienceStatus.DEPRECATED,
                fingerprint="COLUMN_NOT_FOUND:old:hive",
            )
            store.save(deprecated)
            proposal, path, counts = MemoryConsolidator(directory).consolidate()
            self.assertTrue(path.exists())
            actions = {action.action for action in proposal.actions}
            self.assertTrue(
                {"MERGE", "MARK_CONFLICT", "MARK_DEPRECATED", "UPDATE_CARD"}
                <= actions
            )
            self.assertNotIn("DELETE", actions)
            self.assertNotIn("AUTO_ADD_KEYWORD", actions)
            self.assertGreater(counts["scanned"], 0)
            self.assertEqual(
                store.lookup_fingerprint(deprecated.error_fingerprint), []
            )
            saved = json.loads(path.read_text())
            self.assertEqual(saved["proposal_id"], proposal.proposal_id)


if __name__ == "__main__":
    unittest.main()

class MemoryServiceReuseTest(unittest.TestCase):
    def test_service_persists_retrieval_trace_and_referenced_experience(self) -> None:
        from sql_self_healing_agent.core.models import UpstreamTaskEvent
        from sql_self_healing_agent.orchestrator.repair_agent_service import RepairAgentService
        from tests.fakes import FakeLLMClient

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            memory_dir = root / "memory_store"
            MemoryStore(memory_dir).save(make_experience("exp_service"))
            service = RepairAgentService(
                root / "sessions",
                llm_client=FakeLLMClient(),
                metadata_path=PROJECT_ROOT / "mocks/metadata/tables.json",
                memory_dir=memory_dir,
            )
            result = service.handle_upstream_event(
                UpstreamTaskEvent(
                    id="task_memory_reuse",
                    status="FAILED",
                    sql="SELECT user_id, pay_amt FROM dwd_order_detail WHERE date = ",
                    error_message="Task failed, see logs for details",
                    log_path=str(PROJECT_ROOT / "mocks/logs/task_123_round_1.log"),
                )
            )
            self.assertEqual(result.status, "SQL_READY")
            artifacts = root / "sessions/sess_task_memory_reuse/artifacts/attempt_001"
            retrieval = json.loads((artifacts / "memory_retrieval.json").read_text())
            plan = json.loads((artifacts / "repair_plan.json").read_text())
            self.assertEqual(retrieval["retrieved"][0]["experience_id"], "exp_service")
            self.assertEqual(plan["referenced_experience_ids"], ["exp_service"])
            trace = (root / "sessions/sess_task_memory_reuse/trace.jsonl").read_text()
            self.assertIn("memory_retrieval_started", trace)
            self.assertIn("memory_retrieval_finished", trace)

class MemoryScenarioReuseTest(unittest.TestCase):
    def test_similar_missing_column_scenario_reuses_memory_and_writes_new_success(self) -> None:
        from sql_self_healing_agent.mock_external_system.mock_upstream_event_executor import MockUpstreamEventExecutor
        from sql_self_healing_agent.mock_external_system.mock_upstream_event_runner import MockUpstreamEventRunner
        from sql_self_healing_agent.mock_external_system.mock_upstream_models import MockScenario
        from sql_self_healing_agent.orchestrator.repair_agent_service import RepairAgentService
        from tests.fakes import FakeLLMClient

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            memory_dir = root / "memory_store"
            MemoryStore(memory_dir).save(make_experience("exp_seed"))
            scenario = MockScenario.model_validate_json(
                (PROJECT_ROOT / "mocks/scenarios/similar_missing_column_with_memory.json").read_text()
            )
            scenario.rounds[0].log_path = str(
                PROJECT_ROOT / "mocks/logs/task_123_round_1.log"
            )
            service = RepairAgentService(
                root / "sessions",
                llm_client=FakeLLMClient(),
                metadata_path=PROJECT_ROOT / "mocks/metadata/tables.json",
                allow_medium_risk=scenario.allow_medium_risk,
                memory_dir=memory_dir,
            )
            result = MockUpstreamEventRunner(
                service, MockUpstreamEventExecutor()
            ).run(scenario)
            self.assertEqual(result.status, "MOCK_SUCCESS")
            experiences = MemoryStore(memory_dir).list_experiences()
            self.assertEqual(len(experiences), 2)
            plan_path = (
                root
                / "sessions"
                / f"sess_{scenario.task_id}"
                / "artifacts/attempt_001/repair_plan.json"
            )
            plan = json.loads(plan_path.read_text())
            self.assertEqual(plan["referenced_experience_ids"], ["exp_seed"])
