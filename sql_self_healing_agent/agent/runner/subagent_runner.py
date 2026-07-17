from collections.abc import Callable

from sql_self_healing_agent.agent.models.context import AgentContext
from sql_self_healing_agent.agent.models.subagent_models import SubAgentRequest, SubAgentResult
from sql_self_healing_agent.agent.runner.subagent_task_spec import SubAgentTaskSpecRegistry


class SubAgentRunner:
    def __init__(self, worker: Callable[[SubAgentRequest, dict[str, object]], SubAgentResult], registry: SubAgentTaskSpecRegistry | None = None) -> None:
        self.worker = worker
        self.registry = registry or SubAgentTaskSpecRegistry()

    def run(self, request: SubAgentRequest, parent_context: AgentContext) -> SubAgentResult:
        try:
            spec = self.registry.get(request.task_name)
        except KeyError:
            return SubAgentResult(status="FAILED", summary="未注册的 SubAgent 任务", stop_reason="TASK_NOT_REGISTERED")
        if "RunSubAgentTool" in request.allowed_tools or request.limits.allow_recursive_sub_agent:
            return SubAgentResult(status="HUMAN_REQUIRED", summary="禁止递归 SubAgent", stop_reason="RECURSIVE_SUB_AGENT_FORBIDDEN")
        if not set(request.allowed_tools) <= set(spec.allowed_tools):
            return SubAgentResult(status="HUMAN_REQUIRED", summary="SubAgent Tool 权限越界", stop_reason="TOOL_NOT_ALLOWED")
        if any(not ref.startswith(f"artifact://{parent_context.session_id}/") for ref in request.context_refs):
            return SubAgentResult(status="HUMAN_REQUIRED", summary="上下文引用不属于当前 Session", stop_reason="CONTEXT_FORBIDDEN")
        restricted_view = {"session_id": parent_context.session_id, "attempt_id": parent_context.attempt_id, "objective": request.objective, "context_refs": list(request.context_refs)}
        result = self.worker(request, restricted_view)
        return SubAgentResult.model_validate(result)
