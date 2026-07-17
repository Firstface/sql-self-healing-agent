from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    observation_id: str
    action_type: str
    status: Literal["SUCCEEDED", "FAILED", "BLOCKED", "SKIPPED"]
    summary: str
    artifact_refs: list[str] = Field(default_factory=list)
    produced_workspace_keys: list[str] = Field(default_factory=list)
    plan_step_id: str | None = None
    created_at: str
