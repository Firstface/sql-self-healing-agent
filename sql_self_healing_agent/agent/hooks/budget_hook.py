from pydantic import BaseModel, ConfigDict, Field

from sql_self_healing_agent.agent.hooks.hook_models import HookDecision, OperationContext
from sql_self_healing_agent.agent.models.run_state import AgentRunLimits, AgentRunState


class CompactionBudget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_calls: int = 2
    max_output_tokens: int = 2000
    timeout_ms: int = 10000
    call_count: int = Field(default=0, ge=0)


class BudgetHook:
    name = "BudgetHook"
    order = 20

    def __init__(self, run_state: AgentRunState | None = None, limits: AgentRunLimits | None = None, compaction_budget: CompactionBudget | None = None) -> None:
        self.run_state = run_state
        self.limits = limits or AgentRunLimits()
        self.compaction_budget = compaction_budget or CompactionBudget()

    def applies_to(self, operation: OperationContext) -> bool:
        return True

    def before(self, operation: OperationContext) -> HookDecision:
        if operation.operation_type == "CONTEXT_COMPACTION":
            if self.compaction_budget.call_count >= self.compaction_budget.max_calls:
                return HookDecision(action="BLOCK", reason_code="COMPACTION_BUDGET_EXCEEDED")
            return HookDecision(action="CONTINUE")
        if self.run_state is None:
            return HookDecision(action="CONTINUE")
        checks = {
            "TOOL_CALL": self.run_state.tool_call_count >= self.limits.max_tool_calls,
            "SUB_AGENT_RUN": self.run_state.sub_agent_call_count >= self.limits.max_sub_agent_calls,
            "GATE_RUN": self.run_state.gate_repair_rounds > self.limits.max_gate_repair_rounds,
            "LLM_CALL": self.run_state.wall_time_ms >= self.limits.max_wall_time_ms,
        }
        if checks.get(operation.operation_type, False):
            return HookDecision(action="BLOCK", reason_code="OPERATION_BUDGET_EXCEEDED")
        return HookDecision(action="CONTINUE")

    def after(self, operation: OperationContext, result: object | None, error: Exception | None) -> None:
        if operation.operation_type == "CONTEXT_COMPACTION" and operation.status == "SUCCEEDED":
            self.compaction_budget.call_count += 1
