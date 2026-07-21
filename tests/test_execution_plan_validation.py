import unittest

from sql_self_healing_agent.agent.models.execution_plan import ExecutionStep, build_initial_execution_plan
from sql_self_healing_agent.agent.planning.execution_plan_validator import ExecutionPlanValidator, InvalidExecutionPlan


class ExecutionPlanValidationTest(unittest.TestCase):
    def test_valid_transition_and_invalid_cycle(self) -> None:
        old = build_initial_execution_plan()
        new = old.model_copy(deep=True)
        new.revision = 1
        new.steps[0].status = "IN_PROGRESS"
        ExecutionPlanValidator().validate_transition(old, new)
        cyclic = new.model_copy(deep=True)
        cyclic.revision = 2
        cyclic.steps[0].depends_on = ["diagnose"]
        with self.assertRaises(InvalidExecutionPlan):
            ExecutionPlanValidator().validate_transition(new, cyclic)

    def test_gate_is_system_control_and_execute_sql_is_forbidden(self) -> None:
        old = build_initial_execution_plan()
        replacement = old.model_copy(deep=True)
        replacement.revision = 1
        ExecutionPlanValidator().validate_transition(old, replacement)
        execute = old.model_copy(deep=True)
        execute.revision = 1
        execute.steps.append(ExecutionStep(step_id="execute_sql", title="执行生产 SQL", action_type="TOOL_CALL", tool_name="ExecuteSQLTool"))
        with self.assertRaises(InvalidExecutionPlan):
            ExecutionPlanValidator().validate_transition(old, execute)
