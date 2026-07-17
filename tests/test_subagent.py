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


    def test_mini_loop_context_request_is_limited(self) -> None:
        calls=[]
        def worker(request, view):
            calls.append(view)
            return {"type":"NEED_MORE_CONTEXT","requested_context_refs":[]}
        request=SubAgentRequest(task_name="diagnose_sql_error",objective="x",expected_output_schema="x")
        result=SubAgentRunner(worker).run(request,context())
        self.assertEqual(result.status,"BUDGET_EXCEEDED")
        self.assertEqual(result.stop_reason,"MAX_CONTEXT_REQUESTS")
        self.assertEqual(len(calls),2)

    def test_mini_loop_rejects_unallowed_tool(self) -> None:
        def worker(request, view):
            return {"type":"CALL_ALLOWED_TOOL","tool_name":"ExecuteSQLTool"}
        request=SubAgentRequest(task_name="diagnose_sql_error",objective="x",expected_output_schema="x")
        result=SubAgentRunner(worker).run(request,context())
        self.assertEqual(result.status,"HUMAN_REQUIRED")


    def test_task_specs_are_differentiated(self) -> None:
        from sql_self_healing_agent.agent.runner.subagent_task_spec import SubAgentTaskSpecRegistry
        registry=SubAgentTaskSpecRegistry()
        diagnosis=registry.get("diagnose_sql_error")
        planning=registry.get("plan_sql_repair")
        self.assertNotEqual(diagnosis.allowed_tools,planning.allowed_tools)
        self.assertNotEqual(diagnosis.output_schema_name,planning.output_schema_name)
        self.assertIn("log_digest",diagnosis.required_context_refs)
