from sql_self_healing_agent.agent.models.execution_plan import ExecutionPlan


class InvalidExecutionPlan(ValueError):
    pass


class ExecutionPlanValidator:
    ALLOWED_TRANSITIONS = {
        "PENDING": {"PENDING", "IN_PROGRESS", "SKIPPED"},
        "IN_PROGRESS": {"IN_PROGRESS", "COMPLETED", "BLOCKED", "SKIPPED"},
        "BLOCKED": {"BLOCKED", "IN_PROGRESS"},
        "COMPLETED": {"COMPLETED"},
        "SKIPPED": {"SKIPPED"},
    }

    def validate_initial(self, plan: ExecutionPlan) -> None:
        self._validate_structure(plan)
        if plan.revision < 0:
            raise InvalidExecutionPlan("initial revision cannot be negative")

    def validate_transition(self, old_plan: ExecutionPlan, new_plan: ExecutionPlan) -> None:
        self._validate_structure(new_plan)
        by_id = {step.step_id: step for step in new_plan.steps}
        old_by_id = {step.step_id: step for step in old_plan.steps}
        for step_id, old_step in old_by_id.items():
            if step_id not in by_id:
                raise InvalidExecutionPlan("existing steps cannot be deleted")
            if by_id[step_id].status not in self.ALLOWED_TRANSITIONS[old_step.status]:
                raise InvalidExecutionPlan("invalid step state transition")
        if new_plan.revision <= old_plan.revision:
            raise InvalidExecutionPlan("revision must increase")
        if new_plan.current_step_id and new_plan.current_step_id not in by_id:
            raise InvalidExecutionPlan("current step does not exist")

    def _validate_structure(self, plan: ExecutionPlan) -> None:
        if not plan.steps:
            raise InvalidExecutionPlan("execution plan cannot be empty")
        ids = [step.step_id for step in plan.steps]
        if len(ids) != len(set(ids)):
            raise InvalidExecutionPlan("step_id must be unique")
        by_id = {step.step_id: step for step in plan.steps}
        for step in plan.steps:
            if any(dependency not in by_id for dependency in step.depends_on):
                raise InvalidExecutionPlan("dependency does not exist")
        self._validate_no_cycle(plan)
        if any("execute_sql" in step.step_id.casefold() or "执行生产 sql" in step.title.casefold() or (step.tool_name and "execute" in step.tool_name.casefold() and "sql" in step.tool_name.casefold()) for step in plan.steps):
            raise InvalidExecutionPlan("production SQL execution is forbidden")
        if any(step.action_type == "TOOL_CALL" and not step.tool_name for step in plan.steps):
            raise InvalidExecutionPlan("TOOL_CALL plan step requires tool_name")
        if plan.current_step_id and plan.current_step_id not in by_id:
            raise InvalidExecutionPlan("current step does not exist")

    @staticmethod
    def _validate_no_cycle(plan: ExecutionPlan) -> None:
        graph = {step.step_id: step.depends_on for step in plan.steps}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str) -> None:
            if node in visiting:
                raise InvalidExecutionPlan("execution plan contains a cycle")
            if node in visited:
                return
            visiting.add(node)
            for dependency in graph[node]:
                visit(dependency)
            visiting.remove(node)
            visited.add(node)

        for node in graph:
            visit(node)
