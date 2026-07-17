from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SubAgentLimits(StrictModel):
    max_steps: int = Field(default=10, ge=1, le=10)
    max_tool_calls: int = Field(default=3, ge=0, le=3)
    max_wall_time_ms: int = Field(default=30000, ge=1, le=30000)
    max_no_progress_steps: int = Field(default=2, ge=1, le=2)
    max_context_requests: int = Field(default=1, ge=0, le=1)
    allow_recursive_sub_agent: bool = False

    @model_validator(mode="after")
    def prohibit_recursion(self) -> "SubAgentLimits":
        if self.allow_recursive_sub_agent:
            raise ValueError("recursive sub-agent is forbidden")
        return self


class SubAgentRequest(StrictModel):
    task_name: str
    objective: str
    context_refs: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    expected_output_schema: str
    limits: SubAgentLimits = Field(default_factory=SubAgentLimits)


class SubAgentResult(StrictModel):
    status: Literal["SUCCEEDED", "NEED_MORE_CONTEXT", "FAILED", "BUDGET_EXCEEDED", "HUMAN_REQUIRED"]
    summary: str
    artifact_ref: str | None = None
    requested_context_refs: list[str] = Field(default_factory=list)
    structured_output: dict[str, object] | None = None
    stop_reason: str | None = None


class SubAgentTaskSpec(StrictModel):
    task_name: str
    description: str
    objective_template: str
    allowed_tools: list[str]
    required_context_refs: list[str]
    output_schema_name: str
    max_steps: int = Field(le=10)
    max_tool_calls: int = Field(le=3)
    allow_recursive_sub_agent: bool = False
