from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from sql_self_healing_agent.agent.models.context import WorkspaceValue


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ToolSpec(StrictModel):
    name: str
    description: str
    input_schema: dict[str, object]
    allowed_phases: list[str]
    side_effect_level: Literal["READ_ONLY", "INTERNAL_ARTIFACT_WRITE", "NO_EXTERNAL_SIDE_EFFECT"]


class ToolCallResult(StrictModel):
    tool_name: str
    status: Literal["SUCCEEDED", "FAILED", "BLOCKED", "TIMEOUT", "INVALID_INPUT"]
    summary: str | None = None
    artifact_refs: list[str] = Field(default_factory=list)
    workspace_updates: dict[str, WorkspaceValue] = Field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None
    started_at: str
    finished_at: str
