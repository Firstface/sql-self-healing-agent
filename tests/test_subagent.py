import unittest

from pydantic import ValidationError

from sql_self_healing_agent.agent.models.context import AgentContext
from sql_self_healing_agent.agent.models.execution_plan import build_initial_execution_plan
from sql_self_healing_agent.agent.models.subagent_models import SubAgentLimits, SubAgentRequest, SubAgentResult
from sql_self_healing_agent.agent.runner.subagent_runner import SubAgentRunner


def context():
    return AgentContext(session_id="s", attempt_id="a", event_key="e", original_sql="SELECT 1", execution_plan=build_initial_execution_plan())


class SubAgentTest(unittest.TestCase):
    def test_no_recursion_and_limits(self) -> None:
        with self.assertRaises(ValidationError):
            SubAgentLimits(max_steps=11)
        with self.assertRaises(ValidationError):
            SubAgentLimits(allow_recursive_sub_agent=True)
        request = SubAgentRequest(task_name="diagnose_sql_error", objective="diagnose", allowed_tools=["RunSubAgentTool"], expected_output_schema="x")
        runner = SubAgentRunner(lambda request, view: SubAgentResult(status="SUCCEEDED", summary="ok"))
        self.assertEqual(runner.run(request, context()).status, "HUMAN_REQUIRED")

    def test_parent_context_is_restricted_and_result_not_applied(self) -> None:
        parent = context()
        seen = {}
        def worker(request, view):
            seen.update(view)
            return SubAgentResult(status="SUCCEEDED", summary="candidate suggestion", structured_output={"candidate_sql": "SELECT 2"})
        request = SubAgentRequest(task_name="generate_sql_candidate", objective="suggest", allowed_tools=[], expected_output_schema="candidate")
        result = SubAgentRunner(worker).run(request, parent)
        self.assertEqual(result.status, "SUCCEEDED")
        self.assertEqual(parent.candidate.status, "NONE")
        self.assertNotIn("candidate", seen)

    def test_foreign_context_ref_is_rejected(self) -> None:
        request = SubAgentRequest(task_name="diagnose_sql_error", objective="x", context_refs=["artifact://other/x"], expected_output_schema="x")
        result = SubAgentRunner(lambda request, view: SubAgentResult(status="SUCCEEDED", summary="ok")).run(request, context())
        self.assertEqual(result.status, "HUMAN_REQUIRED")
