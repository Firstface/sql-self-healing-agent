from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from sql_self_healing_agent.diagnostics.diagnosis_models import DiagnosisResult
from sql_self_healing_agent.memory.memory_models import MemoryRetrievalResult
from sql_self_healing_agent.metadata.metadata_models import MetadataSnapshot
from sql_self_healing_agent.repair.repair_models import RepairPlan, SQLDiffSummary, ValidationResult


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GateFeedback(StrictModel):
    feedback_id: str
    gate_name: str
    code: str
    severity: Literal["INFO", "WARNING", "ERROR", "BLOCK"]
    message: str
    repair_hint: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    candidate_hash: str
    created_at: str


class GateResult(StrictModel):
    decision: Literal["PASS", "PASS_WITH_WARNING", "REJECT", "HUMAN_REQUIRED"]
    risk_level: Literal["LOW", "MEDIUM", "HIGH", "BLOCKED"]
    gate_name: str
    candidate_hash: str
    feedback: list[GateFeedback] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    checked_invariants: list[str] = Field(default_factory=list)
    failed_invariants: list[str] = Field(default_factory=list)
    created_at: str


class GateRequest(StrictModel):
    original_sql: str
    candidate_sql: str
    diagnosis: DiagnosisResult
    metadata_snapshot: MetadataSnapshot | None = None
    memory_retrieval: MemoryRetrievalResult | None = None
    existing_plan: RepairPlan | None = None
    candidate_artifact_ref: str | None = None
    attempt_id: str
    event_key: str


class StaticGateOutcome(StrictModel):
    result: GateResult
    repair_plan: RepairPlan | None = None
    sql_diff_summary: SQLDiffSummary | None = None
    validation_result: ValidationResult | None = None
