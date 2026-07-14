from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from sql_self_healing_agent.core.enums import AttemptStatus, SessionStatus
from sql_self_healing_agent.diagnostics.diagnosis_models import DiagnosisHistoryItem


class RepairSession(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    session_id: str
    task_id: str
    original_sql: str
    status: SessionStatus = SessionStatus.CREATED

    latest_sql_candidate: str | None = None
    latest_sql_candidate_attempt_id: str | None = None

    confirmed_sql: str | None = None
    confirmed_attempt_id: str | None = None

    upstream_events: list["UpstreamTaskEventRecord"] = Field(default_factory=list)
    attempt_ids: list[str] = Field(default_factory=list)
    diagnosis_history: list[DiagnosisHistoryItem] = Field(default_factory=list)

    trace_path: str
    artifact_dir: str

    created_at: str
    updated_at: str


class UpstreamTaskEventRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str
    task_id: str
    status: Literal["FAILED", "SUCCESS"]
    sql: str
    error_message: str | None = None
    log_path: str | None = None
    received_at: str


class RepairAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    attempt_id: str
    attempt_no: int
    status: AttemptStatus = AttemptStatus.CREATED

    input_event_id: str
    input_failed_sql: str
    input_error_message: str | None = None
    input_log_path: str | None = None

    previous_attempt_id: str | None = None
    post_reflection_result_path: str | None = None

    log_digest_path: str | None = None
    diagnosis_path: str | None = None
    metadata_snapshot_path: str | None = None
    memory_retrieval_path: str | None = None
    repair_plan_path: str | None = None
    sql_candidate_path: str | None = None
    validation_result_path: str | None = None
    pre_reflection_result_path: str | None = None

    sql_candidate: str | None = None
    diagnosed_error_type: str | None = None
    diagnosed_keywords: list[str] = Field(default_factory=list)
    error_fingerprint: str | None = None

    created_at: str
    updated_at: str


RepairSession.model_rebuild()
