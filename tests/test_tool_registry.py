import unittest

from pydantic import BaseModel

from sql_self_healing_agent.agent.models.context import AgentContext
from sql_self_healing_agent.agent.models.execution_plan import build_initial_execution_plan
from sql_self_healing_agent.agent.models.run_state import AgentRunState
from sql_self_healing_agent.agent.tools.tool_registry import ToolRegistry


class Input(BaseModel):
    value: str
class Output(BaseModel):
    summary: str
class Tool:
    name = "ReadLogTool"
    description = "read"
    input_model = Input
    output_model = Output
    allowed_phases = {"INIT"}
    max_output_tokens = 10
    produces_artifact = False
    def run(self, context, input_data):
        return Output(summary=input_data.value)


def context(phase="INIT"):
    return AgentContext(session_id="s", attempt_id="a", event_key="e", original_sql="SELECT 1", execution_plan=build_initial_execution_plan(), phase=phase)


class ToolRegistryTest(unittest.TestCase):
    def test_whitelist_schema_and_phase(self) -> None:
        registry = ToolRegistry()
        registry.register(Tool())
        state = AgentRunState(started_at="now")
        self.assertEqual(registry.execute("ReadLogTool", context(), {"value": "ok"}, state).status, "SUCCEEDED")
        self.assertEqual(registry.execute("ReadLogTool", context(), {}, state).status, "INVALID_INPUT")
        self.assertEqual(registry.execute("ReadLogTool", context("GATING"), {"value": "x"}, state).status, "BLOCKED")
        self.assertEqual(registry.execute("ExecuteSQLTool", context(), {}, state).status, "BLOCKED")

    def test_forbidden_tool_cannot_register(self) -> None:
        tool = Tool()
        tool.name = "WriteMemoryTool"
        with self.assertRaises(ValueError):
            ToolRegistry().register(tool)
