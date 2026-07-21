import unittest

from sql_self_healing_agent.agent.models.action import AgentAction
from sql_self_healing_agent.agent.models.context import AgentContext
from sql_self_healing_agent.agent.models.execution_plan import build_initial_execution_plan
from sql_self_healing_agent.agent.models.observation import Observation
from sql_self_healing_agent.agent.models.run_state import AgentRunLimits, AgentRunState
from sql_self_healing_agent.agent.runner.agent_result import AgentRunResult
from sql_self_healing_agent.agent.runner.agent_runner import AgentRunner


class SequenceAgent:
    def __init__(self, actions):
        self.actions = iter(actions)
    def next_action(self, context, run_state):
        return next(self.actions)


class Executor:
    def execute(self, action, context, run_state):
        return Observation(observation_id="o", action_type=action.type, status="SUCCEEDED", summary="same", created_at="now")


class Gate:
    def __init__(self):
        self.calls = 0
    def run(self, context, run_state):
        self.calls += 1
        context.candidate.formal_sql = context.candidate.draft_sql
        context.candidate.status = "READY"
        run_state.status = "SUCCEEDED"
        return AgentRunResult(status="CANDIDATE_READY", candidate_sql=context.candidate.formal_sql, plan_revision=context.execution_plan.revision, step_count=run_state.step_count)


def context():
    return AgentContext(session_id="s", attempt_id="a", event_key="e", original_sql="SELECT 1", execution_plan=build_initial_execution_plan())


class AgentRunnerTest(unittest.TestCase):
    def test_candidate_immediately_enters_gate(self) -> None:
        gate = Gate()
        runner = AgentRunner(SequenceAgent([AgentAction(type="PROPOSE_SQL_CANDIDATE", candidate_sql="SELECT 2")]), Executor(), gate)
        result = runner.run(context(), AgentRunState(started_at="now"))
        self.assertEqual(result.status, "CANDIDATE_READY")
        self.assertEqual(gate.calls, 1)

    def test_invalid_action_and_no_progress_stop_safely(self) -> None:
        invalid = AgentRunner(SequenceAgent([{"type": "PROPOSE_SQL_CANDIDATE"}]), Executor(), Gate())
        self.assertEqual(invalid.run(context(), AgentRunState(started_at="now")).status, "HUMAN_REQUIRED")
        actions = [AgentAction(type="TOOL_CALL", tool_name="x", tool_input={})] * 3
        runner = AgentRunner(SequenceAgent(actions), Executor(), Gate(), AgentRunLimits(max_no_progress_steps=1))
        result = runner.run(context(), AgentRunState(started_at="now"))
        self.assertEqual(result.status, "HUMAN_REQUIRED")
        self.assertEqual(result.stop_reason, "NO_PROGRESS")

    def test_invalid_plan_is_blocked_without_crashing_runner(self) -> None:
        invalid_plan = build_initial_execution_plan()
        invalid_plan.steps = [step for step in invalid_plan.steps if step.step_id != "gate_candidate"]
        invalid_plan.revision = 1
        actions = [
            AgentAction(type="UPDATE_PLAN", execution_plan=invalid_plan),
            AgentAction(type="PROPOSE_SQL_CANDIDATE", candidate_sql="SELECT 2"),
        ]
        state = AgentRunState(started_at="now")
        ctx = context()
        result = AgentRunner(SequenceAgent(actions), Executor(), Gate()).run(ctx, state)
        self.assertEqual(result.status, "CANDIDATE_READY")
        self.assertEqual(state.plan_revision_count, 0)
        self.assertTrue(any(item.status == "BLOCKED" and "gate_candidate" in item.summary for item in ctx.recent_observations))

    def test_step_budget_stops_without_candidate(self) -> None:
        runner = AgentRunner(SequenceAgent([]), Executor(), Gate(), AgentRunLimits(max_steps=0))
        result = runner.run(context(), AgentRunState(started_at="now"))
        self.assertEqual(result.status, "HUMAN_REQUIRED")
        self.assertIsNone(result.candidate_sql)
