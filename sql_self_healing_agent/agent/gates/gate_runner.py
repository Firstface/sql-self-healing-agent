from sql_self_healing_agent.agent.gates.gate_models import GateRequest, GateResult
from sql_self_healing_agent.agent.gates.output_contract_gate import OutputContractGate
from sql_self_healing_agent.agent.gates.risk_aggregator import aggregate_results
from sql_self_healing_agent.agent.gates.semantic_pre_reflection_gate import SemanticPreReflectionGate
from sql_self_healing_agent.agent.gates.static_validation_gate import StaticValidationGate
from sql_self_healing_agent.agent.models.candidate import GateFeedback as CandidateGateFeedback
from sql_self_healing_agent.agent.models.context import AgentContext
from sql_self_healing_agent.agent.models.run_state import AgentRunState
from sql_self_healing_agent.agent.runner.agent_result import AgentRunResult


class GateRunner:
    def __init__(self, static_gate: StaticValidationGate | None = None, semantic_gate: SemanticPreReflectionGate | None = None, output_gate: OutputContractGate | None = None) -> None:
        self.static_gate = static_gate or StaticValidationGate()
        self.semantic_gate = semantic_gate or SemanticPreReflectionGate()
        self.output_gate = output_gate or OutputContractGate()
        self.last_result: GateResult | None = None
        self.execution_order: list[str] = []

    def run_request(self, request: GateRequest) -> GateResult:
        self.execution_order = []
        self.execution_order.append("StaticValidationGate")
        static = self.static_gate.run(request)
        if static.result.decision in {"REJECT", "HUMAN_REQUIRED"}:
            self.last_result = static.result
            return static.result
        self.execution_order.append("SemanticPreReflectionGate")
        semantic = self.semantic_gate.run(request, static)
        if semantic.decision in {"REJECT", "HUMAN_REQUIRED"}:
            self.last_result = semantic
            return semantic
        self.execution_order.append("OutputContractGate")
        output = self.output_gate.run(request, [static.result, semantic])
        aggregated = aggregate_results(static.result, semantic, output, allow_medium_risk=request.allow_medium_risk)
        self.last_result = aggregated
        return aggregated

    def run_repair(self, request: GateRequest, repaired_candidate_sql: str, run_state: AgentRunState, *, budget_available: bool = True) -> GateResult:
        if not budget_available or run_state.gate_repair_rounds >= 1:
            from sql_self_healing_agent.agent.gates.gate_utils import result
            return result("GateRunner", repaired_candidate_sql, "HUMAN_REQUIRED", "HIGH", code="GATE_REPAIR_BUDGET_EXHAUSTED", message="Gate 修复次数或总预算已耗尽。", failed=["GATE_REPAIR_BUDGET"])
        run_state.gate_repair_rounds += 1
        repaired = request.model_copy(update={"candidate_sql": repaired_candidate_sql})
        return self.run_request(repaired)

    def run(self, context: AgentContext, run_state: AgentRunState, request: GateRequest | None = None) -> AgentRunResult:
        if request is None:
            run_state.status = "HUMAN_REQUIRED"
            run_state.stop_reason = "GATE_REQUEST_MISSING"
            context.candidate.status = "GATE_REJECTED"
            return AgentRunResult(status="HUMAN_REQUIRED", reason="GATE_REQUEST_MISSING", stop_reason=run_state.stop_reason, plan_revision=context.execution_plan.revision, step_count=run_state.step_count)
        result = self.run_request(request)
        context.candidate.gate_feedback.extend(CandidateGateFeedback(gate_name=item.gate_name, decision="PASS" if item.severity in {"INFO", "WARNING"} else "REJECT" if item.severity == "BLOCK" else "HUMAN_REQUIRED", reason=item.message) for item in result.feedback)
        if result.decision == "PASS" or (result.decision == "PASS_WITH_WARNING" and request.allow_medium_risk):
            context.candidate.formal_sql = context.candidate.draft_sql
            context.candidate.status = "READY"
            run_state.status = "SUCCEEDED"
            return AgentRunResult(status="CANDIDATE_READY", candidate_sql=context.candidate.formal_sql, candidate_artifact_ref=context.candidate.draft_artifact_ref, risk_level=result.risk_level, reason=None, plan_revision=context.execution_plan.revision, step_count=run_state.step_count)
        context.candidate.status = "GATE_REJECTED"
        run_state.status = "HUMAN_REQUIRED" if result.decision == "HUMAN_REQUIRED" else "NO_SQL"
        return AgentRunResult(status="HUMAN_REQUIRED" if result.decision == "HUMAN_REQUIRED" else "NO_SQL", risk_level=result.risk_level, reason=result.feedback[0].message if result.feedback else result.decision, plan_revision=context.execution_plan.revision, step_count=run_state.step_count)
