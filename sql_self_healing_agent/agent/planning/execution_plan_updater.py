from sql_self_healing_agent.agent.models.execution_plan import ExecutionPlan
from sql_self_healing_agent.agent.models.observation import Observation
from sql_self_healing_agent.agent.models.run_state import AgentRunLimits, AgentRunState
from sql_self_healing_agent.agent.planning.execution_plan_validator import ExecutionPlanValidator


class ExecutionPlanUpdater:
    def __init__(self, validator: ExecutionPlanValidator | None = None) -> None:
        self.validator = validator or ExecutionPlanValidator()

    def replace(self, current: ExecutionPlan, proposed: ExecutionPlan, state: AgentRunState, limits: AgentRunLimits) -> ExecutionPlan:
        if state.plan_revision_count >= limits.max_plan_revisions:
            raise ValueError("max_plan_revisions exceeded")
        updated = proposed.model_copy(update={"revision": current.revision + 1}, deep=True)
        if state.plan_revision_count == 0:
            self.validator.validate_initial(updated)
        else:
            self.validator.validate_transition(current, updated)
        state.plan_revision_count += 1
        return updated

    def apply_observation(self, plan: ExecutionPlan, observation: Observation) -> None:
        if not observation.plan_step_id:
            return
        steps = {step.step_id: step for step in plan.steps}
        step = steps.get(observation.plan_step_id)
        if step is None:
            return
        step.execution_count += 1
        step.result_refs = list(dict.fromkeys([*step.result_refs, *observation.artifact_refs]))
        if observation.status == "SUCCEEDED":
            step.status = "COMPLETED"
            step.failure_reason = None
        elif observation.status == "SKIPPED":
            step.status = "SKIPPED"
        else:
            step.status = "BLOCKED"
            step.failure_reason = observation.summary
        plan.current_step_id = next((item.step_id for item in plan.steps if item.status == "PENDING" and all(steps[dep].status in {"COMPLETED", "SKIPPED"} for dep in item.depends_on)), None)
