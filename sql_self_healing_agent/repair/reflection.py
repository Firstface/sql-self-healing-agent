from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from sql_self_healing_agent.core.enums import RiskLevel
from sql_self_healing_agent.diagnostics.diagnosis_models import DiagnosisResult
from sql_self_healing_agent.memory.memory_models import MemoryRetrievalResult
from sql_self_healing_agent.metadata.metadata_models import MetadataSnapshot
from sql_self_healing_agent.repair.repair_models import RepairPlan, SQLDiffSummary, ValidationResult


class PreReflectionDecision(str, Enum):
    RETURN_SQL = "RETURN_SQL"
    REGENERATE = "REGENERATE"
    BLOCK = "BLOCK"
    MANUAL_REQUIRED = "MANUAL_REQUIRED"


class PreReflectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    failed_sql: str
    sql_candidate: str
    diagnosis: DiagnosisResult
    repair_plan: RepairPlan
    validation_result: ValidationResult
    sql_diff_summary: SQLDiffSummary
    metadata_snapshot: MetadataSnapshot | None = None
    memory_retrieval: MemoryRetrievalResult | None = None


class PreReflectionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: PreReflectionDecision
    confidence: float = Field(ge=0.0, le=1.0)
    follows_repair_plan: bool
    minimal_change: bool
    semantic_risk_level: RiskLevel
    reasons: list[str] = Field(default_factory=list)
    violated_constraints: list[str] = Field(default_factory=list)
    regeneration_instruction: str | None = None
