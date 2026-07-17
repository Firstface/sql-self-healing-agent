import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from sql_self_healing_agent.artifacts.artifact_store import ArtifactStore
from sql_self_healing_agent.core.models import UpstreamTaskEvent
from sql_self_healing_agent.memory.memory_retriever import MemoryRetriever
from sql_self_healing_agent.metadata.mock_metadata_provider import MockMetadataProvider
from sql_self_healing_agent.orchestrator.agentic_failed_event_processor import AgenticFailedEventProcessor, ProcessorDependencies
from sql_self_healing_agent.repair.repair_planner import RepairPlanner
from sql_self_healing_agent.repair.sql_generator import SQLGenerator
from sql_self_healing_agent.session.session_store import SessionStore

ROOT=Path(__file__).parents[1]


class AgenticFailedProcessorTest(unittest.TestCase):
    def test_dynamic_actions_reach_gate_and_candidate_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); sessions=root/".sessions"; memory=root/".memory"
            store=SessionStore(sessions); artifacts=ArtifactStore(sessions); provider=MockMetadataProvider(ROOT/"mocks/metadata/tables.json")
            event=UpstreamTaskEvent(id="task",status="FAILED",sql="SELECT user_id, pay_amt FROM dwd_order_detail WHERE date = ",error_message="Invalid column reference pay_amt")
            session=store.load_or_create_for_event(event); record=store.create_event_record(session,event); store.append_upstream_event(session,record); attempt=store.create_attempt(session,record)
            vocab=json.loads((ROOT/"sql_self_healing_agent/logs/keyword_vocab.json").read_text())
            deps=ProcessorDependencies(vocab,provider,MemoryRetriever(memory),RepairPlanner(provider),SQLGenerator())
            processor = AgenticFailedEventProcessor(deps,artifacts)
            from sql_self_healing_agent.agent.tools.tool_registry import ToolRegistry
            original_execute = ToolRegistry.execute
            calls = []
            def tracked_execute(registry, name, context, tool_input, run_state):
                calls.append(name)
                return original_execute(registry, name, context, tool_input, run_state)
            with patch.object(ToolRegistry, "execute", tracked_execute):
                result,context,state,executor=processor.run(event,session,attempt)
            self.assertEqual(result.status,"CANDIDATE_READY", f"{result=} {state=} observations={context.recent_observations} workspace={context.workspace} objects={executor.objects}")
            self.assertEqual(context.candidate.status,"READY")
            self.assertGreaterEqual(state.step_count,7)
            self.assertEqual([item.action_type for item in context.recent_observations[:-1]],["TOOL_CALL"]*6)
            self.assertIn("repair_plan",executor.objects)
            self.assertTrue(context.candidate.draft_artifact_ref)
            self.assertEqual(calls, ["ReadLogTool", "DiagnoseTool", "MetadataQueryTool", "MemoryRetrieveTool", "BuildRepairPlanTool", "GenerateCandidateTool"])
            self.assertGreaterEqual(len(processor.context_manager.snapshots), 2)
            artifact_names = {path.name for path in (sessions / session.session_id / "attempts" / attempt.attempt_id / "artifacts").iterdir()}
            self.assertIn("snapshot_0001.json", artifact_names)


    def test_unknown_diagnosis_may_run_one_governed_subagent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); sessions=root/".sessions"
            store=SessionStore(sessions); artifacts=ArtifactStore(sessions)
            event=UpstreamTaskEvent(id="unknown",status="FAILED",sql="SELECT 1",error_message="unclassified engine issue")
            session=store.load_or_create_for_event(event); record=store.create_event_record(session,event); store.append_upstream_event(session,record); attempt=store.create_attempt(session,record)
            vocab=json.loads((ROOT/"sql_self_healing_agent/logs/keyword_vocab.json").read_text())
            provider=MockMetadataProvider(ROOT/"mocks/metadata/tables.json")
            deps=ProcessorDependencies(vocab,provider,MemoryRetriever(root/".memory"),RepairPlanner(provider),SQLGenerator())
            result,context,state,executor=AgenticFailedEventProcessor(deps,artifacts).run(event,session,attempt)
            self.assertLessEqual(state.sub_agent_call_count,1)
            if state.sub_agent_call_count:
                self.assertIn("subagent_diagnosis",context.workspace)
