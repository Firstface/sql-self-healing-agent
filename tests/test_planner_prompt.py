import unittest

from sql_self_healing_agent.agent.runner.llm_main_agent import PlannerInput
from sql_self_healing_agent.agent.models.execution_plan import ExecutionPlan
from sql_self_healing_agent.llm.prompt_templates import structured_prompt


class PlannerPromptTest(unittest.TestCase):
    def test_context_and_output_contract_are_unambiguous(self):
        payload = PlannerInput(
            original_sql="select x", error_message="missing", current_phase="INIT",
            workspace_summaries={}, available_tools=[], remaining_budget={},
        )
        prompt = structured_prompt("plan", payload, ExecutionPlan)
        self.assertIn("OUTPUT TYPE: ExecutionPlan", prompt)
        self.assertIn("<<<CONTEXT_START>>>", prompt)
        self.assertIn("never copy its top-level fields", prompt)
        self.assertNotIn('"execution_plan"', payload.model_dump_json())
