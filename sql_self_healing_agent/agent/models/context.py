from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from sql_self_healing_agent.agent.models.action import AgentAction
from sql_self_healing_agent.agent.models.candidate import CandidateState
from sql_self_healing_agent.agent.models.execution_plan import ExecutionPlan
from sql_self_healing_agent.agent.models.observation import Observation


class WorkspaceValue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["AVAILABLE", "FAILED", "MISSING"]
    summary: str | None = None
    artifact_ref: str | None = None
    updated_at: str


class AgentContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    attempt_id: str
    event_key: str
    original_sql: str
    error_message: str | None = None
    log_path: str | None = None
    workspace: dict[str, WorkspaceValue] = Field(default_factory=dict)
    execution_plan: ExecutionPlan
    candidate: CandidateState = Field(default_factory=CandidateState)
    recent_observations: list[Observation] = Field(default_factory=list)
    phase: Literal["INIT", "DIAGNOSING", "PLANNING", "GENERATING", "GATING", "COMPLETED"] = "INIT"
    last_action: AgentAction | None = None
    last_error: str | None = None
