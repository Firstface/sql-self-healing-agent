from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from sql_self_healing_agent.agent.artifacts.artifact_ref import ArtifactRef
from sql_self_healing_agent.agent.models.tool_models import ToolSpec
from sql_self_healing_agent.agent.models.execution_plan import ExecutionPlan


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ContextItem(StrictModel):
    item_id: str
    item_type: Literal[
        "TASK_INPUT", "CONSTRAINT", "WORKSPACE_VALUE", "OBSERVATION", "PLAN",
        "CANDIDATE", "GATE_FEEDBACK", "ARTIFACT_REF", "MEMORY_RESULT", "SUB_AGENT_RESULT",
    ]
    key: str
    summary: str
    content_ref: str | None = None
    priority: Literal["P0", "P1", "P2", "P3", "P4", "P5"]
    source: str
    created_at: str
    expires_at: str | None = None
    removable: bool
    security_critical: bool


class MainAgentInput(StrictModel):
    goal: str
    original_sql: str
    error_message: str | None
    execution_plan: ExecutionPlan
    execution_plan_summary: str
    current_phase: str
    workspace_summaries: dict[str, str]
    recent_observations: list[dict[str, object]]
    candidate_summary: str
    gate_feedback_summary: list[str]
    artifact_refs: list[ArtifactRef]
    available_tools: list[ToolSpec]
    remaining_budget: dict[str, int]


class SubAgentInput(StrictModel):
    task_name: str
    objective: str
    constraints: list[str]
    inline_context: dict[str, str]
    artifact_refs: list[ArtifactRef]
    expected_output_schema: str
    remaining_budget: dict[str, int]


class GateEvidence(StrictModel):
    original_sql: str
    candidate_sql: str
    target_table: str | None
    static_partition: str | None
    metadata_snapshot_ref: str | None
    diagnosis_ref: str | None
    gate_feedback: list[str]
    candidate_hash: str
    attempt_id: str
    event_key: str


class ContextSummary(StrictModel):
    current_goal: str
    confirmed_facts: list[str]
    unresolved_questions: list[str]
    important_artifact_refs: list[str]
    current_plan_step: str | None
    candidate_status: str
    gate_constraints: list[str]


class CompactionLimits(StrictModel):
    max_calls: int = Field(default=2, ge=0, le=2)
    max_output_tokens: int = Field(default=2000, ge=1, le=2000)
    timeout_ms: int = Field(default=10000, ge=1, le=10000)


class ContextSnapshot(StrictModel):
    snapshot_id: str
    session_id: str
    attempt_id: str
    before_hash: str
    after_hash: str
    retained_keys: list[str]
    removed_keys: list[str]
    artifact_refs: list[str]
    compaction_method: Literal["DETERMINISTIC_TRIM", "LLM_SUMMARY"]
    created_at: str
