import time
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from sql_self_healing_agent.agent.context.context_manager import ContextManager
from sql_self_healing_agent.agent.models.context import AgentContext
from sql_self_healing_agent.agent.models.run_state import AgentRunState
from sql_self_healing_agent.agent.models.subagent_models import SubAgentBudget, SubAgentRequest, SubAgentResult
from sql_self_healing_agent.agent.runner.subagent_task_spec import SubAgentTaskSpecRegistry
from sql_self_healing_agent.agent.tools.tool_registry import ToolRegistry


class SubAgentAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["READ_ARTIFACT", "CALL_ALLOWED_TOOL", "RETURN_RESULT", "NEED_MORE_CONTEXT", "RETURN_FAILED"]
    tool_name: str | None = None
    tool_input: dict[str, object] = Field(default_factory=dict)
    requested_context_refs: list[str] = Field(default_factory=list)
    result: SubAgentResult | None = None


class SubAgentRunner:
    def __init__(self, worker: Callable[[SubAgentRequest, dict[str, object]], object], registry: SubAgentTaskSpecRegistry | None = None, *, tool_registry: ToolRegistry | None = None, context_manager: ContextManager | None = None) -> None:
        self.worker = worker
        self.registry = registry or SubAgentTaskSpecRegistry()
        self.tool_registry = tool_registry
        self.context_manager = context_manager

    def run(self, request: SubAgentRequest, parent_context: AgentContext) -> SubAgentResult:
        try:
            spec = self.registry.get(request.task_name)
        except KeyError:
            return SubAgentResult(status="FAILED", summary="未注册的 SubAgent 任务", stop_reason="TASK_NOT_REGISTERED")
        if "RunSubAgentTool" in request.allowed_tools or request.limits.allow_recursive_sub_agent:
            return SubAgentResult(status="HUMAN_REQUIRED", summary="禁止递归 SubAgent", stop_reason="RECURSIVE_SUB_AGENT_FORBIDDEN")
        if not set(request.allowed_tools) <= set(spec.allowed_tools):
            return SubAgentResult(status="HUMAN_REQUIRED", summary="SubAgent Tool 权限越界", stop_reason="TOOL_NOT_ALLOWED")
        for ref in request.context_refs:
            if ref.startswith("artifact://") and not ref.startswith(f"artifact://{parent_context.session_id}/"):
                return SubAgentResult(status="HUMAN_REQUIRED", summary="上下文引用不属于当前 Session", stop_reason="CONTEXT_FORBIDDEN")
            if not ref.startswith("artifact://") and ref not in parent_context.workspace:
                return SubAgentResult(status="HUMAN_REQUIRED", summary="上下文引用不存在", stop_reason="CONTEXT_FORBIDDEN")
        view = self.context_manager.prepare_for_sub_agent(parent_context, request).model_dump(mode="json") if self.context_manager else {"session_id": parent_context.session_id, "attempt_id": parent_context.attempt_id, "objective": request.objective, "context_refs": list(request.context_refs)}
        budget = SubAgentBudget(max_steps=request.limits.max_steps, max_tool_calls=request.limits.max_tool_calls, max_wall_time_ms=request.limits.max_wall_time_ms, max_context_requests=request.limits.max_context_requests)
        started = time.monotonic()
        observations: list[dict[str, object]] = []
        while not budget.exceeded():
            if int((time.monotonic() - started) * 1000) >= budget.max_wall_time_ms:
                return SubAgentResult(status="BUDGET_EXCEEDED", summary="SubAgent 独立时间预算耗尽", stop_reason="MAX_WALL_TIME")
            budget.step_count += 1
            try:
                raw = self.worker(request, {**view, "budget": budget.model_dump(), "observations": observations})
            except Exception as error:
                return SubAgentResult(
                    status="FAILED",
                    summary=f"SubAgent worker failed: {type(error).__name__}",
                    stop_reason="SUB_AGENT_LLM_ERROR",
                )
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
                additional = request.model_copy(update={"context_refs": action.requested_context_refs})
                if self.context_manager:
                    supplied = self.context_manager.prepare_for_sub_agent(parent_context, additional)
                    view["inline_context"] = {**view.get("inline_context", {}), **supplied.inline_context}
                    view["artifact_refs"] = [*view.get("artifact_refs", []), *[item.model_dump(mode="json") for item in supplied.artifact_refs]]
                else:
                    view["context_refs"] = list(dict.fromkeys([*view.get("context_refs", []), *action.requested_context_refs]))
                continue
            if action.type in {"READ_ARTIFACT", "CALL_ALLOWED_TOOL"}:
                if action.tool_name not in request.allowed_tools:
                    return SubAgentResult(status="HUMAN_REQUIRED", summary="SubAgent Tool 权限越界", stop_reason="TOOL_NOT_ALLOWED")
                if self.tool_registry is None:
                    return SubAgentResult(status="FAILED", summary="SubAgent ToolRegistry 未配置", stop_reason="TOOL_REGISTRY_UNAVAILABLE")
                budget.tool_call_count += 1
                result = self.tool_registry.execute(action.tool_name, parent_context, action.tool_input, AgentRunState(started_at="subagent"))
                observations.append(result.model_dump(mode="json"))
                if result.status not in {"SUCCEEDED"}:
                    return SubAgentResult(status="FAILED", summary="SubAgent Tool 执行失败", stop_reason=result.error_code)
                continue
        return SubAgentResult(status="BUDGET_EXCEEDED", summary="SubAgent 独立预算耗尽", stop_reason="SUB_AGENT_BUDGET_EXCEEDED")
