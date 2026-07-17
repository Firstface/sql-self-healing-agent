from sql_self_healing_agent.agent.hooks import BudgetHook, HookManager, SafetyHook
from sql_self_healing_agent.agent.llm import LLMAdapter, LLMCallContext
from sql_self_healing_agent.agent.models.run_state import AgentRunState


def build_test_llm_adapter(client):
    manager=HookManager([BudgetHook(AgentRunState(started_at="test")),SafetyHook()])
    return LLMAdapter(client,manager,LLMCallContext(session_id="test_session",attempt_id="test_attempt"))
