import re
from sql_self_healing_agent.agent.hooks.hook_models import HookDecision, OperationContext


class SafetyHook:
    name = "SafetyHook"
    order = 30
    _forbidden = re.compile(r"(?i)(authorization\s*:|ark_api_key|execute\s+(?:production\s+)?sql|bypass\s+gate|write(?:session|attempt|memory)|submit_without_gate)")

    def applies_to(self, operation: OperationContext) -> bool:
        return True

    def before(self, operation: OperationContext) -> HookDecision:
        caller_policy = {
            "SUB_AGENT_RUN": {"MAIN_AGENT"},
            "GATE_RUN": {"GATE_RUNNER"},
            "CONTEXT_COMPACTION": {"CONTEXT_MANAGER"},
        }
        allowed_callers = caller_policy.get(operation.operation_type)
        if allowed_callers is not None and operation.caller not in allowed_callers:
            return HookDecision(action="BLOCK", reason_code="OPERATION_CALLER_FORBIDDEN", message="操作调用方不符合安全策略。")
        if not operation.session_id or (operation.operation_type != "CONTEXT_COMPACTION" and operation.attempt_id is None):
            return HookDecision(action="BLOCK", reason_code="OPERATION_OWNERSHIP_MISSING", message="操作缺少 Session 或 Attempt 归属。")
        if operation.input_summary and self._forbidden.search(operation.input_summary):
            return HookDecision(action="BLOCK", reason_code="SAFETY_POLICY_BLOCKED", message="操作包含凭据或越权意图。")
        return HookDecision(action="CONTINUE")

    def after(self, operation: OperationContext, result: object | None, error: Exception | None) -> None:
        return None
