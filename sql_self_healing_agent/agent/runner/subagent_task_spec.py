from sql_self_healing_agent.agent.models.subagent_models import SubAgentTaskSpec


class SubAgentTaskSpecRegistry:
    TASK_NAMES = (
        "diagnose_sql_error",
        "reflect_previous_failure",
        "plan_sql_repair",
        "generate_sql_candidate",
        "evaluate_sql_candidate",
        "summarize_human_required",
    )

    def __init__(self) -> None:
        definitions = {
            "diagnose_sql_error": ("基于日志和元数据诊断 SQL 错误", ["ReadLogTool", "ReadArtifactTool", "MetadataQueryTool"], ["log_digest"], "DiagnosisResult"),
            "reflect_previous_failure": ("比较前后失败并识别振荡", ["ReadArtifactTool"], ["diagnosis", "post_reflection"], "PostReflectionResult"),
            "plan_sql_repair": ("依据诊断、元数据和成功经验提出修复计划", ["ReadArtifactTool", "MetadataQueryTool", "MemoryRetrieveTool"], ["diagnosis", "metadata_snapshot"], "RepairPlan"),
            "generate_sql_candidate": ("严格按 RepairPlan 生成候选建议", ["ReadArtifactTool"], ["repair_plan"], "SQLGenerationResult"),
            "evaluate_sql_candidate": ("独立评估候选语义风险，不替代 Gate", ["ReadArtifactTool", "MetadataQueryTool"], ["repair_plan", "metadata_snapshot"], "PreReflectionResult"),
            "summarize_human_required": ("生成不含敏感信息的人工介入摘要", ["ReadArtifactTool"], ["diagnosis"], "HumanRequiredSummary"),
        }
        self._specs = {
            name: SubAgentTaskSpec(
                task_name=name,
                description=description,
                objective_template="{objective}",
                allowed_tools=tools,
                required_context_refs=refs,
                output_schema_name=schema,
                max_steps=10,
                max_tool_calls=min(3, len(tools)),
            )
            for name, (description, tools, refs, schema) in definitions.items()
        }

    def get(self, task_name: str) -> SubAgentTaskSpec:
        if task_name not in self._specs:
            raise KeyError(task_name)
        return self._specs[task_name]
