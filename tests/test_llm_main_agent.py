import unittest

from sql_self_healing_agent.agent.context.context_models import MainAgentInput
from sql_self_healing_agent.agent.models.execution_plan import ExecutionStep, build_initial_execution_plan
from sql_self_healing_agent.agent.models.run_state import AgentRunState
from sql_self_healing_agent.agent.runner.llm_main_agent import LLMMainAgent
from sql_self_healing_agent.agent.models.subagent_models import SubAgentRequest
from sql_self_healing_agent.agent.runner.llm_main_agent import PlanDecision


class Adapter:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = 0

    def generate_structured(self, *args, **kwargs):
        self.calls += 1
        return next(self.responses)


class Fallback:
    def next_action(self, context, state):
        from sql_self_healing_agent.agent.models.action import AgentAction
        return AgentAction(type="TOOL_CALL", tool_name="build_log_digest", tool_input={})


def main_input():
    return MainAgentInput(
        goal="repair", original_sql="select x", error_message="missing",
        execution_plan=build_initial_execution_plan(), execution_plan_summary="read_log",
        current_phase="INIT", workspace_summaries={}, recent_observations=[],
        candidate_summary="NONE", gate_feedback_summary=[], artifact_refs=[],
        available_tools=[], remaining_budget={},
    )


class LLMMainAgentTest(unittest.TestCase):
    def test_plan_driven_subagent_step_is_executed(self):
        value = main_input()
        value.execution_plan.steps = [ExecutionStep(
            step_id="sub", title="inspect", action_type="RUN_SUB_AGENT",
            sub_agent_request=SubAgentRequest(task_name="diagnose_sql_error", objective="inspect", expected_output_schema="DiagnosisResult"),
        )]
        value.execution_plan.current_step_id = "sub"
        value.recent_observations = [{"status": "SUCCEEDED"}]
        agent = LLMMainAgent(Adapter([PlanDecision(decision="CONTINUE_PLAN")]), Fallback())
        agent._initialized = True
        action = agent.next_action(value, AgentRunState(started_at="now"))
        self.assertEqual(action.type, "RUN_SUB_AGENT")
        self.assertEqual(action.sub_agent_request.task_name, "diagnose_sql_error")

    def test_invalid_plan_is_repaired_once_then_falls_back(self):
        invalid = build_initial_execution_plan()
        invalid.steps.append(ExecutionStep(step_id="execute_sql", title="执行生产 SQL", action_type="TOOL_CALL", tool_name="ExecuteSQLTool"))
        agent = LLMMainAgent(Adapter([invalid, invalid]), Fallback())
        action = agent.next_action(main_input(), AgentRunState(started_at="now"))
        self.assertEqual(action.type, "TOOL_CALL")
        self.assertEqual(agent.adapter.calls, 2)

    def test_valid_plan_is_returned_with_normalized_revision(self):
        proposed = build_initial_execution_plan()
        proposed.steps[0].status = "IN_PROGRESS"
        adapter = Adapter([proposed])
        action = LLMMainAgent(adapter, Fallback()).next_action(main_input(), AgentRunState(started_at="now"))
        self.assertEqual(action.type, "UPDATE_PLAN")
        self.assertEqual(action.execution_plan.revision, 1)
