from collections.abc import Callable
from typing import TypeVar
from uuid import uuid4

from sql_self_healing_agent.agent.hooks.base import Hook, HookBlockedError
from sql_self_healing_agent.agent.hooks.hook_models import OperationContext
from sql_self_healing_agent.core.time_utils import utc_now_iso
from sql_self_healing_agent.llm.retry_hook import LLMRetryHook

T = TypeVar("T")


class HookManager:
    def __init__(self, hooks: list[Hook], retry_hook: LLMRetryHook | None = None) -> None:
        self.hooks = sorted(hooks, key=lambda item: item.order)
        self.retry_hook = retry_hook or LLMRetryHook()
        self.operations: list[OperationContext] = []

    def create_operation(self, operation_type: str, session_id: str, attempt_id: str | None, caller: str, purpose: str, input_summary: str | None = None, parent_operation_id: str | None = None) -> OperationContext:
        return OperationContext(operation_id=f"op_{uuid4().hex}", operation_type=operation_type, session_id=session_id, attempt_id=attempt_id, parent_operation_id=parent_operation_id, caller=caller, purpose=purpose, started_at=utc_now_iso(), input_summary=input_summary)

    def execute_operation(self, operation: OperationContext, execute_fn: Callable[[], T]) -> T:
        executed: list[Hook] = []
        result: T | None = None
        error: Exception | None = None
        operation.status = "RUNNING"
        self.operations.append(operation)
        try:
            for hook in self.hooks:
                if not hook.applies_to(operation):
                    continue
                decision = hook.before(operation)
                executed.append(hook)
                if decision.action == "BLOCK":
                    operation.status = "BEFORE_BLOCKED"
                    operation.error_code = decision.reason_code
                    raise HookBlockedError(decision.reason_code or "HOOK_BLOCKED", decision.message)
            result = execute_fn()
            operation.status = "SUCCEEDED"
            operation.output_summary = self._summary(result)
            return result
        except TimeoutError as exc:
            error = exc
            operation.status = "TIMEOUT"
            operation.error_code = "OPERATION_TIMEOUT"
            raise
        except HookBlockedError as exc:
            error = exc
            operation.status = "BEFORE_BLOCKED"
            operation.error_code = exc.reason_code
            raise
        except Exception as exc:
            error = exc
            operation.status = "FAILED"
            operation.error_code = type(exc).__name__
            raise
        finally:
            operation.finished_at = utc_now_iso()
            for hook in reversed(executed):
                try:
                    hook.after(operation, result, error)
                except Exception:
                    continue

    def execute_llm_call(self, call: Callable[[str | None], T], *, session_id: str, attempt_id: str | None, purpose: str, input_summary: str | None = None, caller: str = "MAIN_AGENT") -> T:
        operation = self.create_operation("LLM_CALL", session_id, attempt_id, caller, purpose, input_summary)
        return self.execute_operation(operation, lambda: self.retry_hook.run(call))

    def execute_tool_call(self, call: Callable[[], T], *, session_id: str, attempt_id: str | None, purpose: str, input_summary: str | None = None, caller: str = "MAIN_AGENT") -> T:
        return self.execute_operation(self.create_operation("TOOL_CALL", session_id, attempt_id, caller, purpose, input_summary), call)

    def execute_sub_agent(self, call: Callable[[], T], *, session_id: str, attempt_id: str | None, purpose: str, input_summary: str | None = None) -> T:
        return self.execute_operation(self.create_operation("SUB_AGENT_RUN", session_id, attempt_id, "MAIN_AGENT", purpose, input_summary), call)

    def execute_gate(self, call: Callable[[], T], *, session_id: str, attempt_id: str | None, purpose: str, input_summary: str | None = None) -> T:
        return self.execute_operation(self.create_operation("GATE_RUN", session_id, attempt_id, "GATE_RUNNER", purpose, input_summary), call)

    def execute_compaction(self, call: Callable[[str | None], T], *, session_id: str, attempt_id: str | None, purpose: str, input_summary: str | None = None) -> T:
        operation = self.create_operation("CONTEXT_COMPACTION", session_id, attempt_id, "CONTEXT_MANAGER", purpose, input_summary)
        return self.execute_operation(operation, lambda: self.retry_hook.run(call))

    @staticmethod
    def _summary(result: object | None) -> str:
        if result is None:
            return ""
        status = getattr(result, "status", None) or getattr(result, "decision", None)
        return f"{type(result).__name__}:{status}" if status is not None else type(result).__name__
