import re
from sql_self_healing_agent.agent.hooks.hook_models import HookDecision, OperationContext


class SafetyHook:
    name = "SafetyHook"
    order = 30
    _forbidden = re.compile(r"(?i)(authorization\s*:|ark_api_key|execute\s+(?:production\s+)?sql|bypass\s+gate|write(?:session|attempt|memory)|submit_without_gate)")

    def applies_to(self, operation: OperationContext) -> bool:
        return True

    def before(self, operation: OperationContext) -> HookDecision:
        if operation.input_summary and self._forbidden.search(operation.input_summary):
            return HookDecision(action="BLOCK", reason_code="SAFETY_POLICY_BLOCKED", message="操作包含凭据或越权意图。")
        return HookDecision(action="CONTINUE")

    def after(self, operation: OperationContext, result: object | None, error: Exception | None) -> None:
        return None
