
from sql_self_healing_agent.agent.context.context_models import MainAgentInput
from sql_self_healing_agent.agent.llm import LLMAdapter
from sql_self_healing_agent.agent.models.action import AgentAction
from sql_self_healing_agent.agent.models.run_state import AgentRunState
from sql_self_healing_agent.llm.prompt_templates import structured_prompt


class LLMMainAgent:
    def __init__(self, adapter: LLMAdapter, fallback) -> None:
        self.adapter = adapter
        self.fallback = fallback

    def next_action(self, context: MainAgentInput, run_state: AgentRunState) -> AgentAction:
        prompt = structured_prompt(
            "你是 SQL 修复编排 Agent。只能返回 AgentAction；不得执行 SQL、写 Session/Memory 或绕过 Gate。必须保留 gate_candidate 和所有既有步骤，不得新增 execute_sql 或任何生产 SQL 执行步骤；revision 必须递增且状态转移合法。必须遵循 ExecutionPlan、工具白名单和剩余预算。只输出符合 schema 的 JSON。",
            context,
            AgentAction,
        )
        try:
            return self.adapter.generate_structured(prompt, AgentAction, purpose="main_agent_action", input_summary="controlled MainAgentInput")
        except Exception:
            return self.fallback.next_action(context, run_state)
