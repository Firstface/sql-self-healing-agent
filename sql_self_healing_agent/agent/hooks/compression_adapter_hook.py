from collections.abc import Callable

from sql_self_healing_agent.agent.hooks.hook_models import HookDecision, OperationContext


class CompressionAdapterHook:
    name = "ContextCompressionHook"
    order = 40

    def __init__(self, compact: Callable[[], object] | None = None) -> None:
        self.compact = compact

    def applies_to(self, operation: OperationContext) -> bool:
        return operation.operation_type in {"LLM_CALL", "SUB_AGENT_RUN"}

    def before(self, operation: OperationContext) -> HookDecision:
        if self.compact is not None:
            try:
                self.compact()
            except Exception:
                return HookDecision(action="BLOCK", reason_code="CONTEXT_COMPACTION_FAILED")
        return HookDecision(action="CONTINUE")

    def after(self, operation: OperationContext, result: object | None, error: Exception | None) -> None:
        return None
