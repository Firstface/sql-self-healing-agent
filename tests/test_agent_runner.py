import unittest

from sql_self_healing_agent.agent.models.action import AgentAction
from sql_self_healing_agent.agent.models.context import AgentContext, WorkspaceValue
from sql_self_healing_agent.agent.models.execution_plan import ExecutionPlan, ExecutionStep, build_initial_execution_plan
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
    def test_first_autonomous_plan_replaces_scaffold(self) -> None:
        proposed = ExecutionPlan(
            revision=1,
            steps=[ExecutionStep(step_id="ark_step_1", title="read", action_type="TOOL_CALL", tool_name="build_log_digest")],
            current_step_id="ark_step_1",
        )
        state = AgentRunState(started_at="now")
        ctx = context()
        result = AgentRunner(
            SequenceAgent([AgentAction(type="UPDATE_PLAN", execution_plan=proposed), AgentAction(type="RETURN_HUMAN_REQUIRED", reason="done")]),
            Executor(), Gate(),
        ).run(ctx, state)
        self.assertEqual(result.status, "HUMAN_REQUIRED")
        self.assertEqual(ctx.execution_plan.current_step_id, "ark_step_1")
        self.assertEqual(state.plan_revision_count, 1)

    def test_candidate_immediately_enters_gate(self) -> None:
        gate = Gate()
        runner = AgentRunner(SequenceAgent([AgentAction(type="PROPOSE_SQL_CANDIDATE", candidate_sql="SELECT 2")]), Executor(), gate)
        ctx = context()
        ctx.workspace["candidate_sql"] = WorkspaceValue(status="AVAILABLE", summary="SELECT 2", artifact_ref="owned-ref", updated_at="now")
        ctx.candidate.draft_artifact_ref = "owned-ref"
        result = runner.run(ctx, AgentRunState(started_at="now"))
        self.assertEqual(result.status, "CANDIDATE_READY")
        self.assertEqual(gate.calls, 1)

    def test_candidate_without_owned_workspace_artifact_never_enters_gate(self) -> None:
        gate = Gate()
        result = AgentRunner(
            SequenceAgent([AgentAction(type="PROPOSE_SQL_CANDIDATE", candidate_sql="SELECT invented")]),
            Executor(), gate,
        ).run(context(), AgentRunState(started_at="now"))
        self.assertEqual(result.status, "HUMAN_REQUIRED")
        self.assertEqual(result.stop_reason, "NO_CANDIDATE_ARTIFACT")
        self.assertEqual(gate.calls, 0)

    def test_invalid_action_and_no_progress_stop_safely(self) -> None:
        invalid = AgentRunner(SequenceAgent([{"type": "PROPOSE_SQL_CANDIDATE"}]), Executor(), Gate())
        self.assertEqual(invalid.run(context(), AgentRunState(started_at="now")).status, "HUMAN_REQUIRED")
        actions = [AgentAction(type="TOOL_CALL", tool_name="x", tool_input={})] * 3
        runner = AgentRunner(SequenceAgent(actions), Executor(), Gate(), AgentRunLimits(max_no_progress_steps=1))
        result = runner.run(context(), AgentRunState(started_at="now"))
        self.assertEqual(result.status, "HUMAN_REQUIRED")
        self.assertEqual(result.stop_reason, "NO_PROGRESS")

    def test_invalid_plan_is_blocked_without_crashing_runner(self) -> None:
        from sql_self_healing_agent.agent.models.execution_plan import ExecutionStep
        invalid_plan = build_initial_execution_plan()
        invalid_plan.steps.append(ExecutionStep(step_id="execute_sql", title="执行生产 SQL", action_type="TOOL_CALL", tool_name="ExecuteSQLTool"))
        invalid_plan.revision = 1
        actions = [
            AgentAction(type="UPDATE_PLAN", execution_plan=invalid_plan),
            AgentAction(type="PROPOSE_SQL_CANDIDATE", candidate_sql="SELECT 2"),
        ]
        state = AgentRunState(started_at="now")
        ctx = context()
        ctx.workspace["candidate_sql"] = WorkspaceValue(status="AVAILABLE", summary="SELECT 2", artifact_ref="owned-ref", updated_at="now")
        ctx.candidate.draft_artifact_ref = "owned-ref"
        result = AgentRunner(SequenceAgent(actions), Executor(), Gate()).run(ctx, state)
        self.assertEqual(result.status, "CANDIDATE_READY")
        self.assertEqual(state.plan_revision_count, 0)
        self.assertTrue(any(item.status == "BLOCKED" and "production SQL" in item.summary for item in ctx.recent_observations))

    def test_observation_binds_to_autonomous_current_step(self) -> None:
        ctx = context()
        ctx.execution_plan.steps[0].step_id = "ark_step_1"
        for step in ctx.execution_plan.steps:
            step.depends_on = ["ark_step_1" if dep == "read_log" else dep for dep in step.depends_on]
        ctx.execution_plan.current_step_id = "ark_step_1"
        result = AgentRunner(
            SequenceAgent([AgentAction(type="TOOL_CALL", tool_name="x", tool_input={}), AgentAction(type="RETURN_HUMAN_REQUIRED", reason="done")]),
            Executor(), Gate(), AgentRunLimits(max_no_progress_steps=2),
        ).run(ctx, AgentRunState(started_at="now"))
        self.assertEqual(result.status, "HUMAN_REQUIRED")
        self.assertEqual(ctx.recent_observations[0].plan_step_id, "ark_step_1")
        self.assertEqual(ctx.execution_plan.steps[0].execution_count, 1)
        self.assertEqual(ctx.execution_plan.steps[0].status, "COMPLETED")

    def test_step_budget_stops_without_candidate(self) -> None:
        runner = AgentRunner(SequenceAgent([]), Executor(), Gate(), AgentRunLimits(max_steps=0))
        result = runner.run(context(), AgentRunState(started_at="now"))
        self.assertEqual(result.status, "HUMAN_REQUIRED")
        self.assertIsNone(result.candidate_sql)
