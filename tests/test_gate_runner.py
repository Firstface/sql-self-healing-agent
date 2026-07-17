import unittest
from datetime import datetime, timezone

from sql_self_healing_agent.agent.gates.gate_models import GateRequest, GateResult, StaticGateOutcome
from sql_self_healing_agent.agent.gates.gate_runner import GateRunner
from sql_self_healing_agent.agent.gates.risk_aggregator import aggregate_results
from sql_self_healing_agent.agent.gates.static_validation_gate import StaticValidationGate
from sql_self_healing_agent.agent.models.candidate import CandidateState
from sql_self_healing_agent.agent.models.context import AgentContext
from sql_self_healing_agent.agent.models.execution_plan import build_initial_execution_plan
from sql_self_healing_agent.agent.models.run_state import AgentRunState
from sql_self_healing_agent.core.enums import DiagnosedErrorType, RiskLevel
from sql_self_healing_agent.diagnostics.diagnosis_models import DiagnosisResult
from sql_self_healing_agent.repair.repair_models import RepairAction, RepairActionType, RepairPlan, SQLDiffSummary, ValidationResult

NOW = datetime.now(timezone.utc).isoformat()


def diagnosis():
    return DiagnosisResult(diagnosed_error_type=DiagnosedErrorType.COLUMN_NOT_FOUND, diagnosed_keywords=["column_not_found"], error_fingerprint="unused", confidence=1, is_repairable=True, primary_entity="pay_amt")


def plan(risk="LOW"):
    return RepairPlan(plan_id="p", repairable=True, actions=[RepairAction(action_type=RepairActionType.REPLACE_COLUMN, target_fragment="pay_amt", replacement_fragment="payment_amount", reason="metadata", risk_level=risk)], confidence=1)


def request(candidate="SELECT payment_amount FROM orders"):
    return GateRequest(original_sql="SELECT pay_amt FROM orders", candidate_sql=candidate, diagnosis=diagnosis(), existing_plan=plan(), attempt_id="attempt_1", event_key="event_1")


def gate_result(name, decision="PASS", risk="LOW", sql="SELECT payment_amount FROM orders"):
    from sql_self_healing_agent.agent.gates.gate_utils import result
    return result(name, sql, decision, risk)


class StubStatic:
    def __init__(self, calls, outcome): self.calls, self.outcome = calls, outcome
    def run(self, value): self.calls.append("static"); return self.outcome
class StubSemantic:
    def __init__(self, calls, outcome): self.calls, self.outcome = calls, outcome
    def run(self, value, static): self.calls.append("semantic"); return self.outcome
class StubOutput:
    def __init__(self, calls, outcome): self.calls, self.outcome = calls, outcome
    def run(self, value, previous): self.calls.append("output"); return self.outcome


class GateRunnerTest(unittest.TestCase):
    def test_fixed_order(self):
        calls=[]
        runner=GateRunner(StubStatic(calls, StaticGateOutcome(result=gate_result("static"))), StubSemantic(calls, gate_result("semantic")), StubOutput(calls, gate_result("output")))
        self.assertEqual(runner.run_request(request()).decision, "PASS")
        self.assertEqual(calls, ["static", "semantic", "output"])

    def test_static_reject_short_circuits_and_candidate_not_ready(self):
        calls=[]
        runner=GateRunner(StubStatic(calls, StaticGateOutcome(result=gate_result("static", "REJECT", "BLOCKED"))), StubSemantic(calls, gate_result("semantic")), StubOutput(calls, gate_result("output")))
        context=AgentContext(session_id="s", attempt_id="attempt_1", event_key="event_1", original_sql=request().original_sql, execution_plan=build_initial_execution_plan(), candidate=CandidateState(draft_sql=request().candidate_sql, status="DRAFT"))
        result=runner.run(context, AgentRunState(started_at=NOW), request())
        self.assertEqual(calls, ["static"])
        self.assertEqual(result.status, "NO_SQL")
        self.assertEqual(context.candidate.status, "GATE_REJECTED")
        self.assertIsNone(context.candidate.formal_sql)

    def test_blocked_high_medium_never_aggregate_to_pass(self):
        self.assertEqual(aggregate_results(gate_result("a", "PASS", "LOW"), gate_result("b", "PASS", "BLOCKED")).decision, "REJECT")
        self.assertEqual(aggregate_results(gate_result("a", "PASS", "HIGH"), gate_result("b")).decision, "HUMAN_REQUIRED")
        self.assertEqual(aggregate_results(gate_result("a", "PASS", "MEDIUM"), gate_result("b")).decision, "HUMAN_REQUIRED")

    def test_static_gate_reuses_validator_and_blocks_dangerous_sql(self):
        outcome=StaticValidationGate().run(request("DROP TABLE orders"))
        self.assertEqual(outcome.result.decision, "REJECT")
        self.assertEqual(outcome.result.risk_level, "BLOCKED")
        self.assertIn("DANGEROUS_SQL", outcome.result.failed_invariants)

    def test_medium_defaults_human_required(self):
        req=request(); req.existing_plan=plan("MEDIUM")
        outcome=StaticValidationGate().run(req)
        self.assertEqual(outcome.result.decision, "HUMAN_REQUIRED")
        self.assertEqual(outcome.result.risk_level, "MEDIUM")

    def test_plan_or_diff_failure_is_human_required(self):
        req=request("")
        outcome=StaticValidationGate().run(req)
        self.assertEqual(outcome.result.decision, "HUMAN_REQUIRED")
        self.assertIn("PLAN_AND_DIFF_AVAILABLE", outcome.result.failed_invariants)

class CandidateCommitterTest(unittest.TestCase):
    def test_commit_only_ready_hash_matched_owned_artifact(self):
        import tempfile
        from pathlib import Path
        from sql_self_healing_agent.agent.gates.candidate_committer import CandidateCommitter
        from sql_self_healing_agent.agent.gates.gate_utils import result
        from sql_self_healing_agent.artifacts.artifact_store import ArtifactStore
        from sql_self_healing_agent.core.enums import SessionStatus
        from sql_self_healing_agent.core.models import UpstreamTaskEvent
        from sql_self_healing_agent.session.session_store import SessionStore

        with tempfile.TemporaryDirectory() as tmp:
            sessions=Path(tmp)/".sessions"; store=SessionStore(sessions); artifacts=ArtifactStore(sessions)
            event=UpstreamTaskEvent(id="task", status="FAILED", sql="SELECT pay_amt FROM orders")
            session=store.load_or_create_for_event(event); record=store.create_event_record(session,event); store.append_upstream_event(session,record); attempt=store.create_attempt(session,record)
            sql="SELECT payment_amount FROM orders"
            ref=artifacts.save_text_ref(session.session_id,attempt.attempt_id,"candidate_v1.sql",sql,"CANDIDATE_SQL")
            candidate=CandidateState(draft_sql=sql,draft_artifact_ref=ref.model_dump_json(),formal_sql=sql,status="READY")
            CandidateCommitter(store,artifacts).commit(session,attempt,candidate,result("GateRunner",sql,"PASS","LOW"))
            self.assertEqual(session.status,SessionStatus.SQL_READY_PENDING_UPSTREAM)
            self.assertEqual(session.latest_sql_candidate,sql)
            bad=candidate.model_copy(update={"formal_sql":"SELECT other FROM orders"})
            with self.assertRaises(ValueError): CandidateCommitter(store,artifacts).commit(session,attempt,bad,result("GateRunner",sql,"PASS","LOW"))

    def test_rejected_candidate_never_commits(self):
        import tempfile
        from pathlib import Path
        from sql_self_healing_agent.agent.gates.candidate_committer import CandidateCommitter
        from sql_self_healing_agent.agent.gates.gate_utils import result
        from sql_self_healing_agent.artifacts.artifact_store import ArtifactStore
        from sql_self_healing_agent.core.models import UpstreamTaskEvent
        from sql_self_healing_agent.session.session_store import SessionStore
        with tempfile.TemporaryDirectory() as tmp:
            sessions=Path(tmp)/".sessions"; store=SessionStore(sessions); artifacts=ArtifactStore(sessions)
            event=UpstreamTaskEvent(id="task",status="FAILED",sql="SELECT a"); session=store.load_or_create_for_event(event); rec=store.create_event_record(session,event); store.append_upstream_event(session,rec); attempt=store.create_attempt(session,rec)
            candidate=CandidateState(draft_sql="DROP TABLE x",formal_sql="DROP TABLE x",status="READY",draft_artifact_ref="{}")
            with self.assertRaises(ValueError): CandidateCommitter(store,artifacts).commit(session,attempt,candidate,result("GateRunner","DROP TABLE x","REJECT","BLOCKED"))
            self.assertIsNone(session.latest_sql_candidate)

class GateRepairTest(unittest.TestCase):
    def test_v2_reruns_all_three_gates_once(self):
        calls=[]
        runner=GateRunner(StubStatic(calls, StaticGateOutcome(result=gate_result("static"))), StubSemantic(calls, gate_result("semantic")), StubOutput(calls, gate_result("output")))
        state=AgentRunState(started_at=NOW)
        repaired="SELECT payment_amount FROM orders"
        result=runner.run_repair(request(),repaired,state)
        self.assertEqual(result.decision,"PASS")
        self.assertEqual(calls,["static","semantic","output"])
        self.assertEqual(state.gate_repair_rounds,1)
        blocked=runner.run_repair(request(),repaired,state)
        self.assertEqual(blocked.decision,"HUMAN_REQUIRED")
        self.assertEqual(calls,["static","semantic","output"])

    def test_total_budget_blocks_repair_before_any_gate(self):
        calls=[]
        runner=GateRunner(StubStatic(calls, StaticGateOutcome(result=gate_result("static"))), StubSemantic(calls, gate_result("semantic")), StubOutput(calls, gate_result("output")))
        state=AgentRunState(started_at=NOW)
        result=runner.run_repair(request(),request().candidate_sql,state,budget_available=False)
        self.assertEqual(result.decision,"HUMAN_REQUIRED")
        self.assertEqual(calls,[])

class RealGateFlowTest(unittest.TestCase):
    def test_low_risk_candidate_passes_all_three(self):
        runner=GateRunner()
        result=runner.run_request(request())
        self.assertEqual(result.decision,"PASS")
        self.assertEqual(result.risk_level,"LOW")
        self.assertEqual(runner.execution_order,["StaticValidationGate","SemanticPreReflectionGate","OutputContractGate"])

    def test_semantic_reject_stops_before_output(self):
        class RejectEvaluator:
            def pre_reflect(self, value):
                from sql_self_healing_agent.repair.reflection import PreReflectionDecision, PreReflectionResult
                return PreReflectionResult(decision=PreReflectionDecision.BLOCK,confidence=1,follows_repair_plan=False,minimal_change=False,semantic_risk_level=RiskLevel.LOW,reasons=["over rewrite"])
        from sql_self_healing_agent.agent.gates.semantic_pre_reflection_gate import SemanticPreReflectionGate
        runner=GateRunner(semantic_gate=SemanticPreReflectionGate(RejectEvaluator()))
        result=runner.run_request(request())
        self.assertEqual(result.decision,"REJECT")
        self.assertEqual(runner.execution_order,["StaticValidationGate","SemanticPreReflectionGate"])

    def test_output_contract_rejects_changed_hash(self):
        from sql_self_healing_agent.agent.gates.output_contract_gate import OutputContractGate
        output=OutputContractGate().run(request(),[gate_result("static",sql="SELECT changed"),gate_result("semantic")])
        self.assertEqual(output.decision,"HUMAN_REQUIRED")
