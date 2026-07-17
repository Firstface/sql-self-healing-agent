from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, ConfigDict

from sql_self_healing_agent.agent.models.context import AgentContext
from sql_self_healing_agent.agent.models.subagent_models import SubAgentBudget, SubAgentRequest, SubAgentResult
from sql_self_healing_agent.agent.runner.subagent_task_spec import SubAgentTaskSpecRegistry


class SubAgentAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["READ_ARTIFACT", "CALL_ALLOWED_TOOL", "RETURN_RESULT", "NEED_MORE_CONTEXT", "RETURN_FAILED"]
    tool_name: str | None = None
    requested_context_refs: list[str] = []
    result: SubAgentResult | None = None



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
        budget = SubAgentBudget(
            max_steps=request.limits.max_steps,
            max_tool_calls=request.limits.max_tool_calls,
            max_wall_time_ms=request.limits.max_wall_time_ms,
            max_context_requests=request.limits.max_context_requests,
        )
        while not budget.exceeded():
            budget.step_count += 1
            raw = self.worker(request, {**restricted_view, "budget": budget.model_dump()})
            if isinstance(raw, SubAgentResult):
                return SubAgentResult.model_validate(raw)
            try:
                action = SubAgentAction.model_validate(raw)
            except Exception:
                return SubAgentResult(status="FAILED", summary="SubAgent Action 非法", stop_reason="INVALID_SUB_AGENT_ACTION")
            if action.type == "RETURN_RESULT" and action.result is not None:
                return action.result
            if action.type == "RETURN_FAILED":
                return action.result or SubAgentResult(status="FAILED", summary="SubAgent 返回失败")
            if action.type == "NEED_MORE_CONTEXT":
                budget.context_request_count += 1
                if budget.exceeded():
                    return SubAgentResult(status="BUDGET_EXCEEDED", summary="SubAgent 上下文请求超限", stop_reason="MAX_CONTEXT_REQUESTS")
                restricted_view["context_refs"] = list(dict.fromkeys([*restricted_view["context_refs"], *action.requested_context_refs]))
                continue
            if action.type in {"READ_ARTIFACT", "CALL_ALLOWED_TOOL"}:
                if action.tool_name not in request.allowed_tools:
                    return SubAgentResult(status="HUMAN_REQUIRED", summary="SubAgent Tool 权限越界", stop_reason="TOOL_NOT_ALLOWED")
                budget.tool_call_count += 1
                continue
        return SubAgentResult(status="BUDGET_EXCEEDED", summary="SubAgent 独立预算耗尽", stop_reason="SUB_AGENT_BUDGET_EXCEEDED")
