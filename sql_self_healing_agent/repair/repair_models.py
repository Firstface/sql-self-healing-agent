from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from sql_self_healing_agent.core.enums import RiskLevel
from sql_self_healing_agent.diagnostics.diagnosis_models import DiagnosisResult
from sql_self_healing_agent.logs.log_models import LogDigest
from sql_self_healing_agent.memory.memory_models import MemoryRetrievalResult
from sql_self_healing_agent.metadata.metadata_models import MetadataSnapshot


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RepairActionType(str, Enum):
    REPLACE_COLUMN = "REPLACE_COLUMN"
    ADD_CAST = "ADD_CAST"
    REPLACE_TABLE = "REPLACE_TABLE"
    ADD_PARTITION_FILTER = "ADD_PARTITION_FILTER"
    REWRITE_FUNCTION = "REWRITE_FUNCTION"
    QUALIFY_COLUMN = "QUALIFY_COLUMN"
    FIX_SYNTAX = "FIX_SYNTAX"
    NO_SAFE_REPAIR = "NO_SAFE_REPAIR"


class RepairAction(StrictModel):
    action_type: RepairActionType
    target_fragment: str | None = None
    replacement_fragment: str | None = None
    reason: str
    evidence: str | None = None
    risk_level: Literal["LOW", "MEDIUM", "HIGH"]


class RepairPlan(StrictModel):
    plan_id: str
    repairable: bool
    actions: list[RepairAction] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    referenced_experience_ids: list[str] = Field(default_factory=list)
    manual_repair_recommendation: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)


class RepairPlannerInput(StrictModel):
    failed_sql: str
    diagnosis: DiagnosisResult
    log_digest: LogDigest
    metadata_snapshot: MetadataSnapshot | None = None
    memory_retrieval: MemoryRetrievalResult | None = None
    post_reflection_result: dict | None = None


class SQLGeneratorInput(StrictModel):
    failed_sql: str
    repair_plan: RepairPlan


class ChangedFragment(StrictModel):
    before: str
    after: str
    action_type: RepairActionType
    reason: str


class SQLGenerationResult(StrictModel):
    generated: bool
    sql_candidate: str | None = None
    cannot_generate_safely: bool = False
    reason: str | None = None


class SQLGeneratorLLMOutput(SQLGenerationResult):
    changed_fragments: list[ChangedFragment] = Field(default_factory=list)


class SQLDiffSummary(StrictModel):
    changed_fragment_count: int
    changed_fragments: list[ChangedFragment]
    removed_where: bool = False
    removed_join_condition: bool = False
    changed_group_by: bool = False
    changed_insert_target: bool = False
    changed_static_partition: bool = False
    parse_success: bool


class ValidationIssue(StrictModel):
    code: str
    message: str
    risk_level: RiskLevel
    evidence: str | None = None


class ValidationResult(StrictModel):
    risk_level: RiskLevel
    passed: bool
    issues: list[ValidationIssue] = Field(default_factory=list)
    allow_return_sql: bool
    reason: str | None = None
