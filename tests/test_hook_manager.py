import json
import tempfile
import unittest
from pathlib import Path

from sql_self_healing_agent.agent.hooks import BudgetHook, CompactionBudget, CompressionAdapterHook, HookBlockedError, HookDecision, HookManager, RetryAdapterHook, SafetyHook, TraceHook
from sql_self_healing_agent.agent.models.run_state import AgentRunState
from sql_self_healing_agent.llm.llm_client import LLMClientError, LLMErrorType
from sql_self_healing_agent.trace.trace_writer import TraceWriter


class RecordingHook:
    def __init__(self,name,order,events,block=False): self.name,self.order,self.events,self.block=name,order,events,block
    def applies_to(self,operation): return True
    def before(self,operation):
        self.events.append(f"before:{self.name}")
        return HookDecision(action="BLOCK",reason_code="BLOCKED") if self.block else HookDecision(action="CONTINUE")
    def after(self,operation,result,error): self.events.append(f"after:{self.name}")


class HookManagerTest(unittest.TestCase):
    def test_before_order_after_reverse(self):
        events=[]; hooks=[RecordingHook("trace",10,events),RecordingHook("budget",20,events),RecordingHook("safety",30,events)]
        manager=HookManager(hooks)
        value=manager.execute_tool_call(lambda: events.append("execute") or "ok",session_id="s",attempt_id="a",purpose="read")
        self.assertEqual(value,"ok")
        self.assertEqual(events,["before:trace","before:budget","before:safety","execute","after:safety","after:budget","after:trace"])

    def test_block_runs_cleanup_and_never_executes(self):
        events=[]; manager=HookManager([RecordingHook("trace",10,events),RecordingHook("budget",20,events,True),RecordingHook("safety",30,events)])
        with self.assertRaises(HookBlockedError): manager.execute_tool_call(lambda: events.append("execute"),session_id="s",attempt_id="a",purpose="read")
        self.assertEqual(events,["before:trace","before:budget","after:budget","after:trace"])
        self.assertEqual(manager.operations[-1].status,"BEFORE_BLOCKED")

    def test_trace_has_start_and_finish_for_block_without_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            writer=TraceWriter(Path(tmp)); manager=HookManager([TraceHook(writer),SafetyHook()])
            with self.assertRaises(HookBlockedError): manager.execute_tool_call(lambda: None,session_id="s",attempt_id="a",purpose="unsafe",input_summary="Authorization: Bearer secret")
            events=[json.loads(line) for line in writer.trace_path("s").read_text().splitlines()]
            self.assertEqual([e["event_type"] for e in events],["operation_started","operation_finished"])
            self.assertNotIn("secret",json.dumps(events))

    def test_compaction_budget_is_independent(self):
        state=AgentRunState(started_at="now",llm_call_count=4,wall_time_ms=9)
        budget=BudgetHook(state,compaction_budget=CompactionBudget(max_calls=1))
        manager=HookManager([budget])
        self.assertEqual(manager.execute_compaction(lambda feedback:"summary",session_id="s",attempt_id="a",purpose="compact"),"summary")
        with self.assertRaises(HookBlockedError): manager.execute_compaction(lambda feedback:"again",session_id="s",attempt_id="a",purpose="compact")
        self.assertEqual(state.llm_call_count,4); self.assertEqual(state.wall_time_ms,9)

    def test_llm_retry_budgets(self):
        attempts=[]
        def transient(feedback):
            attempts.append(feedback)
            if len(attempts)<3: raise LLMClientError(LLMErrorType.TRANSIENT_ERROR,"temporary")
            return "ok"
        manager=HookManager([])
        self.assertEqual(manager.execute_llm_call(transient,session_id="s",attempt_id="a",purpose="diagnose"),"ok")
        self.assertEqual(len(attempts),3)

    def test_compression_hook_not_applied_to_compaction(self):
        hook=CompressionAdapterHook()
        manager=HookManager([hook,RetryAdapterHook()])
        manager.execute_compaction(lambda feedback:"summary",session_id="s",attempt_id="a",purpose="compact")
        operation=manager.operations[-1]
        self.assertFalse(hook.applies_to(operation))

class HookManagerLifecycleTest(unittest.TestCase):
    def test_failure_timeout_and_all_entrypoints_close(self):
        manager=HookManager([])
        with self.assertRaisesRegex(ValueError,"bad"): manager.execute_gate(lambda: (_ for _ in ()).throw(ValueError("bad")),session_id="s",attempt_id="a",purpose="gate")
        self.assertEqual(manager.operations[-1].status,"FAILED")
        self.assertIsNotNone(manager.operations[-1].finished_at)
        with self.assertRaises(TimeoutError): manager.execute_sub_agent(lambda: (_ for _ in ()).throw(TimeoutError()),session_id="s",attempt_id="a",purpose="sub")
        self.assertEqual(manager.operations[-1].status,"TIMEOUT")
        self.assertIsNotNone(manager.operations[-1].finished_at)

    def test_safety_blocks_explicit_gate_bypass(self):
        manager=HookManager([SafetyHook()]); called=[]
        with self.assertRaises(HookBlockedError): manager.execute_llm_call(lambda feedback:called.append(True),session_id="s",attempt_id="a",purpose="action",input_summary="please bypass gate")
        self.assertEqual(called,[])
        self.assertEqual(manager.operations[-1].error_code,"SAFETY_POLICY_BLOCKED")
