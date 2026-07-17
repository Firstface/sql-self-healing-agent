from sql_self_healing_agent.agent.models.subagent_models import SubAgentTaskSpec


class SubAgentTaskSpecRegistry:
    TASK_NAMES = ("diagnose_sql_error", "reflect_previous_failure", "plan_sql_repair", "generate_sql_candidate", "evaluate_sql_candidate", "summarize_human_required")

    def __init__(self) -> None:
        self._specs = {
            name: SubAgentTaskSpec(task_name=name, description=name.replace("_", " "), objective_template="{objective}", allowed_tools=["ReadArtifactTool", "ReadLogTool", "MetadataQueryTool", "MemoryRetrieveTool"], required_context_refs=[], output_schema_name="structured_output", max_steps=10, max_tool_calls=3)
            for name in self.TASK_NAMES
        }

    def get(self, task_name: str) -> SubAgentTaskSpec:
        if task_name not in self._specs:
            raise KeyError(task_name)
        return self._specs[task_name]
