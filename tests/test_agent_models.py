import unittest

from pydantic import ValidationError

from sql_self_healing_agent.agent.models.action import AgentAction
from sql_self_healing_agent.agent.models.context import AgentContext
from sql_self_healing_agent.agent.models.execution_plan import build_initial_execution_plan
from sql_self_healing_agent.agent.models.run_state import AgentRunState


class AgentModelsTest(unittest.TestCase):
    def test_context_has_only_declared_workspace_shape(self) -> None:
        context = AgentContext(session_id="s", attempt_id="a", event_key="e", original_sql="SELECT 1", execution_plan=build_initial_execution_plan())
        self.assertEqual(context.workspace, {})
        with self.assertRaises(ValidationError):
            AgentContext(session_id="s", attempt_id="a", event_key="e", original_sql="SELECT 1", execution_plan=build_initial_execution_plan(), diagnosis_ref="x")

    def test_run_state_rejects_business_fields(self) -> None:
        with self.assertRaises(ValidationError):
            AgentRunState(started_at="now", candidate_sql="SELECT 1")

    def test_action_schema_matches_type(self) -> None:
        self.assertEqual(AgentAction(type="RETURN_NO_SQL", reason="none").type, "RETURN_NO_SQL")
        with self.assertRaises(ValidationError):
            AgentAction(type="PROPOSE_SQL_CANDIDATE")
        with self.assertRaises(ValidationError):
            AgentAction(type="EXECUTE_SQL", reason="forbidden")
