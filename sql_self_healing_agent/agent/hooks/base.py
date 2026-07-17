from typing import Protocol
from sql_self_healing_agent.agent.hooks.hook_models import HookDecision, OperationContext


class Hook(Protocol):
    name: str
    order: int
    def applies_to(self, operation: OperationContext) -> bool: ...
    def before(self, operation: OperationContext) -> HookDecision: ...
    def after(self, operation: OperationContext, result: object | None, error: Exception | None) -> None: ...


class HookBlockedError(RuntimeError):
    def __init__(self, reason_code: str, message: str | None = None) -> None:
        super().__init__(message or reason_code)
        self.reason_code = reason_code
