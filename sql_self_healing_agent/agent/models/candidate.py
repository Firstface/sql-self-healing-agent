from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class GateFeedback(BaseModel):
    model_config = ConfigDict(extra="forbid")
    gate_name: str
    decision: Literal["PASS", "REJECT", "HUMAN_REQUIRED"]
    reason: str


class CandidateState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    draft_sql: str | None = None
    draft_artifact_ref: str | None = None
    formal_sql: str | None = None
    status: Literal["NONE", "DRAFT", "GATE_REJECTED", "READY"] = "NONE"
    gate_feedback: list[GateFeedback] = Field(default_factory=list)
