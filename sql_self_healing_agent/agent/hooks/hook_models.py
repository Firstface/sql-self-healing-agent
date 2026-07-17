from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OperationContext(StrictModel):
    operation_id: str
    operation_type: Literal["LLM_CALL", "TOOL_CALL", "SUB_AGENT_RUN", "GATE_RUN", "CONTEXT_COMPACTION"]
    session_id: str
    attempt_id: str | None = None
    parent_operation_id: str | None = None
    caller: Literal["MAIN_AGENT", "SUB_AGENT", "GATE_RUNNER", "CONTEXT_MANAGER", "SYSTEM"]
    purpose: str
    started_at: str
    finished_at: str | None = None
    status: Literal["CREATED", "RUNNING", "SUCCEEDED", "BEFORE_BLOCKED", "FAILED", "TIMEOUT", "CANCELLED"] = "CREATED"
    error_code: str | None = None
    artifact_ref: str | None = None
    input_summary: str | None = None
    output_summary: str | None = None


class HookDecision(StrictModel):
    action: Literal["CONTINUE", "BLOCK"]
    reason_code: str | None = None
    message: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
