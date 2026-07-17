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

    def test_gate_cannot_be_removed_and_execute_sql_forbidden(self) -> None:
        old = build_initial_execution_plan()
        no_gate = old.model_copy(deep=True)
        no_gate.revision = 1
        no_gate.steps = [step for step in no_gate.steps if step.step_id != "gate_candidate"]
        with self.assertRaises(InvalidExecutionPlan):
            ExecutionPlanValidator().validate_transition(old, no_gate)
        execute = old.model_copy(deep=True)
        execute.revision = 1
        execute.steps.append(ExecutionStep(step_id="execute_sql", title="执行生产 SQL"))
        with self.assertRaises(InvalidExecutionPlan):
            ExecutionPlanValidator().validate_transition(old, execute)
