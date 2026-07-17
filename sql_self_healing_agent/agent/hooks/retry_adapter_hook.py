from sql_self_healing_agent.agent.hooks.hook_models import HookDecision, OperationContext


class RetryAdapterHook:
    name = "LLMRetryHook"
    order = 50
    def applies_to(self, operation: OperationContext) -> bool:
        return operation.operation_type in {"LLM_CALL", "GATE_RUN", "CONTEXT_COMPACTION", "SUB_AGENT_RUN"}
    def before(self, operation: OperationContext) -> HookDecision:
        return HookDecision(action="CONTINUE")
    def after(self, operation: OperationContext, result: object | None, error: Exception | None) -> None:
        return None
