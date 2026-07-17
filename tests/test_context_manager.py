import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from sql_self_healing_agent.agent.context.context_manager import ContextCompactionError, ContextManager
from sql_self_healing_agent.agent.context.context_models import ContextSummary
from sql_self_healing_agent.agent.hooks.context_compression_hook import ContextCompressionHook
from sql_self_healing_agent.agent.models.candidate import CandidateState, GateFeedback
from sql_self_healing_agent.agent.models.context import AgentContext, WorkspaceValue
from sql_self_healing_agent.agent.models.execution_plan import build_initial_execution_plan
from sql_self_healing_agent.agent.models.observation import Observation
from sql_self_healing_agent.agent.models.run_state import AgentRunState
from sql_self_healing_agent.agent.models.subagent_models import SubAgentRequest
from sql_self_healing_agent.artifacts.artifact_store import ArtifactStore

NOW = datetime.now(timezone.utc).isoformat()


def make_context() -> AgentContext:
    return AgentContext(
        session_id="sess_1", attempt_id="attempt_1", event_key="evt_1",
        original_sql="SELECT pay_amt FROM orders WHERE ds='20260717'",
        error_message="column pay_amt not found",
        execution_plan=build_initial_execution_plan(),
        candidate=CandidateState(
            draft_sql="SELECT payment_amount FROM orders WHERE ds='20260717'",
            status="DRAFT",
            gate_feedback=[GateFeedback(gate_name="static", decision="REJECT", reason="keep static partition")],
        ),
    )


class ContextManagerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.manager = ContextManager(ArtifactStore(Path(self.tmp.name)), inline_char_limit=20, max_inline_chars=500)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_long_content_becomes_owned_artifact(self) -> None:
        context = make_context()
        value = self.manager.store_long_content(context, "log", "x" * 100, "RAW_LOG", "long log")
        self.assertEqual(value.summary, "long log")
        self.assertIsNotNone(value.artifact_ref)
        ref = self.manager._artifact_ref_from_workspace(value)
        self.assertEqual(self.manager.read_artifact(context, ref), "x" * 100)

    def test_main_subagent_and_gate_are_distinct_readonly_views(self) -> None:
        context = make_context()
        context.workspace["target_table"] = WorkspaceValue(status="AVAILABLE", summary="orders", updated_at=NOW)
        context.workspace["static_partition"] = WorkspaceValue(status="AVAILABLE", summary="ds='20260717'", updated_at=NOW)
        context.workspace["safe"] = WorkspaceValue(status="AVAILABLE", summary="summary", updated_at=NOW)
        state = AgentRunState(started_at=NOW)
        main = self.manager.prepare_for_main_agent(context, state)
        sub = self.manager.prepare_for_sub_agent(context, SubAgentRequest(task_name="diagnose", objective="inspect", context_refs=["safe"], expected_output_schema="Diagnosis"))
        gate = self.manager.prepare_for_gate(context)
        self.assertEqual(main.original_sql, context.original_sql)
        self.assertEqual(sub.inline_context, {"safe": "summary"})
        self.assertEqual(gate.target_table, "orders")
        self.assertEqual(gate.static_partition, "ds='20260717'")
        self.assertEqual(context.workspace["safe"].summary, "summary")

    def test_deterministic_trim_preserves_p0_and_cleans_memory(self) -> None:
        context = make_context()
        context.workspace["memory_temp_all"] = WorkspaceValue(status="AVAILABLE", summary="full contents", updated_at=NOW)
        context.workspace["memory_retrieval"] = WorkspaceValue(
            status="AVAILABLE",
            summary=json.dumps({"scanned_count": 3, "matched_count": 1, "discarded_count": 2, "matched": ["e1"], "matched_by": ["column_not_found"], "unmatched_full_body": "must disappear"}),
            updated_at=NOW,
        )
        duplicate = Observation(observation_id="o1", action_type="READ", status="FAILED", summary="same", created_at=NOW)
        context.recent_observations = [duplicate, duplicate.model_copy(update={"observation_id": "o2"})]
        original = context.original_sql
        candidate = context.candidate.draft_sql
        removed = self.manager.deterministic_trim(context)
        self.assertIn("memory_temp_all", removed)
        self.assertNotIn("unmatched_full_body", context.workspace["memory_retrieval"].summary)
        self.assertEqual(len(context.recent_observations), 1)
        self.assertEqual(context.original_sql, original)
        self.assertEqual(context.candidate.draft_sql, candidate)
        self.assertEqual(context.event_key, "evt_1")

    def test_summary_budget_is_independent_and_no_recursion(self) -> None:
        calls = []
        def summarize(payload, limits):
            calls.append(payload)
            return ContextSummary(
                current_goal="repair", confirmed_facts=[], unresolved_questions=[], important_artifact_refs=[],
                current_plan_step="read_log", candidate_status="DRAFT", gate_constraints=["keep static partition"],
            )
        manager = ContextManager(ArtifactStore(Path(self.tmp.name)), max_inline_chars=1, soft_token_limit=1, summary_callable=summarize)
        context = make_context()
        state = AgentRunState(started_at=NOW, llm_call_count=7, wall_time_ms=99)
        manager.compact_if_needed(context, state)
        manager.compact_if_needed(context, state)
        manager.compact_if_needed(context, state)
        self.assertEqual(len(calls), 2)
        self.assertEqual(state.llm_call_count, 7)
        self.assertEqual(state.wall_time_ms, 99)
        self.assertIs(manager.compact_if_needed(context, state, operation_type="CONTEXT_COMPACTION"), context)
        self.assertFalse(ContextCompressionHook().should_compact("CONTEXT_COMPACTION"))

    def test_invalid_summary_falls_back_without_changing_gate_facts(self) -> None:
        def forged(payload, limits):
            return ContextSummary(
                current_goal="repair", confirmed_facts=["invented"], unresolved_questions=[],
                important_artifact_refs=["forged"], current_plan_step="wrong", candidate_status="READY", gate_constraints=[],
            )
        manager = ContextManager(ArtifactStore(Path(self.tmp.name)), max_inline_chars=1, soft_token_limit=1, summary_callable=forged)
        context = make_context()
        manager.compact_if_needed(context, AgentRunState(started_at=NOW))
        self.assertNotIn("context_summary", context.workspace)
        self.assertEqual(context.candidate.status, "DRAFT")
        self.assertEqual(context.candidate.gate_feedback[0].reason, "keep static partition")

    def test_missing_critical_context_requires_human(self) -> None:
        context = make_context().model_copy(update={"original_sql": ""})
        with self.assertRaises(ContextCompactionError):
            self.manager.compact_if_needed(context, AgentRunState(started_at=NOW))
