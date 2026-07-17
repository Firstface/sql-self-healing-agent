from sql_self_healing_agent.agent.hooks.hook_models import HookDecision, OperationContext
from sql_self_healing_agent.core.persistence_sanitizer import PersistenceSanitizer
from sql_self_healing_agent.trace.trace_writer import TraceWriter


class TraceHook:
    name = "TraceHook"
    order = 10

    def __init__(self, writer: TraceWriter, sanitizer: PersistenceSanitizer | None = None) -> None:
        self.writer = writer
        self.sanitizer = sanitizer or PersistenceSanitizer()

    def applies_to(self, operation: OperationContext) -> bool:
        return True

    def before(self, operation: OperationContext) -> HookDecision:
        self.writer.emit(operation.session_id, "operation_started", operation.operation_type, {"operation_id": operation.operation_id, "caller": operation.caller, "purpose": operation.purpose}, operation.attempt_id)
        return HookDecision(action="CONTINUE")

    def after(self, operation: OperationContext, result: object | None, error: Exception | None) -> None:
        summary = self.sanitizer.sanitize(operation.output_summary or "")[:500]
        self.writer.emit(operation.session_id, "operation_finished", operation.operation_type, {"operation_id": operation.operation_id, "status": operation.status, "error_code": operation.error_code, "output_summary": summary, "artifact_ref": operation.artifact_ref}, operation.attempt_id)
