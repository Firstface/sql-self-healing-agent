from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExecutionStep(StrictModel):
    step_id: str
    title: str
    action_type: Literal["TOOL_CALL", "RUN_SUB_AGENT", "PROPOSE_SQL_CANDIDATE"]
    tool_name: str | None = None
    tool_input: dict[str, object] = Field(default_factory=dict)
    status: Literal["PENDING", "IN_PROGRESS", "COMPLETED", "BLOCKED", "SKIPPED"] = "PENDING"
    depends_on: list[str] = Field(default_factory=list)
    execution_count: int = 0
    result_refs: list[str] = Field(default_factory=list)
    failure_reason: str | None = None


class ExecutionPlan(StrictModel):
    revision: int = 0
    steps: list[ExecutionStep] = Field(default_factory=list)
    current_step_id: str | None = None
    summary: str | None = None


def build_initial_execution_plan() -> ExecutionPlan:
    return ExecutionPlan(
        steps=[
            ExecutionStep(step_id="read_log", title="读取并整理错误日志", action_type="TOOL_CALL", tool_name="build_log_digest"),
            ExecutionStep(step_id="diagnose", title="诊断 SQL 错误", action_type="TOOL_CALL", tool_name="diagnose", depends_on=["read_log"]),
            ExecutionStep(step_id="query_metadata", title="查询必要元数据", action_type="TOOL_CALL", tool_name="query_metadata", depends_on=["diagnose"]),
            ExecutionStep(step_id="retrieve_memory", title="检索相关成功经验", action_type="TOOL_CALL", tool_name="retrieve_memory", depends_on=["diagnose"]),
            ExecutionStep(step_id="generate_candidate", title="生成候选 SQL", action_type="TOOL_CALL", tool_name="generate_candidate", depends_on=["diagnose", "query_metadata", "retrieve_memory"]),
        ],
        current_step_id="read_log",
    )
