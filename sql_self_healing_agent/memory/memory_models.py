from pydantic import BaseModel, ConfigDict, Field

from sql_self_healing_agent.core.enums import DiagnosedErrorType, ExperienceStatus


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RepairStep(StrictModel):
    step_no: int
    description: str
    before_fragment: str | None = None
    after_fragment: str | None = None


class Experience(StrictModel):
    schema_version: int = 1
    experience_id: str
    status: ExperienceStatus = ExperienceStatus.ACTIVE
    source_session_id: str
    source_attempt_id: str
    task_id: str
    diagnosed_error_type: DiagnosedErrorType
    diagnosed_keywords: list[str]
    error_fingerprint: str
    primary_entity: str | None = None
    original_sql: str
    failed_sql: str
    confirmed_sql: str
    repair_steps: list[RepairStep] = Field(default_factory=list)
    metadata_summary: dict = Field(default_factory=dict)
    verified_count: int = 1
    failed_count: int = 0
    last_failed_reason: str | None = None
    created_at: str
    updated_at: str
    last_verified_at: str
    last_failed_at: str | None = None


class RetrievedExperience(StrictModel):
    experience_id: str
    score: float
    match_reasons: list[str]
    experience: dict


class MemoryRetrievalResult(StrictModel):
    retrieved: list[RetrievedExperience]
    fingerprint_matches: list[str] = Field(default_factory=list)
    keyword_matches: list[str] = Field(default_factory=list)
