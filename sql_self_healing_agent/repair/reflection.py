from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from sql_self_healing_agent.core.enums import RiskLevel
from sql_self_healing_agent.diagnostics.diagnosis_models import (
    DiagnosisHistoryItem,
    DiagnosisResult,
)
from sql_self_healing_agent.logs.log_models import LogDigest
from sql_self_healing_agent.memory.memory_models import MemoryRetrievalResult
from sql_self_healing_agent.metadata.metadata_models import MetadataSnapshot
from sql_self_healing_agent.repair.repair_models import (
    RepairPlan,
    SQLDiffSummary,
    ValidationResult,
)
from sql_self_healing_agent.session.session_models import RepairAttempt


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PreReflectionDecision(str, Enum):
    RETURN_SQL = "RETURN_SQL"
    REGENERATE = "REGENERATE"
    BLOCK = "BLOCK"
    MANUAL_REQUIRED = "MANUAL_REQUIRED"


class PreReflectionInput(StrictModel):
    failed_sql: str
    sql_candidate: str
    diagnosis: DiagnosisResult
    repair_plan: RepairPlan
    validation_result: ValidationResult
    sql_diff_summary: SQLDiffSummary
    metadata_snapshot: MetadataSnapshot | None = None
    memory_retrieval: MemoryRetrievalResult | None = None


class PreReflectionResult(StrictModel):
    decision: PreReflectionDecision
    confidence: float = Field(ge=0.0, le=1.0)
    follows_repair_plan: bool
    minimal_change: bool
    semantic_risk_level: RiskLevel
    reasons: list[str] = Field(default_factory=list)
    violated_constraints: list[str] = Field(default_factory=list)
    regeneration_instruction: str | None = None


class PostReflectionStatus(str, Enum):
    FAILED_BUT_PROGRESSING = "FAILED_BUT_PROGRESSING"
    FAILED_UNCHANGED = "FAILED_UNCHANGED"
    FAILED_REGRESSED = "FAILED_REGRESSED"
    FAILED_UNRELATED = "FAILED_UNRELATED"
    MANUAL_REQUIRED = "MANUAL_REQUIRED"
    OSCILLATING = "OSCILLATING"


class PostReflectionInput(StrictModel):
    previous_attempt: RepairAttempt
    previous_diagnosis: DiagnosisResult
    previous_repair_plan: RepairPlan
    previous_sql_candidate: str
    current_failed_sql: str
    current_log_digest: LogDigest
    current_diagnosis: DiagnosisResult
    diagnosis_history: list[DiagnosisHistoryItem] = Field(default_factory=list)


class PostReflectionResult(StrictModel):
    status: PostReflectionStatus
    previous_error_resolved: bool
    new_error_introduced: bool
    recommendation_for_next_plan: str | None = None
    reasons: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
