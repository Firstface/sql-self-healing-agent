from typing import Literal

from pydantic import BaseModel, ConfigDict


class AgentRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["CANDIDATE_READY", "NO_SQL", "HUMAN_REQUIRED", "FAILED"]
    candidate_sql: str | None = None
    candidate_artifact_ref: str | None = None
    risk_level: Literal["LOW", "MEDIUM", "HIGH", "BLOCKED"] | None = None
    gate_result_ref: str | None = None
    reason: str | None = None
    stop_reason: str | None = None
    plan_revision: int
    step_count: int
    trace_ref: str | None = None
