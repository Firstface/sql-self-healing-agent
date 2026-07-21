from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

from sql_self_healing_agent.agent.context.context_models import MainAgentInput
from sql_self_healing_agent.agent.llm import LLMAdapter
from sql_self_healing_agent.agent.models.action import AgentAction
from sql_self_healing_agent.agent.models.execution_plan import ExecutionPlan
from sql_self_healing_agent.agent.models.run_state import AgentRunState
from sql_self_healing_agent.agent.planning.execution_plan_validator import ExecutionPlanValidator, InvalidExecutionPlan
from sql_self_healing_agent.llm.prompt_templates import structured_prompt


class PlanDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["CONTINUE_PLAN", "REVISE_PLAN", "PROPOSE_SQL_CANDIDATE", "RETURN_HUMAN_REQUIRED"]
    execution_plan: ExecutionPlan | None = None
    candidate_sql: str | None = None
    reason: str | None = None


class LLMMainAgent:
    """Plan-execute-replan policy. Fallback is used only after one governed repair fails."""

    def __init__(self, adapter: LLMAdapter, fallback) -> None:
        self.adapter = adapter
        self.fallback = fallback
        self.validator = ExecutionPlanValidator()
        self._initialized = False
        self.last_rejection_reason: str | None = None

    def _generate_plan(self, context: MainAgentInput, purpose: str, feedback: str | None = None) -> ExecutionPlan:
        system = (
            "为当前 SQL 修复任务生成完整可执行 ExecutionPlan。每个步骤必须声明 action_type；"
            "TOOL_CALL 必须使用 available_tools 中的工具并填写 tool_name/tool_input；依赖必须无环；"
            "不得执行生产 SQL。Gate 不属于计划，候选提交后由 Runner 强制执行。"
        )
        if feedback:
            system += f"上一次计划校验失败：{feedback}。请修复该问题。"
        plan = self.adapter.generate_structured(
            structured_prompt(system, context, ExecutionPlan),
            ExecutionPlan,
            purpose=purpose,
            input_summary="controlled planning context",
        )
        plan = plan.model_copy(update={"revision": context.execution_plan.revision + 1}, deep=True)
        self.validator.validate_transition(context.execution_plan, plan)
        return plan

    def _plan_with_one_repair(self, context: MainAgentInput) -> ExecutionPlan | None:
        try:
            return self._generate_plan(context, "main_agent_initial_plan" if not self._initialized else "main_agent_replan")
        except Exception as first:
            try:
                return self._generate_plan(context, "main_agent_plan_repair", str(first))
            except Exception as second:
                self.last_rejection_reason = type(second).__name__
                return None

    @staticmethod
    def _step_action(context: MainAgentInput) -> AgentAction | None:
        step_id = context.execution_plan.current_step_id
        step = next((item for item in context.execution_plan.steps if item.step_id == step_id), None)
        if step is None:
            return None
        if step.action_type == "TOOL_CALL":
            return AgentAction(type="TOOL_CALL", tool_name=step.tool_name, tool_input=step.tool_input)
        return None

    def next_action(self, context: MainAgentInput, run_state: AgentRunState) -> AgentAction:
        if not self._initialized:
            plan = self._plan_with_one_repair(context)
            self._initialized = True
            return AgentAction(type="UPDATE_PLAN", execution_plan=plan) if plan else self.fallback.next_action(context, run_state)

        if context.recent_observations:
            try:
                decision = self.adapter.generate_structured(
                    structured_prompt(
                        "根据当前计划和最新 Observation 判断是否继续、重规划、提交候选或人工介入。候选必须来自 workspace candidate_sql；不要生成新的 SQL。Gate 由 Runner 强制执行。",
                        context,
                        PlanDecision,
                    ),
                    PlanDecision,
                    purpose="main_agent_replan_decision",
                    input_summary="plan and latest structured observation",
                )
            except Exception:
                decision = PlanDecision(decision="CONTINUE_PLAN")
            if decision.decision == "REVISE_PLAN":
                plan = self._plan_with_one_repair(context)
                if plan:
                    return AgentAction(type="UPDATE_PLAN", execution_plan=plan)
            elif decision.decision == "PROPOSE_SQL_CANDIDATE":
                candidate = context.workspace_summaries.get("candidate_sql") or decision.candidate_sql
                if candidate and candidate != "FAILED":
                    return AgentAction(type="PROPOSE_SQL_CANDIDATE", candidate_sql=candidate)
            elif decision.decision == "RETURN_HUMAN_REQUIRED":
                return AgentAction(type="RETURN_HUMAN_REQUIRED", reason=decision.reason or "LLM 判断需要人工介入")

        action = self._step_action(context)
        return action or self.fallback.next_action(context, run_state)
