from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExecutionStep(StrictModel):
    step_id: str
    title: str
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
            ExecutionStep(step_id="read_log", title="读取并整理错误日志"),
            ExecutionStep(step_id="diagnose", title="诊断 SQL 错误", depends_on=["read_log"]),
            ExecutionStep(step_id="query_metadata", title="查询必要元数据", depends_on=["diagnose"]),
            ExecutionStep(step_id="retrieve_memory", title="检索相关成功经验", depends_on=["diagnose"]),
            ExecutionStep(step_id="generate_candidate", title="生成候选 SQL", depends_on=["diagnose", "query_metadata", "retrieve_memory"]),
            ExecutionStep(step_id="gate_candidate", title="执行三关 Gate", depends_on=["generate_candidate"]),
        ],
        current_step_id="read_log",
    )
