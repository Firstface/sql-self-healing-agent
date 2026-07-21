from typing import Literal

from pydantic import BaseModel, ConfigDict


class AgentRunState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    started_at: str
    step_count: int = 0
    tool_call_count: int = 0
    sub_agent_call_count: int = 0
    llm_call_count: int = 0
    wall_time_ms: int = 0
    gate_repair_rounds: int = 0
    plan_revision_count: int = 0
    no_progress_steps: int = 0
    status: Literal["RUNNING", "SUCCEEDED", "NO_SQL", "HUMAN_REQUIRED", "FAILED"] = "RUNNING"
    stop_reason: str | None = None


class AgentRunLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_steps: int = 12
    max_tool_calls: int = 10
    max_sub_agent_calls: int = 4
    max_wall_time_ms: int = 240000
    max_plan_revisions: int = 6
    max_gate_repair_rounds: int = 1
    max_no_progress_steps: int = 2
